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
from urllib.request import urlopen

from desktop import Page
from codex_file_fixture import WorkspaceFixture


def choose_save_path(path, window):
    # The modern Save dialog exposes its file name as an automation element,
    # sometimes inside DirectUI rather than a Win32 Edit child of cmb13.
    script = """
    $ErrorActionPreference='Stop'
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName System.Windows.Forms
    $ae=[Windows.Automation.AutomationElement]
    $dialog=$ae::FromHandle([intptr][long]$env:QA_SAVE_WINDOW)
    $scope=[Windows.Automation.TreeScope]::Descendants
    $all=$dialog.FindAll($scope, [Windows.Automation.Condition]::TrueCondition)
    $edits=@($all | Where-Object {$_.Current.ControlType -eq [Windows.Automation.ControlType]::Edit})
    $field=$edits | Where-Object {
      $_.Current.Name -match 'file name|文件名' -or $_.Current.AutomationId -in '1001','1148'
    } | Select-Object -First 1
    $save=$all | Where-Object {
      $_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and
      ($_.Current.AutomationId -eq '1' -or $_.Current.Name -in 'Save','保存')
    } | Select-Object -First 1
    if ($field -and $save) {
      $value=$field.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
      $value.SetValue($env:QA_SAVE_PATH)
      $save.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    } else {
      # Explorer's File name mnemonic targets the input even when the UIA edit
      # provider is unavailable. The clipboard preserves the full Unicode string.
      $keyboard=New-Object -ComObject WScript.Shell
      if (!$keyboard.AppActivate($dialog.Current.Name)) { throw 'Could not focus the Save dialog' }
      [Windows.Forms.Clipboard]::SetText($env:QA_SAVE_PATH)
      [Windows.Forms.SendKeys]::SendWait('%n')
      [Windows.Forms.SendKeys]::SendWait('^a')
      [Windows.Forms.SendKeys]::SendWait('^v')
      [Windows.Forms.SendKeys]::SendWait('{ENTER}')
    }
    """
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-STA", "-Command", script], check=True,
                   env={**os.environ, "QA_SAVE_PATH": str(path), "QA_SAVE_WINDOW": str(window)}, timeout=18)


def choose_attachment(path, title="添加会议材料"):
    import ctypes
    from ctypes import wintypes as wt
    import time

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    user.FindWindowW.restype = wt.HWND
    user.GetDlgItem.argtypes = [wt.HWND, ctypes.c_int]
    user.GetDlgItem.restype = wt.HWND
    user.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
    user.FindWindowExW.restype = wt.HWND
    user.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.SetForegroundWindow.argtypes = [wt.HWND]
    user.IsWindowVisible.argtypes = [wt.HWND]
    user.IsWindowEnabled.argtypes = [wt.HWND]
    user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wt.LPARAM]
    user.EnumChildWindows.argtypes = [wt.HWND, callback_type, wt.LPARAM]
    def window_text(hwnd, fn):
        buf = ctypes.create_unicode_buffer(400)
        fn(hwnd, buf, len(buf))
        return buf.value
    def dialogs():
        records = []
        @callback_type
        def capture(hwnd, _):
            if user.IsWindowVisible(hwnd) and window_text(hwnd, user.GetClassNameW) == "#32770":
                records.append(window_text(hwnd, user.GetWindowTextW))
                @callback_type
                def detail(child, _):
                    text = window_text(child, user.GetWindowTextW)
                    if text:
                        records.append((window_text(child, user.GetClassNameW), text))
                    return True
                user.EnumChildWindows(hwnd, detail, 0)
            return True
        user.EnumWindows(capture, 0)
        return records
    until = time.monotonic() + 30
    while time.monotonic() < until:
        dialog = user.FindWindowW("#32770", title)
        if dialog:
            if title == "导出会议记录" and user.IsWindowEnabled(dialog):
                user.SetForegroundWindow(dialog)
                choose_save_path(path, dialog)
                limit = time.monotonic() + 6
                while time.monotonic() < limit:
                    if not user.IsWindowVisible(dialog):
                        return
                    time.sleep(0.1)
                raise RuntimeError("Native save dialog did not close: " + str(dialogs()))
            # Explorer-style OPENFILENAME uses cmb13; classic layouts use edt1.
            combo = user.GetDlgItem(dialog, 1148)
            inner = user.FindWindowExW(combo, None, "ComboBox", None) if combo else None
            field = (user.FindWindowExW(inner or combo, None, "Edit", None) if combo else None) or user.GetDlgItem(dialog, 1152)
            button = user.GetDlgItem(dialog, 1)
            if field and button and user.IsWindowEnabled(dialog) and user.IsWindowEnabled(button):
                user.SetForegroundWindow(dialog)
                # Quotes disambiguate spaces in the multi-select open dialog.
                value = ctypes.create_unicode_buffer(f'"{path}"')
                if not user.SendMessageW(field, 0x000C, 0, ctypes.addressof(value)):
                    raise RuntimeError("Windows rejected the selected file name")
                if not user.PostMessageW(dialog, 0x0111, 1, button):  # WM_COMMAND, IDOK
                    raise RuntimeError("Cannot confirm the attachment picker")
                limit = time.monotonic() + 5
                while time.monotonic() < limit:
                    if not user.IsWindowVisible(dialog):
                        return
                    time.sleep(0.08)
                raise RuntimeError("Native picker did not accept the selected path: " + str(dialogs()))
        time.sleep(0.15)
    raise RuntimeError("No Windows file-name field in the native attachment picker: " + str(dialogs()))


