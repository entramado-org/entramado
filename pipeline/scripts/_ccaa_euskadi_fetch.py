#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["certifi"]
# ///
"""READ-ONLY adaptador CCAA Euskadi (spec 054, sub-barrido B). Zero graph writes.

Contratos MENORES del Registro de Contratos vasco (Revascon/KontratazioA, ~800 poderes
adjudicadores) vía la API REST oficial (verificada en vivo 2026-07-19; OpenAPI en
opendata.euskadi.eus, consola webopd00-apicontract):

  GET https://api.euskadi.eus/procurements/contracts
      ?minor-contract=true&award-date.gt=YYYY-MM-DD&award-date.lt=YYYY-MM-DD
      &itemsOfPage=50&currentPage=N            (máx 50/página, sin auth)

Campos: id, object, CIF (NIF adjudicatario), socialReason, awardDate, awardAmount[/
WithoutVAT], CPV, minorContract (== contractProcedureType.id 10), mainEntityOfPage (URL
del anuncio). NB: los menores explotan desde 2025 (publicación masiva reciente); antes
~100-200/año. GOTCHA API: en la ruta por autoridad el filtro minor-contract se ignora —
aquí usamos solo la ruta global, que sí filtra.

  uv run pipeline/scripts/_ccaa_euskadi_fetch.py harvest    # ventanas anuales, reanudable
  uv run pipeline/scripts/_ccaa_euskadi_fetch.py normalize  # -> _index_ccaa_euskadi/*.jsonl
"""
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import certifi

DATA = Path(os.environ.get("PLACSP_DATA")
            or Path(__file__).resolve().parents[1] / "data" / "contratacion")
RAW = DATA / "_dumps_ccaa_euskadi"
INDEX = DATA / "_index_ccaa_euskadi"
API = "https://api.euskadi.eus/procurements/contracts"
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
YEAR0 = 2011


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
        return json.loads(r.read())


def harvest() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for year in range(YEAR0, date.today().year + 1):
        shard = RAW / f"menores_{year}.jsonl"
        done = RAW / f"menores_{year}.done"
        if done.exists():
            print(f"skip {year}")
            continue
        n, page = 0, 1
        with shard.open("w") as out:
            while True:
                url = (f"{API}?minor-contract=true&award-date.gt={year-1}-12-31"
                       f"&award-date.lt={year+1}-01-01&itemsOfPage=50&currentPage={page}")
                try:
                    doc = _get(url)
                except Exception as e:  # noqa: BLE001
                    print(f"  ERROR {year} p{page}: {type(e).__name__}: {e} — retry")
                    time.sleep(3)
                    try:
                        doc = _get(url)
                    except Exception as e2:  # noqa: BLE001
                        print(f"  ABORT año {year}: {type(e2).__name__} (shard parcial sin .done)")
                        return
                items = doc.get("items") or []
                for it in items:
                    out.write(json.dumps(it, ensure_ascii=False) + "\n")
                    n += 1
                total_pages = doc.get("totalPages") or 0
                if page >= total_pages or not items:
                    break
                page += 1
                if page % 50 == 0:
                    print(f"  {year}: página {page}/{total_pages} ({n})")
                time.sleep(0.05)
        done.write_text(f"{n}\n")
        print(f"{year}: {n} menores")


def normalize() -> None:
    INDEX.mkdir(parents=True, exist_ok=True)
    for path in sorted(RAW.glob("menores_*.jsonl")):
        shard = INDEX / path.name
        done = INDEX / f"{path.stem}.done"
        if done.exists():
            print(f"skip {path.name}")
            continue
        n = 0
        with shard.open("w") as out:
            for line in path.open():
                it = json.loads(line)
                links = it.get("_links") or {}
                org_ref = links.get("org")
                if isinstance(org_ref, dict):
                    org_ref = org_ref.get("href")
                rec = {
                    "src": f"api.euskadi.eus/{path.name}",
                    "id": it.get("id"),
                    "link": it.get("mainEntityOfPage"),
                    # el listado NO trae el órgano inline: se resuelve por org_ref
                    # (GET al href) SOLO para los hits, en curación
                    "organo": None,
                    "organo_ref": org_ref,
                    "title": it.get("object"),
                    "procedure": "MENOR",
                    "cpv": (it.get("CPV") or {}).get("id")
                           if isinstance(it.get("CPV"), dict) else it.get("CPV"),
                    "amount": it.get("awardAmountWithoutVAT") or it.get("awardAmount"),
                    "amount_iva": it.get("awardAmount"),
                    "winner_nif": it.get("CIF"),
                    "winner_name": it.get("socialReason"),
                    "award_date": it.get("awardDate"),
                }
                if not rec["winner_name"] and not rec["winner_nif"]:
                    continue
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
        done.write_text(f"{n}\n")
        print(f"{path.name}: {n} registros")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "harvest":
        harvest()
    elif cmd == "normalize":
        normalize()
    else:
        sys.exit("uso: _ccaa_euskadi_fetch.py {harvest|normalize}")
