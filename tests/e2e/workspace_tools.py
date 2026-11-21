"""Exercise ten meeting-workspace features through the packaged desktop UI.

Use --app inside an isolated Linux test display, or --seed-dir to create data/
and config/ for a Windows / Wayland test account, then --port on its debug port.
See docs/acceptance.md for the meeting-flow tests driving real ASR.
"""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory

from desktop import debug_environment, Page


TITLE = "工作台回归 · 春季复盘"
OTHER_TITLE = "工作台回归 · 独立会议"
QUERY = "Orchid_[A]%"
SPEAKER = "发言人甲"


def seed(folder):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from meeting_agent import storage

    for part in ("data", "config"):
        if (folder / part).exists():
            raise ValueError(f"Fixture destination must be empty: {folder / part}")
    previous_data, previous_config = storage.DATA, storage.CONFIG
    storage.DATA, storage.CONFIG = folder / "data", folder / "config"
    try:
        store = storage.Store()
        try:
            mid = store.create(TITLE)
            for i in range(240):
                marker = QUERY if i < 75 else "review"
                store.event(mid, "speech", i * 3, i * 3 + 2, SPEAKER if i % 2 == 0 else "发言人乙",
                            f"{marker} 第 {i:03d} 条 <会议&材料>")
            store.event(mid, "assistant", 730, 730, "会议 AI", QUERY + " 助手分析")
            store.set_source(mid, "", "", 0, 730)
            store.tool(mid, "create_note", dict(kind="task", title="待办保留", body="待处理资料"), "我")
            store.tool(mid, "create_note", dict(kind="decision", title="决策保留", body="范围已确认"), "我")
            other = store.create(OTHER_TITLE)
            store.event(other, "speech", 0, 1, SPEAKER, QUERY + " 不可跨会议泄漏")
            storage.Settings().save(selected=mid)
        finally:
            store.db.close()
    finally:
        storage.DATA, storage.CONFIG = previous_data, previous_config


def input_text(page, selector, value):
    selector, value = json.dumps(selector), json.dumps(value)
    page.js(f"(() => {{const el=document.querySelector({selector});el.focus();el.value={value};"
            'el.dispatchEvent(new Event("input",{bubbles:true}));return true})()')


def key(page, key_name, **modifiers):
    event = json.dumps(dict(key=key_name, bubbles=True, cancelable=True, **modifiers))
    page.js(f"document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{event}))")


