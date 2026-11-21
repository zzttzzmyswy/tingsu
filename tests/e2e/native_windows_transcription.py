"""Drive the Windows release through audio import, failed ASR, restart, retry and display."""
import json
import math
import os
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import wave

from native_windows_codex import choose_attachment, desktop
from native_windows_regression import profile


class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        assert self.path == "/asr" and self.headers["Authorization"] == "Bearer local-test-key"
        assert b"RIFF" in data and b"WAVE" in data
        if not self.server.available:
            self.send_error(503, "temporarily offline")
            return
        self.server.uploads += 1
        body = json.dumps({"segments": [{"start": 0.2, "end": 1.5, "speaker": 0, "text": "Caption A"},
                                       {"start": 1.6, "end": 2.8, "speaker": 1, "text": "Caption B"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def verify(app):
    with TemporaryDirectory(prefix="tingsu-audio-") as tmp, ThreadingHTTPServer(("127.0.0.1", 0), Model) as model:
        root = Path(tmp)
        source = root / "语音 采集.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", round(8000 * math.sin(i*0.08))) for i in range(46400)))
        env = profile(root)
        env.update(TINGSU_SILICONFLOW_URL=f"http://127.0.0.1:{model.server_port}/asr", SILICONFLOW_API_KEY="local-test-key")
        model.available = False
        model.uploads = 0
        Thread(target=model.serve_forever, daemon=True).start()
        try:
            with desktop(app, root, env) as p:
                p.click("library")
                p.click("import")
                choose_attachment(source, "添加会议录屏或音频")
                p.click("media", source.stem)
                p.field("#meeting-name", "原生 Windows 字幕验收")
                p.click("create-start")
                p.until("S.capture==='idle' && S.pending===1", 100)
                meeting = p.js("S.meeting.id")
            model.available = True
            with desktop(app, root, env) as p:
                p.until("S.meeting.id===" + json.dumps(meeting) + " && S.pending===1")
                p.click("retry")
                p.until("S.capture==='idle' && S.pending===0 && document.querySelector('.event-speech .event-copy')?.innerText")
                rows = p.js("S.events.filter(e=>e.kind==='speech').map(e=>[e.at,e.end,e.speaker,e.text])")
                assert [r[3] for r in rows] == ["Caption A", "Caption B"], rows
                assert rows[0][2] != rows[1][2] and 0 <= rows[0][0] < rows[0][1] <= rows[1][0] < rows[1][1] <= 2.91
                assert model.uploads == 1
            print("PASS Windows capture, ASR retry after restart, two speakers and rendered timestamped subtitles", flush=True)
        finally:
            model.shutdown()
