"""Codex app-server's newline-delimited JSON-RPC transport."""

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path

from .agent_materials import AgentCancelled, AgentError
from .runtime import app_directory, child_options, codex_binary
from .agent_tools import CODEX_GUIDANCE, CODEX_OUTPUT, execute


def _visible_prefix(raw):
    """Decode complete escapes from an incrementally streamed JSON message string."""
    header = re.match(r'^\s*\{\s*"message"\s*:\s*"', raw)
    if not header:
        return ""
    match = re.match(r'(?:[^"\\]|\\(?:["\\/bfnrt]|u[\da-fA-F]{4}))*', raw[header.end() :])
    decoded = json.loads('"' + match[0] + '"')
    if decoded and 0xD800 <= ord(decoded[-1]) < 0xDC00:
        return decoded[:-1]
    return decoded


class CodexBackend:
    def __init__(self, model):
        self.model = model
        self.proc = None
        self.reader = None
        self.pending = {}
        self.seq = 0
        self.queue = None
        self.cwd = None
        self.tool_config = {}

    async def connect(self):
        if self.proc and self.proc.returncode is None:
            return
        executable = codex_binary()
        if not executable:
            raise AgentError("找不到 Codex CLI，请先安装并运行 codex login。")
        root = app_directory("data")
        workspace = root / "codex-workspace"
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cwd = str(workspace.resolve())
        cmd = [executable, "app-server", "--stdio", "-c", 'web_search="disabled"']
        for feature in (
            "shell_tool",
            "unified_exec",
            "apps",
            "plugins",
            "computer_use",
            "hooks",
            "image_generation",
            "view_image",
        ):
            cmd += ["-c", f"features.{feature}=false"]
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("SOPHNET_", "SILICONFLOW_", "TINGSU_"))
        }
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=64 * 1024**2,
            **child_options(),
        )
        self.reader = asyncio.create_task(self._read())
        try:
            await self.rpc(
                "initialize",
                {
                    "clientInfo": {"name": "tingsu", "version": "1.2.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self._write({"method": "initialized"})
            account = await self.rpc("account/read", {})
            if not account.get("account") and account.get("requiresOpenaiAuth", True):
                raise AgentError("Codex 尚未登录。请点击「登录 Codex」完成账号授权。")
            config = (await self.rpc("config/read", {"cwd": self.cwd}))["config"]
            self.tool_config = {
                "mcp_servers": {name: {"enabled": False} for name in (config.get("mcp_servers") or {})}
            }
        except BaseException:
            await self.close()
            raise

    async def _write(self, value):
        if not self.proc or self.proc.returncode is not None:
            raise AgentError("Codex 连接已关闭，请重新连接。")
        data = json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _read(self):
        try:
            while line := await self.proc.stdout.readline():
                packet = json.loads(line)
                if "method" in packet:
                    if "id" in packet:  # No third-party tools or approval prompts are serviced.
                        await self._write(
                            {
                                "id": packet["id"],
                                "error": {"code": -32601, "message": "听序仅提供本次会议的操作。"},
                            }
                        )
                    elif self.queue is not None and packet["method"] in (
                        "turn/completed",
                        "item/agentMessage/delta",
                        "item/completed",
                    ):
                        self.queue.put_nowait(packet)
                else:
                    future = self.pending.get(packet.get("id"))
                    if future and not future.done():
                        future.set_result(packet)
        finally:
            error = AgentError("Codex 进程已断开，请重新连接。")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            if self.queue is not None:
                self.queue.put_nowait(None)

    async def rpc(self, method, params):
        self.seq += 1
        seq = self.seq
        future = asyncio.get_running_loop().create_future()
        self.pending[seq] = future
        try:
            await self._write({"id": seq, "method": method, "params": params})
            result = await asyncio.wait_for(future, 45)
            if result.get("error"):
                raise AgentError("Codex：" + str(result["error"].get("message", "请求失败"))[:700])
            return result["result"]
        finally:
            self.pending.pop(seq, None)

    async def list_sessions(self):
        rows = []
        cursor = None
        while True:
            params = {"limit": 100, "sortKey": "updated_at", "modelProviders": []}
            if cursor:
                params["cursor"] = cursor
            page = await self.rpc("thread/list", params)
            for row in page["data"]:
                title = row.get("name") or row.get("preview") or row["id"]
                rows.append({"id": row["id"], "title": " ".join(title.split())[:110]})
            cursor = page.get("nextCursor")
            if not cursor:
                return rows

    async def open_session(self, session_id, title):
        params = {
            "cwd": self.cwd,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "runtimeWorkspaceRoots": [],
            "baseInstructions": CODEX_GUIDANCE,
            "config": self.tool_config,
        }
        if self.model:
            params["model"] = self.model
        if session_id:
            result = await self.rpc(
                "thread/resume", {**params, "threadId": session_id, "excludeTurns": True}
            )
        else:
            result = await self.rpc(
                "thread/start", {**params, "environments": [], "selectedCapabilityRoots": []}
            )
        thread_id = result["thread"]["id"]
        if not session_id and title:
            # Some CLI versions materialize a thread only after the first turn.
            with contextlib.suppress(AgentError):
                await self.rpc("thread/name/set", {"threadId": thread_id, "name": title[:120]})
        return thread_id

    async def _turn(self, session_id, inputs, on_delta):
        self.queue = asyncio.Queue()
        turn_id = None
        finished = False
        try:
            params = {
                "threadId": session_id,
                "input": inputs,
                "outputSchema": CODEX_OUTPUT,
                "environments": [],
                "runtimeWorkspaceRoots": [],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                "effort": "low",
            }
            # A cancellation during turn/start must still collect the new id to interrupt the turn.
            starting = asyncio.create_task(self.rpc("turn/start", params))
            try:
                started = await asyncio.shield(starting)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    started = await starting
                    turn_id = started["turn"]["id"]
                raise
            turn_id = started["turn"]["id"]
            buffers, emitted = {}, {}
            final_raw = ""
            while True:
                packet = await asyncio.wait_for(self.queue.get(), 180)
                if packet is None:
                    raise AgentError("Codex 连接已中断。")
                event, values = packet["method"], packet["params"]
                if values.get("threadId") != session_id:
                    continue
                if event == "turn/completed":
                    turn = values["turn"]
                    if turn["id"] != turn_id:
                        continue
                    finished = True
                    if turn["status"] == "interrupted":
                        raise AgentCancelled("已停止回复。")
                    if turn["status"] != "completed":
                        detail = (turn.get("error") or {}).get("message") or turn["status"]
                        raise AgentError("Codex：" + detail[:700])
                    break
                if values.get("turnId") != turn_id:
                    continue
                if event == "item/agentMessage/delta":
                    key = values["itemId"]
                    raw = buffers.get(key, "") + values["delta"]
                    buffers[key] = raw
                    visible = _visible_prefix(raw)
                    count = emitted.get(key, 0)
                    if len(visible) > count:
                        on_delta(visible[count:])
                        emitted[key] = len(visible)
                elif event == "item/completed":
                    item = values["item"]
                    if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                        final_raw = item["text"]
                        visible = _visible_prefix(final_raw)
                        count = emitted.get(item["id"], 0)
                        if len(visible) > count:
                            on_delta(visible[count:])
            try:
                return json.loads(final_raw)
            except ValueError as exc:
                raise AgentError("Codex 的回复格式不完整，请重试。") from exc
        finally:
            self.queue = None
            if turn_id and not finished:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self.rpc("turn/interrupt", {"threadId": session_id, "turnId": turn_id}), 3
                    )

    async def send(self, session_id, inputs, on_delta, handler):
        messages = []
        for _ in range(8):

            def emit(text):
                if messages and not current:
                    on_delta("\n")
                current.append(text)
                on_delta(text)

            current = []
            reply = await self._turn(session_id, inputs, emit)
            if reply["message"]:
                messages.append(reply["message"])
            actions = reply["actions"]
            if not actions:
                return "\n".join(messages)
            outputs = []
            for action in actions:
                outputs.append(
                    {
                        "name": action["name"],
                        "output": execute(handler, action["name"], action["arguments"]),
                    }
                )
            inputs = [
                {
                    "type": "text",
                    "text": json.dumps({"meeting_tool_results": outputs}, ensure_ascii=False),
                }
            ]
        raise AgentError("已完成八轮会议工具调用，请继续提问。")

    async def close(self):
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
        if self.reader is not None:
            with contextlib.suppress(Exception):
                await self.reader
        self.proc = self.reader = self.cwd = None
