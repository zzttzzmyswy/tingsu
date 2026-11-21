"""Verify the shared board, autonomous opening reminders and AI status updates in a real GUI."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path

from meeting_topics import TOPICS, TopicModel, connect, run


FOLLOWUPS = {TOPICS[0]: "验收窗口 <周五>", TOPICS[1]: "渠道预算 & 负责人"}
FINISHED = "已归档的旧问题"
AI_TASK = "请核实提测时间"


class BoardModel(TopicModel):
    def do_POST(self):
        if self.path == "/v1/conversations":
            return super().do_POST()
        if not self.authorized():
            return
        assert self.path == "/v1/responses"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        theme = self.server.names[request["conversation"]]
        item = request["input"][0]
        if item.get("type") == "function_call_output":
            result = json.loads(item["output"])["result"]
            if item["call_id"] == "create":
                assert result["title"] == AI_TASK
                return self.message("已记录提测待办。")
            if item["call_id"] == "find":
                assert result["total"] == 1
                note = result["items"][0]
                assert note["title"] == FOLLOWUPS[theme] and note["status"] == "in_progress"
                return self.tool("set_topic_note_status", "resolve", {
                    "id": note["id"], "version": note["version"], "status": "done",
                })
            assert item["call_id"] == "resolve" and result["status"] == "done"
            return self.message("已将首场的问题标为完成。")
        entry = json.loads(item["content"][0]["text"])
        ctx = json.loads(entry["meeting_context"])
        assert ctx["主题"] == theme
        visible = {n["title"] for n in ctx["主题共享便签"]}
        assert FOLLOWUPS[TOPICS[1] if theme == TOPICS[0] else TOPICS[0]] not in visible
        if entry["request"].startswith("开场跟进"):
            pending = ctx["本次开场跟进"]
            names = {n["title"] for n in pending}
            expected = {FOLLOWUPS[theme], AI_TASK} if theme == TOPICS[0] else {FOLLOWUPS[theme]}
            assert names == expected and FINISHED not in names
            assert all(n["source_meeting"] == self.server.meetings[theme][0] for n in pending)
            mid = ctx["本场会议ID"]
            assert mid not in self.server.notified, "Repeated opening after reconnect"
            self.server.notified.add(mid)
            return self.message("开场请跟进：" + FOLLOWUPS[theme])
        if entry["request"] == "创建提测待办":
            return self.tool("create_topic_note", "create", {
                "kind": "task", "title": AI_TASK, "body": "周五前确认", "status": "open",
            })
        if entry["request"] == "问题已解决，请关闭":
            return self.tool("list_topic_notes", "find", {
                "query": FOLLOWUPS[theme], "status": "all", "offset": 0, "limit": 20,
            })
        assert entry["request"] == "检查连接"
        self.message("连接正常。")


def note(page, title, *, kind, status=None):
    page.click("note-new", area=".panel-header ")
    page.field("#note-kind", kind)
    page.field("#note-title", title)
    page.field("#note-body", "下次会上检查")
    if status:
        page.field("#note-status", status)
    page.click("note-save")
    page.until("!modal")


def send(page, prompt, reply):
    page.field("#message", prompt)
    page.click("send")
    page.until("!S.sending && S.events.some(e=>e.kind==='assistant' && e.text===" + json.dumps(reply) + ")")


def board_flow(launch, model):
    model.notified = set()
    ids = {}
    sessions = {}
    with ExitStack() as processes:
        p = processes.enter_context(launch())
        p.until("$('#board-tools').hidden")
        for number in range(1, 6):
            for theme in TOPICS:
                former = p.js("S.meeting.id")
                p.click("new")
                p.field("#meeting-name", "联合例会 " + str(number))
                p.field("#topic-select", ids.get(theme, "new-topic"))
                if theme not in ids:
                    p.field("#topic-name", theme)
                p.click("create")
                p.until("S.meeting.id!==" + json.dumps(former) + " && !modal && boardScope==='topic'")
                ids[theme] = p.js("S.meeting.topic_id")
                mid = p.js("S.meeting.id")
                model.meetings[theme].append(mid)
                title = FOLLOWUPS[theme]
                if number == 1:
                    if theme == TOPICS[0]:
                        note(p, title, kind="question", status="in_progress")
                        note(p, FINISHED, kind="question", status="done")
                    else:
                        p.click("board-scope", "本场便签")
                        note(p, title, kind="task")
                        p.click("note-edit", title)
                        p.click("note-share")
                        p.until("!modal && boardScope==='topic' && S.topicNotes.length===1")
                        assert p.js("S.notes[0].id !== S.topicNotes[0].id")
                else:
                    p.until("$('#notes').innerText.includes(" + json.dumps(title) + ")")
                    assert p.js("S.notes.length") == 0
                    assert FINISHED not in p.js("$('#notes').innerText")
                sid = connect(p, number == 1, choose=number == 1)
                assert sessions.setdefault(theme, sid) == sid
                if number == 1 and theme == TOPICS[0]:
                    send(p, "创建提测待办", "已记录提测待办。")
                if number > 1:
                    text = json.dumps("开场请跟进：" + title)
                    p.until("!S.sending && S.events.some(e=>e.kind==='assistant' && e.text===" + text + ")")
                    assert p.js("S.events.every(e=>e.kind!=='user')")
                if number == 3 and theme == TOPICS[1]:
                    processes.close()
                    p = processes.enter_context(launch())
                    p.until("S.meeting.id===" + json.dumps(mid))
                    assert connect(p, False) == sid
                    send(p, "检查连接", "连接正常。")
                    assert p.js("S.events.filter(e=>e.text.startsWith('开场请跟进')).length") == 1
                if number == 5 and theme == TOPICS[0]:
                    send(p, "问题已解决，请关闭", "已将首场的问题标为完成。")
                    p.until("!$('#notes').innerText.includes(" + json.dumps(title) + ")")
                    p.field("#board-status", "done")
                    p.until("$('#notes').innerText.includes(" + json.dumps(title) + ")")
                    saved = next(n for n in p.js("S.topicNotes") if n["title"] == title)
                    assert saved["source_meeting"] == model.meetings[theme][0] and saved["updated_meeting"] == mid
                    assert saved["updated_by"] == "AI"
            print(f"PASS isolated shared cards across both themes through meeting {number}", flush=True)

        assert len(model.names) == 2 and len(model.notified) == 8
        p.click("topic")
        p.click("select", "联合例会 1", area=".topic-history ")
        p.until("S.meeting.id===" + json.dumps(model.meetings[TOPICS[1]][0]))
        p.click("note-status", "标为完成")
        p.until("S.topicNotes[0].status==='done'")
        p.field("#board-status", "done")
        p.click("note-status", "重新打开")
        p.until("S.topicNotes[0].status==='open'")
        p.click("board-scope", "本场便签")
        p.until("$('#notes').innerText.includes(" + json.dumps(FOLLOWUPS[TOPICS[1]]) + ")")
        print("PASS opening reminders without speech or manual prompts, restart deduplication, AI completion and manual reopen", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--wine", action="store_true")
    modes.add_argument("--wayland", action="store_true")
    args = parser.parse_args()
    run(args.app.resolve(), args.wine, args.wayland, fixture=BoardModel, exercise=board_flow)
