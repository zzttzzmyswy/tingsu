"""Drive the Windows release through Microsoft UI Automation, including a restart.

Run on an interactive Windows desktop with system WebView2 installed:
    python -u tests/e2e/native_windows_uia.py --app Tingsu-Windows-x86_64.exe
Only Python and the Windows built-in PowerShell/.NET UIAutomationClient are used.
"""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


AUTOMATION = r"""
param([int]$Launcher, [string]$MeetingName, [string]$Mode)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Windows.Forms

function Find-HostWindow {
    # A PyInstaller one-file launcher starts a Python process which starts the UI host.
    $candidates = @(Get-Process -Name 'tingsu-webview' -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 })
    if ($candidates.Count -eq 0) { return }
    $tree = @{}
    Get-CimInstance Win32_Process | ForEach-Object { $tree[[int]$_.ProcessId] = [int]$_.ParentProcessId }
    foreach ($candidate in $candidates) {
        $id = $candidate.Id
        while ($id -and $tree.ContainsKey($id)) {
            if ($id -eq $Launcher) { return $candidate }
            $id = $tree[$id]
        }
    }
}

function Wait-For([scriptblock]$Action, [string]$Description, [int]$Seconds = 45) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $result = & $Action
        if ($null -ne $result) { return $result }
        if (!(Get-Process -Id $Launcher -ErrorAction SilentlyContinue)) { throw 'The application exited' }
        Start-Sleep -Milliseconds 300
    } while ($timer.Elapsed.TotalSeconds -lt $Seconds)
    throw "Timed out waiting for $Description"
}

function Find-Element($Property, $Value) {
    $condition = [System.Windows.Automation.PropertyCondition]::new($Property, $Value)
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
}

function Named-Button([string]$Name) {
    $byName = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $Name)
    $byType = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    $conditions = [System.Windows.Automation.Condition[]]@($byName, $byType)
    $condition = [System.Windows.Automation.AndCondition]::new($conditions)
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
}

function Invoke-Button([string]$Name) {
    $element = Wait-For { Named-Button $Name } "accessible button $Name"
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        $pattern.Invoke()
    } else {
        $element.SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    }
    Write-Host "Invoked UIA button $Name"
}

function Get-Editor {
    $editor = Find-Element ([System.Windows.Automation.AutomationElement]::AutomationIdProperty) 'meeting-name'
    if ($null -ne $editor) { return $editor }
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
    return $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition) |
        Where-Object { $_.Current.Name -like '*会议名称*' } | Select-Object -First 1
}

$hostProcess = Wait-For { Find-HostWindow } 'the native WebView2 host window' 95
$root = [System.Windows.Automation.AutomationElement]::FromHandle($hostProcess.MainWindowHandle)
Write-Host "Found native window pid=$($hostProcess.Id) class=$($root.Current.ClassName) name=$($root.Current.Name)"
if ($root.Current.ClassName -ne 'TingsuWebView') { throw 'Unexpected native window class' }
try {
    $null = Wait-For { Named-Button '新建会议' } 'the new-meeting button in the rendered page' 75
    if ($Mode -eq 'create') {
        Invoke-Button '新建会议'
        $editor = Wait-For { Get-Editor } 'the meeting-name input'
        $editor.SetFocus()
        $editor.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($MeetingName)
        # Blink updates its UIA properties asynchronously after receiving a native action.
        $readTitle = {
            $field = Get-Editor
            if ($null -ne $field) {
                $pattern = $field.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                if ($pattern.Current.Value -ceq $MeetingName) { return $field }
            }
        }
        try { $null = Wait-For $readTitle 'the entered meeting name' 4 }
        catch {
            $editor.SetFocus()
            [System.Windows.Forms.SendKeys]::SendWait('^a')
            [System.Windows.Forms.SendKeys]::SendWait($MeetingName)
            $null = Wait-For $readTitle 'the entered meeting name after keyboard input' 10
        }
        Invoke-Button '仅创建会议'
    }
    # The title of the named meeting is present in the normally rendered page after reopening.
    $title = Wait-For { Find-Element ([System.Windows.Automation.AutomationElement]::NameProperty) $MeetingName } 'the saved meeting title'
    Write-Host "Verified meeting in the accessible tree ($Mode): $($title.Current.Name)"
} catch {
    $cause = $_
    try {
        $nodes = $root.FindAll([System.Windows.Automation.TreeScope]::Subtree,
            [System.Windows.Automation.Condition]::TrueCondition)
        Write-Host "Available UIA elements ($($nodes.Count)):"
        $nodes | Select-Object -First 80 | ForEach-Object {
            Write-Host "$($_.Current.ControlType.ProgrammaticName) [$($_.Current.AutomationId)] $($_.Current.Name)"
        }
    } catch { Write-Host "Unable to inspect UIA tree: $_" }
    throw $cause
}
if (!$hostProcess.CloseMainWindow()) { throw 'The native window rejected the close request' }
"""


def verify(app):
    if os.name != "nt":
        raise RuntimeError("This test requires a Windows desktop")
    env = {key: value for key, value in os.environ.items()
           if not any(word in key for word in ("TOKEN", "API_KEY"))}
    # Chromium can hold a cache file open briefly after its top-level process has exited.
    with tempfile.TemporaryDirectory(prefix="tingsu-uia-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
            target = root / key.lower()
            target.mkdir()
            env[key] = str(target)
        env["HOME"] = env["USERPROFILE"]
        env["TINGSU_APP_DIR"] = directory
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--force-renderer-accessibility"
        # Windows PowerShell 5.1 reads UTF-8 scripts with a BOM.
        script = root / "verify.ps1"
        script.write_text(AUTOMATION, encoding="utf-8-sig")
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        title = "UIA meeting " + uuid.uuid4().hex[:12]
        for mode in ("create", "reopen"):
            proc = subprocess.Popen([str(app)], env=env, cwd=root)
            try:
                subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(script), "-Launcher", str(proc.pid), "-MeetingName", title,
                                "-Mode", mode], check=True, timeout=270)
                if proc.wait(timeout=35) != 0:
                    raise RuntimeError(f"Application exited with status {proc.returncode}")
            finally:
                if proc.poll() is None:
                    subprocess.run(["taskkill", "/pid", str(proc.pid), "/t", "/f"], timeout=20, check=False)
                    proc.wait(timeout=15)
    print("PASS Windows UI Automation created the named meeting, closed the app, and found it after relaunch.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    verify(parser.parse_args().app.resolve(strict=True))
