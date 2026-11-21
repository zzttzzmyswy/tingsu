"""A loopback model that must call the installed Codex's filesystem tools to answer."""

import json
import shlex

if __package__:
    from .openai_fixture import Fixture
else:
    from openai_fixture import Fixture


class WorkspaceFixture(Fixture):
    def do_POST(self):
        if self.path != "/v1/responses":
            return self.send_error(404)
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state = self.server
        if not state.waiting_result:
            for item in req["input"]:
                if item.get("role") != "user":
                    continue
                for block in item.get("content", []):
                    if not isinstance(block, dict) or block.get("type") != "input_text":
                        continue
                    try:
                        payload = json.loads(block["text"])
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and payload.get("attachments"):
                        state.relative_path = payload["attachments"][0]["path"]
            relative = state.relative_path
            assert relative.startswith("attachments/") and ".." not in relative.split("/")
            # The marker is deliberately absent from the user prompt and metadata.
            if not state.reads:
                assert state.marker not in json.dumps(req, ensure_ascii=False)
            tool = "exec_command"
            assert any(row.get("name") == tool for row in req["tools"])
            if state.platform == "windows":
                command = "Get-Content -LiteralPath '" + relative.replace("'", "''") + "' -Tail 1"
            else:
                # Exercise an arbitrary host-installed interpreter on an actual CSV.
                command = "python3 -c " + shlex.quote(
                    'import csv,sys; print(list(csv.reader(open(sys.argv[1], encoding="utf-8")))[-1][-1])'
                ) + " " + shlex.quote(relative)
            call_id = "call-file-" + str(state.reads)
            item = {"type": "function_call", "name": tool, "id": "fc" + call_id, "call_id": call_id,
                    "arguments": json.dumps({"cmd": command, "login": False}), "status": "completed"}
            state.waiting_result = True
        else:
            outputs = [v for v in req["input"] if v.get("type") == "function_call_output" and
                       v.get("call_id") == "call-file-" + str(state.reads)]
            result = outputs[-1]["output"] if outputs else "No tool result"
            # Return a useful failure to the driving test instead of breaking the HTTP stream.
            answer = state.marker if state.marker in str(result) else "Native read failed: " + str(result)[:450]
            state.reads += 1
            state.waiting_result = False
            item = {"type": "message", "role": "assistant", "id": "file-answer-" + str(state.reads),
                    "status": "completed", "content": [{"type": "output_text", "annotations": [],
                    "text": json.dumps({"message": answer, "actions": []}, ensure_ascii=False)}]}
        response = {"id": "resp-" + item["id"], "object": "response", "output": [item], "status": "completed"}
        self.reply([
            {"type": "response.created", "response": {**response, "output": [], "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ], stream=True)
