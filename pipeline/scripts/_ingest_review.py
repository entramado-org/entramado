"""Consolidate swarm extractions into a MANUAL-REVIEW document (pre-graph gate).

Reads data/reports/extractions/batch_*.json (the ingest swarm output), aggregates
entities and relations, cross-checks every proposed entity against (a) the LIVE graph
via the public /search API (fulltext over names+aliases) and (b) the local context
YAMLs (figures/organizaciones/merges), flags low-value nodes, and writes:

  data/reports/senado_review/REVIEW-<tag>.md      human review document
  data/reports/senado_review/review_state.yaml    machine state (veredicto: PENDIENTE)

NOTHING here touches Neo4j or the facts cache. The apply step (_ingest_apply +
ingest_tail) runs only after the review file is approved.

Usage (from pipeline/):
    uv run python -m scripts._ingest_review <tag>
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

from src import config
from src.entity_resolution import _slug

REPORTS = config.DATA_DIR.parent / "pipeline" / "data" / "reports"
if not REPORTS.exists():  # DATA_DIR layout safety
    REPORTS = Path(__file__).resolve().parent.parent / "data" / "reports"
EXTRACTIONS = REPORTS / "extractions"
OUT_DIR = REPORTS / "senado_review"
API = "https://entramado.org/api/search"

# Nodes with no analytical value on their own (the "desayuno" class).
LOW_VALUE_RE = re.compile(
    r"^(el |la |los |las |un |una )?(desayuno|caf[eé]|t[eé]|comida|cena|almuerzo|reuni[oó]n(es)?|"
    r"encuentro|comparecencia|sesi[oó]n|comisi[oó]n( de investigaci[oó]n)?|investigaci[oó]n|"
    r"rescate|viaje|visita|llamada|mensaje|whatsapp|correo|carta|nota|informe|auto|expediente|"
    r"contrato|obra(s)?|factura(s)?|n[oó]mina(s)?|medalla|multa|pasaporte|maleta(s)?|"
    r"declaraci[oó]n|testimonio|pregunta|respuesta|orden del d[ií]a|plan de trabajo|"
    r"senado|congreso|gobierno|estado|espa[ñn]a|madrid|venezuela)$",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _last_token(name: str) -> str:
    toks = [t for t in re.split(r"\s+", _norm(name)) if len(t) > 2]
    return toks[-1] if toks else _norm(name)


def _search(q: str, limit: int = 5) -> list[dict]:
    url = f"{API}?q={urllib.parse.quote(q)}&limit={limit}"
    # Cloudflare devuelve 403 al User-Agent por defecto de urllib — mandar uno de navegador.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("results", [])
        except Exception as e:
            if attempt == 2:
                print(f"  WARN search '{q}' falló: {e}", file=sys.stderr)
            time.sleep(1.0 * (attempt + 1))
    return []


def _load_context_names() -> dict[str, str]:
    """name(normalized) -> 'context:<file>' for every curated canonical/alias."""
    out: dict[str, str] = {}
    ctx = config.DATA_DIR.parent / "pipeline" / "data" / "context"
    if not ctx.exists():
        ctx = Path(__file__).resolve().parent.parent / "data" / "context"
    for f in sorted(ctx.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("name", "canonical") and isinstance(v, str):
                        out.setdefault(_norm(v), f"context:{f.name}")
                    if k == "canonical" and isinstance(v, dict) and isinstance(v.get("name"), str):
                        out.setdefault(_norm(v["name"]), f"context:{f.name}")
                    if k in ("duplicates", "aliases") and isinstance(v, list):
                        for d in v:
                            if isinstance(d, str):
                                out.setdefault(_norm(d), f"context:{f.name}")
                            elif isinstance(d, dict) and isinstance(d.get("name"), str):
                                out.setdefault(_norm(d["name"]), f"context:{f.name}")
                    walk(v)
            elif isinstance(obj, list):
                for it in obj:
                    walk(it)
        walk(data)
    return out


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "senado"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # chunk_id -> pages
    pages: dict[str, tuple[int, int]] = {}
    dump = json.load(open(REPORTS / "ingest_chunks.json"))
    for b in dump["batches"]:
        for c in b["chunks"]:
            pages[c["chunk_id"]] = (c["page_start"], c["page_end"], c["doc_id"])

    # aggregate
    ents: dict[tuple[str, str], dict] = {}
    rels: list[dict] = []
    files = sorted(EXTRACTIONS.glob("batch_*.json"), key=lambda f: int(f.stem.split("_")[1]))
    for f in files:
        for rec in json.load(open(f)):
            cid, did = rec.get("chunk_id"), rec.get("doc_id")
            pg = pages.get(cid, (None, None, did))
            for e in rec.get("entities", []) or []:
                if not e.get("name") or not e.get("type"):
                    continue
                key = (_norm(e["name"]), e["type"])
                slot = ents.setdefault(key, {
                    "name": e["name"], "type": e["type"], "roles": set(),
                    "aliases": set(), "docs": defaultdict(set), "mentions": 0,
                })
                slot["mentions"] += 1
                if e.get("role"):
                    slot["roles"].add(str(e["role"])[:200])
                for a in e.get("aliases") or []:
                    if isinstance(a, str):
                        slot["aliases"].add(a)
                if pg[0] is not None:
                    slot["docs"][pg[2]].add(pg[0])
            for r in rec.get("relations", []) or []:
                if not r.get("source") or not r.get("target") or not r.get("type"):
                    continue
                rels.append({
                    "source": r["source"], "target": r["target"], "type": r["type"],
                    "quote": (r.get("source_quote") or "")[:160],
                    "date": r.get("date"), "amount": r.get("amount"),
                    "currency": r.get("currency"),
                    "doc": pg[2], "page": pg[0], "chunk_id": cid,
                })

    # classify entities against live graph + context
    ctx_names = _load_context_names()
    ent_rows = []
    for (nname, etype), slot in sorted(ents.items(), key=lambda kv: -kv[1]["mentions"]):
        pid = f"{etype}:{_slug(slot['name'])}"
        cands = _search(slot["name"])
        if not cands:
            cands = _search(_last_token(slot["name"]))
        time.sleep(0.15)
        status, match = "NUEVO", None
        for c in cands:
            if c["id"] == pid:
                status, match = "EXISTE", c
                break
        if match is None:
            for c in cands:
                same_type = c["id"].split(":")[0] == etype
                if same_type and (_last_token(c["name"]) == _last_token(slot["name"])
                                  or _norm(c["name"]) in nname or nname in _norm(c["name"])):
                    status, match = "POSIBLE_DUPLICADO", c
                    break
        if status == "NUEVO" and nname in ctx_names:
            status, match = "POSIBLE_DUPLICADO", {"id": ctx_names[nname], "name": slot["name"], "degree": ""}
        low = bool(LOW_VALUE_RE.match(slot["name"])) or etype in ("Event",)
        ent_rows.append({
            "name": slot["name"], "type": etype, "proposed_id": pid,
            "mentions": slot["mentions"],
            "roles": sorted(slot["roles"])[:3],
            "aliases": sorted(slot["aliases"])[:8],
            "docs": {d: sorted(ps) for d, ps in slot["docs"].items()},
            "status": status,
            "match": ({"id": match["id"], "name": match.get("name"), "degree": match.get("degree")} if match else None),
            "low_value": low,
            "veredicto": "PENDIENTE",
        })

    ent_by_norm = { _norm(r["name"]): r for r in ent_rows }
    for i, r in enumerate(rels):
        src, tgt = ent_by_norm.get(_norm(r["source"])), ent_by_norm.get(_norm(r["target"]))
        r["idx"] = i
        r["flags"] = sorted(set(
            (["low_value_endpoint"] if (src and src["low_value"]) or (tgt and tgt["low_value"]) else [])
            + (["endpoint_sin_entidad"] if not src or not tgt else [])
        ))
        r["veredicto"] = "PENDIENTE"

    state = {"tag": tag, "entities": ent_rows, "relations": rels}
    (OUT_DIR / "review_state.yaml").write_text(
        yaml.safe_dump(state, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")

    # human markdown
    n_new = sum(1 for e in ent_rows if e["status"] == "NUEVO")
    n_dup = sum(1 for e in ent_rows if e["status"] == "POSIBLE_DUPLICADO")
    n_ex = sum(1 for e in ent_rows if e["status"] == "EXISTE")
    n_low = sum(1 for e in ent_rows if e["low_value"])
    md = [f"# Revisión manual — extracción Senado ({tag})\n",
          f"**{len(ent_rows)} entidades únicas** (EXISTE {n_ex} · POSIBLE_DUPLICADO {n_dup} · NUEVO {n_new} · "
          f"low-value ⚠️ {n_low}) · **{len(rels)} relaciones**. NADA se ha cargado al grafo.\n",
          "Leyenda: EXISTE = mismo entity_id ya en el grafo vivo (merge natural, sin duplicado). "
          "POSIBLE_DUPLICADO = hay un nodo parecido — decidir canónico o añadir a merges.yaml. "
          "NUEVO = no encontrado. ⚠️ = candidato a nodo sin valor.\n"]

    md.append("\n## Entidades propuestas\n")
    md.append("| # | Entidad | Tipo | Menciones | Estado | Nodo existente | ⚠️ |")
    md.append("|---|---|---|---|---|---|---|")
    for i, e in enumerate(ent_rows):
        m = f"`{e['match']['id']}` ({e['match'].get('degree','')})" if e["match"] else ""
        md.append(f"| E{i} | **{e['name']}** | {e['type']} | {e['mentions']} | {e['status']} | {m} | {'⚠️' if e['low_value'] else ''} |")

    md.append("\n### Detalle de entidades NUEVAS o dudosas\n")
    for i, e in enumerate(ent_rows):
        if e["status"] == "EXISTE" and not e["low_value"]:
            continue
        roles = "; ".join(e["roles"]) or "—"
        docs = ", ".join(f"{d} pp.{ps[0]}–{ps[-1]}" for d, ps in e["docs"].items())
        md.append(f"- **E{i} · {e['name']}** ({e['type']}, {e['status']}{', ⚠️ low-value' if e['low_value'] else ''}) — "
                  f"id propuesto `{e['proposed_id']}` · rol: {roles} · fuentes: {docs}")

    md.append("\n## Relaciones propuestas (todas, con cita)\n")
    for doc in sorted({r['doc'] for r in rels}):
        md.append(f"\n### {doc}\n")
        md.append("| # | Relación | Fecha | Importe | p. | Cita | Flags |")
        md.append("|---|---|---|---|---|---|---|")
        for r in sorted([r for r in rels if r["doc"] == doc], key=lambda r: (r["page"] or 0, r["idx"])):
            rel = f"{r['source']} —{r['type']}→ {r['target']}"
            amt = f"{r['amount']} {r['currency'] or ''}".strip() if r.get("amount") else ""
            fl = " ".join(r["flags"])
            md.append(f"| R{r['idx']} | {rel} | {r['date'] or ''} | {amt} | {r['page']} | «{r['quote']}» | {fl} |")

    md.append("\n---\nRevisar y marcar veredictos (OK / FUERA / EDITAR) sobre los índices E#/R#; "
              "`review_state.yaml` es el estado machine-readable que consumirá el apply.\n")
    (OUT_DIR / f"REVIEW-{tag}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"✔ {len(ent_rows)} entidades ({n_ex} existen, {n_dup} posibles dup, {n_new} nuevas, {n_low} low-value)")
    print(f"✔ {len(rels)} relaciones")
    print(f"→ {OUT_DIR / f'REVIEW-{tag}.md'}")
    print(f"→ {OUT_DIR / 'review_state.yaml'}")


if __name__ == "__main__":
    main()
