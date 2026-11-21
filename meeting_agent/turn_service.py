"""Schedule meeting agent turns on a serial owner thread, independently of any GUI."""

from collections import deque
import json
import threading

from .agent_materials import AgentCancelled


AUTO_INTERVAL = 28
AUTO_PROMPT = "观察最新发言，有重要结论、待办或未决问题时更新便签，避免重复。用简短中文反馈。"


class TurnService:
    """Public methods run on the owner thread. post() dispatches worker callbacks there.

    prepare_frame(meeting, position) runs on the owner and can return a worker-safe
    function for obtaining the frame selected for this turn.
    """

    def __init__(self, store, *, post, position, prepare_frame, changed, stream, notify, automatic):
        self.store = store
        self.post = post
        self.position = position
        self.prepare_frame = prepare_frame
        self.changed = changed
        self.stream = stream
        self.notify = notify
        self._automatic = automatic
        self._client = None
        self._meeting = None
        self._active = None
        self._users = deque()
        self._auto_pending = False
        self._last_auto = 0
        self._text = ""
        self._tools_lock = threading.Lock()

    @property
    def sending(self):
        return self._active is not None

    @property
    def queued(self):
        return len(self._users)

    def bind(self, meeting, client, duration):
        self.detach()
        self._meeting, self._client = meeting, client
        self._last_auto = duration - AUTO_INTERVAL

    def detach(self):
        self.cancel()
        self._active = None
        self._client = self._meeting = None
        self._users.clear()
        self._auto_pending = False
        if self._text:
            self._text = ""
            self.stream("")

    def cancel(self):
        self._auto_pending = False
        if self._active is not None:
            # A tool mutation that already entered the store finishes before detach.
            with self._tools_lock:
                self._active.set()
            self._client.cancel()

    def set_automatic(self, enabled):
        self._automatic = enabled
        if not enabled:
            self._auto_pending = False

    def transcribed(self, meeting, end, has_text):
        if meeting != self._meeting or not self._automatic or not has_text:
            return
        if end - self._last_auto >= AUTO_INTERVAL:
            self._last_auto = end
            self._auto_pending = True
            self._start_next()

    def submit(self, text, files):
        if self._client is None:
            raise ValueError("请先连接 AI 会话")
        text, files = text.strip(), tuple(files)
        if not text and not files:
            raise ValueError("请输入消息或添加材料")
        text = text or "请阅读这些会议材料。"
        at = self.position()
        self.store.event(self._meeting, "user", at, at, "我", text, files)
        self._users.append((text, files))
        if self.sending:
            self.changed()
        else:
            self._start_next()

    def _start_next(self):
        if self.sending or self._client is None:
            return
        if self._users:
            prompt, attachments = self._users.popleft()
        elif self._auto_pending:
            self._auto_pending = False
            prompt, attachments = AUTO_PROMPT, ()
        else:
            return
        mid, client = self._meeting, self._client
        at = self.position()
        token = self._active = threading.Event()
        self._text = ""
        self.stream("")
        self.changed()
        try:
            get_frame = self.prepare_frame(mid, at)
        except Exception as exc:
            self._finish(token, mid, at, "", exc)
            return

        def tool(name, args):
            with self._tools_lock:
                if token.is_set():
                    raise AgentCancelled("会议请求已结束")
                result = self.store.tool(mid, name, args)
            if name not in ("read_transcript", "list_notes"):
                self.post(self.changed)
            return result

        def delta(piece):
            self.post(lambda: self._append(token, piece))

        def run():
            try:
                context = json.dumps(self.store.agent_context(mid), ensure_ascii=False)
                files = list(attachments)
                if get_frame and not token.is_set():
                    files.append(get_frame())
                if token.is_set():
                    raise AgentCancelled("已停止请求")
                reply = client.send(prompt, attachments=files, on_delta=delta, tool_handler=tool,
                                    context=context, cancel_event=token)
            except Exception as exc:
                self.post(lambda e=exc: self._finish(token, mid, at, "", e))
            else:
                self.post(lambda: self._finish(token, mid, at, reply, None))

        threading.Thread(target=run, name="tingsu-agent-turn", daemon=True).start()

    def _append(self, token, piece):
        if token is self._active:
            self._text += piece
            self.stream(self._text)

    def _finish(self, token, mid, at, reply, error):
        if token is not self._active:
            return
        with self._tools_lock:
            cancelled = token.is_set() or isinstance(error, AgentCancelled)
            token.set()  # Retire the tool callback before another turn can start.
        self._active = None
        try:
            if cancelled:
                reply = self._text.rstrip() + "\n（回复已停止）" if self._text.strip() else ""
                self.notify("已停止本次 AI 回复", "ok")
            elif error:
                detail = str(error) or type(error).__name__
                self.notify(detail[:800], "error")
                self.store.event(mid, "system", at, at, "系统", "AI 回复失败：" + detail[:700])
            if reply and reply.strip():
                end = self.position()
                self.store.event(mid, "assistant", end, end, "听序 AI", reply)
        finally:
            if cancelled or error:
                self._auto_pending = False
            self._text = ""
            self.stream("")
            self.changed()
            self._start_next()
