"""DeepSeek Harness's ACP stdio client and its per-session meeting MCP endpoint."""

import asyncio
import contextlib
import hmac
import json
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent_materials import AgentCancelled, AgentError
from .agent_tools import GUIDANCE, TOOLS
from .local_cli import LocalCLI
from .meeting_mcp import meeting_mcp_reply
from .runtime import app_directory


# ACP mounts client-supplied MCP tools in the session's agent scope. Hide global coding
# tools and reject unrelated scoped tools as well. The ACP row explicitly requires this
# plugin's readiness, so a missing/incompatible guard cannot start an unrestricted server.
_GUARD = """
export const name = 'tingsu-meeting-tools';
export const inject = ['agents', 'tools'];
export function apply(ctx) {
  const allowed = new Set(ALLOWED_TOOLS);
  ctx.on('agent/created', ({agent}) => {
    agent.ctx.tools.restrict({allow: []});
    agent.ctx.tools.guard(call => allowed.has(call.name) ? undefined : 'Meeting tools only');
  }, {prepend: true});
  ctx.provide('tingsuMeetingTools', {ready: true});
}
"""
_TOOL_NAMES = {f"mcp__tingsu__{tool}" for tool in (*TOOLS, "load_tools")}


def _profile(directory):
    plugin = directory / "meeting-tools.mjs"
    plugin.write_text(_GUARD.replace("ALLOWED_TOOLS", json.dumps(sorted(_TOOL_NAMES))), encoding="utf-8")
    patch = directory / "meeting.patch.yml"
    patch.write_text(json.dumps([
        {"insert": [{"id": "tingsu-tools", "name": plugin.as_uri()}]},
        {"id": "acp", "inject": ["acpAppStartup", "tingsuMeetingTools"]},
        {"id": "system-prompt", "config": {"personaPrefix": GUIDANCE}},
        {"id": "agent-instructions", "disabled": True},
        {"id": "mcp-resources", "disabled": True},
        {"id": "session-log-deepseek", "config": {"enabled": False}},
    ], ensure_ascii=False), encoding="utf-8")
    return str(patch)


