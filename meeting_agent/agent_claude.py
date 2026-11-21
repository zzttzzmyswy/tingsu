"""Claude Code print-mode adapter, using its bidirectional stream-json SDK transport."""

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import uuid

from .agent_materials import AgentError
from .agent_tools import GUIDANCE, TOOLS
from .local_cli import LocalCLI
from .meeting_mcp import meeting_mcp_reply
from .runtime import app_directory


def _uuid(value):
    try:
        normalized = str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None
    return normalized if value.lower() == normalized else None


def _title(path):
    # The native session picker uses metadata from both ends of each transcript.
    # Read bounded windows: a session file may contain many large base64 images.
    fallback = ""
    with path.open("rb") as stream:
        prefix = stream.read(8192)
        size = stream.seek(0, os.SEEK_END)
        chunks = [prefix]
        if size > len(prefix):
            stream.seek(max(len(prefix), size - 8192))
            chunks.append(stream.read(8192))
    title = ""
    for chunk in chunks:
        for line in chunk.splitlines():
            try:
                record = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "custom-title" and isinstance(record.get("customTitle"), str):
                title = record["customTitle"]
            for key in ("aiTitle", "lastPrompt", "summary"):
                if isinstance(record.get(key), str):
                    fallback = record[key]
            message = record.get("message")
            if not fallback and record.get("type") == "user" and isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    fallback = content
                elif isinstance(content, list):
                    fallback = next(
                        (
                            item["text"]
                            for item in content
                            if isinstance(item, dict) and isinstance(item.get("text"), str)
                        ),
                        "",
                    )
    return " ".join((title or fallback or path.stem).split())[:110]


def _content(inputs):
    blocks = []
    for part in inputs:
        if part["type"] == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part["type"] == "image":
            header, data = part["url"].split(",", 1)
            mime = header.removeprefix("data:").removesuffix(";base64")
            blocks.append(
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
            )
        else:
            raise AgentError("Claude Code 收到不支持的材料类型。")
    return blocks


async def _write(proc, packet):
    proc.stdin.write((json.dumps(packet, ensure_ascii=False) + "\n").encode())
    await proc.stdin.drain()


def _control_response(packet, handler):
    request = packet["request"]
    subtype = request.get("subtype")
    reply = {"request_id": packet["request_id"], "subtype": "success"}
    if subtype == "mcp_message" and request.get("server_name") == "meeting":
        mcp = meeting_mcp_reply(request.get("message"), handler)
        # The outer SDK request still needs an acknowledgement for a JSON-RPC notification.
        reply["response"] = {"mcp_response": mcp or {"jsonrpc": "2.0", "result": {}}}
    elif subtype == "can_use_tool":
        reply["response"] = {"behavior": "deny", "message": "本次会议仅启用听序的会议工具。"}
    else:
        reply.update(subtype="error", error="Unsupported control request")
    return {"type": "control_response", "response": reply}


