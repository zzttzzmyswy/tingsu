import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from meeting_agent.agent_materials import AgentError
from meeting_agent.local_cli import LocalCLI, _remove_bundle_paths, _search_dirs, _windows_entry, host_environment


class HostProcessTests(unittest.TestCase):
    def test_appimage_restores_missing_and_user_supplied_values(self):
        env = {
            "PATH": "/bundle/bin",
            "LD_LIBRARY_PATH": "/bundle/lib",
            "PYTHONHOME": "/bundle",
            "TINGSU_HOST_VARIABLES": "PATH LD_LIBRARY_PATH PYTHONHOME",
            "TINGSU_HOST_PATH": "/host/bin",
            "TINGSU_HOST_LD_LIBRARY_PATH": "",
            "TINGSU_APPIMAGE_RUNTIME": "1",
            "SOPHNET_API_KEY": "fixture",
            "HOME": "/user",
            "DEEPSEEK_API_KEY": "agent-test-identity",
            "APPDIR": "/bundle",
        }
        with patch.dict(os.environ, env, clear=True):
            actual = host_environment()
        self.assertEqual(
            actual,
            {
                "PATH": "/host/bin",
                "LD_LIBRARY_PATH": "",
                "HOME": "/user",
                "DEEPSEEK_API_KEY": "agent-test-identity",
            },
        )

    def test_qt_freezer_paths_do_not_reach_windows_children(self):
        env = {
            "PATH": r'C:\Temp\_MEI;D:\tools;C:\Temp\_MEI-other;"c:\TEMP\_mei\bin"',
            "QML2_IMPORT_PATH": r"C:\Temp\_MEI\PySide6\qml",
            "QT_PLUGIN_PATH": r"C:\Temp\_MEI\qt;D:\QT",
            "OTHER": r"C:\Temp\_MEI",
        }
        _remove_bundle_paths(env, r"C:\Temp\_MEI")
        self.assertEqual(
            env,
            {
                "PATH": r"D:\tools;C:\Temp\_MEI-other",
                "QT_PLUGIN_PATH": r"D:\QT",
                "OTHER": r"C:\Temp\_MEI",
            },
        )

    def test_windows_npm_shim_resolves_manifest_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "npm prefix"
            pkg = base / "node_modules/@deepseek-ai/dsh"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text(json.dumps({"bin": {"dsh": "./start.mjs"}}))
            script = pkg / "start.mjs"
            script.touch()
            node = base / "node.exe"
            node.touch()
            for shim in (base / "dsh.cmd", base / "node_modules/.bin/dsh.cmd"):
                self.assertEqual(_windows_entry(shim, "deepseek", [base]), [str(node), str(script)])

    def test_windows_codex_shim_uses_its_own_package_and_node(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "User npm"
            pkg = base / "node_modules/@openai/codex"
            (pkg / "bin").mkdir(parents=True)
            script = pkg / "bin/codex.js"
            script.touch()
            (pkg / "package.json").write_text(json.dumps({"bin": {"codex": "./bin/codex.js"}}))
            node = base / "node.exe"
            node.touch()
            self.assertEqual(_windows_entry(base / "codex.cmd", "codex", []), [str(node), str(script)])

    @unittest.skipUnless(os.name == "nt", "Windows environment keys")
    def test_node_found_in_program_files_when_desktop_path_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            npm = root / "npm"
            package = npm / "node_modules/@openai/codex"
            package.mkdir(parents=True)
            script = package / "start.js"
            script.touch()
            (package / "package.json").write_text(json.dumps({"bin": {"codex": "start.js"}}))
            programs = root / "Program Files"
            node = programs / "nodejs/node.exe"
            node.parent.mkdir(parents=True)
            node.touch()
            for name in ("PROGRAMFILES", "ProgramFiles"):
                directories = _search_dirs({name: str(programs), "PATH": ""})
                self.assertEqual(_windows_entry(npm / "codex.cmd", "codex", directories),
                                 [str(node), str(script)])

    @unittest.skipIf(os.name == "nt", "Linux desktop install paths")
    def test_desktop_discovers_user_node_managers_and_old_claude_installer(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory, "PATH": ""}, clear=True):
            home = Path(directory)
            for version in ("v9.0.1", "v24.2.0"):
                node_bin = home / ".nvm/versions/node" / version / "bin"
                node_bin.mkdir(parents=True)
                (node_bin / "codex").symlink_to(sys.executable)
            command, env = LocalCLI("codex").invocation("app-server")
            self.assertEqual(command, [str(node_bin / "codex"), "app-server"])
            self.assertEqual(Path(env["PATH"].split(os.pathsep)[0]), node_bin)
            for name, prefix, binary in (("deepseek", ".local/share/fnm/node-versions/v22.4.0/installation/bin", "dsh"),
                                         ("claude", ".claude/local", "claude")):
                path = home / prefix / binary
                path.parent.mkdir(parents=True)
                path.symlink_to(sys.executable)
                self.assertEqual(LocalCLI(name).invocation("--version")[0], [str(path), "--version"])

    @unittest.skipIf(os.name == "nt", "POSIX host execution")
    def test_custom_cli_path_and_arguments_are_passed_literally(self):
        async def verify():
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "test cli"
                path.symlink_to(sys.executable)
                cli = LocalCLI("deepseek", str(path))
                arg = 'a b; & "x" $(echo test)'
                proc = await cli.spawn(
                    "-c",
                    "import sys; print(sys.argv[1])",
                    arg,
                    stdout=asyncio.subprocess.PIPE,
                    cwd=directory,
                )
                output, _ = await proc.communicate()
                self.assertEqual(output.decode().strip(), arg)
                self.assertEqual(proc.returncode, 0)
                with self.assertRaises(AgentError):
                    await LocalCLI("claude", "relative/path").spawn("--version")

        asyncio.run(verify())
