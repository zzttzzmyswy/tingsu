"""A loopback model that must call the installed Codex's filesystem tools to answer."""

import json
from pathlib import PureWindowsPath
import shlex

if __package__:
    from .openai_fixture import Fixture
else:
    from openai_fixture import Fixture


def utf16_units(text):
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


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
                        info = payload["attachments"][0]
                        state.relative_path = info["path"]
                        if state.platform == "windows":
                            assert info["name"] == state.filename == info["path"].rsplit("/", 1)[1]
                            state.workspace = payload["workspace"]
                            assert PureWindowsPath(state.workspace).is_absolute()
                            assert PureWindowsPath(info["absolute_path"]) == PureWindowsPath(state.workspace) / info["path"]
                            assert info["absolute_path_utf16"] == utf16_units(info["absolute_path"])
                            state.path_units = info["absolute_path_utf16"]
            relative = state.relative_path
            assert relative.startswith("attachments/") and ".." not in relative.split("/")
            # The marker is deliberately absent from the user prompt and metadata.
            if not state.reads:
                assert state.marker not in json.dumps(req, ensure_ascii=False)
            tool = "exec_command"
            assert any(row.get("name") == tool for row in req["tools"])
            options = {"login": False}
            if state.platform == "windows":
                options["workdir"] = state.workspace
                path = ",".join(map(str, state.path_units))
                cwd = ",".join(map(str, utf16_units(state.workspace)))
                relative_units = ",".join(map(str, utf16_units(relative)))
                command = (
                    f"$w=-join [char[]]({cwd}); $p=-join [char[]]({path}); $r=-join [char[]]({relative_units}); "
                    "try { $f=Get-Item -LiteralPath $p -ErrorAction Stop; "
                    "'filename='+(([int[]][char[]]$f.Name) -join ','); "
                    "$line=Get-Content -LiteralPath $f.FullName -Tail 1 -ErrorAction Stop; "
                    "Write-Output $line; Set-Location -LiteralPath $w -ErrorAction Stop; "
                    "'workspace='+($line -eq (Get-Content -LiteralPath $r -Tail 1 -ErrorAction Stop)); "
                    "} catch { "
                    "Write-Output $_; whoami.exe /user /fo csv /nh; whoami.exe /logonid; "
                    "whoami.exe /groups /fo csv /nh | Select-String 'S-1-5-5-|CodexSandbox'; icacls.exe $w }"
                )
            else:
                # Exercise an arbitrary host-installed interpreter on an actual CSV.
                command = "python3 -c " + shlex.quote(
                    'import csv,sys; print(list(csv.reader(open(sys.argv[1], encoding="utf-8")))[-1][-1])'
                ) + " " + shlex.quote(relative)
            call_id = "call-file-" + str(state.reads)
            item = {"type": "function_call", "name": tool, "id": "fc" + call_id, "call_id": call_id,
                    "arguments": json.dumps({"cmd": command, **options}), "status": "completed"}
            state.waiting_result = True
        else:
            outputs = [v for v in req["input"] if v.get("type") == "function_call_output" and
                       v.get("call_id") == "call-file-" + str(state.reads)]
            result = outputs[-1]["output"] if outputs else "No tool result"
            # Return a useful failure to the driving test instead of breaking the HTTP stream.
            matched = state.marker in str(result)
            if state.platform == "windows":
                filename = ",".join(map(str, utf16_units(state.filename)))
                matched = matched and "workspace=True" in str(result) and "filename=" + filename in str(result)
                if not matched:
                    print("Synthetic Codex shell diagnostics:", str(result)[:5000], flush=True)
            answer = state.marker if matched else "Native read failed: " + str(result)[:700]
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
