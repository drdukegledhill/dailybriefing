"""Optional: send the PDF straight to a network printer over IPP.

Works with almost any network printer made in the last ten years (AirPrint, IPP Everywhere
or Mopria), with no drivers or CUPS. Set PRINT=true and PRINTER_URI in .env.
"""

from __future__ import annotations

import logging
import ssl
import struct
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings

log = logging.getLogger(__name__)

# IPP value tags
_TAG_OPERATION, _TAG_JOB, _TAG_END = 0x01, 0x02, 0x03
_INTEGER, _BOOLEAN, _ENUM = 0x21, 0x22, 0x23
_TEXT, _NAME, _KEYWORD, _URI, _CHARSET, _LANG, _MIME = 0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49

_PRINT_JOB, _GET_PRINTER_ATTRIBUTES = 0x0002, 0x000B


def _attr(tag: int, name: str, value) -> bytes:
    if isinstance(value, int):
        raw = struct.pack(">i", value)
    else:
        raw = value.encode()
    n = name.encode()
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(raw)) + raw


def _http_url(uri: str) -> str:
    u = urlparse(uri)
    scheme = {"ipp": "http", "ipps": "https"}.get(u.scheme, u.scheme)
    port = u.port or 631
    return f"{scheme}://{u.hostname}:{port}{u.path or '/ipp/print'}"


def _ipp_request(uri: str, operation: int, groups: bytes, data: bytes = b"") -> tuple[int, bytes]:
    header = struct.pack(">BBHI", 2, 0, operation, 1)
    op_attrs = (
        bytes([_TAG_OPERATION])
        + _attr(_CHARSET, "attributes-charset", "utf-8")
        + _attr(_LANG, "attributes-natural-language", "en-gb")
        + _attr(_URI, "printer-uri", uri)
    )
    body = header + op_attrs + groups + bytes([_TAG_END]) + data
    req = urllib.request.Request(
        _http_url(uri), data=body, method="POST", headers={"Content-Type": "application/ipp"}
    )
    ctx = ssl._create_unverified_context()  # printers use self-signed certificates
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        payload = resp.read()
    status = struct.unpack(">H", payload[2:4])[0]
    return status, payload


def _parse_attrs(payload: bytes) -> dict[str, list]:
    """Minimal IPP response parser: returns {name: [values]}."""
    out: dict[str, list] = {}
    pos, name = 8, None
    while pos < len(payload):
        tag = payload[pos]
        pos += 1
        if tag == _TAG_END:
            break
        if tag < 0x10:  # group delimiter
            continue
        nlen = struct.unpack(">H", payload[pos : pos + 2])[0]
        pos += 2
        if nlen:
            name = payload[pos : pos + nlen].decode(errors="replace")
        pos += nlen
        vlen = struct.unpack(">H", payload[pos : pos + 2])[0]
        pos += 2
        raw = payload[pos : pos + vlen]
        pos += vlen
        if tag in (_INTEGER, _ENUM) and vlen == 4:
            val = struct.unpack(">i", raw)[0]
        elif tag == _BOOLEAN:
            val = bool(raw[0]) if raw else False
        elif tag in (0x35, 0x36) and vlen >= 4:  # textWithLanguage / nameWithLanguage
            llen = struct.unpack(">H", raw[:2])[0]
            val = raw[4 + llen :].decode(errors="replace")
        else:
            val = raw.decode(errors="replace")
        out.setdefault(name, []).append(val)
    return out


def ipp_status(uri: str) -> dict[str, list]:
    wanted = ["printer-make-and-model", "printer-state", "printer-state-reasons",
              "document-format-supported", "sides-supported", "print-color-mode-supported",
              "marker-names", "marker-levels"]
    groups = b"".join(
        _attr(_KEYWORD, "requested-attributes" if i == 0 else "", w) for i, w in enumerate(wanted)
    )
    status, payload = _ipp_request(uri, _GET_PRINTER_ATTRIBUTES, groups)
    if status >= 0x0100:
        raise RuntimeError(f"Printer returned IPP status 0x{status:04x}")
    return _parse_attrs(payload)


def print_ipp(pdf: Path, settings: Settings) -> None:
    if not settings.printer_uri:
        raise RuntimeError("PRINTER_URI is not set (e.g. ipp://192.168.1.50:631/ipp/print)")
    ops = _attr(_NAME, "requesting-user-name", "morning-brief") + _attr(
        _NAME, "job-name", pdf.stem
    ) + _attr(_MIME, "document-format", "application/pdf")
    job = bytes([_TAG_JOB])
    if settings.print_duplex:
        job += _attr(_KEYWORD, "sides", "two-sided-long-edge")
    if settings.print_colour in ("monochrome", "color"):
        job += _attr(_KEYWORD, "print-color-mode", settings.print_colour)
    job += _attr(_KEYWORD, "media", "iso_a4_210x297mm")
    status, payload = _ipp_request(settings.printer_uri, _PRINT_JOB, ops + job, pdf.read_bytes())
    if status >= 0x0100:
        msg = _parse_attrs(payload).get("status-message", [""])[0]
        raise RuntimeError(f"Printer rejected the job: IPP status 0x{status:04x} {msg}")
    job_id = _parse_attrs(payload).get("job-id", ["?"])[0]
    log.info("Sent to printer over IPP (job %s)", job_id)
