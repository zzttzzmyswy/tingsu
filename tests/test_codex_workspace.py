"""Exercise reading a real material through the separately installed Codex shell."""

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from e2e.codex_file_fixture import WorkspaceFixture
from meeting_agent.agent_codex import CodexBackend
from meeting_agent.agent_materials import make_inputs, Material


@unittest.skipUnless(os.environ.get("TINGSU_TEST_CODEX"), "set TINGSU_TEST_CODEX to test the system CLI")
class CodexWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_reads_csv_with_host_tools_and_resumes(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ):
            root = Path(folder)
            cwd = root / "meeting"
            material = cwd / "attachments" / "session 1" / "会议 正本.csv"
            material.parent.mkdir(parents=True)
            marker = secrets.token_hex(12)
            material.write_text("name,value\n" * 30_000 + "total," + marker + "\n", encoding="utf-8")
            home = root / "codex-profile"
            home.mkdir()
            os.environ.update(CODEX_HOME=str(home), OPENAI_API_KEY="tingsu-local-acceptance")
            (home / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "tingsu-local-acceptance"}))
            with ThreadingHTTPServer(("127.0.0.1", 0), WorkspaceFixture) as service:
                service.platform = "windows" if os.name == "nt" else "unix"
                service.filename = material.name
                service.reads, service.waiting_result, service.marker = 0, False, marker
                (home / "config.toml").write_text(
                    'model_provider="local_fixture"\n[model_providers.local_fixture]\nname="Test"\n'
                    f'base_url="http://127.0.0.1:{service.server_port}/v1"\nwire_api="responses"\n'
                    'supports_websockets=false\n')
                Thread(target=service.serve_forever, daemon=True).start()
                try:
                    saved = None
                    for turn in (1, 2):
                        client = CodexBackend("qa-model", os.environ["TINGSU_TEST_CODEX"], workspace=cwd)
                        try:
                            await client.connect()
                            sid = await client.open_session(saved, "Reading attachments")
                            attachments = []
                            if turn == 1:
                                saved = sid
                                attachments = [Material(material, "test", material.name, material.stat().st_size)]
                            self.assertEqual(sid, saved)
                            inputs = make_inputs("codex", "Read the original file and return its last value",
                                                 attachments, "", workspace=cwd)
                            text = await client.send(sid, inputs, lambda _: None, None)
                            if os.name == "nt" and text != marker:
                                from meeting_agent.windows_workspace import _logon_sid
                                print("Test desktop logon SID:", _logon_sid(), flush=True)
                                for target in (root, cwd, material.parent, material):
                                    subprocess.run(["icacls.exe", str(target)], check=False)
                            self.assertEqual(text, marker)
                        finally:
                            await client.close()
                    self.assertEqual(service.reads, 2)
                finally:
                    service.shutdown()
