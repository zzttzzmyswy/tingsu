from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import QFileDialog

from .agent_providers import PROVIDERS, provider_choices
from .agents import AgentClient, AgentCancelled
from .asr import ASRConfig
from .codex_login import CodexLogin
from .capture import ScreenCapture, extract_video_frame, file_source, list_audio_sources
from .library import MEDIA, describe, thumbnail
from .runtime import media_directory
from .storage import DATA, KINDS, Settings, Store, private_dir, uid
from .transcription_service import TranscriptionService
from .turn_service import TurnService


def clock(seconds):
    sec = max(0, int(seconds))
    return f"{sec//3600:02d}:{sec//60%60:02d}:{sec%60:02d}"


class Bridge(QObject):
    state = Signal(str)
    notice = Signal(str, str)
    event = Signal(str, str)
    dispatch = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.window = parent
        self.login = CodexLogin(self)
        self.login.finished.connect(self.login_finished)
        self.login.link_available.connect(self.update)
        self.store, self.settings = Store(), Settings()
        self.mid = self.settings.values["selected"]
        if not self.store.meeting(self.mid):
            self.mid = self.store.latest_meeting_id()
        self.sources, self.media, self.files = [], [], []
        self.library_busy = False
        self.library_changed = False
        self.screen = self.client = None
        self.agent_status = "offline"
        self.sessions, self.session, self.agent_name = [], "", ""
        self.generation, self.screen_version = 0, 0
        self.turns = TurnService(
            self.store, post=self.post, position=self.position, prepare_frame=self.prepare_turn_frame,
            changed=self.update, stream=lambda text: self.event.emit("delta", text),
            notify=self.notice.emit, automatic=self.settings.values["auto"],
        )
        self.frame = self.frame_image = ""
        self.screen_on = False
        self.closed = False
        self.limit = 120
        self.dispatch.connect(self.deliver, Qt.ConnectionType.QueuedConnection)
        self.refresh = QTimer(self)
        self.refresh.setSingleShot(True)
        self.refresh.setInterval(45)
        self.refresh.timeout.connect(self.publish)
        self.ticker = QTimer(self)
        self.ticker.setInterval(500)
        self.ticker.timeout.connect(self.publish_position)
        self.ticker.start()
        self.transcription = TranscriptionService(
            self.store, DATA / "audio", changed=lambda: self.post(self.update),
            transcribed=lambda mid, end, has: self.post(lambda: self.transcribed(mid, end, has)),
            failed=lambda mid, exc: self.transcription_notice(mid, str(exc) or type(exc).__name__, "error"),
            completed=lambda mid: self.transcription_notice(mid, "已完成本次转写", "ok"),
        )

    @Slot(object)
    def deliver(self, fn):
        if not self.closed:
            try:
                fn()
            except Exception as exc:
                self.fail(exc)

    def post(self, fn):
        if not self.closed:
            self.dispatch.emit(fn)

    def work(self, task, done, failed=None):
        def run():
            try:
                value = task()
            except Exception as exc:
                self.post(lambda e=exc: (failed or self.fail)(e))
            else:
                self.post(lambda: done(value))

        threading.Thread(target=run, daemon=True).start()

    def update(self):
        self.refresh.start()

    def fail(self, exc):
        self.notice.emit(str(exc)[:800] or type(exc).__name__, "error")
        self.update()

    @property
    def capture_status(self):
        return self.transcription.status

    @property
    def has_active_work(self):
        return self.capture_status != "idle" or self.turns.sending

    def check_idle(self):
        self.transcription.ensure_idle()

    def transcription_notice(self, mid, message, level):
        def show():
            if mid == self.mid:
                self.notice.emit(message[:800], level)
                self.update()

        self.post(show)

    def current(self):
        return self.store.meeting(self.mid)

    def sources_for_ui(self):
        return [dict(id=self.source_key(s), label=s.label, kind=s.kind) for s in self.sources]

    @staticmethod
    def source_key(source):
        return source.id if source.kind == "file" else f"{source.kind}:{source.id}"

    def publish_position(self):
        if self.capture_status == "recording":
            position = self.transcription.position
            if position is not None:
                self.event.emit("position", str(position))

    def position(self):
        position = self.transcription.position
        return position if position is not None else self.current()["duration"]

    @Slot()
    def ready(self):
        self.publish()
        self.load_sources()
        self.load_library()

    def publish(self):
        if self.closed:
            return
        snapshot = self.store.workspace_snapshot(self.mid, self.limit)
        data = dict(
            **snapshot,
            loginLink=self.login.url is not None,
            fileSource=Path(snapshot["meeting"]["source"]).is_absolute(),
            settings=self.settings.public(),
            providers=provider_choices(),
            sources=self.sources_for_ui(),
            media=self.media,
            libraryBusy=self.library_busy,
            capture=self.capture_status,
            agent=self.agent_status,
            agentName=self.agent_name,
            session=self.session,
            sessions=self.sessions,
            sending=self.turns.sending,
            queued=self.turns.queued,
            files=[p.name for p in self.files],
            frame=self.frame_image,
            screen=self.screen_on,
        )
        self.state.emit(json.dumps(data, ensure_ascii=False))

    @Slot(str, str)
    def command(self, name, raw):
        try:
            args = json.loads(raw)
            if not isinstance(args, dict) or name.startswith("_"):
                raise ValueError("操作无效")
            action = getattr(self, "cmd_" + name, None)
            if not action:
                raise ValueError("未知操作")
            action(args)
        except Exception as exc:
            self.fail(exc)

    def load_sources(self):
        def done(items):
            self.sources = list(items)
            self.update()

        self.work(list_audio_sources, done)

    def cmd_sources(self, a):
        self.load_sources()

    def load_library(self):
        if self.library_busy:
            return
        self.library_busy = True
        self.update()

        def scan():
            files = set(self.store.media_paths())
            directory = media_directory()
            if directory.is_dir():
                files.update(p for p in directory.iterdir() if p.suffix.lower() in MEDIA)
            result, errors = [], []
            for path in sorted(files):
                if path.is_file():
                    try:
                        result.append(describe(path))
                    except Exception:
                        errors.append(path.name)
            return result, errors

        def done(result):
            self.media, errors = result
            self.library_busy = False
            self.update()
            if errors:
                self.fail("无法读取媒体信息：" + "、".join(errors))
            if self.library_changed:
                self.library_changed = False
                self.load_library()

        def failed(exc):
            self.library_busy = False
            self.fail(exc)

        self.work(scan, done, failed)

    def cmd_import_media(self, a):
        files, _ = QFileDialog.getOpenFileNames(
            self.window,
            "添加会议录屏或音频",
            "",
            "媒体文件 (" + " ".join("*" + s for s in sorted(MEDIA)) + ")",
        )
        if files:
            self.store.add_media(files)
            if self.library_busy:
                self.library_changed = True
            else:
                self.load_library()

    def cmd_settings(self, a):
        values = {}
        for key in (
            "provider",
            "siliconflow_model",
            "sophnet_model",
            "model",
            "agent",
            "auto",
            "share_frames",
            "chunk",
            "openai_key",
            *(key + "_path" for key, spec in PROVIDERS.items() if spec.cli),
            "siliconflow_key",
            "sophnet_key",
        ):
            if key in a:
                values[key] = a[key]
        if values.get("provider", "siliconflow") not in ("siliconflow", "sophnet") or values.get(
            "agent", "codex"
        ) not in PROVIDERS:
            raise ValueError("未知供应商")
        if "chunk" in values:
            values["chunk"] = max(4, min(45, int(values["chunk"])))
        if self.client and any(
            k in values and self.settings.values.get(k) != values[k]
            for k in ("agent", "model", "openai_key", self.agent_name + "_path")
        ):
            self.disconnect_agent()
        self.settings.save(**values)
        self.turns.set_automatic(self.settings.values["auto"])
        self.publish()
        self.event.emit("settings-saved", "")
        self.notice.emit("设置已保存", "ok")

    def change(self, mid):
        self.check_idle()
        if self.turns.sending or self.turns.queued:
            raise ValueError("请先结束当前 AI 回复，再切换会议。")
        if not self.store.meeting(mid):
            raise ValueError("会议不存在")
        self.disconnect_agent()
        self.stop_screen()
        self.files = []
        self.mid, self.limit = mid, 120
        self.transcription.forget_speakers()
        self.settings.save(selected=mid)
        self.update()
        self.event.emit("workspace", "")

    def cmd_select(self, a):
        if a["id"] != self.mid:
            self.change(a["id"])

    def cmd_new(self, a):
        self.check_idle()
        if self.turns.sending or self.turns.queued:
            raise ValueError("请先结束当前 AI 回复")
        title = str(a.get("title", "")).strip()[:120] or "未命名会议"
        source = a.get("source", "")
        src = self.source(source) if source else None
        offset = float(a.get("offset", 0))
        if not math.isfinite(offset) or offset < 0:
            raise ValueError("录屏起点无效")
        media = next((m for m in self.media if m["path"] == source), None)
        if offset and (not media or offset >= media["duration"]):
            raise ValueError("录屏起点必须在媒体时长之内")
        mid = self.store.create(title, source=self.source_key(src) if src else "",
                                source_label=src.label if src else "", origin=offset,
                                topic_id=a.get("topic_id", ""), topic_name=a.get("topic_name", ""))
        self.change(mid)
        if a.get("start"):
            self.cmd_start({})

    def cmd_rename(self, a):
        name = a.get("title", "").strip()[:120]
        if not name:
            raise ValueError("请输入会议名称")
        self.store.rename_meeting(self.mid, name)
        self.update()

    def cmd_set_meeting_topic(self, a):
        if a.get("meeting") != self.mid:
            raise ValueError("会议已切换，请重新选择主题")
        self.check_idle()
        if self.turns.sending or self.turns.queued:
            raise ValueError("请先结束当前 AI 回复，再加入主题。")
        self.store.set_meeting_topic(self.mid, topic_id=a.get("topic_id", ""),
                                     topic_name=a.get("topic_name", ""))
        self.disconnect_agent()
        self.files = []
        self.publish()
        self.event.emit("topic-saved", "")
        self.notice.emit("会议已加入主题", "ok")

    def source(self, sid):
        found = next((s for s in self.sources if self.source_key(s) == sid), None)
        if found:
            return found
        if any(m["path"] == sid for m in self.media) or (
            sid == self.current()["source"] and Path(sid).is_absolute()
        ):
            if not Path(sid).is_file():
                raise ValueError("找不到录屏文件，请确认原文件路径。")
            return file_source(sid)
        raise ValueError("请选择可用的采集来源，或刷新音频设备。")

    def cmd_set_source(self, a):
        self.check_idle()
        s = self.source(a["id"])
        m = self.current()
        if m["demo"] or (
            m["duration"] > m["origin"] and (Path(m["source"]).is_absolute() or s.kind == "file")
        ):
            raise ValueError("已有录音的会议不能更换录屏文件。请新建会议选择文件。")
        offset = m["origin"] if self.source_key(s) == m["source"] or m["duration"] > m["origin"] else 0
        length = m["duration"] if m["duration"] > m["origin"] else offset
        self.store.set_source(self.mid, self.source_key(s), s.label, offset, length)
        if a.get("start"):
            self.cmd_start({})
        self.update()

    def cmd_older(self, a):
        self.limit += 160
        self.update()

    def cmd_jump(self, a):
        at = float(a["at"])
        after = self.store.count_events_from(self.mid, at)
        self.limit = max(self.limit, after + 2)
        self.publish()
        self.event.emit("jump", str(at))

    def cmd_search_events(self, a):
        mid = self.mid
        if a.get("meeting") != mid:
            return
        request_id, offset = int(a["id"]), max(0, int(a.get("offset", 0)))
        query = str(a.get("query", "")).strip()
        speaker, category = str(a.get("speaker", "")), a.get("filter", "all")

        def done(result):
            if self.mid == mid:
                total, items = result
                payload = dict(meeting=mid, id=request_id, offset=offset, total=total, items=items)
                self.event.emit("search-results", json.dumps(payload, ensure_ascii=False))

        self.work(lambda: self.store.search_events(mid, query, speaker, category, offset), done)

    def cmd_note(self, a):
        op = {"add": "create_note", "edit": "update_note", "delete": "delete_note"}[a["op"]]
        self.store.tool(self.mid, op, a, "我")
        self.event.emit("note-saved", "")
        self.update()

    def cmd_topic_note(self, a):
        if a.get("meeting") != self.mid:
            raise ValueError("会议已切换，请重新打开主题看板。")
        op = {"add": "create_topic_note", "edit": "update_topic_note", "delete": "delete_topic_note",
              "status": "set_topic_note_status", "share": "share_note_to_topic"}[a["op"]]
        self.store.tool(self.mid, op, a, "我")
        self.publish()
        self.event.emit("note-saved", "")

    def cmd_export(self, a):
        title = self.current()["title"]
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        target, _ = QFileDialog.getSaveFileName(
            self.window, "导出会议记录", name + ".md", "Markdown (*.md)"
        )
        if not target:
            return
        lines = ["# " + title, "", self.current()["created"], "", "## 会议动态", ""]
        for e in self.store.events(self.mid):
            lines += [f"**{clock(e['at'])} · {e['speaker']}**", "", e["text"], ""]
            if json.loads(e["files"]):
                lines += ["附件：" + "、".join(json.loads(e["files"])), ""]
        lines += ["## 会议便签", ""]
        for n in self.store.notes(self.mid):
            lines += [f"### {n['title']}", "", KINDS[n["kind"]], "", n["body"], ""]
        Path(target).write_text("\n".join(lines), encoding="utf-8")
        self.notice.emit("会议记录已导出", "ok")

    def cmd_start(self, a):
        self.check_idle()
        m = self.current()
        if m["demo"]:
            raise ValueError("请新建会议后开始采集")
        s = self.source(m["source"])
        if s.kind != "file" and m["source_label"] != s.label:
            raise ValueError("音频设备或应用流已变化，请重新选择采集源。")
        media = next((item for item in self.media if item["path"] == m["source"]), None)
        if media and media["duration"] - m["duration"] <= 0.05:
            raise ValueError("已到达录屏末尾。")
        self.transcription.record(self.mid, s, self.asr_config(), chunk_seconds=self.settings.values["chunk"],
                                  start_seconds=m["duration"])

    def cmd_retry(self, a):
        self.transcription.retry(self.mid, self.asr_config())

    def asr_config(self):
        settings = self.settings.values
        provider = settings["provider"]
        return ASRConfig(provider=provider, api_key=self.settings.key(provider), model=settings[provider + "_model"])

    def transcribed(self, mid, end, has_text):
        if mid != self.mid:
            return
        self.update()
        self.turns.transcribed(mid, end, has_text)

    def cmd_stop(self, a):
        self.transcription.stop()

    def cmd_attach(self, a):
        if self.agent_status != "ready":
            raise ValueError("请先连接 AI 会话")
        files, _ = QFileDialog.getOpenFileNames(self.window, "添加会议材料")
        for f in files:
            p = Path(f)
            if p not in self.files:
                self.files.append(p)
        self.update()

    def cmd_remove_file(self, a):
        idx = int(a["index"])
        if 0 <= idx < len(self.files):
            self.files.pop(idx)
        self.update()

    def stop_screen(self):
        self.screen_version += 1
        if self.screen:
            self.screen.stop()
        self.screen = None
        self.frame = self.frame_image = ""
        self.screen_on = False

    def cmd_screen_off(self, a):
        self.stop_screen()
        self.update()

    def cmd_screen(self, a):
        if self.screen_on:
            self.cmd_snapshot(a)
            return
        self.screen_version += 1
        version = self.screen_version
        self.screen_on = True
        self.update()

        def frame(path):
            self.post(lambda: self.got_frame(version, path))

        def failed(msg):
            def apply():
                if self.screen_version == version:
                    self.stop_screen()
                    self.fail(msg)

            self.post(apply)

        self.screen = ScreenCapture(
            on_frame=frame, on_error=failed, output_dir=private_dir(DATA / "frames" / self.mid)
        )
        try:
            self.screen.start()
        except Exception:
            self.stop_screen()
            raise

    def got_frame(self, version, path):
        if version != self.screen_version:
            return
        self.frame, self.frame_image = str(path), thumbnail(path)
        self.update()

    def cmd_snapshot(self, a):
        if self.screen:
            self.screen.snapshot()
        else:
            source = self.current()["source"]
            if source and Path(source).is_file():
                path = private_dir(DATA / "frames" / self.mid) / (uid() + ".jpg")
                position = self.position()
                version = self.screen_version
                self.work(
                    lambda: extract_video_frame(source, max(0, position), path),
                    lambda _: self.got_frame(version, path),
                )
            else:
                self.cmd_screen(a)

    def cmd_frame_attach(self, a):
        if self.frame and Path(self.frame) not in self.files:
            self.files.append(Path(self.frame))
            self.update()

    def cmd_login(self, a):
        if self.agent_status != "offline":
            raise ValueError("请先断开当前会话")
        self.login.start()
        self.agent_status = "login"
        self.update()

    def cmd_login_browser(self, a):
        self.login.open_browser()

    def login_finished(self, success):
        self.agent_status = "offline"
        if success:
            self.cmd_connect({"provider": "codex"})
        else:
            self.fail("Codex 登录未完成，可以重新登录。")

    def disconnect_agent(self):
        self.login.stop()
        self.generation += 1
        self.turns.detach()
        client = self.client
        self.client = None
        if client:
            threading.Thread(target=client.close, daemon=True).start()
        self.sessions, self.session, self.agent_name = [], "", ""
        self.agent_status = "offline"
        self.update()

    def cmd_disconnect(self, a):
        self.disconnect_agent()

    def cmd_connect(self, a):
        if self.turns.sending:
            raise ValueError("AI 正在回复")
        self.disconnect_agent()
        meeting = self.current()
        provider = a.get("provider") or meeting["topic_provider"] or self.settings.values["agent"]
        if provider not in PROVIDERS:
            raise ValueError("未知的 AI 服务")
        model = self.settings.values["model"] if provider == "openai" else ""
        client = AgentClient(
            provider, api_key=self.settings.key("openai") if provider == "openai" else "",
            model=model, executable=self.settings.values.get(provider + "_path", "")
        )
        generation = self.generation
        self.client, self.agent_name, self.agent_status = client, provider, "connecting"
        self.update()

        def connect():
            client.connect()
            if meeting["topic_id"]:
                return []
            blocked = self.store.unavailable_session_ids(meeting["id"], provider)
            return [s for s in client.list_sessions() if s["id"] not in blocked]

        def done(sessions):
            if self.generation != generation:
                return
            self.sessions, self.agent_status = sessions, "choose"
            saved = self.store.agent_sessions(self.mid).get(provider) if meeting["topic_id"] else None
            if saved:
                self.cmd_session({"id": saved})
            else:
                self.update()
                self.event.emit("choose", "")

        def failed(exc):
            if self.generation == generation:
                self.disconnect_agent()
                self.fail(exc)

        self.work(connect, done, failed)

    def cmd_session(self, a):
        if self.agent_status != "choose" or not self.client:
            raise ValueError("请先连接 AI 服务")
        client, mid, generation, provider = self.client, self.mid, self.generation, self.agent_name
        meeting = self.current()
        requested = a.get("id") or None
        saved = None
        if meeting["topic_id"]:
            saved = self.store.agent_sessions(mid).get(provider, "")
            if requested and requested != saved:
                raise ValueError("请选择本主题自己的 AI 会话。")
            if saved and not requested and a.get("replace") is not True:
                raise ValueError("本主题已有会话。请继续原会话，或明确选择重新建立。")
        if requested:
            self.store.check_agent_session(mid, provider, requested)
        self.agent_status = "connecting"
        self.update()

        def done(session):
            if generation != self.generation:
                return
            try:
                if requested and meeting["topic_id"] and requested != session:
                    raise ValueError("服务返回了不同的会话，请重试。")
                self.store.bind_agent(mid, provider, session, expected_session=saved)
            except Exception as exc:
                failed(exc)
                return
            self.session, self.agent_status = session, "ready"
            self.turns.bind(mid, client, self.current()["duration"])
            self.update()
            self.event.emit("connected", "")
            self.notice.emit("已连接主题「" + meeting["topic_name"] + "」的 AI 会话"
                             if meeting["topic_id"] else "AI 会话已连接", "ok")

        def failed(exc):
            if generation == self.generation:
                self.agent_status = "choose"
                self.fail(exc)
                self.event.emit("choose", "")

        title = meeting["topic_name"] or meeting["title"]
        self.work(lambda: client.open_session(requested, title=title), done, failed)

    def cmd_send(self, a):
        self.turns.submit(a.get("text", ""), self.files)
        self.files = []
        self.event.emit("sent", "")

    def prepare_turn_frame(self, mid, at):
        if not self.settings.values["share_frames"]:
            return None
        screen, version = self.screen, self.screen_version
        if screen:
            def capture_frame():
                path = screen.snapshot_sync(timeout=12)
                if version != self.screen_version:
                    raise AgentCancelled("画面共享已结束")
                return path

            return capture_frame

        source = self.current()["source"]
        if any(item["path"] == source and item.get("width") for item in self.media):
            def video_frame():
                shot = private_dir(DATA / "frames" / mid) / (uid() + ".jpg")
                extract_video_frame(source, max(0, at), shot)
                self.post(lambda: self.got_frame(version, shot) if self.mid == mid else None)
                return shot

            return video_frame
        return None

    def cmd_cancel_agent(self, a):
        self.turns.cancel()

    def shutdown(self):
        self.closed = True
        self.login.stop()
        self.turns.detach()
        self.transcription.close()
        self.stop_screen()
        if self.client:
            self.client.close()
