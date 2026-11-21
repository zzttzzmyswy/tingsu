"""Claude Code print-mode adapter, using its bidirectional stream-json SDK transport."""

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .agent_materials import AgentError
from .agent_tools import LOCAL_GUIDANCE, LOCAL_TOOLS
from .local_cli import LocalCLI
from .meeting_mcp import meeting_mcp_reply
from .office_files import OFFICE_TOOLS, inspect_office
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
        elif part["type"] == "document":
            blocks.append(part)
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


async def _read_stderr(stream, tail):
    # CLI diagnostics may contain credentials or echo a request. Retain a small
    # window for classification only, and never forward its contents to the GUI.
    while data := await stream.read(4096):
        tail.extend(data)
        del tail[:-16384]


def _failure_detail(raw, code=None):
    detail = raw.lower()
    prefix = f"Claude Code 已退出（退出码 {code}）。" if code is not None else "Claude Code："
    rules = (
        (r"unknown option|unrecognized (argument|option)|invalid choice|invalid argument.*--",
         "当前 CLI 无法识别本次启动参数，请更新 Claude Code，或将模型、思考等级和速率恢复为本机默认值。"),
        (r"401|403|unauthori[sz]ed|forbidden|authenticat|not logged in|invalid api key|api key.*(invalid|missing)|login required",
         "认证失败。请使用同一系统账号在终端运行 claude，检查登录状态或本机服务的凭据。"),
        (r"429|rate.limit|quota|credit balance",
         "服务额度不足或请求过于频繁，请检查该账号的额度并稍后重试。"),
        (r"session.{0,60}(not found|already in use)|no conversation found",
         "会话不存在或正被其他进程占用，请在终端确认该会话可恢复，或新建会话。"),
        (r"(model.{0,80}(not found|unavailable|not available|not supported)|invalid model|model_not_found)",
         "当前模型不可用，请检查模型名称和服务权限，或选择本机默认模型。"),
        (r"econn|enotfound|etimedout|connection (error|refused|reset)|fetch failed|certificate",
         "无法连接模型服务，请检查 Claude Code 使用的服务地址、代理和证书设置。"),
        (r"cannot find module|node: not found|node: no such file|error while loading shared libraries",
         "本地 CLI 缺少运行依赖，请检查 Node.js 或重新安装 Claude Code。"),
    )
    for pattern, hint in rules:
        if re.search(pattern, detail):
            return prefix + hint
    return prefix + "未返回完整回复。请在终端中使用同一账号和模型运行 claude，检查会话及 CLI 配置。"


FILE_TOOLS = ("Read", "Grep", "Glob")
CLAUDE_TOOLS = {**LOCAL_TOOLS, **OFFICE_TOOLS}


def _workspace_read(request, workspace):
    name, args = request.get("tool_name"), request.get("input")
    if name not in FILE_TOOLS or not isinstance(args, dict):
        return False
    path = args.get("file_path") if name == "Read" else args.get("path", ".")
    if not isinstance(path, str) or not path:
        return False
    if name == "Glob":
        if not isinstance(args.get("pattern"), str):
            return False
        pattern = Path(args["pattern"])
        if pattern.anchor or ".." in pattern.parts:
            return False
    try:
        candidate = workspace / Path(path).expanduser()
        if not candidate.is_relative_to(workspace):
            return False
        target = candidate.resolve(strict=True)
        return target.is_relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return False


def _control_response(packet, handler, workspace):
    request = packet["request"]
    subtype = request.get("subtype")
    reply = {"request_id": packet["request_id"], "subtype": "success"}
    if subtype == "mcp_message" and request.get("server_name") == "meeting":
        def run(name, args):
            if name == "inspect_office":
                return inspect_office(workspace, args)
            return handler(name, args)

        mcp = meeting_mcp_reply(request.get("message"), run if handler else None, tools=CLAUDE_TOOLS)
        # The outer SDK request still needs an acknowledgement for a JSON-RPC notification.
        reply["response"] = {"mcp_response": mcp or {"jsonrpc": "2.0", "result": {}}}
    elif subtype == "can_use_tool":
        if _workspace_read(request, workspace):
            reply["response"] = {"behavior": "allow", "updatedInput": request["input"]}
        else:
            reply["response"] = {"behavior": "deny", "message": "请选择本次会议工作目录中的文件。"}
    else:
        reply.update(subtype="error", error="Unsupported control request")
    return {"type": "control_response", "response": reply}