class _MeetingEndpoint:
    """An authenticated, stateless Streamable HTTP MCP server bound only to loopback."""

    def __init__(self):
        self.handler = None
        self.token = secrets.token_urlsafe(32)
        endpoint = self

        class Request(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(8)

            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/mcp":
                    return self.send_error(404)
                if self.headers.get("Origin") or not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(), ("Bearer " + endpoint.token).encode()
                ):
                    return self.send_error(403)
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 2 * 1024 * 1024:
                        return self.send_error(413)
                    message = json.loads(self.rfile.read(size))
                except (ValueError, OSError):
                    return self.send_error(400)
                response = meeting_mcp_reply(message, endpoint.call)
                data = json.dumps(response, ensure_ascii=False).encode() if response is not None else b""
                self.send_response(200 if response is not None else 202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send_error(405)  # Optional standalone SSE stream.

            do_DELETE = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Request)
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def call(self, name, args):
        handler = self.handler
        if handler is None:
            raise AgentError("本场会议当前没有正在执行的 AI 请求。")
        return handler(name, args)

    def descriptor(self):
        return {"type": "http", "name": "tingsu", "url": f"http://127.0.0.1:{self.server.server_port}/mcp",
                "headers": [{"name": "Authorization", "value": "Bearer " + self.token}]}

    def close(self):
        self.handler = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class DeepSeekBackend:
    def __init__(self, model="", executable=""):
        self.model = model
        self.cli = LocalCLI("deepseek", executable)
        self.proc = self.reader = self.profile = self.endpoint = None
        self.pending = {}
        self.next_id = 0
        self.sessions = {}
        self.active = None
        self.on_message = None
        self.can_image = False
        self.permission_calls = {}

    async def connect(self):
        if self.proc and self.proc.returncode is None:
            return
        self.cwd = app_directory("data").resolve() / "deepseek-workspace"
        self.cwd.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.profile = tempfile.TemporaryDirectory(prefix="tingsu-dsh-")
        try:
            patch = _profile(Path(self.profile.name).resolve())
            self.proc = await self.cli.spawn(
                "acp", "--patch", patch, cwd=str(self.cwd), env_overrides={"DSH_PERMISSION_MODE": "read-only"},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=32 * 1024 * 1024,
            )
            self.reader = asyncio.create_task(self._read())
            # A cold Windows install can spend over a minute loading Node dependencies.
            try:
                result = await self.rpc("initialize", {
                    "protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "tingsu", "version": "1"},
                }, timeout=120)
            except asyncio.TimeoutError as exc:
                raise AgentError("本机 DeepSeek Harness 在 120 秒内未完成启动，请检查 dsh 安装路径后重连。") from exc
            caps = result.get("agentCapabilities", {})
            sessions = caps.get("sessionCapabilities", {})
            if (result.get("protocolVersion") != 1 or not caps.get("mcpCapabilities", {}).get("http")
                or not all(k in sessions for k in ("list", "resume", "close"))):
                raise AgentError("DeepSeek Harness 缺少所需的 ACP 会话或 HTTP MCP 能力，请更新本机 dsh。")
            self.can_image = bool(caps.get("promptCapabilities", {}).get("image"))
        except BaseException:
            await self.close()
            raise

    async def _write(self, packet):
        if not self.proc or self.proc.returncode is not None:
            raise AgentError("DeepSeek Harness 已断开，请重新连接。")
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **packet}, ensure_ascii=False).encode() + b"\n")
        await self.proc.stdin.drain()

    async def _read(self):
        try:
            while line := await self.proc.stdout.readline():
                packet = json.loads(line)
                if "method" not in packet:
                    future = self.pending.get(packet.get("id"))
                    if future and not future.done():
                        future.set_result(packet)
                elif "id" in packet:
                    if packet["method"] == "session/request_permission":
                        params = packet.get("params", {})
                        call_id = params.get("toolCall", {}).get("toolCallId")
                        allowed = (params.get("sessionId") == self.active and self.on_message is not None
                                   and self.permission_calls.get(call_id) in _TOOL_NAMES)
                        kind = "allow_once" if allowed else "reject_once"
                        option = next((v for v in params.get("options", []) if v["kind"] == kind), None)
                        outcome = {"outcome": "selected", "optionId": option["optionId"]} if option else {"outcome": "cancelled"}
                        await self._write({"id": packet["id"], "result": {"outcome": outcome}})
                    else:
                        await self._write({"id": packet["id"], "error": {"code": -32601, "message": "Method not supported"}})
                elif packet["method"] == "session/update":
                    params = packet.get("params", {})
                    if params.get("sessionId") == self.active and self.on_message:
                        update = params["update"]
                        if update["sessionUpdate"] == "tool_call":
                            self.permission_calls[update["toolCallId"]] = update.get("title")
                        elif update["sessionUpdate"] == "agent_message_chunk" and update["content"]["type"] == "text":
                            self.on_message(update["content"]["text"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(AgentError("DeepSeek Harness 的 ACP 进程已断开，请检查 dsh 安装与配置并重新连接。"))

    async def rpc(self, method, params, timeout=45):
        self.next_id += 1
        key = self.next_id
        self.pending[key] = future = asyncio.get_running_loop().create_future()
        try:
            if self.reader is None or self.reader.done():
                raise AgentError("DeepSeek Harness 已断开，请重新连接。")
            await self._write({"id": key, "method": method, "params": params})
            packet = await asyncio.wait_for(future, timeout)
            if "error" in packet:
                detail = str(packet["error"].get("message", "请求失败"))
                if self.endpoint:
                    detail = detail.replace(self.endpoint.token, "…")
                raise AgentError(f"DeepSeek Harness：{detail[:550]}")
            return packet["result"]
        finally:
            self.pending.pop(key, None)

    async def list_sessions(self):
        summaries = []
        cursor = None
        seen = set()
        self.sessions = {}
        while True:
            page = await self.rpc("session/list", {"cursor": cursor} if cursor else {})
            for item in page["sessions"]:
                sid, cwd = item["sessionId"], item["cwd"]
                self.sessions[sid] = cwd
                summaries.append({"id": sid, "title": item.get("title") or f"{Path(cwd).name} · {sid[:12]}"})
            cursor = page.get("nextCursor")
            if not cursor:
                return summaries
            if cursor in seen:
                raise AgentError("DeepSeek Harness 返回了重复的会话分页。")
            seen.add(cursor)

    async def _close_session(self):
        if self.active:
            await self.rpc("session/close", {"sessionId": self.active}, 5)
            self.active = None
        if self.endpoint:
            self.endpoint.close()
            self.endpoint = None

    async def open_session(self, session_id, title):
        if session_id and self.active == session_id:
            return session_id
        await self._close_session()
        if session_id and session_id not in self.sessions:
            await self.list_sessions()
        if session_id and session_id not in self.sessions:
            raise AgentError("DeepSeek Harness 找不到可续接的会话。请关闭占用它的客户端并刷新列表。")
        self.endpoint = _MeetingEndpoint()
        try:
            params = {"cwd": self.sessions[session_id] if session_id else str(self.cwd),
                      "mcpServers": [self.endpoint.descriptor()]}
            if session_id:
                result = await self.rpc("session/resume", {**params, "sessionId": session_id})
            else:
                result = await self.rpc("session/new", params)
                session_id = result["sessionId"]
            self.active = session_id
            if self.model:
                option = next((opt for opt in result.get("configOptions", []) if opt["id"] == "model"), {})
                choices = []
                for group in option.get("options", []):
                    for choice in group.get("options", [group]):
                        value = choice["value"]
                        if self.model in (choice.get("name"), value) or json.loads(value)[1] == self.model:
                            choices.append(value)
                if len(choices) != 1:
                    raise AgentError("无法唯一选择 Harness 模型，请使用模型配置中的完整选项值。")
                await self.rpc("session/set_config_option", {"sessionId": session_id, "configId": "model", "value": choices[0]})
            return session_id
        except BaseException:
            with contextlib.suppress(Exception):
                await self._close_session()
            raise

    async def send(self, session_id, inputs, on_delta, handler):
        if session_id != self.active or self.endpoint is None:
            raise AgentError("请重新打开 DeepSeek Harness 会话。")
        prompt = []
        for part in inputs:
            if part["type"] == "text":
                prompt.append({"type": "text", "text": part["text"] + "\n"})
            elif part["type"] == "image":
                if not self.can_image:
                    raise AgentError("当前 DeepSeek Harness ACP 配置未开放图片输入。请使用支持图片的模型路由或移除截图。")
                prefix, encoded = part["url"].split(",", 1)
                prompt.append({"type": "image", "mimeType": prefix[5:].split(";")[0], "data": encoded})
            else:
                raise AgentError("当前 DeepSeek Harness 无法读取此材料类型。")
        chunks = []
        def emit(text):
            chunks.append(text)
            on_delta(text)
        self.permission_calls.clear()
        self.on_message = emit
        self.endpoint.handler = handler
        turn = asyncio.create_task(self.rpc("session/prompt", {"sessionId": session_id, "prompt": prompt}, None))
        try:
            result = await asyncio.wait_for(asyncio.shield(turn), 300)
            reason = result.get("stopReason")
            if reason == "cancelled":
                raise AgentCancelled("已停止回复。")
            if reason != "end_turn" or not "".join(chunks).strip():
                raise AgentError("DeepSeek Harness 未完成文字回复。请检查本地模型和身份配置。")
            return "".join(chunks)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            self.endpoint.handler = None
            self.on_message = None
            try:
                await self._write({"method": "session/cancel", "params": {"sessionId": session_id}})
                await asyncio.wait_for(turn, 6)
            except Exception:
                await self.close()
            raise
        finally:
            self.on_message = None
            if self.endpoint:
                self.endpoint.handler = None
            self.permission_calls.clear()

    async def close(self):
        try:
            if self.proc and self.proc.returncode is None and self.reader and not self.reader.done():
                with contextlib.suppress(Exception):
                    await self._close_session()
        finally:
            self.active = self.on_message = None
            if self.endpoint:
                self.endpoint.close()
                self.endpoint = None
            await self.cli.stop(self.proc)
            if self.reader:
                with contextlib.suppress(Exception):
                    await self.reader
            self.proc = self.reader = None
            if self.profile:
                self.profile.cleanup()
                self.profile = None