@contextmanager
def debugger(port):
    import ctypes
    import winreg
    flags = f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1 --disable-gpu"
    # Elevated hosts (including the Actions runner) ignore WebView2 environment overrides
    # since Runtime 150. Set only our test host's machine policy, then restore it.
    if not ctypes.windll.shell32.IsUserAnAdmin():
        yield flags
        return
    path = r"SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"
    name = "tingsu-webview.exe"
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, access=winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            previous = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            previous = None
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, flags)
        try:
            yield flags
        finally:
            if previous:
                winreg.SetValueEx(key, name, 0, previous[1], previous[0])
            else:
                winreg.DeleteValue(key, name)


@contextmanager
def desktop(app, root, env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with debugger(port) as flags:
        with running_desktop(app, root, {**env, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": flags}, port) as page:
            yield page


@contextmanager
def running_desktop(app, root, env, port):
    proc = subprocess.Popen([str(app)], env=env, cwd=root)
    page = None
    try:
        page = Page(port, timeout=120)
        page.until("S?.meeting?.id")
        yield page
    except Exception:
        # Surface host/runtime failures even when the inspector never gets a page.
        print("Windows GUI exit status:", proc.poll(), flush=True)
        if page:
            print("Rendered state:", page.js("({view,modal,libraryBusy:S.libraryBusy,media:S.media.map(m=>m.title),"
                                             "last:S.events.slice(-4).map(e=>[e.kind,e.text]),files:S.files,"
                                             "notices:document.getElementById('notices').innerText})"), flush=True)
        for path in ("json/version", "json/list"):
            try:
                with urlopen(f"http://127.0.0.1:{port}/{path}", timeout=2) as response:
                    print(path, response.read(4096).decode(errors="replace"), flush=True)
            except OSError as exc:
                print(path, str(exc), flush=True)
        diagnostics = """
        Get-Process -ErrorAction SilentlyContinue -Name tingsu-webview,Tingsu-Windows-x86_64,msedgewebview2 |
            Select-Object Name,Id,MainWindowTitle,Path | Format-Table -AutoSize | Out-String -Width 240
        Get-ChildItem "${env:ProgramFiles(x86)}/Microsoft/EdgeWebView/Application/*/msedgewebview2.exe" -EA SilentlyContinue |
            Select-Object FullName,Length
        Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" |
            Where-Object { $_.CommandLine -notmatch '--type=' } | Select-Object -ExpandProperty CommandLine
        Get-NetTCPConnection -LocalPort $env:QA_WEBVIEW_PORT -EA SilentlyContinue | Format-Table -AutoSize
        """
        subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", diagnostics], timeout=25,
                       env={**env, "QA_WEBVIEW_PORT": str(port)})
        raise
    finally:
        if page:
            page.close()
        if proc.poll() is None:
            # The Go backend owns the native WebView2 window process.
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
    with tempfile.TemporaryDirectory(prefix="听序 Windows 会议 ", dir=Path.home()) as profile:
        root = Path(profile)
        home = root / "codex-profile"
        home.mkdir()
        # Run with an empty desktop profile. All model inputs, credentials and replies are synthetic.
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ("TOKEN", "API_KEY"))}
        if os.environ.get("TINGSU_TEST_DEFAULT_WINDOWS_PROFILE") == "1":
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise RuntimeError("The default-profile check requires a disposable GitHub Actions VM")
            for key in ("LOCALAPPDATA", "APPDATA"):
                if (Path(env[key]) / "Tingsu").exists():
                    raise RuntimeError("Default Tingsu data already exists; this check requires a clean test account")
        else:
            for key, folder in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming")):
                path = root / folder
                path.mkdir()
                env[key] = str(path)
        env.update(CODEX_HOME=str(home), TINGSU_APP_DIR=str(root))
        with ThreadingHTTPServer(("127.0.0.1", 0), WorkspaceFixture) as model:
            model.platform = "windows"
            model.reads, model.waiting_result = 0, False
            model.marker = secrets.token_hex(12)
            original = root / "原始会议 表格.csv"
            model.filename = original.name
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
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--suite", choices=("all", "workspace", "board", "codex", "transcription"), default="all")
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    from native_windows_regression import verify_board, verify_workspace
    if args.suite in ("all", "transcription"):
        from native_windows_transcription import verify as verify_transcription
        verify_transcription(app)
    if args.suite in ("all", "codex") and not args.codex:
        parser.error("--codex is required to test the external Codex CLI")
    if args.suite in ("all", "workspace"):
        verify_workspace(app)
    if args.suite in ("all", "board"):
        verify_board(app)
    if args.suite in ("all", "codex"):
        verify(app, args.codex.resolve(strict=True))
