"""Fetch and normalise items from RSS/Atom feeds."""

from __future__ import annotations

import calendar
import hashlib
import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; morning-brief/1.0; RSS reader)"
SECTIONS = {
    "sector": "UK HE policy & sector",
    "engineering": "Engineering",
    "computing": "Computer science & AI",
    "games": "Games",
    "research": "Research & funding",
}


@dataclass
class Source:
    name: str
    url: str
    section: str
    max: int = 15
    include: str | None = None
    exclude: str | None = None


@dataclass
class Item:
    source: str
    section: str
    title: str
    link: str
    summary: str
    published: datetime | None
    id: int = 0  # position in today's list, assigned later

    @property
    def key(self) -> str:
        """Stable identity used to avoid showing the same story twice."""
        norm = re.sub(r"\W+", " ", self.title.lower()).strip()
        return hashlib.sha1(norm.encode()).hexdigest()


@dataclass
class FetchReport:
    items: list[Item] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def load_sources(path: Path) -> list[Source]:
    data = yaml.safe_load(path.read_text()) or {}
    out = []
    for raw in data.get("sources", []):
        if raw.get("section") not in SECTIONS:
            raise ValueError(f"Source {raw.get('name')!r} has unknown section {raw.get('section')!r}")
        out.append(Source(**raw))
    return out


def _clean(html: str, limit: int = 500) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def _fetch_one(src: Source) -> list[Item]:
    req = urllib.request.Request(src.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = resp.read()
    feed = feedparser.parse(body)
    if feed.bozo and not feed.entries:
        raise ValueError(f"not a valid feed ({feed.bozo_exception})")

    pattern = re.compile(src.include, re.I) if src.include else None
    reject = re.compile(src.exclude, re.I) if src.exclude else None
    items = []
    for e in feed.entries:
        title = _clean(e.get("title", ""), 300)
        if not title:
            continue
        summary = _clean(e.get("summary", "") or e.get("description", ""))
        # Google News puts the headline again in the summary; drop it.
        if summary.startswith(title[:40]):
            summary = ""
        if pattern and not pattern.search(f"{title} {summary}"):
            continue
        if reject and reject.search(title):
            continue
        source = src.name
        link = e.get("link", "")
        # Google News headlines end with " - Publisher": show the real publisher instead.
        if "news.google.com" in link and " - " in title:
            title, source = title.rsplit(" - ", 1)
        items.append(
            Item(
                source=source,
                section=src.section,
                title=title,
                link=link,
                summary=summary,
                published=_published(e),
            )
        )
    items.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


def fetch_all(sources: list[Source], since: datetime | None = None) -> FetchReport:
    report = FetchReport()

    def run(src: Source):
        try:
            return src, _fetch_one(src), None
        except Exception as exc:  # one broken feed must not stop the briefing
            return src, [], str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, sources))

    seen_keys: set[str] = set()
    for src, items, err in results:
        if err:
            log.warning("Feed failed: %s: %s", src.name, err)
            report.errors[src.name] = err
            continue
        kept = []
        for it in items:
            if since and it.published and it.published < since:
                continue
            if it.key in seen_keys:
                continue
            seen_keys.add(it.key)
            kept.append(it)
            if len(kept) >= src.max:
                break
        report.counts[src.name] = len(kept)
        report.items.extend(kept)
    return report