class ClaudeBackend:
    def __init__(self, model="", executable="", effort="", speed="", workspace=None):
        self.cli = LocalCLI("claude", executable)
        self.model = model
        self.effort, self.speed = effort, speed
        self.flags = set()
        self.state_dir = app_directory("data") / "claude-workspace"
        self.workspace = Path(workspace or self.state_dir / "files").resolve()
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
        self.flags = set(re.findall(r"--[a-z][a-z0-9-]*", help_text.decode(errors="replace")))
        if code or not {"--input-format", "--output-format", "--strict-mcp-config", "--tools"} <= self.flags:
            raise AgentError("所选程序不支持 Claude Code 的双向 stream-json 会议协议，请检查可执行文件或更新 CLI。")
        if self.effort and "--effort" not in self.flags:
            raise AgentError("此 Claude CLI 没有 --effort 选项。请选择本机默认思考等级或更新 Claude Code。")
        if self.speed and ("--settings" not in self.flags or self.speed not in ("standard", "fast")):
            raise AgentError("该版本或速率选项不可用。请选择本机默认速率或更新 Claude Code。")
        raw = b""
        if re.search(rb"(?m)^\s*auth(?:\s|$)", help_text):
            _, raw = await self._probe("auth", "status", "--json")
        try:
            status = json.loads(raw)
        except (ValueError, UnicodeError):
            status = {}
        if not isinstance(status, dict):
            status = {}
        # Older CLIs lack a JSON auth probe. The first SDK request authenticates with
        # the CLI's own credentials, including user-configured third-party providers.
        config = Path(
            status.get("configDirectory")
            or os.getenv("CLAUDE_CONFIG_DIR")
            or Path.home() / ".claude"
        )
        if status.get("loggedIn") is False:
            user = {}
            with contextlib.suppress(OSError, ValueError):
                user = json.loads((config / "settings.json").read_text(encoding="utf-8-sig"))
            user = user if isinstance(user, dict) else {}
            env = user.get("env")
            configured = {**(env if isinstance(env, dict) else {}), **os.environ}
            if not user.get("apiKeyHelper") and not any(configured.get(k) for k in (
                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            )):
                raise AgentError("Claude Code 报告未登录。请在终端检查 claude auth status，或配置本机服务的凭据。")
        self.projects = Path(status.get("projectsDirectory") or config / "projects")
        # The CLI materializes sessions on first prompt. Remember empty meeting
        # sessions across an app restart without writing fabricated Claude transcripts.
        profile = hashlib.sha256(os.fsencode(config.resolve())).hexdigest()[:20]
        self.drafts = self.state_dir / "drafts" / profile
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
        # A native resume across directories may record later turns under a new
        # project. Start from the most recently updated native transcript.
        return max(self.projects.glob("*/" + sid + ".jsonl"), key=lambda p: p.stat().st_mtime_ns, default=None)

    def _resume_argument(self, native):
        if "--safe-mode" in self.flags:
            return str(native)
        # Legacy Claude resolves a UUID only in its cwd's project directory and
        # assigns a different UUID when passed a JSONL path. Expose the existing
        # transcript at the legacy CLI's own project key without moving history.
        key = re.sub(r"[^a-zA-Z0-9]", "-", str(self.workspace))
        project = self.projects / key
        target = project / native.name
        if not target.exists():
            project.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                target.hardlink_to(native)
            except FileExistsError:
                pass
            except OSError:
                # Some network/home filesystems lack hard links.
                try:
                    out = target.open("xb")
                except FileExistsError:
                    pass
                else:
                    try:
                        with out, native.open("rb") as source:
                            shutil.copyfileobj(source, out)
                    except OSError:
                        target.unlink(missing_ok=True)
                        raise
        return native.stem

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
            args = ["--resume", await asyncio.to_thread(self._resume_argument, native)]
        elif draft.is_file():
            args = ["--session-id", session_id]
            if "--name" in self.flags:
                args += ["--name", draft.read_text(encoding="utf-8")]
        else:
            raise AgentError("本机会话已移除，请重新选择 Claude Code 会话。")
        if self.model:
            args.extend(["--model", self.model])
        if self.effort:
            args.extend(["--effort", self.effort])
        # Ask the SDK host to validate file-tool paths even if the user's CLI has
        # broad allow rules. Newer CLIs also enforce the working-directory fence.
        settings = {"permissions": {"ask": list(FILE_TOOLS), "blockReadsOutsideWorkingDirectories": True}}
        if "--safe-mode" in self.flags:
            args.append("--safe-mode")
        else:
            settings["disableAllHooks"] = True
        if self.speed:
            settings["fastMode"] = self.speed == "fast"
        if settings and "--settings" in self.flags:
            args.extend(["--settings", json.dumps(settings)])
        if "--setting-sources" in self.flags:
            args.extend(["--setting-sources", "user"])
        for flag in ("--no-chrome", "--disable-slash-commands", "--include-partial-messages"):
            if flag in self.flags:
                args.append(flag)
        if "--system-prompt-snapshot" in self.flags:
            args.extend(["--system-prompt-snapshot", "off"])
        if handler:
            args.extend(
                [
                    "--mcp-config",
                    '{"mcpServers":{"meeting":{"type":"sdk","name":"meeting"}}}',
                    "--allowedTools",
                    ",".join("mcp__meeting__" + n for n in CLAUDE_TOOLS),
                ]
            )
        proc = await self._spawn(
            "-p",
            "--verbose",
            "--strict-mcp-config",
            "--tools",
            ",".join(FILE_TOOLS),
            "--permission-prompt-tool",
            "stdio",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--system-prompt",
            LOCAL_GUIDANCE,
            "--max-turns",
            "12",
            *args,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024**2,
        )
        stderr = bytearray()
        diagnostics = asyncio.create_task(_read_stderr(proc.stderr, stderr))
        try:
            try:
                answer = await self._read_turn(proc, session_id, inputs, on_delta, handler)
            except (BrokenPipeError, ConnectionResetError):
                answer = None
            proc.stdin.close()
            if answer is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), 2)
                    await asyncio.wait_for(asyncio.shield(diagnostics), 1)
                raise AgentError(_failure_detail(stderr.decode(errors="replace"), proc.returncode))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.stdout.read(), 5)
            if draft.exists() and await asyncio.to_thread(self._native, session_id):
                draft.unlink(missing_ok=True)
            return answer
        except asyncio.TimeoutError as exc:
            raise AgentError("Claude Code 等待回复超时，请检查网络和 CLI 配置。") from exc
        finally:
            await self._finish(proc)
            diagnostics.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await diagnostics

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
                reply = await asyncio.to_thread(_control_response, packet, handler, self.workspace)
                await _write(proc, reply)
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
                    raise AgentError(_failure_detail(detail))
                if message and not emitted:
                    on_delta(message)
                return message or "".join(emitted)
        return None

    async def close(self):
        if self.proc is not None:
            await self._finish(self.proc)
