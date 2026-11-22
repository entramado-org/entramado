"""Export the data layer as `decide` items for the semantic PII sweep (spec 067), one item per entry.

The deterministic scanner (`pii_tree_scan.py`) only catches identifiers with a shape (DNI, IBAN,
phones, plates, addresses with a cue). What it cannot see is semantic: a home described without a
number, personal assets, relatives or minors named, health data, prose that re-identifies a judicial
pseudonym. Those are N units x the same closed questions, so they go to Jev through `decide`
(workspace engineering default 9), and only ids and classes come back into the session.

A unit is an entry, not a run of lines, so a name and the address next to it are judged together and
an edit elsewhere never moves it: in YAML each item of a top-level list and each other top-level key,
as whole lines (comments go with the entry below them); in JSON each element of a list of objects, or
each root key; other text in overlapping line windows. Units over 40 lines or 500 words go out as
overlapping subwindows headed by the unit id. `meta.sha` (schema 2) hashes the unit's exact text,
indentation included, and keys the maintainer's private list of reviewed units (`make pii-semantico
REVISADOS=…`). Only regular files tracked by this checkout are read (a symlink would send its target),
and OUT may not sit in a git work tree that does not ignore it, whichever checkout it belongs to. YAML
aliases and repeated JSON keys are refused with a message rather than mis-measured or silently collapsed.

    make pii-semantico [OUT=DIR]    # the whole gate: everything tracked in the data layer -> Jev -> selection policy
    git ls-files pipeline/data api/app/data \
        | uv run --project pipeline python pipeline/scripts/pii_semantic_export.py --out DIR --files-from -
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCHEMA = 2
MAX_LINES = 40
MAX_WORDS = 180
LONG_WORDS = 500
OVERLAP = 8
# The first of these keys an item has (with a scalar value) names it in its unit id: `file#key[k=v]`.
DISCRIMINATORS = ("entity_id", "id", "entity", "source", "slug", "name", "pack_id")
TEXT_EXT = (".md", ".cypher", ".txt")
SKIP_EXT = {
    ".pdf": "documento fuente: tiene su propio régimen en el censo (data/SOURCES.md)",
    ".png": "imagen", ".jpg": "imagen", ".jpeg": "imagen", ".webp": "imagen",
    ".sqlite": "base de datos binaria", ".gz": "binario comprimido", ".tgz": "binario comprimido",
    ".zip": "binario comprimido",
}


@dataclass(frozen=True)
class Unit:
    id: str
    text: str
    l1: int | None = None  # 1-based lines of `text` in its file; None when the text is re-serialised (JSON)
    l2: int | None = None


def unit_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """git run in `cwd`, blind to GIT_* variables (a hook's GIT_DIR would answer for another repository)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env)


def out_dir(path: str) -> Path:
    """OUT, refused (exit 2) inside any git work tree whose repository does not ignore it (AC5): texts and
    predictions must never be one `git add` away from a commit, from this checkout or any other."""
    out = Path(path).expanduser().resolve()
    if out.exists() and not out.is_dir():
        print(f"--out {out} existe y no es un directorio", file=sys.stderr)
        raise SystemExit(2)
    probe = out
    while not probe.is_dir():
        probe = probe.parent
    top = _git(probe, "rev-parse", "--show-toplevel")
    if top.returncode == 0 and _git(Path(top.stdout.strip()), "check-ignore", "-q", "--", f"{out}/").returncode != 0:
        print(f"--out {out} está en el árbol de trabajo de {top.stdout.strip()} y ese git no lo ignora: "
              "unidades y predicciones nunca van a git", file=sys.stderr)
        raise SystemExit(2)
    return out


def tracked_files(names: list[str]) -> list[str]:
    """The names as repo-relative paths of regular files tracked by git; anything else exits 2 naming it.
    `git ls-files` also lists symlinks, and reading one would send its target (an `.env`, say) to Jev."""
    rels = sorted({Path(name).as_posix() for name in names})
    refused = {}
    for rel in rels:
        path = REPO / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            refused[rel] = "fuera del repo (las rutas son relativas a su raíz)"
        elif path.resolve() != path:
            refused[rel] = "pasa por un enlace simbólico"
        elif not path.is_file():
            refused[rel] = "no es un fichero regular"
    candidates = [rel for rel in rels if rel not in refused]
    if candidates:
        listed = _git(REPO, "--literal-pathspecs", "ls-files", "-z", "--", *candidates)
        tracked = set(listed.stdout.split("\0")) if listed.returncode == 0 else set()
        refused |= {rel: "no está versionado" for rel in candidates if rel not in tracked}
    if refused:
        print("pii-semantico: solo se exportan ficheros regulares versionados, con su ruta relativa al repo:",
              file=sys.stderr)
        for rel, why in sorted(refused.items()):
            print(f"  {rel}: {why}", file=sys.stderr)
        raise SystemExit(2)
    return rels


def kind_of(rel: str) -> str | None:
    ext = Path(rel).suffix.lower()
    if ext in (".yaml", ".yml"):
        return "yaml"
    if ext == ".json":
        return "json"
    if ext in TEXT_EXT:
        return "texto"
    return "saltado" if ext in SKIP_EXT else None


def _lines(text: str) -> list[str]:
    """Lines with their '\\n', split on '\\n' alone: positions come from character offsets, so the other
    breaks YAML counts as lines (NEL, U+2028) cannot shift them."""
    lines = text.split("\n")
    return [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])


