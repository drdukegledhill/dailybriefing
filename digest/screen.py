"""Status screen for a Pimoroni Inky pHAT (2.13" e-ink) on the Pi.

Shows the issue number, date, when it was made, and whether each step worked:
edited by Claude, PDF saved, print sent. Turn it on with INKY=true in .env.

The screen is a nice-to-have: nothing here may stop the briefing, so every
error is logged and ignored. The last status is kept in data/today/screen.json,
and `./brief.sh screen` redraws it (or writes a PNG with --png, for testing
without the display).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import ROOT, Settings

log = logging.getLogger(__name__)

FONTS = ROOT / "templates" / "fonts"
WHITE, BLACK, ACCENT = 0, 1, 2  # palette indices, as the Inky library expects
PNG_ACCENT = {"red": (192, 32, 32), "yellow": (214, 170, 0), "black": (0, 0, 0)}

# Each step is one of: "ok", "fail", "off", "wait" (not reached yet), or None (hidden).
SAMPLE = {
    "state": "done", "issue": 2, "day": "Fri 13 Nov 2026", "time": "06:17", "preview": False,
    "steps": {
        "edit": ["ok", "Claude Sonnet 5.5"],
        "pdf": ["ok", "2 pages in briefings"],
        "print": ["off", "printing is off"],
    },
    "note": "132 new stories · 4 top stories",
}


class StatusScreen:
    """Keeps the run's status and redraws the e-ink screen when asked."""

    def __init__(self, settings: Settings, **status):
        self.settings = settings
        self.status = {"state": "working", "steps": {}, "note": "", **status}

    def step(self, name: str, result: str, text: str) -> None:
        self.status["steps"][name] = [result, text]

    def show(self, state: str | None = None, note: str | None = None) -> None:
        if state:
            self.status["state"] = state
        if note is not None:
            self.status["note"] = note
        try:
            path = self.settings.today_dir / "screen.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.status, indent=2))
        except OSError:
            pass
        if self.settings.inky:
            show_on_inky(self.status, self.settings)


def show_on_inky(status: dict, settings: Settings) -> bool:
    try:
        display = _inky(settings)
        colour = getattr(display, "colour", "black") or "black"
        img = draw(status, (display.width, display.height), colour)
        if settings.inky_rotate:
            img = img.rotate(settings.inky_rotate, expand=False)
        display.set_border(display.WHITE)
        display.set_image(img)
        display.show()
        return True
    except Exception as exc:  # never let the screen break the briefing
        log.warning("Couldn't update the Inky screen: %s", exc)
        return False


def _inky(settings: Settings):
    if settings.inky_colour == "auto":
        from inky.auto import auto  # reads the pHAT's ID chip

        return auto(ask_user=False, verbose=False)
    from inky import InkyPHAT

    return InkyPHAT(settings.inky_colour)


def save_png(status: dict, path: Path, size=(250, 122), colour="red", zoom=3) -> None:
    """Draw the screen to a PNG (enlarged), for checking the layout without the display."""
    img = draw(status, size, colour)
    img.putpalette([255, 255, 255, 0, 0, 0, *PNG_ACCENT.get(colour, (0, 0, 0))] + [0, 0, 0] * 253)
    img.convert("RGB").resize((img.width * zoom, img.height * zoom), Image.NEAREST).save(path)


# --- drawing ----------------------------------------------------------------

def _font(name: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"{name}.woff"), max(8, round(size)),
                              layout_engine=ImageFont.Layout.BASIC)  # even spacing without anti-aliasing


