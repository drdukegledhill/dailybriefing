"""Command line (run through ./brief.sh).

  ./brief.sh run                      make today's briefing: fetch, edit with Claude, save the PDF (and print)
  ./brief.sh run --preview --model M  try a model on the last day's news without affecting the real issue
  ./brief.sh sources                  check every feed
  ./brief.sh printer                  check the printer (only needed if PRINT=true)
  ./brief.sh printer --test           print the latest briefing as a test
  ./brief.sh screen [--png FILE]      redraw the Inky pHAT status (or save it as a picture)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ai import edit_with_claude
from .config import Settings
from .editorial import build_request, no_ai_digest, tidy
from .fetch import fetch_all, load_sources
from .printing import ipp_status, print_ipp
from .render import render_fitted
from .screen import SAMPLE, StatusScreen, save_png, show_on_inky
from .store import Store

log = logging.getLogger("digest")


def run(settings: Settings, model: str | None, preview: bool, hours: int | None) -> int:
    settings.ensure_dirs()
    store = Store(settings.data_dir)
    day = datetime.now().strftime("%Y-%m-%d")
    model = model or settings.model
    screen = StatusScreen(
        settings, issue=store.issue_number(day), day=datetime.now().strftime("%a %-d %b %Y"),
        time=_clock(), preview=preview,
    )
    screen.show("working", "Fetching the news…")
    try:
        return _run(settings, store, screen, day, model, preview, hours)
    except Exception as exc:
        screen.status["time"] = _clock()
        screen.show("failed", f"Stopped: {exc}")
        raise


def _clock() -> str:
    return datetime.now().strftime("%H:%M")


def _run(settings: Settings, store: Store, screen: StatusScreen, day: str, model: str,
         preview: bool, hours: int | None) -> int:

    # 1. Gather -------------------------------------------------------------
    now = datetime.now(timezone.utc)
    if hours is None and preview:
        hours = 30
    if hours is not None:
        since = now - timedelta(hours=hours)
    else:
        last = store.last_success()
        since = last if last else now - timedelta(hours=settings.first_run_hours)
        since = max(since, now - timedelta(hours=settings.max_lookback_hours))
    log.info("Collecting items published since %s", since.isoformat(timespec="minutes"))

    report = fetch_all(load_sources(settings.sources_file), since=since)
    items = report.items if preview else store.unseen(report.items)
    items.sort(key=lambda i: i.published or now, reverse=True)
    items = items[: settings.max_items]
    for n, it in enumerate(items, 1):
        it.id = n
    log.info("%d new items (%d feeds ok, %d failed)", len(items), len(report.counts), len(report.errors))
    if not items:
        if not preview:
            store.record_run(True, 0, "no new items")
        print("NOTHING NEW: no new items since the last briefing.")
        screen.status["issue"] = None
        screen.step("edit", "off", "no new stories")
        screen.step("pdf", "off", "nothing to make")
        screen.step("print", "off", "nothing to print")
        screen.status["time"] = _clock()
        screen.show("nothing", f"No new stories since the last issue ({len(report.counts)} feeds checked)")
        return 0

    profile = settings.profile_file.read_text() if settings.profile_file.exists() else ""
    request = build_request(items, profile, store.recent_context(day))
    settings.today_dir.mkdir(parents=True, exist_ok=True)
    (settings.today_dir / "request.md").write_text(request)

    # 2. Edit ---------------------------------------------------------------
    try:
        digest = tidy(edit_with_claude(request, settings, model), items)
        if not digest["top_stories"] and not digest["sections"]:
            raise ValueError("the reply had no usable stories")
        editor, ok = _pretty(model), True
        screen.step("edit", "ok", editor)
    except Exception as exc:
        log.error("Editing with %s failed (%s); saving a plain headline list instead", model, exc)
        digest, editor, ok = no_ai_digest(items), "nobody (the AI step failed)", False
        screen.step("edit", "fail", "AI failed: headlines only")
    (settings.today_dir / "digest.json").write_text(json.dumps(digest, indent=2))

    # 3. Publish ------------------------------------------------------------
    issue = store.issue_number(day)
    stem = settings.archive_dir / (f"{day}-preview-{model}" if preview else day)
    pdf = stem.with_suffix(".pdf")
    html, pages = render_fitted(digest, items, report, settings, pdf, issue=issue, ai_name=editor)
    stem.with_suffix(".html").write_text(html)
    log.info("Made %s (%d pages)", pdf.name, pages)

    out_dir = Path(settings.output_dir).expanduser()
    name = f"{settings.title} – {datetime.now().strftime('%a %-d %b %Y')}"
    if preview:
        name += f" (preview, {model})"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, out_dir / f"{name}.pdf")
        print(f"SAVED: {out_dir / (name + '.pdf')}")
        screen.step("pdf", "ok", f"{pages} page{'s' if pages != 1 else ''} in {out_dir.name}")
    except OSError as exc:  # e.g. the shared folder isn't mounted; the archive copy is still there
        log.error("Couldn't save to %s (%s); the PDF is in %s", out_dir, exc, pdf.parent)
        screen.step("pdf", "fail", f"{out_dir.name} unavailable; kept in archive")

    stories = f"{len(items)} new stories · {len(digest['top_stories'])} top stories"
    if preview:
        screen.step("print", "off", "not printed (preview)")
        screen.status["time"] = _clock()
        screen.show("done" if ok else "failed", stories if ok else "AI step failed (preview)")
        return 0
    if settings.print_enabled:
        try:
            print_ipp(pdf, settings)
            print("PRINTED")
            screen.step("print", "ok", "sent to printer")
        except Exception as exc:  # the PDF is saved; don't fail the run
            log.error("Printing failed: %s", exc)
            screen.step("print", "fail", "printer didn't respond")
    else:
        screen.step("print", "off", "printing is off")
    if ok:
        store.save_digest(day, digest)
        store.mark_seen(items)
        store.record_run(True, len(items))
    else:
        # Leave the stories unseen so tomorrow's run can edit them properly.
        store.record_run(False, len(items), "AI step failed; headline list saved")
    print(f"PUBLISHED: issue No. {issue}, edited by {editor}.")
    screen.status["time"] = _clock()
    screen.show("done" if ok else "failed", stories if ok else "AI step failed; will retry these stories next run")
    return 0 if ok else 1


def _pretty(model: str) -> str:
    """claude-haiku-5-5 -> Claude Haiku 5.5"""
    parts = model.split("-")
    if len(parts) >= 3 and parts[0] == "claude":
        return f"Claude {parts[1].title()} {'.'.join(parts[2:])}"
    return model


def check_sources(settings: Settings) -> int:
    sources = load_sources(settings.sources_file)
    report = fetch_all(sources, since=datetime.now(timezone.utc) - timedelta(hours=48))
    for s in sources:
        if s.name in report.errors:
            print(f"  FAIL  {s.name}: {report.errors[s.name]}")
        else:
            print(f"  {report.counts.get(s.name, 0):>4}  {s.name}")
    print(f"\n{len(report.items)} items in the last 48h from {len(report.counts)} working feeds; "
          f"{len(report.errors)} failed.")
    return 0


def check_printer(settings: Settings, test: bool = False) -> int:
    attrs = ipp_status(settings.printer_uri)
    states = {3: "idle", 4: "printing", 5: "stopped"}
    print(f"Printer:  {attrs.get('printer-make-and-model', ['?'])[0]}")
    print(f"State:    {states.get(attrs.get('printer-state', [0])[0], '?')}")
    print(f"PDF:      {'yes' if 'application/pdf' in attrs.get('document-format-supported', []) else 'NO'}")
    for name, level in zip(attrs.get("marker-names", []), attrs.get("marker-levels", [])):
        print(f"Toner:    {name}: {level}%")
    print(f"Printing is {'ON' if settings.print_enabled else 'OFF'} (PRINT in .env).")
    if test:
        pdfs = sorted(settings.archive_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
        if not pdfs:
            print("No briefing to print yet: run ./brief.sh run --preview first.")
            return 1
        print_ipp(pdfs[-1], settings)
        print(f"PRINTED: {pdfs[-1].name} "
              f"({'double-sided' if settings.print_duplex else 'single-sided'}, {settings.print_colour})")
    return 0


def redraw_screen(settings: Settings, png: str | None, sample: bool) -> int:
    path = settings.today_dir / "screen.json"
    status = SAMPLE if sample or not path.exists() else json.loads(path.read_text())
    if png:
        save_png(status, Path(png))
        print(f"Saved {png}")
        return 0
    if not settings.inky:
        print("INKY isn't switched on in .env (INKY=true). Use --png FILE to see the screen as a picture.")
        return 1
    return 0 if show_on_inky(status, settings) else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("weasyprint", "fontTools", "httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="brief.sh", description="The Morning Brief")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="make today's briefing")
    r.add_argument("--model", help="Claude model to use (default: CLAUDE_MODEL in .env)")
    r.add_argument("--preview", action="store_true",
                   help="try it on the last 30 hours of news without printing or changing the real issue")
    r.add_argument("--hours", type=int, help="look back this many hours instead of since the last briefing")
    sub.add_parser("sources", help="check every feed")
    pr = sub.add_parser("printer", help="check the printer")
    pr.add_argument("--test", action="store_true", help="also print the latest briefing")
    sc = sub.add_parser("screen", help="redraw the Inky pHAT status screen")
    sc.add_argument("--png", help="save the screen as a picture instead of showing it")
    sc.add_argument("--sample", action="store_true", help="show a made-up status instead of the last run")
    args = p.parse_args(argv)

    settings = Settings()
    if args.cmd == "run":
        return run(settings, args.model, args.preview, args.hours)
    if args.cmd == "sources":
        return check_sources(settings)
    if args.cmd == "screen":
        return redraw_screen(settings, args.png, args.sample)
    return check_printer(settings, args.test)


if __name__ == "__main__":
    sys.exit(main())
