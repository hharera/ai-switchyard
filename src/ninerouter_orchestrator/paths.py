"""Platform-native storage with a non-destructive fallback for existing installations."""

import os
import sys
from pathlib import Path


def user_data_root(home: Path | None = None) -> Path:
    home = home or Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
    if sys.platform == "darwin":
        return home / "Library/Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))


def data_dir(home: Path | None = None) -> Path:
    home = home or Path.home()
    override = os.environ.get("SWITCHYARD_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("SWITCHYARD_DATA_DIR must be an absolute directory path")
        return path
    legacy = home / ".local/share/9router-orchestrator"
    # Reuse the entire old directory rather than splitting configuration across locations.
    if legacy.is_dir():
        return legacy
    name = "Switchyard" if sys.platform in {"win32", "darwin"} else "9router-orchestrator"
    return user_data_root(home) / name
