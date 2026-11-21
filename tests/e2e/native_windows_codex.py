"""Run the Windows release GUI and a separately installed official Codex against a loopback model."""

import argparse
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from threading import Thread

from desktop import Page
from codex_file_fixture import WorkspaceFixture


def choose_attachment(path):
    # UI Automation reaches the native file picker even on a non-interactive CI desktop.
    script = """
    Add-Type -AssemblyName UIAutomationClient
    $element = [Windows.Automation.AutomationElement]
    $scope = [Windows.Automation.TreeScope]::Descendants
    $name = New-Object Windows.Automation.PropertyCondition($element::NameProperty, '添加会议材料')
    $fileId = New-Object Windows.Automation.PropertyCondition($element::AutomationIdProperty, '1148')
    $openId = New-Object Windows.Automation.PropertyCondition($element::AutomationIdProperty, '1')
    $edit = New-Object Windows.Automation.PropertyCondition($element::ControlTypeProperty, [Windows.Automation.ControlType]::Edit)
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
      $dialog = $element::RootElement.FindFirst($scope, $name)
      if ($dialog) {
        $combo = $dialog.FindFirst($scope, $fileId)
        $open = $dialog.FindFirst($scope, $openId)
        if ($combo -and $open) {
          $field = $combo.FindFirst($scope, $edit)
          if (!$field) { $field = $combo }
          $field.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($env:QA_ATTACHMENT)
          $open.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
          exit 0
        }
      }
      Start-Sleep -Milliseconds 200
    }
    throw 'Native attachment picker was unavailable'
    """
    subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", script], check=True, timeout=45,
                   env={**os.environ, "QA_ATTACHMENT": str(path)})


@contextmanager
def desktop(app, root, env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([str(app)], env={**env, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS":
                            f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1 --disable-gpu"},
                            cwd=root)
    page = None
    try:
        page = Page(port, timeout=120)
        page.until("S?.meeting?.id")
        yield page
    finally:
        if page:
            page.close()
        if proc.poll() is None:
            # The one-file parent starts Python, which owns the native WebView2 window process.
            close = """
            $processes = @(Get-CimInstance Win32_Process)
            $ids = @([int]$env:QA_APP_PID)
            $ids += @($processes | Where-Object { $_.ParentProcessId -in $ids } | ForEach-Object ProcessId)
            $native = @($processes | Where-Object { $_.ParentProcessId -in $ids -and $_.Name -eq 'tingsu-webview.exe' })
            $windows = @($native | ForEach-Object { Get-Process -Id $_.ProcessId } | Where-Object { $_.MainWindowHandle -ne 0 })
            if ($windows.Count -ne 1 -or !$windows[0].CloseMainWindow()) { exit 1 }
            """
            try:
                subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", close], check=True,
                               env={**env, "QA_APP_PID": str(proc.pid)}, timeout=20)
                assert proc.wait(timeout=35) == 0, "Desktop did not exit cleanly"
            finally:
                if proc.poll() is None:
                    subprocess.run(["taskkill", "/pid", str(proc.pid), "/t", "/f"], timeout=20)
                    proc.wait(timeout=15)


def verify(app, codex):
    if sys.platform != "win32":
        raise RuntimeError("A native Windows runner is required")
    with tempfile.TemporaryDirectory(prefix="tingsu-release-qa-", dir=Path.home()) as profile:
        root = Path(profile)
        home = root / "codex-profile"
        home.mkdir()
        # Run with an empty desktop profile. All model inputs, credentials and replies are synthetic.
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ("TOKEN", "API_KEY"))}
        for key, folder in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("USERPROFILE", "user")):
            path = root / folder
            path.mkdir()
            env[key] = str(path)
        env.update(CODEX_HOME=str(home), HOME=env["USERPROFILE"], TINGSU_APP_DIR=str(root))
        with ThreadingHTTPServer(("127.0.0.1", 0), WorkspaceFixture) as model:
            model.platform = "windows"
            model.reads, model.waiting_result = 0, False
            model.marker = secrets.token_hex(12)
            original = root / "原始会议 表格.csv"
            original.write_text("item,code\n" * 30_000 + "total," + model.marker + "\n", encoding="utf-8")
            Thread(target=model.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{model.server_port}/v1"
                env.update(OPENAI_API_KEY="tingsu-local-acceptance", OPENAI_BASE_URL=base)
                (home / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": env["OPENAI_API_KEY"]}),
                                                encoding="utf-8")
                (home / "config.toml").write_text(
                    'model = "qa-windows"\nmodel_provider = "fixture"\n[model_providers.fixture]\n'
                    f'name = "Loopback"\nbase_url = "{base}"\nwire_api = "responses"\nsupports_websockets = false\n',
                    encoding="utf-8")
                with desktop(app, root, env) as page:
                    page.click("agent")
                    page.field("#connect-provider", "codex")
                    page.click("ai-settings")
                    page.field('[data-cli-path="codex"]', str(codex))
                    page.click("settings-save")
                    page.until("!modal && S.settings.codex_path===" + json.dumps(str(codex)))
                    page.click("agent")
                    page.click("connect")
                    page.until("S.agent==='choose'", 90)
                    page.click("session-new")
                    page.until("S.agent==='ready' && !modal", 90)
                    session = page.js("S.session")
                    assert session, "Missing Codex thread ID"
                    page.click("attach")
                    choose_attachment(original)
                    page.until("S.files.includes(" + json.dumps(original.name) + ")")
                    page.field("#message", "从附件末行读取编号。")
                    page.click("send")
                    reply = "e.kind==='assistant' && e.text===" + json.dumps(model.marker)
                    page.until(f"!S.sending && S.events.some(e=>{reply})", 100)
                original.unlink()
                with desktop(app, root, env) as page:
                    page.until(f"S.events.some(e=>{reply})")
                    page.click("agent")
                    page.click("connect")
                    page.until("S.agent==='choose'", 90)
                    page.click("session", "继续本会议的会话")
                    page.until("S.agent==='ready' && S.session===" + json.dumps(session), 90)
                    page.field("#message", "请再次打开此前发送的原文件，重新读取末行。")
                    page.click("send")
                    page.until(f"!S.sending && S.events.filter(e=>{reply}).length===2", 100)
                assert model.reads == 2
                print("PASS native Windows release, attachment picker, external Codex read its working directory twice, restart and same thread")
            finally:
                model.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--codex", required=True, type=Path)
    args = parser.parse_args()
    verify(args.app.resolve(strict=True), args.codex.resolve(strict=True))
