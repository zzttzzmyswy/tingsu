"""Exercise the GTK/WebKit host on an isolated Linux display, including a pending file chooser."""

import argparse
import json
from pathlib import Path
import selectors
import subprocess
import tempfile


def verify(host):
    with tempfile.TemporaryDirectory(prefix="tingsu-gtk-test-") as folder:
        directory = Path(folder)
        page = directory / "index.html"
        page.write_text('<meta charset="UTF-8"><script src="roundtrip.js"></script>', encoding="utf-8")
        (directory / "roundtrip.js").write_text("""
          const send = packet => window.webkit.messageHandlers.tingsu.postMessage(JSON.stringify(packet));
          window.tingsuReceive = p => {
            if (p.name === 'navigate') location.href = 'other.html';
            else send({type:'command', name:'echo', args:p});
          };
          send({type:'ready'});
        """, encoding="utf-8")
        (directory / "other.html").write_text('<title>blocked navigation</title>', encoding="utf-8")
        (directory / "tingsu.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16"/></svg>',
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            [host, page], text=True, encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)

                def receive():
                    if not selector.select(20):
                        raise TimeoutError("No native message from WebKit")
                    return json.loads(proc.stdout.readline())

                def write(packet):
                    proc.stdin.write(json.dumps(packet, ensure_ascii=False) + "\n")
                    proc.stdin.flush()

                assert receive() == {"type": "ready"}
                write({"type": "signal", "name": "navigate", "args": []})
                message = {"type": "signal", "name": "state", "args": ["会议 📝\n" + "工作台" * 50_000]}
                write(message)
                assert receive() == {"type": "command", "name": "echo", "args": message}
                write({
                    "type": "dialog", "kind": "open-files", "title": "测试文件选择",
                    "id": 1, "filter": "*.wav *.mp4", "path": str(directory), "message": "",
                })
                proc.stdin.close()
                assert proc.wait(8) == 0
                print("GTK host passed local scripts, navigation, Unicode, large packet and chooser EOF checks.")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("host", type=Path)
    verify(cli.parse_args().host.resolve(strict=True))
