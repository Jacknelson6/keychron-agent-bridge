"""Group Desktop workers using local session identity and parent metadata only."""

import json
import re
import uuid
from pathlib import Path


def valid_uuid(value):
    try:
        uuid.UUID(value)
        return isinstance(value, str)
    except (ValueError, TypeError, AttributeError):
        return False


def prefix_string(text, key):
    match = re.search(r'(?<!\\)"' + re.escape(key) + r'"\s*:\s*', text)
    if match:
        try:
            value, _ = json.JSONDecoder().raw_decode(text, match.end())
            return value if isinstance(value, str) else None
        except ValueError:
            pass
    return None


class DesktopSessions:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else (
            Path.home() / "Library/Application Support/Claude/claude-code-sessions")
        self.aliases = {}
        self.paths = {}
        self.primary = {}
        self.parents = {}
        self.missing = set()

    def note_start(self, session_id):
        # A Desktop metadata file may be written after the first startup hook.
        self.missing.discard(session_id)
        desktop = self.aliases.get(session_id)
        if desktop in self.parents and self.parents[desktop] is None:
            self.parents.pop(desktop)

    def scan(self):
        # Only small identity headers are read across the index. Full metadata is
        # decoded for matched live sessions, never conversation JSONL files.
        try:
            files = self.root.glob("*/*/local_*.json")
            for index, path in enumerate(files):
                if index >= 4096:
                    break
                if path.is_symlink():
                    continue
                try:
                    with path.open() as stream:
                        prefix = stream.read(4096)
                    cli = prefix_string(prefix, "cliSessionId")
                    desktop = prefix_string(prefix, "sessionId")
                    if valid_uuid(cli) and desktop == path.stem and valid_uuid(desktop[6:]):
                        self.aliases[cli] = desktop
                        self.paths[desktop] = path
                        self.primary[desktop] = cli
                except (OSError, UnicodeError):
                    continue
        except OSError:
            pass

    def identity(self, session_id):
        if not valid_uuid(session_id):
            return session_id
        if session_id not in self.aliases and session_id not in self.missing:
            self.scan()
            if session_id not in self.aliases:
                self.missing.add(session_id)
        return self.aliases.get(session_id, session_id)

    def parent(self, desktop):
        if desktop in self.parents:
            return self.parents[desktop]
        path = self.paths.get(desktop)
        if path is None:
            return None
        try:
            if path.stat().st_size > 8_000_000:
                self.parents[desktop] = None
                return None
            document = json.loads(path.read_text())
            if not isinstance(document, dict) or document.get("sessionId") != desktop:
                self.parents[desktop] = None
                return None
            current = document.get("cliSessionId")
            if valid_uuid(current):
                self.primary[desktop] = current
                self.aliases[current] = desktop
            # A renamed/restarted CLI can keep the same Desktop chat identity.
            previous = document.get("lastAssistantCliSessionId")
            if valid_uuid(previous):
                self.aliases.setdefault(previous, desktop)
            spawn = document.get("spawnedFrom")
            parent = spawn.get("sessionId") if isinstance(spawn, dict) and spawn.get("sideSession") is True else None
            if not isinstance(parent, str) or not parent.startswith("local_") or not valid_uuid(parent[6:]):
                parent = None
            self.parents[desktop] = parent
            return parent
        except (OSError, ValueError, UnicodeError):
            self.parents[desktop] = None
            return None

    def ancestors(self, desktop):
        seen, result = {desktop}, set()
        for _ in range(32):
            parent = self.parent(desktop)
            if parent is None:
                return result
            if parent in seen:
                return set()  # Corrupt relationships must not hide real sessions.
            seen.add(parent)
            result.add(parent)
            desktop = parent
        return set()

    def count(self, session_ids, known_inactive=()):
        session_ids = list(session_ids)
        identities = {self.identity(session_id) for session_id in session_ids}
        for desktop in identities:
            self.parent(desktop)  # Learn previous CLI identities before deduplicating.
        identities = {self.identity(session_id) for session_id in session_ids}
        inactive = set(known_inactive)
        identities = {desktop for desktop in identities if self.primary.get(desktop) not in inactive}
        ancestors = set()
        for desktop in identities:
            ancestors.update(self.ancestors(desktop))
        # Children remain separate indicators; their coordinator is not an extra worker.
        return len(identities - ancestors)
