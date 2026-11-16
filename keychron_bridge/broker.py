"""Event-driven, user-private local IPC. No network ports or status polling."""

import fcntl
import json
import os
import socket
import threading
from pathlib import Path


class Broker:
    def __init__(self, root: Path, dispatch):
        self.path = root / "control.sock"
        if len(os.fsencode(self.path)) > 103:
            raise ValueError("Application directory is too long for a local socket")
        self.lock = open(root / "bridge.lock", "a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("Keychron Agent Bridge is already running")
        self.path.unlink(missing_ok=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.path.chmod(0o600)
        self.server.listen(16)
        self.dispatch = dispatch
        self.stopping = False
        self.thread = threading.Thread(target=self._serve, name="bridge-events", daemon=True)

    def start(self):
        self.thread.start()

    def _serve(self):
        while not self.stopping:
            try:
                connection, _ = self.server.accept()
            except OSError:
                break
            with connection:
                connection.settimeout(0.3)
                try:
                    data = bytearray()
                    while b"\n" not in data and len(data) <= 16384:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        data.extend(chunk)
                    if len(data) > 16384:
                        continue
                    message = json.loads(bytes(data).split(b"\n", 1)[0])
                    if isinstance(message, dict):
                        self.dispatch(message)
                except (OSError, ValueError, TypeError):
                    continue

    def close(self):
        self.stopping = True
        try:
            self.server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.server.close()
        self.thread.join(timeout=1)
        self.path.unlink(missing_ok=True)
        self.lock.close()
