#!/usr/bin/env python3
"""Belt-and-braces PII pass over the WHOLE tracked tree, with the API's own redaction rules.

`pii_check.py` guards the data overlays with narrow regexes and an allowlist. This script reuses
`api/app/redact.py` (the rules the API applies at serve time) over every tracked text file — code,
tests, docs, YAML — because the public tree must not carry a DNI/NIE, phone, WhatsApp id, IBAN, IMEI
or cadastral reference of a natural person anywhere, fixtures included (05-10-2026: the first public
tree shipped real ones inside `api/tests/test_redact.py`).

    python3 pipeline/scripts/pii_tree_scan.py          # tracked tree; exit 1 on any high-severity hit
    python3 pipeline/scripts/pii_tree_scan.py --all    # also list addresses and plates (manual review)
    python3 pipeline/scripts/pii_tree_scan.py FILE...  # only these files

High severity: DNI, NIE, IBAN, bank account (CCC), IMEI, phone / WhatsApp id, cadastral reference, personal e-mail
address. Low: street addresses and plates (company seats, court addresses and synthetic examples are frequent there;
reviewed by hand). The e-mail rule is this scan's own (the API serves no e-mail to mask) and also reads an address
spelled out ("usuario_ficticio arroba gmail punto com") or flattened into words ("usuario_ficticio gmail com"), the
way a generated key carries it. Written with an @, any domain counts except the project's own, the RFC example
domains and WhatsApp JIDs.
The serve-time rules only match compact, upper-case identifiers, so each line with enough digits is also
scanned, with the high-severity rules, in normalised variants: letters stuck to digits upper-cased
("12345678z", "es91…"), then dots, dashes or spaces of any width removed between digits, one kind of
separator per variant ("612-34-56-78", "612.345.678", an IBAN grouped with U+00A0). A hit that only a
variant finds must carry a valid check character (DNI/NIE letter, IBAN mod 97, IMEI Luhn): a variant
exists to catch a real identifier written in another format, not a number that happens to touch a letter.
Shapes that are never identifiers are skipped (hex hashes, the scheme, host and path of a URL — never its
query or fragment —, amounts with their currency, procurement file numbers, round prefixes) and the
synthetic fixtures of the redaction tests are allowlisted BY VALUE, never by file.
Every tracked file is read as text (UTF-8, UTF-16 with BOM, else Latin-1) except the binary formats listed
by extension; a file that is not readable text fails the gate instead of being skipped. The report shows
the masked line only, so a hit never copies the identifier into a CI log.
"""
from __future__ import annotations

import argparse
import codecs
import os
import re
import subprocess
import sys
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "api"))
from app.redact import _PATTERNS, redact_text  # noqa: E402

HIGH = {"DNI", "NIE", "IBAN", "cuenta bancaria", "IMEI", "teléfono", "referencia catastral", "correo"}
_LABEL = re.compile(r"\[([^\]]+?) ocult[oa]\]")

# Synthetic values used by the tests (api/tests/test_redact.py, test_pii_tree_scan.py) and in comments (canonical
# fakes: 12345678Z, the official example IBAN, the Wikipedia IMEI, 612345678, usuario_ficticio@gmail.com — Gmail
# takes no underscore, so no mailbox has it...). Compared after dropping spaces, dots, dashes, the plus sign and the
# at and dot of an e-mail address, written or spelled out, so "+ 34 612 34 56 78" and "34612345678" are the same
# value, and so are every spelling of the fake address.
SYNTHETIC = {
    "12345678Z", "12345678A", "X1234567L", "ES9121000418450200051332", "21000418450200051332", "356938035643809", "35693803564380",
    "612345678", "34612345678", "0034612345678", "34623456789", "51912345678", "34634567890", "1612345678",
    "12345A001000010000AB", "1234567AB1234C0001DE", "1234567AB1234C", "0123456789",
    "USUARIO_FICTICIOGMAILCOM",
}
# Personal e-mail address: a mailbox of 4+ characters, then the provider and its domain, with an @, "(at)" or
# "arroba" between them (dots and "punto" optional), glued to the provider, or as loose words, the way the BOE
# recon sweep flattens a key ("usuario_ficticio gmail com"; a real one slipped through on 07-10-2026). Loose words
# never end in a bare dot: "una cuenta gmail.com" is prose, and a provider with no mailbox before it ("Gmail",
# "@gmail.com") is not an address.
_DOT_WORD = r"(?:[(\[]dot[)\]]|\bpunto\b)"
_DOT = rf"(?:\.|{_DOT_WORD})"
_PROVIDER = r"(?:gmail|hotmail|outlook|yahoo|protonmail|icloud)"
_MAIL = re.compile(
    r"(?<![A-Za-z0-9._-])[A-Za-z0-9][A-Za-z0-9._-]{2,}[A-Za-z0-9]"
    rf"(?:[ \t]*(?:@|[(\[]at[)\]]|\barroba\b)[ \t]*{_PROVIDER}[ \t]*(?:{_DOT}[ \t]*)?"
    rf"|{_PROVIDER}[ \t]*(?:{_DOT}[ \t]*)?"
    rf"|[ \t]+{_PROVIDER}(?:[ \t]+|[ \t]*{_DOT_WORD}[ \t]*))"
    r"(?:com|es|net)(?![A-Za-z0-9])", re.I)
