"""Memory and activity log. Plain files you can read, edit, or delete."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path


class Memory:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # parallel tasks write memory at the same time
        self.path = state_dir / "memory.json"
        self.log_path = state_dir / "activity.jsonl"
        self._stamp: tuple[int, int] | None = None
        self.records: list[dict] = []
        self._load()

    def _stat(self) -> tuple[int, int] | None:
        """(mtime, size) of the file: size catches two edits in the same clock tick."""
        if not self.path.exists():
            return None
        s = self.path.stat()
        return (s.st_mtime_ns, s.st_size)

    def _load(self) -> None:
        self.records = json.loads(self.path.read_text("utf-8")) if self.path.exists() else []
        self._stamp = self._stat()

    def _fresh(self) -> None:
        """Pick up an edit made by another process (for example scripts/reset_demo_calendar.py clearing decisions while
        the server is running). Only reloads when the file changed since this process last read or wrote it."""
        try:
            stamp = self._stat()
        except OSError:
            return
        if stamp != self._stamp:
            try:
                self._load()
            except (OSError, ValueError):
                pass  # half-written file: keep what we have, try again next time

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.records, indent=2), encoding="utf-8")
        self._stamp = self._stat()

    def record(self, *, key: str, skill: str, decision: str, summary: str) -> None:
        with self._lock:
            self._fresh()
            self.records.append(
                {"key": key, "skill": skill, "decision": decision, "summary": summary, "at": datetime.now().isoformat(timespec="seconds")}
            )
            self._save()

    def decided(self, key: str) -> bool:
        with self._lock:
            self._fresh()
            return any(r["key"] == key for r in self.records)

    def decisions(self, skill: str | None = None) -> list[dict]:
        with self._lock:
            self._fresh()
            return [r for r in self.records if skill is None or r["skill"] == skill]

    def forget(self, key: str) -> int:
        with self._lock:
            self._fresh()
            before = len(self.records)
            self.records = [r for r in self.records if r["key"] != key]
            self._save()
            return before - len(self.records)

    def log(self, event: str, **fields) -> None:
        line = {"at": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        with self._lock, self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
