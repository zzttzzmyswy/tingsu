"""Meeting agent client. Run synchronous network operations on a GUI worker thread."""

import asyncio
import concurrent.futures
import contextlib
import hashlib
import threading
import uuid
from pathlib import Path

import httpx

from .agent_materials import AgentCancelled, AgentError, MATERIAL_TOOLS, Material, MaterialLibrary, make_inputs
from .agent_providers import PROVIDERS
from .agent_workspace import MeetingWorkspace
from .runtime import app_directory


class AgentClient:
    def __init__(self, provider, api_key="", model="", executable="", *, effort="", speed="", workspace=None):
        if provider not in PROVIDERS:
            raise AgentError("未知的 Agent 供应商。")
        self.provider = provider
        self.session_id = None
        self._spec = PROVIDERS[provider]
        self.workspace = workspace
        if self.workspace is None and self._spec.cli:
            self.workspace = MeetingWorkspace(app_directory("data"), uuid.uuid4().hex)
        self._backend = self._spec.create(api_key, model, executable, effort, speed,
                                          self.workspace.directory if self.workspace else None)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="tingsu-agent", daemon=True)
        self._thread.start()
        self._lock = threading.Lock()
        self._op_lock = threading.Lock()
        self._active = None
        self._cancellation = None
        self._closed = False
        self._connected = False

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def _call(self, make_operation, *, needs_connection=True):
        if not self._op_lock.acquire(blocking=False):
            raise AgentError("Agent 正在处理另一个请求。")
        try:
            with self._lock:
                if self._closed:
                    raise AgentError("Agent 连接已关闭。")
                if needs_connection and not self._connected:
                    raise AgentError("请先连接 Agent。")
                cancellation = threading.Event()
                self._cancellation = cancellation

                async def run():
                    self._active = asyncio.current_task()
                    try:
                        if cancellation.is_set():
                            raise AgentCancelled("已停止请求。")
                        return await make_operation()
                    finally:
                        self._active = None

                future = asyncio.run_coroutine_threadsafe(run(), self._loop)
            try:
                return future.result()
            except concurrent.futures.CancelledError as exc:
                raise AgentCancelled("已停止请求。") from exc
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                raise AgentError(
                    f"Agent 连接失败或超时（{type(exc).__name__}），请检查网络与供应商配置。"
                ) from exc
            finally:
                with self._lock:
                    self._cancellation = None
        finally:
            self._op_lock.release()

    def connect(self):
        self._call(self._backend.connect, needs_connection=False)
        self._connected = True

    def list_sessions(self):
        return self._call(self._backend.list_sessions)

    def model_catalog(self):
        return self._call(self._backend.model_catalog) if self.provider == "codex" else []

    @property
    def session_options(self):
        return self._backend.session_options if self.provider == "deepseek" else {}

    def open_session(self, session_id=None, title="会议助手"):
        self.session_id = self._call(lambda: self._backend.open_session(session_id, title))
        return self.session_id

    def _library(self, scope):
        if self.workspace:
            return self.workspace.library(scope or self.workspace.meeting)
        key = hashlib.sha256(f"session:{self.provider}:{self.session_id}".encode()).hexdigest()
        data = app_directory("data")
        library = MaterialLibrary(data / "api-materials" / key)
        library.import_previous(data / "materials" / key, legacy=True)
        return library

    def stage_files(self, paths, material_scope=None):
        library = self._library(material_scope)
        frame_dir = app_directory("data") / "frames" / (material_scope or "")
        return [p if isinstance(p, Material) else library.add(p, shared_frame=Path(p).parent == frame_dir)
                for p in paths]

    def send(
        self, text, attachments=(), on_delta=None, tool_handler=None, *, context="", cancel_event=None,
        material_scope=None, frame=None
    ):
        if not self.session_id:
            raise AgentError("请先创建或选择 Agent 会话。")
        if not (str(text).strip() or attachments or context):
            raise AgentError("请输入消息或选择附件。")

        async def send():
            if cancel_event is not None and cancel_event.is_set():
                raise AgentCancelled("已停止请求。")
            library = await asyncio.to_thread(self._library, material_scope)

            def prepare():
                selected = self.stage_files(attachments, material_scope)
                local = self.workspace.directory if self._spec.cli else None
                return make_inputs(self._spec.input_format, text, selected, context, frame=frame,
                                   workspace=local, shared_frames=[m.path for m in selected if local and m.shared_frame])

            def tool(name, args):
                if cancel_event is not None and cancel_event.is_set():
                    raise AgentCancelled("已停止请求。")
                if name in MATERIAL_TOOLS:
                    return library.tool(name, args)
                if tool_handler is None:
                    raise AgentError("本次请求无法执行该操作")
                return tool_handler(name, args)

            inputs = await asyncio.to_thread(prepare)
            if cancel_event is not None and cancel_event.is_set():
                raise AgentCancelled("已停止请求。")
            return await self._backend.send(
                self.session_id, inputs, on_delta or (lambda _: None), tool
            )

        return self._call(send)

    def _request_cancel(self):
        # Waiters regain the operation lock only after the coroutine has interrupted
        # its remote turn and released the stream. A queued user turn can then start.
        token = self._cancellation
        if not token or token.is_set():
            return
        token.set()

        def cancel_on_loop():
            if self._cancellation is token and self._active:
                self._active.cancel()

        self._loop.call_soon_threadsafe(cancel_on_loop)

    def cancel(self):
        with self._lock:
            self._request_cancel()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._request_cancel()
            cleanup = asyncio.run_coroutine_threadsafe(self._backend.close(), self._loop)
        with contextlib.suppress(Exception):
            cleanup.result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
