import asyncio
import base64
import json
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
    print('--safe-mode --system-prompt-snapshot --include-partial-messages')
    sys.exit(0)
if 'auth' in args:
    emit({'loggedIn':True, 'configDirectory':str(root)})
    sys.exit(0)
assert args[args.index('--tools') + 1] == ''
assert '--strict-mcp-config' in args and '--safe-mode' in args
assert args[args.index('--system-prompt-snapshot') + 1] == 'off'
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
