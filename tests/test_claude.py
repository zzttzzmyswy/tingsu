import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from meeting_agent.agent_claude import ClaudeBackend
from meeting_agent.agent_materials import AgentError
from meeting_agent.meeting_mcp import meeting_mcp_reply

# A separate Python process plays the CLI side of the duplex SDK protocol.
CLI = r"""
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
legacy = (root / 'legacy').is_file()
def emit(packet):
    print(json.dumps(packet), flush=True)
def read():
    return json.loads(input())
def mcp(method, params):
    message = {'jsonrpc':'2.0', 'id':1, 'method':method, 'params':params}
    emit({'type':'control_request','request_id':'mcp','request':{
        'subtype':'mcp_message','server_name':'meeting','message':message}})
    return read()['response']['response']['mcp_response']['result']
if '--help' in args:
    if legacy:
        print('--input-format --output-format --strict-mcp-config --tools --settings --permission-mode (choices: "default", "plan")')
        sys.exit(0)
    print('--input-format --output-format --strict-mcp-config --tools --safe-mode --system-prompt-snapshot --include-partial-messages --name --effort --settings')
    print('  --permission-mode (choices: "dontAsk")\n  auth  Manage authentication')
    sys.exit(0)
if 'auth' in args:
    assert not legacy
    emit({'loggedIn':not (root / 'logged-out').exists(), 'configDirectory':str(root)})
    sys.exit(0)
if (root / 'fail').exists():
    print('Error: unknown option --broken, API key=SHOULD_NEVER_APPEAR', file=sys.stderr)
    sys.exit(2)
assert args[args.index('--tools') + 1] == ''
assert '--strict-mcp-config' in args
if legacy:
    assert not any(flag in args for flag in ('--safe-mode', '--system-prompt-snapshot', '--name', '--permission-mode', '--setting-sources'))
    assert json.loads(args[args.index('--settings') + 1])['disableAllHooks']
else:
    assert '--safe-mode' in args
    assert args[args.index('--system-prompt-snapshot') + 1] == 'off'
    assert args[args.index('--permission-mode') + 1] == 'dontAsk'
sid = args[args.index('--resume' if '--resume' in args else '--session-id') + 1]
path = root / 'projects' / 'test-project' / (sid + '.jsonl')
assert ('--resume' in args) == path.exists()
assert read()['request']['subtype'] == 'initialize'
assert mcp('initialize', {'protocolVersion':'2025-06-18'})['protocolVersion'] == '2025-06-18'
emit({'type':'control_response','response':{'request_id':'init','subtype':'success'}})
user = read()
assert user['session_id'] == sid
content = user['message']['content']
assert content[0]['type'] == 'text'
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('a', encoding='utf-8') as f:
    print(json.dumps({'type':'custom-title','customTitle':'Native title'}), file=f)
if content[0]['text'] == 'hold':
    emit({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'waiting'}}})
    time.sleep(60)
if '--session-id' in args:
    image = content[1]['source']
    assert image == {'type':'base64','media_type':'image/png','data':'eA=='}
listed = mcp('tools/list',{})['tools']
assert set(t['name'] for t in listed) == {
    'list_notes', 'read_transcript', 'create_note', 'update_note', 'delete_note',
    'list_topic_meetings', 'read_topic_history',
    'list_topic_notes', 'create_topic_note', 'update_topic_note', 'set_topic_note_status',
    'share_note_to_topic', 'delete_topic_note', 'list_attachments', 'read_attachment',
}
result = mcp('tools/call',{'name':'list_notes','arguments':{}})
assert json.loads(result['content'][0]['text']) == {'result':['marker']}
for part in ['ans','wer']:
    emit({'type':'stream_event','event':{'delta':{'type':'text_delta','text':part}}})
emit({'type':'result','subtype':'success','session_id':sid,'result':'answer'})
"""


class TestCLI:
    def __init__(self, root):
        self.root = root
        self.children = []

    async def spawn(self, *args, cwd, env_overrides, **pipes):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", CLI, str(self.root), *args, cwd=cwd, **pipes
        )
        self.children.append(proc)
        return proc

    async def stop(self, proc):
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


class ClaudeTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_auth_session_resume_and_private_exit_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "meeting_agent.agent_claude.app_directory", return_value=Path(tmp)
        ), patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
            root = Path(tmp)
            (root / "legacy").touch()
            inputs = [{"type": "text", "text": "hello"},
                      {"type": "image", "url": "data:image/png;base64,eA=="}]
            sid = None
            for _ in range(2):
                agent = ClaudeBackend()
                agent.cli = TestCLI(root)
                await agent.connect()
                # The legacy CLI has no auth subcommand. A native resume must still work.
                self.assertEqual(len(agent.cli.children), 1)
                sid = await agent.open_session(sid, "Legacy")
                self.assertEqual(await agent.send(sid, inputs, lambda _: None,
                                                  lambda *_: ["marker"]), "answer")
                await agent.close()
            (root / "fail").touch()
            with self.assertRaisesRegex(AgentError, "退出码 2.*启动参数") as failure:
                await agent.send(sid, inputs, lambda _: None, None)
            self.assertNotIn("SHOULD_NEVER_APPEAR", str(failure.exception))

    async def test_native_auth_report_and_local_provider_settings(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "meeting_agent.agent_claude.app_directory", return_value=Path(tmp)
        ), patch.dict(os.environ, {k: "" for k in (
            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        )}):
            root = Path(tmp)
            (root / "logged-out").touch()
            agent = ClaudeBackend()
            agent.cli = TestCLI(root)
            with self.assertRaisesRegex(AgentError, "未登录"):
                await agent.connect()
            (root / "settings.json").write_text('{"apiKeyHelper":"my-helper"}')
            await agent.connect()
            self.assertEqual(agent.projects, root / "projects")
            await agent.close()

    async def test_native_history_draft_resume_multimodal_tools_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "meeting_agent.agent_claude.app_directory", return_value=Path(tmp)
        ):
            root = Path(tmp) / "config"

            async def connect():
                backend = ClaudeBackend()
                backend.cli = TestCLI(root)
                await backend.connect()
                return backend

            agent = await connect()
            sid = await agent.open_session(None, "Meeting")
            await agent.close()
            agent = await connect()
            self.assertEqual(await agent.list_sessions(), [{"id": sid, "title": "Meeting"}])
            self.assertEqual(await agent.open_session(sid, ""), sid)
            with self.assertRaises(AgentError):
                await agent.open_session("../sample", "")

            deltas, calls = [], []

            def tool(name, args):
                calls.append((name, args))
                return ["marker"]

            image = "data:image/png;base64," + base64.b64encode(b"x").decode()
            inputs = [{"type": "text", "text": "hello"}, {"type": "image", "url": image}]
            self.assertEqual(await agent.send(sid, inputs, deltas.append, tool), "answer")
            self.assertEqual(deltas, ["ans", "wer"])
            self.assertEqual(calls, [("list_notes", {})])
            self.assertEqual(await agent.list_sessions(), [{"id": sid, "title": "Native title"}])
            await agent.close()

            agent = await connect()
            self.assertEqual(await agent.open_session(sid, ""), sid)
            streaming = asyncio.Event()
            task = asyncio.create_task(
                agent.send(sid, [{"type": "text", "text": "hold"}], lambda _: streaming.set(), tool)
            )
            await asyncio.wait_for(streaming.wait(), 5)
            child = agent.cli.children[-1]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNotNone(child.returncode)
            self.assertEqual(await agent.send(sid, inputs[:1], deltas.append, tool), "answer")
            await agent.close()

    def test_mcp_exposes_only_bound_validated_meeting_actions(self):
        def request(method, params):
            return {"jsonrpc": "2.0", "id": 3, "method": method, "params": params}

        self.assertEqual(meeting_mcp_reply(request("tools/list", {}), None)["result"]["tools"], [])
        self.assertIsNone(
            meeting_mcp_reply({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
        )

        def handler(*_):
            self.fail("Invalid tool argument reached meeting storage")

        reply = meeting_mcp_reply(request("tools/call", {"name": "Bash"}), handler)
        self.assertEqual(reply["error"]["code"], -32602)
        reply = meeting_mcp_reply(
            request("tools/call", {"name": "create_note", "arguments": {}}), handler
        )
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("error", json.loads(reply["result"]["content"][0]["text"]))