class ClaudeBackend:
    def __init__(self, model="", executable=""):
        self.cli = LocalCLI("claude", executable)
        self.model = model
        self.workspace = app_directory("data") / "claude-workspace"
        self.projects = None
        self.drafts = None
        self.proc = None

    async def _spawn(self, *args, **options):
        self.proc = await self.cli.spawn(
            *args,
            cwd=self.workspace,
            stdout=asyncio.subprocess.PIPE,
            env_overrides={"CLAUDECODE": None},
            **options,
        )
        return self.proc

    async def _probe(self, *args):
        proc = await self._spawn(
            *args, stdin=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), 30)
            return proc.returncode, data
        finally:
            await self._finish(proc)

    async def _finish(self, proc):
        if self.proc is proc:
            self.proc = None
            await self.cli.stop(proc)

    async def connect(self):
        self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        code, help_text = await self._probe("--help")
        if code or any(
            flag not in help_text
            for flag in (b"--safe-mode", b"--system-prompt-snapshot", b"--include-partial-messages")
        ):
            raise AgentError("当前 Claude Code 缺少会议模式所需的 CLI 选项，请更新 Claude Code。")
        code, raw = await self._probe("--safe-mode", "auth", "status", "--json")
        try:
            status = json.loads(raw)
        except (ValueError, UnicodeError):
            status = {}
        if code or not isinstance(status, dict) or not status.get("loggedIn"):
            raise AgentError("Claude Code 尚未登录。请在终端运行 claude auth login，然后重新连接。")
        config = Path(
            status.get("configDirectory")
            or os.getenv("CLAUDE_CONFIG_DIR")
            or Path.home() / ".claude"
        )
        self.projects = config / "projects"
        # The CLI materializes sessions on first prompt. Remember empty meeting
        # sessions across an app restart without writing fabricated Claude transcripts.
        profile = hashlib.sha256(os.fsencode(config.resolve())).hexdigest()[:20]
        self.drafts = self.workspace / "drafts" / profile
        self.drafts.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _sessions(self):
        rows = {}
        for path in self.drafts.glob("*.txt"):
            if _uuid(path.stem):
                with contextlib.suppress(OSError, UnicodeError):
                    rows[path.stem] = (path.stat().st_mtime, path.read_text(encoding="utf-8"))
        for path in self.projects.glob("*/*.jsonl"):
            if not _uuid(path.stem):
                continue
            with contextlib.suppress(OSError, UnicodeError):
                when = path.stat().st_mtime
                if path.stem not in rows or when >= rows[path.stem][0]:
                    rows[path.stem] = (when, _title(path))
        return [
            {"id": sid, "title": title}
            for sid, (_, title) in sorted(rows.items(), key=lambda row: row[1][0], reverse=True)
        ]

    async def list_sessions(self):
        return await asyncio.to_thread(self._sessions)

    def _native(self, sid):
        return next(self.projects.glob("*/" + sid + ".jsonl"), None)

    async def open_session(self, session_id, title):
        if session_id:
            sid = _uuid(session_id)
            if not sid or not (
                (self.drafts / (sid + ".txt")).is_file()
                or await asyncio.to_thread(self._native, sid)
            ):
                raise AgentError("找不到这个本机 Claude Code 会话，请检查会话 UUID 和登录配置。")
            return sid
        sid = str(uuid.uuid4())
        (self.drafts / (sid + ".txt")).write_text(
            title.strip()[:110] or "会议助手", encoding="utf-8"
        )
        return sid

    async def send(self, session_id, inputs, on_delta, handler):
        native = await asyncio.to_thread(self._native, session_id)
        draft = self.drafts / (session_id + ".txt")
        if native:
            args = ["--resume", session_id]
        elif draft.is_file():
            args = ["--session-id", session_id, "--name", draft.read_text(encoding="utf-8")]
        else:
            raise AgentError("本机会话已移除，请重新选择 Claude Code 会话。")
        if self.model:
            args.extend(["--model", self.model])
        if handler:
            args.extend(
                [
                    "--mcp-config",
                    '{"mcpServers":{"meeting":{"type":"sdk","name":"meeting"}}}',
                    "--allowedTools",
                    ",".join("mcp__meeting__" + n for n in TOOLS),
                ]
            )
        proc = await self._spawn(
            "-p",
            "--safe-mode",
            "--verbose",
            "--strict-mcp-config",
            "--no-chrome",
            "--disable-slash-commands",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "user",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--system-prompt",
            GUIDANCE,
            "--system-prompt-snapshot",
            "off",
            "--max-turns",
            "12",
            *args,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=64 * 1024**2,
        )
        try:
            answer = await self._read_turn(proc, session_id, inputs, on_delta, handler)
            proc.stdin.close()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.communicate(), 5)
            if draft.exists() and await asyncio.to_thread(self._native, session_id):
                draft.unlink(missing_ok=True)
            return answer
        except asyncio.TimeoutError as exc:
            raise AgentError("Claude Code 等待回复超时，请检查网络和 CLI 配置。") from exc
        finally:
            await self._finish(proc)

    async def _read_turn(self, proc, sid, inputs, on_delta, handler):
        await _write(
            proc,
            {
                "type": "control_request",
                "request_id": "init",
                "request": {"subtype": "initialize", "skills": []},
            },
        )
        emitted = []
        while line := await asyncio.wait_for(proc.stdout.readline(), 180):
            try:
                packet = json.loads(line)
            except (UnicodeError, ValueError) as exc:
                raise AgentError("Claude Code 返回了无效的流式消息，请检查本地 CLI 版本。") from exc
            kind = packet.get("type")
            if kind == "control_response" and packet["response"].get("request_id") == "init":
                if packet["response"].get("subtype") != "success":
                    raise AgentError("Claude Code 未能初始化会话。")
                await _write(
                    proc,
                    {
                        "type": "user",
                        "session_id": sid,
                        "parent_tool_use_id": None,
                        "message": {"role": "user", "content": _content(inputs)},
                    },
                )
            elif kind == "control_request":
                await _write(proc, _control_response(packet, handler))
            elif kind == "stream_event":
                delta = packet["event"].get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    emitted.append(delta["text"])
                    on_delta(delta["text"])
            elif kind == "result":
                if packet.get("session_id", sid) != sid:
                    raise AgentError("Claude Code 回复的会话 ID 与本次会议不同。")
                message = packet.get("result") or ""
                if packet.get("is_error") or packet.get("subtype") != "success":
                    detail = message or " ".join(str(item) for item in packet.get("errors", []))
                    raise AgentError(
                        "Claude Code：" + (detail or "本次请求失败，请检查登录、额度和模型。")[:450]
                    )
                if message and not emitted:
                    on_delta(message)
                return message or "".join(emitted)
        raise AgentError("Claude Code 已结束，未返回完整回复。请检查会话、登录和本地 CLI 版本。")

    async def close(self):
        if self.proc is not None:
            await self._finish(self.proc)
