"""Loopback DeepSeek Messages server for exercising the real dsh CLI with synthetic data."""

import argparse
import json
import re
from email.parser import BytesParser
from email.policy import default
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

TOKEN = "tingsu-deepseek-acceptance"
MODEL = "deepseek-v4-flash"


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, mime="application/json"):
        data = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The cancellation test closes its in-flight stream.

    def do_GET(self):
        if self.path == "/models":
            return self.reply({"data": [{"id": MODEL, "input_modalities": ["text", "image"] if self.server.images else ["text"],
                                         "api_capabilities": {"anthropic_messages": {}}}]})
        if self.path.startswith("/v1/files"):
            return self.reply({"data": [], "has_more": False})
        self.send_error(404)

    def do_POST(self):
        key = self.headers.get("x-api-key") or self.headers.get("Authorization", "").removeprefix("Bearer ")
        if key != TOKEN:
            return self.send_error(401)
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/v1/files":
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + data
            )
            file = next(part for part in message.iter_parts() if part.get_filename())
            payload = file.get_payload(decode=True)
            self.server.images_received.append(payload)
            return self.reply({"id": "file-synthetic-1", "type": "file", "size_bytes": len(payload),
                               "filename": file.get_filename(), "mime_type": file.get_content_type(),
                               "created_at": "2026-11-01T00:00:00.000Z"})
        if self.path.split("?")[0] != "/v1/messages":
            return self.send_error(404)
        request = json.loads(data)
        self.server.requests.append(request)
        names = {t["name"] for t in request.get("tools", [])}
        # Claude's streaming transport may add this inert deferred-tool marker.
        if self.server.tool_prefix == "mcp__meeting__":
            names.discard("DeferredToolPlaceholder")
        if any(not name.startswith(self.server.tool_prefix) for name in names):
            return self.send_error(422, "Unexpected non-meeting tool")
        # The native Claude CLI can append a system reminder after the actual user turn.
        msgs = [m for m in request.get("messages", []) if m.get("role") in {"assistant", "user"}]
        if not msgs:
            return self.send_error(422, "Expected conversation input")
        last = json.dumps(msgs[-1], ensure_ascii=False)
        if "tingsu-block-request" in last:
            self.server.block_started.set()
            if not self.server.block_release.wait(15):
                return self.send_error(504)
        content = msgs[-1].get("content", [])
        results = {block.get("tool_use_id") for block in content if isinstance(block, dict)
                   and block.get("type") == "tool_result"}
        file_id = re.search(r'"attachments"\s*:\s*\[\s*\{\s*"id"\s*:\s*"([a-f0-9]{64})"', last.replace('\\"', '"'))
        tool = None
        if results:
            # Claude can group both calls in one assistant message and both results in one user message.
            if ("tool_list_topic_meetings" in results and "tool_read_topic_history" not in results
                    and "prior-topic-session" in last):
                tool = ("read_topic_history", {"meeting_id": "prior-topic-session", "kind": "transcript",
                                               "query": "预算", "offset": 0, "limit": 10})
            elif "tool_read_attachment" in results and "tool_read_transcript" not in results and "ORCHID-718" in last:
                tool = ("read_transcript", {"query": "", "limit": 8, "offset": 0})
            elif ("tool_read_transcript" in results and "tool_create_note" not in results
                    and "ORCHID" in json.dumps(msgs, ensure_ascii=False)):
                tool = ("create_note", {"kind": "task", "title": "验收行动项", "body": "ORCHID-718"})
        elif file_id:
            tool = ("read_attachment", {"id": file_id[1], "format": "text", "query": "ORCHID", "offset": 0, "limit": 500})
        elif "ORCHID" in last:
            tool = ("read_transcript", {"query": "", "limit": 8, "offset": 0})
        elif "tingsu-recall-topic" in last:
            tool = ("list_topic_meetings", {"query": "", "offset": 0, "limit": 10})
        elif "tingsu-resolve-board" in last:
            tool = ("set_topic_note_status", {**self.server.board_card, "status": "done"})
        events = [{"type": "message_start", "message": {"id": "msg_" + uuid4().hex, "type": "message", "role": "assistant",
                  "model": MODEL, "content": [], "usage": {"input_tokens": 12, "output_tokens": 0}}}]
        if tool:
            name, args = tool
            block = {"type": "tool_use", "id": f"tool_{name}", "name": self.server.tool_prefix + name, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(args, ensure_ascii=False)}
        else:
            block = {"type": "text", "text": ""}
            text = "已收到会议内容。" if "tool_result" not in last else "已完成本场会议的便签。"
            if "tool_read_topic_history" in results and "预算 417" in last:
                text = "上场会议确认的预算 417。"
            delta = {"type": "text_delta", "text": text}
        events.extend([
            {"type": "content_block_start", "index": 0, "content_block": block},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
             "usage": {"output_tokens": 10}},
            {"type": "message_stop"},
        ])
        self.reply("".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events), "text/event-stream")


def start(port=0, images=False, *, tool_prefix="mcp__tingsu__"):
    server = ThreadingHTTPServer(("127.0.0.1", port), Fixture)
    server.tool_prefix = tool_prefix
    server.requests = []
    server.images = images
    server.images_received = []
    server.block_started = threading.Event()
    server.block_release = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = start(args.port)
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