# Any other address written with an @, whatever its domain (a mailbox of 4+ characters, like the rule above, a dotted host, a TLD): a
# personal address on a company or ISP domain is as identifying as one at a big provider. The project's own
# addresses, the RFC example domains and WhatsApp JIDs (phone numbers, caught above as such) are not mailboxes.
_MAIL_DOMAINS_OK = r"(?:entramado\.org|example\.(?:com|org|net|invalid)|s\.wha[lt]sapp\.net|g\.us)"
_MAIL_ANY = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{4,}@"
    rf"(?!{_MAIL_DOMAINS_OK}(?![A-Za-z0-9-]))"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-])", re.I)
# Shapes the serve-time rules would mask in prose but that are not identifiers in a repo. Matched on the
# line as written; a hit inside one of them is skipped.
FALSE_POSITIVE = [
    re.compile(r"[0-9a-f]{32,}", re.I),                    # sha / hex ids (audit units, chunk ids)
    # Scheme, host and path of a URL (timestamps, article, BOE and CENDOJ ids). It ends at a quote, bracket
    # or angle bracket, so the markup around it is scanned, and so are its query and fragment.
    re.compile(r"https?://[^\s\"'<>()\[\]{}|\\^`?#]+", re.I),
    # Procurement file numbers, also slugged into entity ids: 2021/EA26/00001152E, LegalCase:2021-ea26-00001152e.
    re.compile(r"\b\d{4}(?:/[A-Z]+\d*/|-[A-Z]+\d+-)\d{8}[A-Z]\b", re.I),
    re.compile(r"\b20\d{6}-[A-Z]\b", re.I),                # audit round prefixes: 20260906-L
    # Dates and the ids built on them: 2021-06-18, run 20260622-153012, case 2023-11-20-004321-1.
    re.compile(r"\b(?:19|20)\d{2}-?[01]\d-?[0-3]\d(?:[-T]\d+)*\b"),
    re.compile(r"\bsize = \d+"),                           # artifact sizes in uv.lock: size = 654321098
    # Amounts with their currency: 600.000.000 €, 699 000 000 de euros, $699.000.000,50.
    re.compile(r"(?:[€$]|\b(?:eur|usd)\b)\s?\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?"
               r"|\b\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?\s?(?:de\s)?(?:[€$]|(?:euros?|eur|usd|d[oó]lares)\b)", re.I),
]
# Binary formats, skipped by extension; any other tracked file must be readable text. The corpus PDFs are
# scanned apart, on their extracted text.
BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".tgz", ".gz",
              ".zip", ".sqlite", ".pyc"}


@dataclass(frozen=True)
class _Rule:
    rx: re.Pattern[str]
    label: str
    placeholder: str  # what the API serves in place of the match
    keeps_cue: bool   # the replacement starts with \1: the locative cue before an address stays visible


def _rule(rx: re.Pattern[str], repl: str) -> _Rule:
    label = _LABEL.search(repl)
    return _Rule(rx, label.group(1) if label else repl, repl.removeprefix("\\1"), repl.startswith("\\1"))


RULES = [_rule(rx, repl) for rx, repl in _PATTERNS] + [_rule(_MAIL, "[correo oculto]"), _rule(_MAIL_ANY, "[correo oculto]")]
_HIGH_RULES = [r for r in RULES if r.label in HIGH]

# Letters stuck to a digit, directly or across one dot or dash: "12345678z", "x-1234567-l", "es91…".
_CASE = re.compile(r"(?<=\d)[-.]?[A-Za-z]+|[A-Za-z]+(?=[-.]?\d)")
# One variant per kind of separator, so a pair of coordinates or a date next to a number do not merge. Dots
# and dashes also join an isolated NIE prefix or trailing control letter ("X.1234567.L"); a letter after a
# space is prose ("612 34 56 78 Y …") and the API already accepts a plain space before a control letter.
_JOINS = [re.compile(rf"(?<=\d){s}(?=\d)|(?<=(?<![A-Z0-9])[XYZ]){s}(?=\d)|(?<=\d){s}(?=[A-Z](?![A-Z0-9]))")
          for s in (r"\.", r"[-\u2010\u2011]")]
