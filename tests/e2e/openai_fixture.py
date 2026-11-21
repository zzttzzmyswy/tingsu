"""A loopback Conversations/Responses test server. Accepts only a synthetic credential."""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, stream=False):
        body = (
            "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in value)
            if stream
            else json.dumps(value, ensure_ascii=False)
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if self.headers.get("Authorization") == "Bearer tingsu-local-acceptance":
            return True
        self.send_error(401)
        return False

    def do_GET(self):
        if not self.authorized():
            return
        if self.path.startswith("/v1/models/"):
            self.reply({"id": "test-model"})
        elif self.path == "/v1/conversations/conv_acceptance":
            self.reply({"id": "conv_acceptance", "metadata": {"title": "验收会话"}})
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.authorized():
            return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/conversations":
            self.reply({"id": "conv_acceptance", "metadata": req["metadata"]})
            return
        if self.path != "/v1/responses" or req.get("conversation") != "conv_acceptance":
            self.send_error(400)
            return
        item = req["input"][0]
        if item.get("type") == "function_call_output":
            result = json.loads(item["output"])["result"]
            if item["call_id"] == "read":
                if not result or not result[0]["text"]:
                    self.send_error(422)
                    return
                self.tool(
                    "create_note", "note", {"kind": "task", "title": "验收行动项", "body": "ORCHID-718"}
                )
            elif item["call_id"] == "note" and result["id"]:
                self.message("已读取转写和材料并保存便签。")
            else:
                self.send_error(400)
            return
        content = item["content"]
        task = json.loads(content[0]["text"])["request"]
        if "ORCHID" in task:
            # The code and screenshot must actually enter the request via attachments.
            if not any(c["type"] == "input_file" and
                       b"ORCHID-718" in base64.b64decode(c["file_data"].split(",", 1)[1]) for c in content[1:]) or not any(
                c["type"] == "input_image" and c["image_url"].startswith("data:image/") for c in content
            ):
                self.send_error(422)
                return
            self.tool("read_transcript", "read", {"query": "", "offset": 0, "limit": 10})
        else:
            self.message("已收到会议上下文。")

    def tool(self, name, call_id, args):
        self.reply(
            [
                {
                    "type": "response.completed",
                    "response": {
                        "output": [
                            {
                                "type": "function_call",
                                "name": name,
                                "call_id": call_id,
                                "arguments": json.dumps(args),
                            }
                        ]
                    },
                }
            ],
            True,
        )

    def message(self, message):
        self.reply(
            [
                {"type": "response.output_text.delta", "delta": message[:3]},
                {"type": "response.output_text.delta", "delta": message[3:]},
                {"type": "response.completed", "response": {"output": []}},
            ],
            True,
        )


def start(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Fixture)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
