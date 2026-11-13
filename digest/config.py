"""Settings, read from .env in the project folder (see .env.example)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env (if present) without overriding real environment variables."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ('"', "'") and value[0] in value[1:]:
            value = value[1 : value.index(value[0], 1)]  # quoted: keep everything inside the quotes
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # drop "   # comment"
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").strip().lower()
    return default if not val else val in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    val = os.environ.get(name, "").strip()
    return int(val) if val else default


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    config_dir: Path = field(default_factory=lambda: Path(_env("CONFIG_DIR", str(ROOT / "config"))))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "data"))))
    # Where to save each briefing for reading.
    output_dir: str = field(default_factory=lambda: _env("OUTPUT_DIR", str(ROOT / "briefings")))

    # How far back to look: normally "since the last briefing", capped here (catches up after time away).
    first_run_hours: int = field(default_factory=lambda: _int("FIRST_RUN_HOURS", 36))
    max_lookback_hours: int = field(default_factory=lambda: _int("MAX_LOOKBACK_HOURS", 168))
    max_items: int = field(default_factory=lambda: _int("MAX_ITEMS", 250))

    # PDF: hard page limit; lower-priority items are trimmed to fit.
    max_pages: int = field(default_factory=lambda: _int("MAX_PAGES", 2))
    title: str = field(default_factory=lambda: _env("DIGEST_TITLE", "The Morning Brief"))
    subtitle: str = field(
        default_factory=lambda: _env("DIGEST_SUBTITLE", "Engineering · Computing · Games in UK Higher Education")
    )

    # AI editor (needs ANTHROPIC_API_KEY in .env). Sonnet 5.5 costs about 5p a day; Haiku 5.5 under 1p.
    model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-sonnet-5-5"))
    effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "medium"))

    # Optional printing straight to a network printer.
    print_enabled: bool = field(default_factory=lambda: _bool("PRINT", False))
    printer_uri: str = field(default_factory=lambda: _env("PRINTER_URI", ""))
    print_duplex: bool = field(default_factory=lambda: _bool("PRINT_DUPLEX", True))
    print_colour: str = field(default_factory=lambda: _env("PRINT_COLOUR", "color").lower())

    # Optional Inky pHAT status screen on the Pi.
    inky: bool = field(default_factory=lambda: _bool("INKY", False))
    inky_colour: str = field(default_factory=lambda: _env("INKY_COLOUR", "auto").lower())
    inky_rotate: int = field(default_factory=lambda: _int("INKY_ROTATE", 0))

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def today_dir(self) -> Path:
        return self.data_dir / "today"

    @property
    def sources_file(self) -> Path:
        return self.config_dir / "sources.yaml"

    @property
    def profile_file(self) -> Path:
        return self.config_dir / "profile.md"

    def ensure_dirs(self) -> None:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