def _trim(lines: list[str], a: int, b: int) -> tuple[int, int]:
    """lines[a:b] without its blank edge lines: they separate entries and belong to none."""
    while a < b and not lines[a].strip():
        a += 1
    while b > a and not lines[b - 1].strip():
        b -= 1
    return a, b


def _cuts(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Windows over lines[start:end], closed at MAX_LINES lines or MAX_WORDS words. Each one opens with the last
    OVERLAP lines of the previous one (at most half of it) and takes at least one line more, so windows of long
    lines still advance and none falls inside the one before."""
    cuts, a, fresh = [], start, start
    while a < end:
        b, words = a, 0
        while b < end and b - a < MAX_LINES and (b <= fresh or words < MAX_WORDS):
            words += len(lines[b].split())
            b += 1
        cuts.append((a, b))
        if b == end:
            break
        a, fresh = max(a + 1, b - min(OVERLAP, (b - a) // 2)), b
    return cuts


def _discriminators(entries: list[dict]) -> list[str]:
    """Per entry, `k=v` from its first discriminating key; `k=v,i` when that repeats; its index without one."""
    found = [next((f"{key}={entry[key]}" for key in DISCRIMINATORS if entry.get(key) not in (None, "")), None)
             for entry in entries]
    counts = Counter(found)
    return [f"{d},{i}" if d and counts[d] > 1 else d or str(i) for i, d in enumerate(found)]


def _content_end(node: yaml.Node, text: str) -> int:
    """Offset just past the node's last character of content. Block collections and block scalars end their
    marks at the next token, past trailing blank lines and comments, so they are measured from inside."""
    if isinstance(node, yaml.ScalarNode):
        start = node.start_mark.index
        return start + len(text[start:node.end_mark.index].rstrip())
    if node.flow_style:
        return node.end_mark.index
    children = [part for pair in node.value for part in pair] if isinstance(node, yaml.MappingNode) else node.value
    return max(_content_end(child, text) for child in children)


def _yaml_items(node: yaml.Node) -> list[tuple[str, yaml.Node]] | None:
    """(discriminator, item) per item of a block list of mappings: the only lists cut one unit per item."""
    if not isinstance(node, yaml.SequenceNode) or node.flow_style or not node.value:
        return None
    if not all(isinstance(item, yaml.MappingNode) for item in node.value):
        return None
    scalars = [{key.value: value.value for key, value in item.value
                if isinstance(key, yaml.ScalarNode) and isinstance(value, yaml.ScalarNode)} for item in node.value]
    return list(zip(_discriminators(scalars), node.value))


def _refuse_aliases(root: yaml.Node | None) -> None:
    """An alias (`*x`) makes compose() return the anchored node again, with the anchor's position: the aliased
    entry would be measured where its anchor is and the units would overlap. The data layer never uses them."""
    seen: set[int] = set()
    stack = [root] if root is not None else []
    while stack:
        node = stack.pop()
        if id(node) in seen:
            raise yaml.YAMLError("alias/ancla YAML (nodo reutilizado): no soportado, la unidad se mediría en la posición "
                                 "del ancla; desdobla la entrada")
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            stack += [part for pair in node.value for part in pair]
        elif isinstance(node, yaml.SequenceNode):
            stack += node.value


def yaml_spans(rel: str, text: str) -> list[tuple[str, int, str]]:
    """(unit id, first line, exact text) of every unit of a YAML file, in order and covering it whole: the header,
    each item of a top-level list of mappings (after a unit of its own for the key line, so a new first item
    leaves the old one intact), each other top-level key, and whatever follows the last entry. A unit ends
    on the last line of content of its entry; blank lines and comments go with the entry that follows."""
    lines = _lines(text)
    starts = list(itertools.accumulate((len(line) for line in lines), initial=0))

    def line_of(offset: int) -> int:
        return bisect.bisect_right(starts, offset) - 1

    def last_line(*nodes: yaml.Node) -> int:
        return line_of(max(_content_end(node, text) for node in nodes) - 1)

    root = yaml.compose(text, Loader=yaml.SafeLoader)
    _refuse_aliases(root)
    entries: list[tuple[str, int]] = []
    if isinstance(root, yaml.MappingNode) and not root.flow_style:
        for key, value in root.value:
            name = f"{rel}#{key.value}"
            items = _yaml_items(value)
            if items is None:
                entries.append((name, last_line(key, value)))
            else:
                entries.append((name, last_line(key)))
                entries += [(f"{name}[{d}]", last_line(item)) for d, item in items]
    elif (items := _yaml_items(root)) is not None:
        entries = [(f"{rel}#[{d}]", last_line(item)) for d, item in items]
    if not entries:
        return [(rel, 0, text)]
    first = line_of(root.start_mark.index)
    bounds = ([(f"{rel}#(cabecera)", 0)] if first else []) + [(name, first) for name, _ in entries[:1]]
    bounds += [(name, last + 1) for (name, _), (_, last) in zip(entries[1:], entries)]
    if entries[-1][1] + 1 < len(lines):
        bounds.append((f"{rel}#(final)", entries[-1][1] + 1))
    ends = [start for _, start in bounds[1:]] + [len(lines)]
    return [(name, a, "".join(lines[a:b])) for (name, a), b in zip(bounds, ends)]


def yaml_units(rel: str, text: str) -> list[Unit]:
    spans = yaml_spans(rel, text)
    assert "".join(raw for _, _, raw in spans) == text, f"{rel}: las unidades no recomponen el fichero"
    units = []
    for name, first, raw in spans:
        lines = _lines(raw)
        a, b = _trim(lines, 0, len(lines))
        if a < b:
            units.append(Unit(name, "".join(lines[a:b]), first + a + 1, first + b))
    return units


class DuplicateJsonKey(ValueError):
    """`json.loads` keeps only the last value of a repeated key: the earlier text would never reach the sweep."""


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise DuplicateJsonKey(f"clave JSON repetida ({', '.join(repeated)}): el barrido solo vería el último valor")
    return dict(pairs)


def json_units(rel: str, text: str) -> list[Unit]:
    """A root list or a root key holding a list of objects: one unit per element. Any other root key: one unit.
    Repeated keys, at any depth, are refused rather than silently collapsed."""
    data = json.loads(text, object_pairs_hook=_pairs)

    def dump(value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=1)

    def per_element(name: str, value) -> list[Unit] | None:
        if not isinstance(value, list) or not value or not all(isinstance(v, dict) for v in value):
            return None
        scalars = [{k: x for k, x in v.items() if isinstance(x, (str, int, float))} for v in value]
        return [Unit(f"{name}[{d}]", dump(v)) for d, v in zip(_discriminators(scalars), value)]

    if isinstance(data, dict) and data:
        return [unit for key, value in data.items()
                for unit in per_element(f"{rel}#{key}", value) or [Unit(f"{rel}#{key}", dump({key: value}))]]
    return per_element(f"{rel}#", data) or [Unit(rel, dump(data))]


def text_units(rel: str, text: str) -> list[Unit]:
    lines = _lines(text)
    return [Unit(f"{rel}:{a + 1}-{b}", "".join(lines[a:b]), a + 1, b)
            for a, b in _cuts(lines, *_trim(lines, 0, len(lines)))]


def _item(rel: str, item_id: str, text: str, l1: int | None, l2: int | None, unidad: str,
          parte: str | None = None) -> dict:
    meta = {"fichero": rel, "l1": l1, "l2": l2, "unidad": unidad, "sha": unit_sha(text),
            "palabras": len(text.split()), "schema": SCHEMA}
    if parte:
        meta["parte"] = parte
    return {"id": item_id, "state": {"fichero": rel, "lineas": f"{l1}-{l2}" if l1 else "", "texto": text}, "meta": meta}


def unit_items(rel: str, unit: Unit) -> list[dict]:
    """The unit as one item or, when long, as overlapping subwindows whose first line names the unit."""
    lines = _lines(unit.text)
    long = len(lines) > MAX_LINES or len(unit.text.split()) > LONG_WORDS
    cuts = _cuts(lines, 0, len(lines)) if long else []
    if len(cuts) < 2:
        return [_item(rel, unit.id, unit.text, unit.l1, unit.l2, unit.id)]
    content = [line.strip() for line in lines if line.strip()]
    first = next((line for line in content if not line.startswith("#") and any(c.isalnum() for c in line)), content[0])
    return [_item(rel, f"{unit.id}~{i}/{len(cuts)}", f"# {unit.id} · {first}\n" + "".join(lines[a:b]),
                  unit.l1 and unit.l1 + a, unit.l1 and unit.l1 + b - 1, unit.id, f"{i}/{len(cuts)}")
            for i, (a, b) in enumerate(cuts, 1)]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True,
                    help="run directory for items.jsonl, outside any git work tree that does not ignore it")
    ap.add_argument("--files-from",
                    help="file with one repo-relative path per line ('-' = stdin), e.g. `git ls-files` output")
    ap.add_argument("files", nargs="*", help="repo-relative paths of tracked files")
    args = ap.parse_args(argv)
    names = list(args.files)
    if args.files_from:
        listing = sys.stdin.read() if args.files_from == "-" else Path(args.files_from).read_text(encoding="utf-8")
        names += [line.strip() for line in listing.splitlines() if line.strip()]
    if not names:
        ap.error("sin ficheros: pásalos como argumentos o con --files-from")
    out = out_dir(args.out)
    rels = tracked_files(names)
    kinds = {rel: kind_of(rel) for rel in rels}
    unknown = [rel for rel, kind in kinds.items() if kind is None]
    if unknown:
        print("pii-semantico: extensión sin clasificar (expórtala o añádela a SKIP_EXT con su motivo):",
              file=sys.stderr)
        for rel in unknown:
            print(f"  {rel}", file=sys.stderr)
        raise SystemExit(3)
    items, units, long_units = [], 0, 0
    for rel in rels:
        kind = kinds[rel]
        if kind == "saltado":
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
            if kind == "texto":
                items += [_item(rel, unit.id, unit.text, unit.l1, unit.l2, unit.id) for unit in text_units(rel, text)]
                continue
            for unit in yaml_units(rel, text) if kind == "yaml" else json_units(rel, text):
                parts = unit_items(rel, unit)
                units += 1
                long_units += len(parts) > 1
                items += parts
        except (UnicodeDecodeError, yaml.YAMLError, json.JSONDecodeError, DuplicateJsonKey) as error:
            raise SystemExit(f"pii-semantico: {rel} no se puede leer como {kind}: {error}")
    repeated = [item_id for item_id, n in Counter(item["id"] for item in items).items() if n > 1]
    if repeated:
        raise SystemExit(f"pii-semantico: ids repetidos (¿claves duplicadas?): {repeated[:5]}")
    for path in reversed([p for p in (out, *out.parents) if not p.exists()]):
        path.mkdir(mode=0o700)  # owner-only, missing parents included: the run holds what goes to Jev
    with (out / "items.jsonl").open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    counts = Counter(kinds.values())
    parts = sum("parte" in item["meta"] for item in items)
    windows = sum(kinds[item["meta"]["fichero"]] == "texto" for item in items)
    print(f"pii-semantico: {len(rels)} ficheros: {counts['yaml']} yaml, {counts['json']} json, "
          f"{counts['texto']} de texto, {counts['saltado']} saltados")
    for rel in rels:
        if kinds[rel] == "saltado":
            print(f"  saltado ({SKIP_EXT[Path(rel).suffix.lower()]}): {rel}")
    print(f"pii-semantico: {units} unidades ({long_units} largas, en {parts} subventanas) + {windows} ventanas de "
          f"texto = {len(items)} ítems -> {out / 'items.jsonl'}")


if __name__ == "__main__":
    main()