def check(page):
    page.until("S.meeting.title===" + json.dumps(TITLE))
    page.until("S.events.length===120 && S.older")
    page.click("find-meeting")
    input_text(page, "#meeting-find", "独立会议")
    page.until("document.querySelectorAll('.recent-item').length===1 && $('.recent-item').title.endsWith('独立会议')")
    page.click("select")
    page.until("S.meeting.title===" + json.dumps(OTHER_TITLE))
    input_text(page, "#meeting-find", "春季")
    page.click("select")
    page.until("S.meeting.title===" + json.dumps(TITLE))
    page.click("find-meeting")
    print("PASS 8 · Filter and open meeting history", flush=True)

    page.click("speakers")
    page.until("document.querySelectorAll('.speaker-stats li').length===2 && [...document.querySelectorAll('.speaker-stats li')].every(el=>el.innerText.includes('04:00 · 50%'))")
    key(page, "Escape")
    page.until("!modal && document.activeElement.dataset.action==='speakers'")
    print("PASS 9 · Both speaker durations and shares", flush=True)

    # App only loaded the last 120 rows, while the first 75 speech matches are earlier.
    key(page, "f", ctrlKey=True)
    page.until("document.activeElement.id==='record-query'")
    input_text(page, "#record-query", QUERY.lower())
    page.until("$('#match-count').innerText==='1–60 / 76 条' && document.querySelectorAll('#feed-list mark').length===60")
    page.until("$('#feed-list .event-copy').innerText.includes('第 000 条 <会议&材料>')")
    page.click("match-next")
    page.until("$('#match-count').innerText==='61–76 / 76 条' && document.querySelectorAll('#feed-list [data-seq]').length===16")
    page.click("match-prev")
    page.until("$('#match-count').innerText==='1–60 / 76 条'")
    print("PASS 1 · Full-history literal search, highlights and pagination", flush=True)

    page.field("#speaker-filter", SPEAKER)
    page.until("$('#match-count').innerText==='38 条记录' && [...document.querySelectorAll('.event-head b')].every(el=>el.innerText==='发言人甲')")
    input_text(page, "#record-query", "")
    page.until("$('#match-count').innerText==='1–60 / 120 条' && $('#feed-list .event-copy').innerText.includes('第 000 条')")
    print("PASS 2 · Speaker across full history with and without a keyword", flush=True)

    page.click("match-context")
    page.until("!findingRecords() && S.events.some(e=>e.text.includes('第 000 条')) && !S.older")
    page.click("select", match=OTHER_TITLE)
    page.until("S.meeting.title===" + json.dumps(OTHER_TITLE))
    page.click("select", match=TITLE)
    page.until("S.events.length===120 && S.older")
    page.js("$('#feed-scroll').scrollTop=0")
    anchor = page.js("$('#feed-list [data-seq]').dataset.seq")
    row = json.dumps('#feed-list [data-seq="' + anchor + '"]')
    top = page.js(f"document.querySelector({row}).getBoundingClientRect().top")
    page.click("older")
    page.until("!S.older && S.events.length===241")
    assert abs(page.js(f"document.querySelector({row}).getBoundingClientRect().top") - top) < 3
    page.until("!$('#return-latest').hidden")
    # A separate state change must also preserve the previously visible row.
    before = page.js(f"document.querySelector({row}).getBoundingClientRect().top")
    page.click("note-new")
    page.field("#note-title", "阅读位置回归")
    page.click("note-save")
    page.until("!modal && S.notes.some(n=>n.title==='阅读位置回归')")
    assert abs(page.js(f"document.querySelector({row}).getBoundingClientRect().top") - before) < 3
    page.click("latest")
    page.until("$('#return-latest').hidden && $('#feed-scroll').scrollHeight-$('#feed-scroll').clientHeight-$('#feed-scroll').scrollTop<3")
    print("PASS 3 · Jump to context, preserve reading position and return to the latest entry", flush=True)

    page.click("find-record")
    input_text(page, "#record-query", QUERY)
    page.until("$('#match-count').innerText==='1–60 / 76 条'")
    input_text(page, "#message", "原草稿")
    page.click("quote-event", area="#feed-list ")
    page.until("$('#message').value.includes('原草稿\\n\\n[00:00] 发言人甲') && $('#message').value.includes('第 000 条') && document.activeElement.id==='message'")
    print("PASS 4 · Quote exact event in composer with original draft intact", flush=True)

    page.click("note-event", area="#feed-list ")
    page.until("modal==='note' && $('#note-body').value.includes('[00:00] 发言人甲') && $('#note-body').value.includes('第 000 条')")
    page.field("#note-title", "引用便签")
    page.field("#note-kind", "question")
    page.js("document.querySelector('#dialog [data-action=note-save]').focus()")
    key(page, "Tab")
    page.until("document.activeElement.dataset.action==='close'")
    key(page, "Tab", shiftKey=True)
    page.until("document.activeElement.dataset.action==='note-save'")
    key(page, "Enter", ctrlKey=True)
    page.until("!modal && S.notes.some(n=>n.title==='引用便签' && n.body.includes('[00:00] 发言人甲'))")
    page.until("document.activeElement.dataset.action==='note-event'")
    print("PASS 5 · Create editable note with source text, speaker and time", flush=True)
    print("PASS 10 · Dialog focus stays inside, returns to origin, and Ctrl+Enter saves", flush=True)

    page.field("#note-filter", "question")
    page.until("$('#notes').innerText.includes('引用便签') && !$('#notes').innerText.includes('决策保留') && !$('#notes').innerText.includes('待办保留')")
    page.field("#note-filter", "task")
    page.until("document.querySelectorAll('#notes .note').length===1 && $('#notes .note h3').innerText==='待办保留'")
    page.field("#note-filter", "idea")
    page.until("$('#notes').innerText.includes('暂无灵感想法')")
    page.click("note-new", area="#notes ")
    page.until("$('#note-kind').value==='idea'")
    page.click("close")
    page.field("#note-filter", "question")
    print("PASS 6 · Filter board and create directly in an empty category", flush=True)

    page.click("note-edit", match="引用便签")
    page.field("#note-body", "更新后的讨论范围")
    page.click("note-follow")
    page.until("!modal && document.activeElement.id==='message' && $('#message').value.includes('原草稿') && $('#message').value.includes('请跟进这张便签：「引用便签」\\n更新后的讨论范围')")
    page.until("S.notes.some(n=>n.title==='引用便签' && n.body==='更新后的讨论范围')")
    print("PASS 7 · Save edited note and append follow-up to existing unsent message", flush=True)


def run_app(app):
    with TemporaryDirectory(prefix="tingsu-workspace-") as root, ExitStack() as stack:
        base = Path(root)
        seed(base / "fixture")
        for area in ("data", "config"):
            (base / area).mkdir()
            (base / area / "tingsu").symlink_to(base / "fixture" / area, target_is_directory=True)
        env = {**os.environ, "XDG_DATA_HOME": str(base / "data"), "XDG_CONFIG_HOME": str(base / "config"),
               "XDG_CACHE_HOME": str(base / "cache"), "CODEX_HOME": str(base / "unused-codex"),
               "QT_QPA_PLATFORM": "xcb", "XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "Openbox",
               "QT_NO_GLIB": "1"}
        env.pop("WAYLAND_DISPLAY", None)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env.update(debug_environment(port))
        log = stack.enter_context((base / "app.log").open("w+"))
        proc = subprocess.Popen([str(app.resolve()), "--x11"], env=env, stdout=log, stderr=log)
        def close_app():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        stack.callback(close_app)
        try:
            page = Page(port)
            stack.callback(page.close)
            check(page)
            window = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--name", "^听序 · 会议工作台$"], text=True).splitlines()[-1]
            subprocess.run(["xdotool", "windowactivate", "--sync", window, "key", "alt+F4"], check=True)
            assert proc.wait(timeout=25) == 0
        except Exception:
            log.seek(0)
            print(log.read()[-4000:], file=sys.stderr)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--app", type=Path)
    action.add_argument("--seed-dir", type=Path, help="Write new data/ and config/ for a disposable native test account")
    action.add_argument("--port", type=int, help="Check a running test desktop with the seeded meeting")
    args = parser.parse_args()
    if args.seed_dir:
        seed(args.seed_dir)
    elif args.port:
        test_page = Page(args.port)
        try:
            check(test_page)
        finally:
            test_page.close()
    else:
        run_app(args.app)
