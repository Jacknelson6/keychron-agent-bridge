"""Private, durable application files."""

import json
import os
import tempfile
from pathlib import Path


def data_dir() -> Path:
    root = Path(os.environ.get("KEYCHRON_BRIDGE_HOME", str(
        Path.home() / "Library/Application Support/Keychron Agent Bridge")))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def socket_path() -> Path:
    return data_dir() / "control.sock"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".bridge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback
