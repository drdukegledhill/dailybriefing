"""Small SQLite store: which stories were already printed, and past briefings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fetch import Item


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "digest.db")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, first_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                run_at TEXT NOT NULL, ok INTEGER NOT NULL, items INTEGER, note TEXT
            );
            CREATE TABLE IF NOT EXISTS digests (day TEXT PRIMARY KEY, json TEXT NOT NULL);
            """
        )

    # --- seen stories -------------------------------------------------------
    def unseen(self, items: list[Item]) -> list[Item]:
        keys = {row[0] for row in self.db.execute("SELECT key FROM seen")}
        return [i for i in items if i.key not in keys]

    def mark_seen(self, items: list[Item]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.executemany(
            "INSERT OR IGNORE INTO seen (key, first_seen) VALUES (?, ?)", [(i.key, now) for i in items]
        )
        # Forget very old keys so the table doesn't grow forever.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        self.db.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
        self.db.commit()

    # --- runs ---------------------------------------------------------------
    def last_success(self) -> datetime | None:
        row = self.db.execute("SELECT MAX(run_at) FROM runs WHERE ok = 1").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def record_run(self, ok: bool, items: int, note: str = "") -> None:
        self.db.execute(
            "INSERT INTO runs (run_at, ok, items, note) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), int(ok), items, note),
        )
        self.db.commit()

    # --- past briefings (gives the AI a memory for spotting patterns) -------
    def save_digest(self, day: str, digest: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO digests (day, json) VALUES (?, ?)", (day, json.dumps(digest))
        )
        self.db.commit()

    def issue_number(self, day: str) -> int:
        """Issue number for the paper printed on `day` (1 for the first one)."""
        row = self.db.execute("SELECT COUNT(*) FROM digests WHERE day < ?", (day,)).fetchone()
        return row[0] + 1

    def recent_context(self, before_day: str, days: int = 14) -> str:
        rows = self.db.execute(
            "SELECT day, json FROM digests WHERE day < ? ORDER BY day DESC LIMIT ?", (before_day, days)
        ).fetchall()
        lines = []
        for day, raw in reversed(rows):
            d = json.loads(raw)
            lines.append(f"{day}: {d.get('lead', '')}")
            for s in d.get("top_stories", []):
                lines.append(f"  - top story: {s.get('title', '')}")
            for sec in d.get("sections", []):
                for e in sec.get("entries", []):
                    lines.append(f"  - in brief: {e.get('title', '')}")
            for t in d.get("trends", []):
                lines.append(f"  - trend: {t.get('title', '')}")
        return "\n".join(lines)
