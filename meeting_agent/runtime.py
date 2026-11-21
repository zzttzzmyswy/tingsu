"""Platform paths used by the desktop application and its child processes."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def app_directory(area: str) -> Path:
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return local / "Tingsu" / area
    defaults = {"data": ".local/share", "config": ".config", "cache": ".cache"}
    return Path(os.getenv(f"XDG_{area.upper()}_HOME") or Path.home() / defaults[area]) / "tingsu"


def media_directory() -> Path:
    if location := os.environ.get("TINGSU_APP_DIR"):
        return Path(location) / "videos"
    parent = (
        Path(sys.executable).parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    return parent / "videos"


def child_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def codex_binary() -> str | None:
    # Prefer a native executable on Windows, including npm's platform package.
    if os.name == "nt":
        native = shutil.which("codex.exe")
        if native:
            return native
        shim = shutil.which("codex.cmd")
        if shim:
            tree = Path(shim).parent / "node_modules/@openai"
            return next((str(p) for p in tree.glob("codex*/**/codex.exe")), None)
        return None
    return shutil.which("codex")
