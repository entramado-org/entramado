#!/usr/bin/env python3
"""Vigía BOE (spec 051): chequeo READ-ONLY para la rutina cloud recordatorio.

Descarga los sumarios BOE de los últimos --days (10 por defecto), filtra la sección
II.A, matchea los títulos contra `pipeline/data/boe/vigia_targets.json` (VERSIONADO,
regenerado en cada ciclo de curación con `match --export-vigia`) y resta lo ya
cubierto/descartado según los YAML committeados (boe.yaml + matches.candidate.yaml).

Emite JSON por stdout: {"n_hits": N, "hits": [{entity_id, name, boe_id, fecha,
titulo, url_html}]} y sale SIEMPRE con código 0 (los hits no son un error).

Deliberadamente autónomo: stdlib puro + un parser YAML mínimo de los DOS campos que
necesita (boe_id_* / boe_id en boe.yaml; boe_id / entity_id por sección en la
semilla) — la rutina cloud solo tiene el checkout, sin uv/PyYAML. La cobertura por
regex es un SUPERSET seguro de la real (un boe_id citado en cualquier parte de esos
ficheros cuenta como cubierto): solo puede callar de más, jamás editar nada.

Uso local (dry-run):  python3 pipeline/scripts/boe_vigia_check.py --days 10
"""

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "pipeline" / "data" / "boe"
API = "https://www.boe.es/datosabiertos/api/boe/sumario/{yyyymmdd}"
UA = "sm-vigia-boe/051 (contacto: entramado.org; uso: investigación, volumen bajo)"

_STOP = {"don", "dona", "de", "del", "la", "las", "los", "y", "e", "a",
         "señor", "senor", "señora", "senora", "d", "dª"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-zñ ]+", " ", s)
    return " ".join(t for t in s.split() if t not in _STOP)


def _fetch_day(d: date) -> list[dict]:
    req = urllib.request.Request(
        API.format(yyyymmdd=d.strftime("%Y%m%d")),
        headers={"Accept": "application/json", "User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:  # día sin BOE
                return []
            if attempt == 2:
                return []
            time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt == 2:
                return []
            time.sleep(2 * (attempt + 1))
    try:
        doc = json.loads(body)
    except json.JSONDecodeError:
        return []
    if str(doc.get("status", {}).get("code")) == "404":
        return []

    def aslist(x):
        return x if isinstance(x, list) else [x] if x else []

    items = []
    for dia in aslist(doc.get("data", {}).get("sumario", {}).get("diario", [])):
        for sec in aslist(dia.get("seccion")):
            if sec.get("codigo") != "2A":
                continue
            for dep in aslist(sec.get("departamento")):
                raw = list(aslist(dep.get("item")))
                for ep in aslist(dep.get("epigrafe")):
                    raw += aslist(ep.get("item"))
                for it in raw:
                    items.append({"fecha": d.isoformat(),
                                  "boe_id": it.get("identificador"),
                                  "titulo": it.get("titulo"),
                                  "url_html": it.get("url_html")})
    return items


def _covered() -> tuple[set, set]:
    """(boe_ids cubiertos, entity_ids descartados) desde los YAML committeados,
    sin PyYAML: superset por regex (ver docstring)."""
    ids: set[str] = set()
    boe_txt = (DATA / "boe.yaml").read_text(encoding="utf-8")
    ids |= set(re.findall(r"\bBOE-A-\d{4}-\d+\b", boe_txt))
    descartados: set[str] = set()
    # matches.candidate.yaml left the repo (5bd4114, public repo): the routine reads its id-only
    # extract vigia_cubiertos.json; the local curation file, when present, still adds to it.
    cub = DATA / "vigia_cubiertos.json"
    if cub.exists():
        c = json.loads(cub.read_text(encoding="utf-8"))
        ids |= set(c.get("boe_ids", []))
        descartados |= set(c.get("descartados", []))
    seed = DATA / "matches.candidate.yaml"
    seed_txt = seed.read_text(encoding="utf-8") if seed.exists() else ""
    ids |= set(re.findall(r"\bBOE-A-\d{4}-\d+\b", seed_txt))
    for section in ("rechazados", "sin_registro_destacados"):
        m = re.search(rf"^{section}:\n(.*?)(?=^\S|\Z)", seed_txt, re.S | re.M)
        if m:
            descartados |= set(re.findall(r"entity_id: (\S+)", m.group(1)))
    return ids, descartados


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args()

    vt = json.loads((DATA / "vigia_targets.json").read_text(encoding="utf-8"))
    targets = vt["targets"]
    covered_ids, descartados = _covered()

    by_first: dict[str, list[tuple[str, dict]]] = {}
    for t in targets:
        for k in t["keys"]:
            by_first.setdefault(k.split()[0], []).append((k, t))

    hits, seen = [], set()
    today = date.today()
    for i in range(args.days):
        for it in _fetch_day(today - timedelta(days=i)):
            if not it["boe_id"] or it["boe_id"] in covered_ids:
                continue
            nt = _norm(it["titulo"] or "")
            padded = f" {nt} "
            toks = set(nt.split())
            for first, pairs in by_first.items():
                if first not in toks:
                    continue
                for key, t in pairs:
                    if (f" {key} " in padded
                            and t["id"] not in descartados
                            and (t["id"], it["boe_id"]) not in seen):
                        seen.add((t["id"], it["boe_id"]))
                        hits.append({"entity_id": t["id"], "name": t["name"], **it})

    print(json.dumps({"n_hits": len(hits), "hits": hits}, ensure_ascii=False, indent=1))
    sys.exit(0)


if __name__ == "__main__":
    main()
