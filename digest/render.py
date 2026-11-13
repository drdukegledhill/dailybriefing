"""Render the briefing to HTML and PDF."""

from __future__ import annotations

import base64
import io
from functools import lru_cache
from datetime import datetime
from pathlib import Path

import segno
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, Settings
from .fetch import SECTIONS, FetchReport, Item

TEMPLATES = ROOT / "templates"


def _qr_svg(url: str) -> str:
    if not url:
        return ""
    buf = io.BytesIO()
    segno.make(url, error="l").save(buf, kind="svg", scale=2, border=0, xmldecl=False, svgns=True)
    return buf.getvalue().decode()


@lru_cache(maxsize=None)
def _font(name: str) -> str:
    """Embed a font as a data URI so archived HTML files look right wherever they're opened."""
    data = base64.b64encode((TEMPLATES / "fonts" / name).read_bytes()).decode()
    return f"data:font/woff;base64,{data}"


def _logo() -> str:
    """Your logo (templates/brand/logo.png) as a data URI, or "" if there isn't one."""
    path = TEMPLATES / "brand" / "logo.png"
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def build_html(digest: dict, items: list[Item], report: FetchReport, settings: Settings,
               now: datetime | None = None, issue: int | None = None, ai_name: str = "AI") -> str:
    now = now or datetime.now()
    by_id = {i.id: i for i in items}

    def refs(ids):
        return [by_id[i] for i in ids if i in by_id]

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    env.filters["qr"] = _qr_svg
    env.globals["font"] = _font
    env.globals["logo"] = _logo
    tmpl = env.get_template("digest.html.j2")
    return tmpl.render(
        d=digest,
        refs=refs,
        item=by_id.get,
        sections=SECTIONS,
        settings=settings,
        date=now.strftime("%A %-d %B %Y"),
        generated=now.strftime("%H:%M"),
        total_items=len(items),
        source_count=len(report.counts),
        errors=report.errors,
        issue=issue,
        ai_name=ai_name,
    )


# Ways to shorten the briefing, least valuable content first. Each step is applied
# on top of the previous ones until the PDF fits in max_pages.
def _limit(key, n):
    def apply(d):
        d[key] = d[key][:n]
    return apply


def _section_entries(n):
    def apply(d):
        for s in d["sections"]:
            s["entries"] = s["entries"][:n]
    return apply


# Ways to shorten each page, least valuable content first.
FRONT_STEPS = [_limit("trends", 2), _limit("top_stories", 4), _limit("top_stories", 3), _limit("trends", 1)]
INSIDE_STEPS = [
    _section_entries(4),
    _limit("worth_reading", 2),
    _section_entries(3),
    _section_entries(2),
    _limit("worth_reading", 0),
    _section_entries(1),
]


def render_fitted(digest: dict, items: list[Item], report: FetchReport, settings: Settings,
                  pdf_path: Path, **extra) -> tuple[str, int]:
    """Render to PDF, trimming content until it fits on settings.max_pages pages.

    Returns the final HTML and page count. The digest dict is trimmed in place,
    so the saved JSON matches what was printed.
    """
    import logging

    from weasyprint import HTML

    log = logging.getLogger(__name__)
    front, inside = iter(FRONT_STEPS), iter(INSIDE_STEPS)
    while True:
        html = build_html(digest, items, report, settings, **extra)
        doc = HTML(string=html, base_url=str(TEMPLATES)).render()
        pages = len(doc.pages)
        if pages <= settings.max_pages:
            break
        # The inside page starts on a new page; if it starts later than page 2,
        # the front page overflowed, so trim the front first.
        inside_starts = next((n for n, p in enumerate(doc.pages) if "inside" in p.anchors), 1)
        step = next(front, None) if inside_starts > 1 else None
        step = step or next(inside, None) or next(front, None)
        if step is None:
            log.warning("Still %d pages after trimming everything optional; saving anyway.", pages)
            break
        log.info("Briefing is %d pages; trimming to fit %d.", pages, settings.max_pages)
        step(digest)
    doc.write_pdf(pdf_path)
    return html, pages