_JOINS.append(re.compile(r"(?<=\d)[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000](?=\d)"))
# No identifier is grouped like a date, and a collapsed date next to a letter reads as a DNI ("01-09-2025 p.12").
_DATE = re.compile(r"(?<!\d)(?:\d{1,2}([-./])\d{1,2}\1(?:19|20)\d{2}|(?:19|20)\d{2}([-./])\d{1,2}\2\d{1,2})(?!\d)")
_DIGITS = str.maketrans("", "", "0123456789")
_FILL = "\0"  # stands for an identifier already masked: no word character, digit or space, like "[DNI oculto]"
_NOT_NEWLINE = re.compile(r"[^\n]")
_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CONTROLS = dict.fromkeys([*range(0x00, 0x09), *range(0x0E, 0x20), 0x7F])


@dataclass
class Hit:
    path: str
    line: int
    label: str
    masked: str  # the line as the API would serve it (identifier replaced by its placeholder)

    @property
    def high(self) -> bool:
        return self.label in HIGH


@dataclass
class TreeScan:
    hits: list[Hit] = field(default_factory=list)
    analysed: Counter = field(default_factory=Counter)   # encoding -> files read
    skipped: Counter = field(default_factory=Counter)    # binary extension, or "no existe" -> files
    unreadable: list[tuple[str, str]] = field(default_factory=list)  # (path, why): these fail the gate


def _normalise(value: str) -> str:
    return re.sub(r"[\s.+\-@]|[(\[](?:at|dot)[)\]]|\b(?:arroba|punto)\b", "", value, flags=re.I)


def _check_ok(label: str, value: str) -> bool:
    """Whether the check character of a normalised identifier is right; labels without one pass."""
    if label == "DNI":
        return _DNI_LETTERS[int(value[:8]) % 23] == value[8]
    if label == "NIE":
        return _DNI_LETTERS[int(str("XYZ".index(value[0])) + value[1:8]) % 23] == value[8]
    if label == "IBAN":
        return int(value[4:] + str(int(value[0], 36)) + str(int(value[1], 36)) + value[2:4]) % 97 == 1
    if label == "IMEI" and len(value) == 15:
        digits = [int(d) for d in value]
        return (sum(digits[-1::-2]) + sum(sum(divmod(2 * d, 10)) for d in digits[-2::-2])) % 10 == 0
    return True


def _matches(rules: list[_Rule], text: str) -> list[tuple[_Rule, int, int, str]]:
    """(rule, start, end, matched text) in the order `redact_text` masks them: each rule runs over what the
    previous ones left. A match is blanked with filler of its own length (newlines kept), so offsets stay
    those of `text` and a later rule cannot match a masked identifier again."""
    found = []
    for rule in rules:
        spans = [(m.end(1) if rule.keeps_cue else m.start(), m.end()) for m in rule.rx.finditer(text)]
        for start, end in spans:
            found.append((rule, start, end, text[start:end]))
            text = text[:start] + _NOT_NEWLINE.sub(_FILL, text[start:end]) + text[end:]
    return found


def _variants(line: str) -> list[tuple[str, list[int]]]:
    """The normalised forms of `line` that differ from it, each with the index in `line` of its characters."""
    cased = _CASE.sub(lambda m: m.group(0).upper(), line)
    out = [(cased, list(range(len(line))))] if cased != line else []
    dates = {i for m in _DATE.finditer(cased) for i in range(*m.span())}
    for join in _JOINS:
        dropped = {m.start() for m in join.finditer(cased)} - dates
        if dropped:
            keep = [i for i in range(len(cased)) if i not in dropped]
            out.append(("".join(cased[i] for i in keep), keep))
    return out


def _masked(line: str, spans: list[tuple[int, int, str]]) -> str:
    out, pos = [], 0
    for start, end, placeholder in sorted(spans):
        if end > pos:
            out += [line[pos:max(start, pos)], placeholder]
            pos = end
    out.append(line[pos:])
    return redact_text("".join(out)).strip()[:160]