def _fit(d: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _icon(d: ImageDraw.ImageDraw, kind: str, x: int, y: int, size: int, accent: int) -> None:
    """A small tick, cross, dash or hollow dot, drawn as lines so it's crisp on e-ink."""
    s = size
    if kind == "ok":
        d.line([(x, y + s * 0.55), (x + s * 0.38, y + s * 0.9), (x + s, y + s * 0.1)], fill=BLACK, width=2)
    elif kind == "fail":
        d.line([(x + 1, y + 1), (x + s - 1, y + s - 1)], fill=accent, width=2)
        d.line([(x + 1, y + s - 1), (x + s - 1, y + 1)], fill=accent, width=2)
    elif kind == "off":
        d.line([(x + 1, y + s / 2), (x + s - 1, y + s / 2)], fill=BLACK, width=2)
    else:  # waiting
        d.ellipse([x + 2, y + 2, x + s - 2, y + s - 2], outline=BLACK, width=1)


def draw(status: dict, size=(250, 122), colour: str = "red") -> Image.Image:
    """The screen as a palette image: 0 white, 1 black, 2 red/yellow."""
    w, h = size
    k = h / 122  # scale for the older 212x104 pHAT
    accent = ACCENT if colour in ("red", "yellow") else BLACK
    img = Image.new("P", size, WHITE)
    img.putpalette([255, 255, 255, 0, 0, 0, 2, 2, 2] + [0, 0, 0] * 253)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"  # no anti-aliasing: e-ink has no greys
    pad = round(4 * k)

    nameplate = _font("PlayfairDisplay-900", 19 * k)
    label = _font("LibreFranklin-700", 11 * k)
    body = _font("LibreFranklin-500", 11 * k)
    small = _font("LibreFranklin-500", 10 * k)

    state = status.get("state", "working")
    failed = state == "failed"

    # Nameplate and issue number (white on black).
    issue = f"No. {status['issue']}" if status.get("issue") else ""
    box_w = 0
    if issue:
        box_w = round(d.textlength(issue, font=label)) + 2 * pad
        d.rectangle([w - box_w - pad, pad, w - pad, pad + round(17 * k)], fill=BLACK)
        d.text((w - box_w, pad + round(2 * k)), issue, font=label, fill=WHITE)
    d.text((pad, pad - round(4 * k)), _fit(d, "The Morning Brief", nameplate, w - box_w - 3 * pad),
           font=nameplate, fill=BLACK)

    # Dateline: date on the left, state and time on the right.
    y = round(25 * k)
    d.rectangle([pad, y, w - pad, y + 1], fill=BLACK)
    y += round(4 * k)
    left = status.get("day", "")
    if status.get("preview"):
        left = f"Preview · {left}"
    when = status.get("time", "")
    right, right_fill = {
        "working": (f"Started {when}", BLACK),
        "done": (f"Ready {when}", BLACK),
        "nothing": (f"Checked {when}", BLACK),
        "failed": (f"FAILED {when}", accent),
    }.get(state, (when, BLACK))
    rw = d.textlength(right, font=label)
    d.text((w - pad - rw, y), right, font=label, fill=right_fill)
    d.text((pad, y), _fit(d, left, body, w - 3 * pad - rw), font=body, fill=BLACK)
    y += round(15 * k)
    d.line([(pad, y), (w - pad, y)], fill=BLACK)

    # One row per step.
    y += round(4 * k)
    row = round(16 * k)
    icon = round(10 * k)
    col = pad + icon + round(6 * k)
    col2 = col + round(d.textlength("Edited ", font=label)) + round(6 * k)
    names = {"edit": "Edited", "pdf": "PDF", "print": "Print"}
    for key in ("edit", "pdf", "print"):
        result, text = (status.get("steps", {}).get(key) or ["wait", "waiting"])
        _icon(d, result, pad, y + round(2 * k), icon, accent)
        d.text((col, y), names[key], font=label, fill=BLACK)
        d.text((col2, y), _fit(d, text, body, w - pad - col2), font=body,
               fill=accent if result == "fail" else BLACK)
        y += row

    # Footer: a count of stories, or what went wrong.
    y += round(2 * k)
    d.line([(pad, y), (w - pad, y)], fill=BLACK)
    note = status.get("note", "")
    if note:
        d.text((pad, y + round(3 * k)), _fit(d, note, small, w - 2 * pad), font=small,
               fill=accent if failed else BLACK)
    return img
