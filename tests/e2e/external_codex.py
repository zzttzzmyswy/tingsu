"""Exercise the external Codex executable and a synthetic loopback model via the GUI."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from meeting_topics import run
from openai_fixture import Fixture
from windows_desktop import windows_path


class CodexFixture(Fixture):
    def do_POST(self):
        if self.path != "/v1/responses":
            return self.send_error(404)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert any("ORCHID-718" in json.dumps(item) for item in request["input"])
        self.server.received.append((request.get("model"), request.get("reasoning", {}).get("effort"),
                                     request.get("service_tier")))
        answer = '{"message":"ORCHID-718 received","actions":[]}'
        part = {"type": "output_text", "text": answer, "annotations": []}
        item = {"type": "message", "role": "assistant", "id": "msg_1", "status": "completed",
                "content": [part]}
        response = {"object": "response", "id": "resp_1", "status": "completed", "output": [item]}
        self.reply([
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
            {"type": "response.content_part.added", "output_index": 0, "content_index": 0,
             "item_id": "msg_1", "part": {**part, "text": ""}},
            {"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
             "item_id": "msg_1", "delta": answer},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ], stream=True)


def verify(app, executable, wine, wayland):
    with tempfile.TemporaryDirectory(prefix="tingsu-external-codex-") as folder:
        home = Path(folder)
        native_path = windows_path if wine else str

        def configure(page, path):
            if not page.js("modal==='agent'"):
                page.click("agent")
            page.field("#connect-provider", "codex")
            page.click("ai-settings")
            page.field('[data-cli-path="codex"]', path)
            page.click("settings-save")
            page.until("!modal && S.settings.codex_path===" + json.dumps(path))
            page.click("agent")

        def scenario(launch, model):
            (home / "auth.json").write_text(json.dumps({
                "auth_mode": "apikey", "OPENAI_API_KEY": "tingsu-local-acceptance"
            }), encoding="utf-8")
            (home / "config.toml").write_text(f"""
model = "unused-fixture-default"
model_provider = "tingsu_fixture"
[model_providers.tingsu_fixture]
name = "Loopback acceptance"
base_url = "http://127.0.0.1:{model.server_port}/v1"
wire_api = "responses"
supports_websockets = false
""", encoding="utf-8")
            with launch() as page:
                model.received = []
                configure(page, native_path(home / "nonexistent-codex"))
                page.click("connect")
                page.until("S.agent==='offline' && $('#notices').innerText.includes('找不到可运行的 codex')")
                configure(page, native_path(executable))
                page.click("agent-models")
                page.until("S.agentCatalogs.codex?.some(m=>m.effort.includes('high') && m.speed.includes('priority'))", 90)
                selected = page.js("S.agentCatalogs.codex.find(m=>m.effort.includes('high') && m.speed.includes('priority')).id")
                group = '#connect-options [data-cli-options="codex"] '
                page.field(group + '[data-pref="model"]', selected)
                page.js("document.querySelector('[data-pref=model]').dispatchEvent(new Event('input',{bubbles:true}))")
                page.field(group + '[data-pref="effort"]', "high")
                page.field(group + '[data-pref="speed"]', "priority")
                page.click("connect")
                page.until("S.agent==='choose'", 100)
                page.click("session-new")
                page.until("S.agent==='ready' && !modal", 100)
                session = page.js("S.session")
                page.field("#message", "ORCHID-718")
                page.click("send")
                page.until("!S.sending && S.events.some(e=>e.kind==='assistant' && e.text==='ORCHID-718 received')", 150)
            assert model.received == [(selected, "high", "priority")], model.received
            with launch() as page:
                options = page.js("S.settings.local_agent_options.codex")
                assert options == {"model": selected, "effort": "high", "speed": "priority"}, options
                page.click("agent")
                page.click("connect")
                page.until("S.agent==='choose'", 100)
                page.click("session", "继续本会议的会话")
                page.until("S.agent==='ready' && S.session===" + json.dumps(session), 100)
            print("PASS missing host CLI, real CLI catalog, model/effort/service tier on loopback, settings and session resume", flush=True)

        with patch.dict(os.environ, {"CODEX_HOME": native_path(home)}):
            run(app, wine, wayland, fixture=CodexFixture, exercise=scenario)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, required=True)
    cli.add_argument("--executable", type=Path, required=True)
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--wine", action="store_true")
    modes.add_argument("--wayland", action="store_true")
    args = cli.parse_args()
    verify(args.app.resolve(), args.executable.resolve(), args.wine, args.wayland)
