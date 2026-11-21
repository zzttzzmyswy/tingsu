"""OpenAI Responses streams backed by durable Conversations API IDs."""

import hashlib
import json
import os
import re
from urllib.parse import quote, urlsplit

import httpx

from .storage import OpenAISessionStore
from .agent_materials import AgentError
from .agent_tools import GUIDANCE, OPENAI_TOOLS, execute


class OpenAIBackend:
    def __init__(self, api_key, model):
        self.key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or "gpt-5-mini"
        self.http = None
        default = "https://api.openai.com/v1/"
        self.base_url = (os.getenv("OPENAI_BASE_URL") or default).rstrip("/") + "/"
        address = urlsplit(self.base_url)
        local_http = address.scheme == "http" and address.hostname in {"localhost", "127.0.0.1", "::1"}
        if address.scheme != "https" and not local_http:
            raise AgentError("OpenAI API 地址需要 HTTPS；本机服务可使用回环地址。")
        account = self.key if self.base_url == default else self.base_url + "\n" + self.key
        self.history = OpenAISessionStore(hashlib.sha256(account.encode()).hexdigest()[:24])

    async def list_sessions(self):
        return self.history.sessions()

    def _check(self, response):
        if response.is_success:
            return
        try:
            detail = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = response.reason_phrase
        detail = str(detail).replace(self.key, "•••") if self.key else str(detail)
        detail = re.sub(r"sk-[\w*.-]+", "•••", detail)
        raise AgentError(f"OpenAI HTTP {response.status_code}：{detail[:500]}")

    async def connect(self):
        if not self.key:
            raise AgentError("请在设置中填写 OpenAI API Key，或设置 OPENAI_API_KEY。")
        if not self.http:
            self.http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": "Bearer " + self.key},
                timeout=httpx.Timeout(120, connect=15),
            )
        result = await self.http.get("models/" + quote(self.model, safe=""))
        self._check(result)

    async def open_session(self, session_id, title):
        if session_id:
            if not re.fullmatch(r"conv_[A-Za-z0-9_-]+", session_id):
                raise AgentError("请输入 Conversations API 的 conv_ 会话 ID。")
            response = await self.http.get("conversations/" + session_id)
        else:
            response = await self.http.post("conversations", json={"metadata": {"title": title[:500]}})
        self._check(response)
        data = response.json()
        title = (data.get("metadata") or {}).get("title", title)
        self.history.remember(data["id"], title)
        return data["id"]

    async def _interrupted_outputs(self, session_id, cached):
        # Conversations persist function calls even if the subsequent response stream
        # is lost. Repair outstanding calls without repeating note mutations.
        response = await self.http.get(
            f"conversations/{session_id}/items", params={"order": "desc", "limit": 100}
        )
        self._check(response)
        seen, missing = set(), []
        for item in response.json()["data"]:
            if item.get("role") == "user":
                break
            call_id = item.get("call_id")
            if item["type"] == "function_call_output":
                seen.add(call_id)
            elif item["type"] == "function_call" and call_id not in seen:
                missing.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": cached.get(
                            call_id, '{"error":"上次请求中断，请先检查当前会议状态。"}'
                        ),
                    }
                )
        return list(reversed(missing))

    async def _stream(self, payload, on_delta):
        parts = []
        completed = None
        async with self.http.stream("POST", "responses", json=payload) as response:
            if not response.is_success:
                await response.aread()
                self._check(response)
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                kind = event.get("type")
                if kind in ("response.output_text.delta", "response.refusal.delta"):
                    parts.append(event["delta"])
                    on_delta(event["delta"])
                elif kind == "response.completed":
                    completed = event["response"]
                    break
                elif kind in ("error", "response.failed", "response.incomplete"):
                    result = event.get("response") or {}
                    err = result.get("error") or result.get("incomplete_details") or event
                    raise AgentError(
                        "OpenAI：" + str(err.get("message") or err.get("reason") or kind)[:600]
                    )
        if completed is None:
            raise AgentError("OpenAI 的流式连接提前结束，请检查网络并重试。")
        return completed, "".join(parts)

    async def send(self, session_id, inputs, on_delta, handler):
        cached = self.history.pending(session_id)
        items = await self._interrupted_outputs(session_id, cached) if cached is not None else []
        items.append({"role": "user", "content": inputs})
        self.history.save_pending(session_id, cached or {})
        messages = []
        for _ in range(8):
            current = []

            def emit(text):
                if messages and not current:
                    on_delta("\n")
                current.append(text)
                on_delta(text)

            payload = {
                "model": self.model,
                "conversation": session_id,
                "instructions": GUIDANCE,
                "input": items,
                "stream": True,
            }
            if handler:
                payload["tools"] = OPENAI_TOOLS
                payload["parallel_tool_calls"] = False
            result, message = await self._stream(payload, emit)
            if message:
                messages.append(message)
            calls = [item for item in result.get("output", []) if item.get("type") == "function_call"]
            if not calls:
                self.history.save_pending(session_id, None)
                return "\n".join(messages)
            items = [
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": execute(handler, call["name"], call["arguments"]),
                }
                for call in calls
            ]
            self.history.save_pending(session_id, {item["call_id"]: item["output"] for item in items})
        raise AgentError("已完成八轮会议工具调用，请继续提问。")

    async def close(self):
        if self.http:
            await self.http.aclose()
            self.http = None
