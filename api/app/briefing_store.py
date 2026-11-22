"""Filesystem access to the Obsidian vault mounted at /vault (read-only).

Briefing parsing (used by the /briefings API endpoints) plus plain-text search
and bounded reads over the whole vault (used by the MCP connector, spec 009).
"""
from __future__ import annotations

import ast
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# /vault in containers (compose mounts ./vault:ro); overridable for bare-metal dev.
VAULT_DIR = Path(os.getenv("VAULT_DIR", "/vault"))
BRIEFINGS_DIR = VAULT_DIR / "_Briefings"

# Folders that are vault plumbing, never content.
_SKIP_DIRS = {".obsidian", ".trash"}

# The only folders the vault tools serve: curated, editorial text. Everything else in the vault (entity notes, timelines, MOCs,
# document notes) is an export of the graph as it was on the day it was generated: it keeps naming entities and relations
# the graph has since withdrawn, and free text cannot be reliably scrubbed against a list of names. The graph tools serve
# that same content up to date. Reopen folder by folder when the vault is regenerated from the live seed.
_EDITORIAL_DIRS = {"_Briefings", "Patrones"}


def _slug(name: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", name)
                if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _briefing_files() -> list[Path]:
    """Briefing .md files, excluding the AppleDouble `._*` companions a
    macOS-made tarball materialises when extracted on Linux (binary, unparseable)."""
    return sorted(p for p in BRIEFINGS_DIR.glob("*.md")
                  if not p.name.startswith("._"))


def _extract_dek(body: str) -> str:
    """The dek is the italic one-liner briefings.py writes right after the H1
    (`*…*`). Surface it as a teaser; "" when absent."""
    after_h1 = re.split(r"^#\s.*$", body, maxsplit=1, flags=re.MULTILINE)
    tail = after_h1[1] if len(after_h1) > 1 else body
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\*(.+)\*$", line)
        return m.group(1).strip() if m else ""
    return ""


def _parse_briefing(path: Path) -> dict:
    """Parse the minimal YAML-ish frontmatter + body our briefings.py writes."""
    raw = path.read_text(encoding="utf-8")
    meta = {"slug": _slug(path.stem), "title": path.stem}
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            for line in raw[4:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip("'\"")
            raw = raw[end + 5 :]
    body = raw.lstrip("\n")
    # `anchors` is written as a Python list literal (e.g. "['Person:x', …]");
    # parse it back into a real list. literal_eval (not eval) is safe and the
    # frontmatter is project-generated, not user input.
    anchors: list[str] = []
    if isinstance(meta.get("anchors"), str):
        try:
            val = ast.literal_eval(meta["anchors"])
            if isinstance(val, list):
                anchors = [str(a) for a in val]
        except (ValueError, SyntaxError):
            anchors = []
    # Briefings carry no date in their frontmatter; they are live-mounted from
    # the vault, so the file mtime IS the last real edit — good enough for the
    # sitemap lastmod and the Article JSON-LD dateModified (spec 035).
    try:
        updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    except OSError:
        updated = None
    return {"slug": meta.get("slug", _slug(path.stem)),
            "title": meta.get("title", path.stem),
            "dek": _extract_dek(body),
            "anchors": anchors,
            "updated": updated,
            "content": body}


# --- Vault-wide search / read (MCP connector, spec 009) -----------------------

def _fold(s: str) -> str:
    """Lowercase + strip accents so 'Cerdan' matches 'Cerdán'."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def _vault_files() -> list[Path]:
    if not VAULT_DIR.exists():
        return []
    return [p for p in VAULT_DIR.rglob("*.md")
            if not any(part in _SKIP_DIRS for part in p.parts)
            and not p.name.startswith("._")]


def _term_forms(term: str) -> set[str]:
    """A term plus its naive singular, so 'rescates' matches a note that only
    says 'rescate' (Spanish plurals are mostly +s/+es). Cheap stemming — no NLP
    dependency — enough for a lexical vault search."""
    forms = {term}
    if len(term) > 3 and term.endswith("s"):
        forms.add(term[:-1])
        if term.endswith("es") and len(term) > 4:
            forms.add(term[:-2])
    return forms


def _served_note(spelled: Path) -> Path | None:
    """The file to read for a note spelled `spelled` (a path under VAULT_DIR), or None when it is not a served note. One
    rule for search and read, applied both to the path as spelled and to the file it resolves to (links followed): inside an
    editorial folder of the vault, markdown, not plumbing, and a regular file. Both, so that a link neither opens a file of
    another folder (or a non-note) nor lends the name of another folder to an editorial note. A path that cannot be resolved
    is not a note."""
    try:
        bases = (Path(os.path.normpath(VAULT_DIR.absolute())), VAULT_DIR.resolve())
        target = spelled.resolve()
        for seen in (Path(os.path.normpath(spelled.absolute())), target):
            rels = [seen.relative_to(base) for base in bases if seen.is_relative_to(base)]
            if not any(len(rel.parts) > 1 and rel.parts[0] in _EDITORIAL_DIRS for rel in rels):
                return None
            if seen.suffix != ".md" or seen.name.startswith("._") or any(part in _SKIP_DIRS for part in seen.parts):
                return None
        return target if target.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _served_files() -> list[tuple[Path, Path]]:
    """The notes the vault tools may search and read, as (path as listed, file to read) — the file that was validated is
    the one that is opened. (`_vault_files` is every note: the entity notes are still indexed to know which names the
    editorial ones must not carry.)"""
    out = []
    for d in sorted(_EDITORIAL_DIRS):
        if (VAULT_DIR / d).is_dir():
            for p in (VAULT_DIR / d).rglob("*.md"):
                target = _served_note(p)
                if target is not None:
                    out.append((p, target))
    return out


_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def vault_entity_notes() -> dict[str, str]:
    """Relative path → entity id, for every entity note. Access to a note is decided by its own id (two notes can share a
    title: the stale one of a merge and the canonical one); the title (`Path(path).stem`) is how the other notes refer to it
    (`[[Título]]`)."""
    out: dict[str, str] = {}
    for p in _vault_files():
        rel = str(p.relative_to(VAULT_DIR))
        eid = note_entity_id(rel)
        if eid:
            out[rel] = eid
    return out


def vault_entity_aliases() -> dict[str, set[str]]:
    """Entity id → the folded aliases its note declares in the frontmatter (`aliases:` list). The other notes name an entity by
    its aliases as often as by its title («el señor X»)."""
    out: dict[str, set[str]] = {}
    for p in _vault_files():
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        if not head.startswith("---"):
            continue
        front = head[3:].split("\n---", 1)[0]
        eid = re.search(r"^entity_id:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", front, re.M)
        block = re.search(r"^aliases:\s*\n((?:[ \t]*-[^\n]*\n?)+)", front, re.M)
        if not eid or not block:
            continue
        names = {_fold(a.strip().strip("\"'")) for a in re.findall(r"^[ \t]*-\s*(.+?)\s*$", block.group(1), re.M)}
        out.setdefault(eid.group(1), set()).update(n for n in names if len(n) >= 3)
    return out


def _unaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


class _PlainMention:
    """Hidden names in running text. A name of two or more words matches as whole words, any case. A single word — a first
    name, a surname, a pseudonym: «don Roman» — matches only capitalised and when it is not the first name of a longer
    name («Roman Pérez» is somebody else), whatever its length, so the rule does not swallow its homonyms."""

    def __init__(self, hidden: frozenset[str]):
        long_ = sorted((t for t in hidden if " " in t), key=len, reverse=True)
        short = sorted((t for t in hidden if t and t not in long_ and t.isalpha()), key=len, reverse=True)
        self._long = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in long_) + r")(?!\w)") if long_ else None
        self._short = (re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n.capitalize()) for n in short)
                                  + r")(?!\w)(?!\s+[A-Z])") if short else None)

    def search(self, line: str) -> bool:
        plain = _unaccent(line)
        return bool((self._long and self._long.search(plain.lower())) or (self._short and self._short.search(plain)))


def _plain_mention(hidden: frozenset[str]) -> _PlainMention | None:
    return _PlainMention(hidden) if hidden else None


def _blocks(text: str) -> list[list[str]]:
    """Lines grouped with their continuations: an item and the indented lines or quote lines right under it (its citation),
    or a run of quote lines. What is withdrawn is the whole block — a citation left behind would read as belonging to
    the item above it."""
    out: list[list[str]] = []
    for line in text.split("\n"):
        cont = bool(out) and out[-1][-1].strip() != "" and line[:1] in (" ", "\t", ">") and line.strip() != ""
        if cont:
            out[-1].append(line)
        else:
            out.append([line])
    return out


def without_hidden(text: str, hidden: frozenset[str], plain: _PlainMention | None = None) -> str:
    """`text` without the blocks that link to (or, with `plain`, name) a note whose folded title is in `hidden`: the vault is
    an older export than the graph, and a line of «Conexiones» or of a timeline can still carry an entity that is no longer
    served."""
    if not hidden:
        return text
    keep: list[str] = []
    for block in _blocks(text):
        if any(_fold(t.strip().rsplit("/", 1)[-1]) in hidden for line in block for t in _WIKILINK.findall(line)):
            continue
        if plain is not None and any(plain.search(line) for line in block):
            continue
        keep.extend(block)
    return "\n".join(keep)


def vault_search(q: str, limit: int = 15, snippet_chars: int = 200,
                 hidden: frozenset[str] = frozenset(), unnamed: frozenset[str] = frozenset()) -> list[dict]:
    """Lexical (term-overlap / BM25-ish) search over the editorial vault notes (`_EDITORIAL_DIRS`) — NOT
    semantic (no embeddings; that infra was dropped for hosting).

    The query is tokenised; each note is scored by how many DISTINCT query terms
    it contains (title hits weighted heavier than body), plus capped term
    frequency and an exact-phrase bonus. This makes a descriptive multi-word
    query like "SEPI rescates Plus Ultra Air Europa" rank the Plus Ultra and
    rescate notes — a plain substring search returned nothing because no note
    contains that whole phrase. Multi-term queries require ≥2 distinct terms to
    match, which keeps a single coincidental word from surfacing noise. ~18 MB of
    markdown — a full scan is sub-second, no index needed at this scale.

    `hidden` (entity ids): their notes are not served at all. `unnamed` (folded names): entities that must
    not be linked or named either — every other note is searched without the blocks that do, so neither a hit nor its
    snippet can come from one of them.
    """
    needle = _fold(q.strip())
    if not needle:
        return []
    limit = max(1, min(limit, 25))
    terms = [t for t in re.findall(r"[\wáéíóúñü]+", _fold(q)) if len(t) >= 2]
    if not terms:
        return []
    term_forms = {t: _term_forms(t) for t in terms}
    # A multi-term query must hit at least two distinct terms (kills noise);
    # a single-term query keeps full recall.
    min_cover = 2 if len(terms) >= 2 else 1

    def present(forms: set[str], hay: str) -> bool:
        return any(f in hay for f in forms)

    plain = _plain_mention(unnamed)
    scored: list[tuple[float, int, dict]] = []
    for p, target in _served_files():
        rel = str(p.relative_to(VAULT_DIR))
        ftitle = _fold(p.stem)
        title_cover = sum(1 for t in terms if present(term_forms[t], ftitle))
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if hidden and _entity_id_of(text) in hidden:
            continue
        text = without_hidden(text, unnamed)
        ftext = _fold(text)
        cover = sum(1 for t in terms if present(term_forms[t], ftitle) or present(term_forms[t], ftext))
        if cover < min_cover:
            continue
        if plain is not None:
            # A block that names a hidden entity in running text must not make the note a hit, nor reach the snippet (which
            # is cut from the text around the hit). Done only for the notes that already matched, to keep the scan cheap.
            text = without_hidden(text, unnamed, plain)
            ftext = _fold(text)
            cover = sum(1 for t in terms if present(term_forms[t], ftitle) or present(term_forms[t], ftext))
            if cover < min_cover:
                continue
        body_cover = sum(1 for t in terms if present(term_forms[t], ftext))
        tf = sum(min(ftext.count(next(iter(fs))), 5) for t, fs in term_forms.items())
        phrase_bonus = 6 if (needle in ftext or needle in ftitle) else 0
        score = title_cover * 4 + cover * 2 + body_cover + tf * 0.2 + phrase_bonus
        # Snippet around the first body term hit; title-only hits get "".
        snippet = ""
        pos = -1
        for t in terms:
            for f in term_forms[t]:
                pos = ftext.find(f)
                if pos != -1:
                    break
            if pos != -1:
                break
        if pos != -1:
            start = max(0, pos - snippet_chars // 2)
            snippet = text[start : start + snippet_chars].replace("\n", " ").strip()
        scored.append((score, cover, {"path": rel, "title": p.stem, "snippet": snippet}))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [hit for _, _, hit in scored[:limit]]


def _entity_id_of(head: str) -> str | None:
    """The `entity_id` of a note's frontmatter, from the start of its text."""
    if not head.startswith("---"):
        return None
    front = head[3:4096].split("\n---", 1)[0]
    m = re.search(r"^entity_id:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", front, re.M)
    return m.group(1) if m else None


def note_entity_id(rel_path: str) -> str | None:
    """The `entity_id` in a note's frontmatter (entity notes exported from the graph), or None for editorial notes."""
    target = (VAULT_DIR / rel_path).resolve()
    if not target.is_relative_to(VAULT_DIR.resolve()) or not target.is_file():
        return None
    with target.open("r", encoding="utf-8", errors="ignore") as fh:
        return _entity_id_of(fh.read(4096))


def vault_available() -> bool:
    """Whether the vault is mounted with any servable notes (false ⇒ not deployed)."""
    return bool(_served_files())


def vault_read(rel_path: str, max_bytes: int = 50_000, hidden: frozenset[str] = frozenset(),
               unnamed: frozenset[str] = frozenset()) -> str:
    """Read one vault note by relative path. Anything that is not a served note (outside the vault, another folder,
    not markdown) does not exist for the reader; truncates large notes with an explicit marker. The note of an entity id in `hidden`
    does not exist for the reader; any other note is read without the blocks that link to or name an `unnamed` name."""
    # Only the exact relative paths that the search lists are readable, and the file opened is the one validated for that
    # entry: the reader's path is never resolved against the filesystem (a `..` through a link of another folder would tell
    # whether that link exists). One answer for everything else: the reader cannot tell why.
    target = next((t for p, t in _served_files() if str(p.relative_to(VAULT_DIR)) == rel_path), None)
    if target is None:
        raise FileNotFoundError(rel_path)
    # Same reading as the search: the whole file as strict UTF-8, and the entity id looked up on that same text.
    try:
        full = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise FileNotFoundError(rel_path) from None
    if hidden and _entity_id_of(full) in hidden:
        raise FileNotFoundError(rel_path)
    data = full.encode("utf-8")
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="ignore")
    text = without_hidden(text, unnamed, _plain_mention(unnamed))
    return text + ("\n\n[truncado]" if truncated else "")
