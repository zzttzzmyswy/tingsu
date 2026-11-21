"""Run user-installed meeting agents with the host runtime and the user's CLI identity."""

import asyncio
import json
import os
from pathlib import Path, PureWindowsPath
import signal
import sys

from .agent_materials import AgentError
from .runtime import child_options

PACKAGES = {
    "codex": ("codex", "@openai/codex"),
    "claude": ("claude", "@anthropic-ai/claude-code"),
    "deepseek": ("dsh", "@deepseek-ai/dsh"),
}


def _remove_bundle_paths(env, root):
    bundle = PureWindowsPath(root)
    for name, value in list(env.items()):
        if name != "PATH" and not name.startswith(("QT", "QML")):
            continue
        pieces = value.split(";")
        keep = [p for p in pieces if not PureWindowsPath(p.strip('"')).is_relative_to(bundle)]
        if len(keep) != len(pieces):
            if keep:
                env[name] = ";".join(keep)
            else:
                del env[name]


def host_environment():
    env = os.environ.copy()
    if env.get("TINGSU_APPIMAGE_RUNTIME"):
        for name in env.get("TINGSU_HOST_VARIABLES", "").split():
            if "TINGSU_HOST_" + name in env:
                env[name] = env["TINGSU_HOST_" + name]
            else:
                env.pop(name, None)
        for name in ("APPIMAGE", "APPDIR", "ARGV0", "OWD"):
            env.pop(name, None)
    if os.name == "nt" and getattr(sys, "frozen", False):
        env["PATH"] = env.get("TINGSU_HOST_PATH", env.get("PATH", ""))
        _remove_bundle_paths(env, sys._MEIPASS)
    for name in tuple(env):
        if name.startswith(("TINGSU_", "_PYI_", "SILICONFLOW_", "SOPHNET_")) or name in (
            "_MEIPASS2",
            "QTWEBENGINE_REMOTE_DEBUGGING",
        ):
            del env[name]
    return env


def _node_versions(directory, suffix):
    # Version managers isolate globally installed CLIs by Node version.
    def version(path):
        return tuple(int(n) for n in path.name.lstrip("v").split(".") if n.isdigit())

    return [p / suffix for p in sorted(directory.glob("v[0-9]*"), key=version, reverse=True)]


def _search_dirs(env):
    home = Path.home()
    dirs = [Path(p) for p in env.get("PATH", "").split(os.pathsep) if p and Path(p).is_absolute()]
    dirs.append(home / ".local/bin")
    if prefix := env.get("NPM_CONFIG_PREFIX") or env.get("npm_config_prefix"):
        dirs.append(Path(prefix) if os.name == "nt" else Path(prefix) / "bin")
    if os.name == "nt":
        if roaming := env.get("APPDATA"):
            dirs.append(Path(roaming) / "npm")
        if programs := env.get("PROGRAMFILES") or env.get("ProgramFiles"):
            dirs.append(Path(programs) / "nodejs")
        if local := env.get("LOCALAPPDATA"):
            dirs.append(Path(local) / "Volta/bin")
        for variable in ("FNM_MULTISHELL_PATH", "NVM_SYMLINK"):
            if directory := env.get(variable):
                dirs.append(Path(directory))
    else:
        dirs.extend((home / ".claude/local", home / ".npm-global/bin", home / ".volta/bin", home / ".bun/bin"))
        if active := env.get("NVM_BIN"):
            dirs.append(Path(active))
        if active := env.get("FNM_MULTISHELL_PATH"):
            dirs.append(Path(active) / "bin")
        nvm = Path(env.get("NVM_DIR") or home / ".nvm")
        xdg = Path(env.get("XDG_DATA_HOME") or home / ".local/share")
        fnm = Path(env.get("FNM_DIR") or xdg / "fnm")
        dirs.append(fnm / "aliases/default/bin")
        dirs.extend(_node_versions(nvm / "versions/node", "bin"))
        dirs.extend(_node_versions(fnm / "node-versions", "installation/bin"))
    return list(dict.fromkeys(directory for directory in dirs if directory.is_absolute()))


def _windows_entry(path, provider, directories):
    if path.suffix.lower() == ".exe":
        return [str(path)]
    # npm generates shell shims beside the installed package. Launch its declared
    # bin with node.exe directly so messages and paths never pass through cmd.exe.
    command, package = PACKAGES[provider]
    if path.suffix.lower() not in (".cmd", ".ps1") or path.stem.lower() != command:
        raise AgentError("请选择此 CLI 的 .exe 或 npm 安装的同名 .cmd 启动文件。")
    folder = path.parent
    modules = (
        folder.parent
        if folder.name == ".bin" and folder.parent.name == "node_modules"
        else folder / "node_modules"
    )
    root = modules / package
    try:
        manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
        entry = manifest["bin"]
        if isinstance(entry, dict):
            entry = entry[command]
        script = (root / entry).resolve(strict=True)
        if not script.is_file() or not script.is_relative_to(root.resolve()):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AgentError(
            f"无法解析 {package} 的 npm 启动文件，请选择原生 .exe 或重新安装 CLI。"
        ) from exc
    if script.suffix.lower() == ".exe":
        return [str(script)]
    node = next(
        (
            folder / "node.exe"
            for folder in (path.parent, *directories)
            if (folder / "node.exe").is_file()
        ),
        None,
    )
    if not node:
        raise AgentError("找不到运行本地 CLI 所需的 Node.js，请将 node.exe 加入系统 PATH。")
    return [str(node), str(script)]


class LocalCLI:
    def __init__(self, provider, executable=""):
        self.provider = provider
        self.executable = executable

    def _command(self, env):
        command = PACKAGES[self.provider][0]
        directories = _search_dirs(env)
        if self.executable:
            path = Path(self.executable).expanduser()
            if not path.is_absolute():
                raise AgentError("本地 CLI 路径必须是可执行文件的绝对路径。")
        else:
            names = (command + ".exe", command + ".cmd") if os.name == "nt" else (command,)
            path = next(
                (
                    folder / name
                    for folder in directories
                    for name in names
                    if (folder / name).is_file() and os.access(folder / name, os.X_OK)
                ),
                None,
            )
        if path is None or not path.is_file() or not os.access(path, os.X_OK):
            raise AgentError(
                f"找不到可运行的 {command}。请先安装本地 CLI，或在「偏好设置 → AI 助手」指定路径。"
            )
        env["PATH"] = os.pathsep.join(map(str, dict.fromkeys([path.parent, *directories])))
        if os.name != "nt":
            return [str(path)]
        helper = (
            Path(sys._MEIPASS) / "bin"
            if getattr(sys, "frozen", False)
            else Path(__file__).parent / "native"
        ) / "tingsu-host.exe"
        if not helper.is_file():
            raise AgentError("找不到 Windows CLI 启动组件，请使用完整发行包或重新构建原生组件。")
        return [str(helper), str(os.getpid()), *_windows_entry(path, self.provider, directories)]

    def invocation(self, *args, env_overrides=None):
        env = host_environment()
        for name, value in (env_overrides or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return [*self._command(env), *args], env

    async def spawn(self, *args, cwd=None, env_overrides=None, **pipes):
        cmd, env = self.invocation(*args, env_overrides=env_overrides)
        return await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, start_new_session=os.name != "nt", **child_options(), **pipes
        )

    async def stop(self, proc):
        if proc is None:
            return
        if os.name != "nt":
            # The Node launcher can exit before its ACP worker.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.returncode is None:
            proc.kill()
        await proc.wait()
