#!/usr/bin/env python3
"""READ-ONLY BDNS recon sweep (spec 041, Fase 1). Zero graph writes.

Cruza la lista objetivo del grafo (pipeline/data/subvenciones/_target_*.tsv, exportada por
_bdns_target_list.sh) contra la API pública del SNPSAP/BDNS:

  1. GET /terceros?busqueda=<nombre>  -> candidatos {id, "NIF - DENOMINACION"}
  2. por candidato: GET /{concesiones,ayudasestado,minimis,partidospoliticos}/busqueda
     ?beneficiario=<id> -> totales + muestra

Salida: pipeline/data/subvenciones/_raw_sweep.jsonl (una línea por entidad del grafo).
Rate limit oficial 10 req/s -> aquí ~5 req/s. Reanudable: se salta entity_ids ya presentes
en el JSONL de salida.
"""
import csv
import json
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.infosubvenciones.es/bdnstrans/api"
DATA = Path(__file__).resolve().parents[1] / "data" / "subvenciones"
OUT = DATA / "_raw_sweep.jsonl"
DETAIL_ENDPOINTS = ["concesiones", "ayudasestado", "minimis", "partidospoliticos"]
MAX_TERCEROS = 20          # más candidatos = nombre demasiado genérico -> curación manual
MAX_DETAIL_CANDIDATES = 3  # ids distintos con detalle por entidad
SLEEP = 0.2                # ~5 req/s

_last = [0.0]


def get(path: str, **params) -> dict | None:
    wait = _last[0] + SLEEP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    url = f"{BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"Accept": "application/json"}), timeout=30) as r:
                body = r.read().decode("utf-8")
                return json.loads(body) if body.strip() else None
        except Exception as e:  # noqa: BLE001 - retry any transport error
            if attempt == 3:
                return {"_error": f"{type(e).__name__}: {e}", "_url": url}
            time.sleep(2 ** attempt)
    return None


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def read_targets(path: Path, kind: str) -> list[dict]:
    rows = []
    with path.open() as f:
        reader = csv.reader(f, skipinitialspace=True)
        header = next(reader)
        for row in reader:
            if not row:
                continue
            if kind == "company":
                label, eid, name, degree, mercantil = row
                rows.append({"entity_id": eid, "name": name, "label": label,
                             "degree": int(degree), "mercantil": mercantil == "TRUE"})
            else:
                eid, name, degree = row
                rows.append({"entity_id": eid, "name": name, "label": "Person",
                             "degree": int(degree), "mercantil": False})
    return rows


def sweep_entity(ent: dict) -> dict:
    rec = {"entity_id": ent["entity_id"], "name": ent["name"], "label": ent["label"],
           "degree": ent["degree"], "query": ent["name"], "terceros": [], "details": {}}
    resp = get("terceros", busqueda=ent["name"])
    if resp is None:
        # /terceros devuelve cuerpo VACÍO (HTTP 200) cuando no hay match: es un
        # negativo ("sin registro"), no un fallo. Legacy: "error": "empty_response".
        rec["no_match"] = True
        return rec
    if "_error" in resp:
        rec["error"] = resp["_error"]
        return rec
    seen: dict[int, str] = {}
    for t in resp.get("terceros", []):
        seen.setdefault(t["id"], t["descripcion"])
    rec["terceros"] = [{"id": i, "descripcion": d} for i, d in seen.items()]
    if len(resp.get("terceros", [])) > MAX_TERCEROS:
        rec["too_generic"] = True
        return rec
    for tid in list(seen)[:MAX_DETAIL_CANDIDATES]:
        det = {}
        for ep in DETAIL_ENDPOINTS:
            r = get(f"{ep}/busqueda", page=0, pageSize=50, beneficiario=tid)
            if r is None or "_error" in r:
                det[ep] = {"error": (r or {}).get("_error", "empty")}
                continue
            det[ep] = {"total": r.get("totalElements", 0)}
            if r.get("content"):
                det[ep]["items"] = [
                    {k: it.get(k) for k in (
                        "codConcesion", "fechaConcesion", "beneficiario", "instrumento",
                        "importe", "ayudaEquivalente", "convocatoria", "convocante",
                        "nivel1", "nivel2", "nivel3", "reglamento", "tipoBeneficiario")
                     if k in it}
                    for it in r["content"]]
        rec["details"][str(tid)] = det
    return rec


def main() -> None:
    done = set()
    if OUT.exists():
        with OUT.open() as f:
            for line in f:
                try:
                    done.add(json.loads(line)["entity_id"])
                except Exception:  # noqa: BLE001
                    pass
    targets = (read_targets(DATA / "_target_companies.tsv", "company")
               + read_targets(DATA / "_target_persons.tsv", "person"))
    todo = [t for t in targets if t["entity_id"] not in done]
    print(f"targets={len(targets)} done={len(done)} todo={len(todo)}", flush=True)
    with OUT.open("a") as out:
        for i, ent in enumerate(todo, 1):
            rec = sweep_entity(ent)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            hit = sum(d.get("total", 0) for det in rec["details"].values()
                      for d in det.values() if isinstance(d, dict))
            if hit or rec.get("too_generic") or rec.get("error"):
                flag = "GENERIC" if rec.get("too_generic") else (
                    "ERROR" if rec.get("error") else f"hits={hit}")
                print(f"[{i}/{len(todo)}] {ent['entity_id']} {flag}", flush=True)
            if i % 100 == 0:
                print(f"[{i}/{len(todo)}] ...", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
