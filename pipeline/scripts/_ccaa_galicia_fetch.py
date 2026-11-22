#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["certifi"]
# ///
"""READ-ONLY adaptador CCAA Galicia (spec 054, sub-barrido B). Zero graph writes.

Contratos MENORES de la Plataforma de Contratos Públicos de Galicia
(contratosdegalicia.gal) vía su API JSON interna (DataTables server-side, verificada en
vivo 2026-07-19): GET /api/v1/organismos/{OR}/contratosmenores/table con ventanas de
FECHA <=3 MESES (más → "Request failed") y paginación start/length. El payload trae
id/publicado/objeto(truncado)/importe/NIF/adjudicatario; el órgano va implícito en {OR}.
Los ~429 IDs de organismo se scrapean del <select name="OR"> de portada.jsp.

  uv run pipeline/scripts/_ccaa_galicia_fetch.py orgs           # -> _orgs.json (id -> nombre)
  uv run pipeline/scripts/_ccaa_galicia_fetch.py harvest        # TODOS los orgs (lento: SERGAS)
  ORGS=14,34,221,... uv run …_ccaa_galicia_fetch.py harvest     # solo esos orgs (alta señal)
  MAX_ORG=40000 uv run …_ccaa_galicia_fetch.py harvest          # salta orgs > N registros
  uv run pipeline/scripts/_ccaa_galicia_fetch.py normalize      # -> _index_ccaa_galicia/*.jsonl

Reanudable por organismo (un shard crudo por org + marker .done). Cortesía: secuencial con
pausa corta. ⚠️ El barrido COMPLETO es inviable de una tirada: el SERGAS (org 11) solo tiene
~686k menores (>2h). Recomendado: `ORGS=<alta-señal>` (lista en findings) o `MAX_ORG` para
cortar los organismos gigantes (sanidad) de baja señal para este reparto. Detalle de un
contrato: https://www.contratosdegalicia.gal/licitacion?N=CM{id}&lang=es (para la curación).
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import certifi

DATA = Path(os.environ.get("PLACSP_DATA")
            or Path(__file__).resolve().parents[1] / "data" / "contratacion")
RAW = DATA / "_dumps_ccaa_galicia"
INDEX = DATA / "_index_ccaa_galicia"
BASE = "https://www.contratosdegalicia.gal"
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
YEAR0 = 2018   # arranque verificado (obligación LCSP; 2012 vacío)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        return r.read()


def orgs() -> dict[str, str]:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / "_orgs.json"
    if path.exists():
        return json.loads(path.read_text())
    html = _get(f"{BASE}/portada.jsp").decode("iso-8859-1", "replace")
    sel = re.search(r'<select[^>]*name="OR"[^>]*>(.*?)</select>', html, re.S)
    if not sel:
        sys.exit("no encuentro el <select name=\"OR\"> en portada.jsp")
    # las options van con comillas SIMPLES (value='48') — aceptar ambas
    found = dict(re.findall(r"<option[^>]*value=['\"](\d+)['\"][^>]*>\s*([^<]+)",
                            sel.group(1)))
    found = {k: v.strip() for k, v in found.items() if v.strip()}
    path.write_text(json.dumps(found, ensure_ascii=False, indent=1))
    print(f"{len(found)} organismos -> {path}")
    return found


def _quarters():
    from datetime import date
    today = date.today()
    y = YEAR0
    while y <= today.year:
        for q0, q1 in ((1, 3), (4, 6), (7, 9), (10, 12)):
            if date(y, q0, 1) > today:
                return
            end_day = {3: 31, 6: 30, 9: 30, 12: 31}[q1]
            yield f"{y}-{q0:02d}-01", f"{y}-{q1:02d}-{end_day}"
        y += 1


def harvest() -> None:
    organisms = orgs()
    only = os.environ.get("ORGS")            # allow-list "14,34,221,..." (alta señal)
    max_org = int(os.environ.get("MAX_ORG") or 0)  # salta orgs con > N registros (SERGAS)
    todo = sorted(organisms, key=int)
    if only:
        keep = {o.strip() for o in only.split(",")}
        todo = [o for o in todo if o in keep]
        print(f"harvest acotado a {len(todo)} organismos (ORGS)")
    for i, org in enumerate(todo):
        shard = RAW / f"org_{org}.jsonl"
        done = RAW / f"org_{org}.done"
        if done.exists():
            continue
        n = 0
        aborted = False
        with shard.open("w") as out:
            for d0, d1 in _quarters():
                if aborted:
                    break
                start = 0
                while True:
                    if max_org and n > max_org:   # organismo gigante (sanidad) -> saltar
                        print(f"  SKIP org {org} ({organisms[org][:40]}): > {max_org} "
                              f"registros (baja señal); shard descartado")
                        aborted = True
                        break
                    url = (f"{BASE}/api/v1/organismos/{org}/contratosmenores/table"
                           f"?draw=1&start={start}&length=100&datestart={d0}&dateend={d1}")
                    try:
                        doc = json.loads(_get(url))
                    except Exception as e:  # noqa: BLE001 - un fallo no mata el harvest
                        print(f"  ERROR org {org} {d0}: {type(e).__name__}: {e}")
                        time.sleep(2)
                        break
                    rows = doc.get("data") or []
                    for row in rows:
                        out.write(json.dumps({**row, "_org": org,
                                              "_org_name": organisms[org]},
                                             ensure_ascii=False) + "\n")
                        n += 1
                    if len(rows) < 100:
                        break
                    start += 100
                    time.sleep(0.1)
                time.sleep(0.1)
        if aborted:                    # organismo gigante saltado: no dejar shard parcial
            shard.unlink(missing_ok=True)
            done.write_text("SKIPPED\n")  # marker para no reintentarlo en un resume
            continue
        done.write_text(f"{n}\n")
        print(f"[{i+1}/{len(todo)}] org {org} ({organisms[org][:40]}): {n}")


def normalize() -> None:
    INDEX.mkdir(parents=True, exist_ok=True)
    shard_out = INDEX / "contratosmenores_galicia.jsonl"
    n = 0
    with shard_out.open("w") as out:
        for path in sorted(RAW.glob("org_*.jsonl")):
            for line in path.open():
                row = json.loads(line)
                pub = (row.get("publicado") or "").strip()      # dd-mm-aaaa
                if len(pub) == 10 and pub[2] == "-":
                    pub = f"{pub[6:10]}-{pub[3:5]}-{pub[0:2]}"
                rec = {
                    "src": f"contratosdegalicia/org_{row.get('_org')}",
                    "id": f"CM{row.get('id')}",
                    "link": f"{BASE}/licitacion?N=CM{row.get('id')}&lang=es",
                    "organo": row.get("_org_name"),
                    "title": (row.get("objeto") or "").strip() or None,
                    "procedure": "MENOR",
                    "amount": row.get("importe"),
                    "winner_nif": (row.get("nif") or "").strip() or None,
                    "winner_name": (row.get("adjudicatario") or "").strip() or None,
                    "award_date": pub or None,
                }
                if not rec["winner_name"] and not rec["winner_nif"]:
                    continue
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    (INDEX / "contratosmenores_galicia.done").write_text(f"{n}\n")
    print(f"{n} registros -> {shard_out}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "orgs":
        orgs()
    elif cmd == "harvest":
        harvest()
    elif cmd == "normalize":
        normalize()
    else:
        sys.exit("uso: _ccaa_galicia_fetch.py {orgs|harvest|normalize}")
