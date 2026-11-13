"""The editorial brief for Claude (rules, output schema) and checking what comes back."""

from __future__ import annotations

import json
import re
from datetime import datetime

from .fetch import SECTIONS, Item

SYSTEM = """You are the editor of a short morning briefing for one reader working in UK higher \
education. You receive today's raw news items (numbered) and a note about the reader. Pick what \
genuinely matters to them, summarise it accurately, and point out patterns across stories and days.

Rules:
- Only use facts that are in the items. Do not invent numbers, quotes, names or dates.
- Several items about the same story should be merged into one entry listing all their ids.
- Leave out items that are irrelevant to the reader (consumer games reviews, US-only politics, adverts, \
  job listings), even if that leaves a section short or empty.
- It is laid out on two A4 pages, so keep it tight: summaries are \
  1-3 sentences, plain British English. Anything that doesn't fit the PDF is cut automatically from the end of \
  each list, so put the most important entries first.
- "why_it_matters" speaks to the reader directly and is specific (e.g. what it could mean for \
  recruitment, curriculum, accreditation, funding or students), not generic.
- Trends must be backed by at least two items today, or by today's items plus the recent briefings \
  you are shown. Say clearly when something continues a pattern from earlier in the week.
- Never mention item numbers or ids in any text the reader sees; they are only for the item_ids fields.
- Summaries say what happened. Keep comments on relevance for "why_it_matters"; don't end summaries \
  with generic lines such as "This is useful for..." or "It is relevant to...".
- Each story appears once: a story in top_stories must not be repeated in a section.
- Limits: top_stories 3-5; each section at most 5 entries; trends 1-3; worth_reading at most 3."""

SYSTEM = re.sub(r" {2,}", " ", SYSTEM)  # tidy the line continuations above

_ENTRY = {
    "type": "object",
    "properties": {
        "item_ids": {"type": "array", "items": {"type": "integer"}},
        "title": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["item_ids", "title", "summary"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "lead": {
            "type": "string",
            "description": "One or two sentences capturing the shape of today's news.",
        },
        "top_stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_ids": {"type": "array", "items": {"type": "integer"}},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                },
                "required": ["item_ids", "title", "summary", "why_it_matters"],
                "additionalProperties": False,
            },
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": list(SECTIONS)},
                    "entries": {"type": "array", "items": _ENTRY},
                },
                "required": ["section", "entries"],
                "additionalProperties": False,
            },
        },
        "trends": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "item_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "explanation", "item_ids"],
                "additionalProperties": False,
            },
        },
        "worth_reading": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["item_id", "reason"],
                "additionalProperties": False,
            },
        },
        "one_thing": {
            "type": "string",
            "description": "One concrete, optional suggestion for the reader today, based on the news.",
        },
    },
    "required": ["lead", "top_stories", "sections", "trends", "worth_reading", "one_thing"],
    "additionalProperties": False,
}


def _format_items(items: list[Item]) -> str:
    lines = []
    for it in items:
        when = it.published.strftime("%a %d %b") if it.published else "date unknown"
        lines.append(f"[{it.id}] ({SECTIONS[it.section]} | {it.source} | {when}) {it.title}")
        if it.summary:
            lines.append(f"    {it.summary}")
    return "\n".join(lines)


def build_request(items: list[Item], profile: str, recent: str) -> str:
    """The full brief for a Claude scheduled task: instructions, reader, items and the output schema."""
    today = datetime.now().strftime("%A %d %B %Y")
    return f"""# Today's briefing request ({today})

## Your job
{SYSTEM}

## The reader
{profile}

## Recent briefings (for spotting patterns; don't repeat these stories unless there is news)
{recent or 'None yet: this is the first briefing.'}

## Today's items ({len(items)})
Each line is `[id] (section | source | date) headline`, with the feed's snippet indented below.

{_format_items(items)}

## Output
Write ONE JSON object (no Markdown fences, no comments) that matches this JSON Schema exactly.
Refer to items only by the numeric ids in square brackets above.
Valid section values: {", ".join(SECTIONS)}.

```json
{json.dumps(SCHEMA, indent=2)}
```
"""


def tidy(d: dict, items: list[Item]) -> dict:
    """Enforce the limits, fill anything missing and drop references to item ids that don't exist.

    Written defensively because small local models sometimes return odd shapes.
    """
    valid = {i.id for i in items}

    def ids(xs):
        return [x for x in (xs or []) if isinstance(x, int) and x in valid]

    def lst(key):
        v = d.get(key)
        return v if isinstance(v, list) else []

    out = {"lead": str(d.get("lead") or ""), "one_thing": str(d.get("one_thing") or "")}
    out["top_stories"] = [
        {"item_ids": ids(s.get("item_ids")), "title": s.get("title", ""), "summary": s.get("summary", ""),
         "why_it_matters": s.get("why_it_matters", "")}
        for s in lst("top_stories") if isinstance(s, dict) and s.get("title")
    ][:5]
    order = list(SECTIONS)
    merged: dict[str, list] = {}
    for s in lst("sections"):
        if not isinstance(s, dict) or s.get("section") not in SECTIONS:
            continue
        for e in s.get("entries") or []:
            if isinstance(e, dict) and e.get("title"):
                merged.setdefault(s["section"], []).append(
                    {"item_ids": ids(e.get("item_ids")), "title": e["title"], "summary": e.get("summary", "")}
                )
    out["sections"] = [{"section": k, "entries": merged[k][:5]} for k in order if merged.get(k)]
    out["trends"] = [
        {"title": t.get("title", ""), "explanation": t.get("explanation", ""), "item_ids": ids(t.get("item_ids"))}
        for t in lst("trends") if isinstance(t, dict) and t.get("title")
    ][:3]
    out["worth_reading"] = [
        {"item_id": w["item_id"], "reason": w.get("reason", "")}
        for w in lst("worth_reading") if isinstance(w, dict) and w.get("item_id") in valid
    ][:3]
    return out


def no_ai_digest(items: list[Item]) -> dict:
    """A plain headline list, used with --no-ai (for testing without an API key)."""
    by_section: dict[str, list[Item]] = {}
    for it in items:
        by_section.setdefault(it.section, []).append(it)
    return {
        "lead": f"{len(items)} new items from your sources. (AI summary switched off.)",
        "top_stories": [],
        "sections": [
            {
                "section": sec,
                "entries": [
                    {"item_ids": [i.id], "title": i.title, "summary": i.summary[:220]}
                    for i in by_section[sec][:6]
                ],
            }
            for sec in SECTIONS
            if sec in by_section
        ],
        "trends": [],
        "worth_reading": [],
        "one_thing": "",
    }
