"""Platform paths used by the desktop application and its child processes."""

import os
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