def scan_text(path: str, text: str) -> list[Hit]:
    """Apply the serve-time rules in order (as `redact_text` does) to the text as written and the high-severity
    ones to the normalised variants of each line; keep every match that is neither a known false-positive
    shape nor a synthetic fixture (nor, when only a variant finds it, a wrong check character)."""
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    found: dict[int, list[tuple[int, int, _Rule, str, bool]]] = {}  # line -> (start, end, rule, raw, as written)
    for rule, start, end, raw in _matches(RULES, text):
        i = bisect_right(starts, start) - 1
        found.setdefault(i, []).append((start - starts[i], min(end - starts[i], len(lines[i])), rule, raw, True))
    for i, line in enumerate(lines):
        if len(line) - len(line.translate(_DIGITS)) < 7:  # every high-severity identifier has 7+ digits
            continue
        for variant, index in _variants(line):
            for rule, start, end, raw in _matches(_HIGH_RULES, variant):
                found.setdefault(i, []).append((index[start], index[end - 1] + 1, rule, raw, False))
    hits = []
    for i, matches in sorted(found.items()):
        fp_spans = [m.span() for fp in FALSE_POSITIVE for m in fp.finditer(lines[i])]
        labels: dict[tuple[str, str], str] = {}
        spans = []
        for start, end, rule, raw, as_written in matches:
            value = _normalise(raw).upper()
            if value in SYNTHETIC or any(a <= start and end <= b for a, b in fp_spans):
                continue
            # Masked in the report even when it does not count (repeated, or a wrong check character).
            spans.append((start, end, rule.placeholder))
            if (rule.label, value) not in labels and (as_written or _check_ok(rule.label, value)):
                labels[rule.label, value] = rule.label
        if labels:
            masked = _masked(lines[i], spans)
            hits.extend(Hit(path, i + 1, label, masked) for label in labels.values())
    return hits


def read_text(path: str) -> tuple[str | None, str]:
    """(text, encoding) of a file, or (None, why) when its bytes are not text the rules can read."""
    with open(path, "rb") as f:
        blob = f.read()
    if blob.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            text, encoding = blob.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return None, "BOM UTF-16 con bytes inválidos"
    else:
        try:
            text, encoding = blob.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            text, encoding = blob.decode("latin-1"), "latin-1"
    # UTF-16/32 without BOM or binary data: NULs and control bytes would split every identifier.
    controls = len(text) - len(text.translate(_CONTROLS))
    if controls * 20 > len(text):
        return None, f"{controls} caracteres de control en {len(text)}: ¿UTF-16/32 sin BOM o binario?"
    return text, encoding


def tracked_files(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def scan_paths(paths: list[str], root: str = ROOT) -> TreeScan:
    result = TreeScan()
    for rel in paths:
        ext = os.path.splitext(rel)[1].lower()
        full = os.path.join(root, rel)
        if ext in BINARY_EXT:
            result.skipped[ext] += 1
        elif not os.path.isfile(full):
            result.skipped["no existe"] += 1
        else:
            text, how = read_text(full)
            if text is None:
                result.unreadable.append((rel, how))
            else:
                result.analysed[how] += 1
                result.hits.extend(scan_text(rel, text))
    return result


def _counts(counter: Counter) -> str:
    return ", ".join(f"{key} {n}" for key, n in counter.most_common()) or "ninguno"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PII scan of the tracked tree with the API redaction rules")
    ap.add_argument("paths", nargs="*", help="files to scan (default: every tracked file)")
    ap.add_argument("--all", action="store_true", help="also print low-severity hits (addresses, plates)")
    args = ap.parse_args(argv)
    paths = args.paths or tracked_files(ROOT)
    result = scan_paths(paths)
    high = [h for h in result.hits if h.high]
    low = [h for h in result.hits if not h.high]
    for path, why in result.unreadable:
        print(f"ILEGIBLE  {path}  ({why}): pásalo a UTF-8 o, si es un formato binario, añade su extensión a "
              "BINARY_EXT")
    for h in high:
        print(f"ALTO  {h.path}:{h.line}  [{h.label}]  {h.masked}")
    if args.all:
        for h in low:
            print(f"bajo  {h.path}:{h.line}  [{h.label}]  {h.masked}")
    print(f"pii-tree-scan: {len(paths)} ficheros: {sum(result.analysed.values())} analizados "
          f"({_counts(result.analysed)}), {sum(result.skipped.values())} saltados ({_counts(result.skipped)}), "
          f"{len(result.unreadable)} ilegibles; {len(high)} hallazgos de alta severidad, {len(low)} de baja "
          f"(domicilios/matrículas{'' if args.all else ', --all para verlos'})")
    return 1 if high or result.unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
