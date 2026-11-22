#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["certifi"]
# ///
"""READ-ONLY adaptador CCAA Andalucía (spec 054, sub-barrido B). Zero graph writes.

Contratos MENORES de la Junta de Andalucía y su sector instrumental (art. 118 LCSP),
desde los dumps anuales del portal de datos abiertos (CKAN, CC-BY 4.0; verificado en vivo
2026-07-19). Cobertura: Junta + agencias/fundaciones/empresas públicas (227 órganos en
2023; SAS ≈ 3/4 del volumen). NO incluye ayuntamientos, diputaciones ni universidades.
NB: menores <5.000 EUR por caja fija están exentos de publicación (caveat legal).

  uv run pipeline/scripts/_ccaa_andalucia_fetch.py download   # dumps anuales 2018->2026
  uv run pipeline/scripts/_ccaa_andalucia_fetch.py normalize  # -> _index_ccaa_andalucia/*.jsonl

Normaliza al MISMO esquema JSONL del índice PLACSP (winner_nif/winner_name/amount/organo/
award_date/title/procedure...) para que `_placsp_recon_sweep.py match
--dataset=ccaa_andalucia` corra sin cambios. Todo bajo pipeline/data/contratacion/_*
(gitignored); raíz redirigible con PLACSP_DATA. CSV fuente: sep '|', ISO-8859-1, campos
padded con espacios; fechas dd/mm/aaaa; importes con coma decimal.
"""
import csv
import io
import json
import os
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

import certifi

DATA = Path(os.environ.get("PLACSP_DATA")
            or Path(__file__).resolve().parents[1] / "data" / "contratacion")
DUMPS = DATA / "_dumps_ccaa_andalucia"
INDEX = DATA / "_index_ccaa_andalucia"
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
PORTAL = "https://www.juntadeandalucia.es/datosabiertos/portal"

# dataset/resource CKAN por año, verificados en vivo 2026-07-19 (package_search
# 'contratacion menor plataforma'); 2018-2023 son ZIP con el CSV dentro, 2024-2025 CSV
# planos, 2026 por trimestres (Q1 al cierre del recon).
RESOURCES = {}  # nombre_fichero -> URL; se rellena con `discover` o a mano abajo


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300, context=SSL_CTX) as r:
        return r.read()


def discover() -> dict[str, str]:
    """Resource URLs de los datasets anuales vía API CKAN (con fallback de host)."""
    out: dict[str, str] = {}
    q = f"{PORTAL}/api/3/action/package_search?q=contratacion+menor+plataforma&rows=30"
    doc = json.loads(_get(q))
    for pkg in doc["result"]["results"]:
        if "contratacion-menor-plataforma" not in pkg["name"]:
            continue
        for res in pkg["resources"]:
            url, fmt = res["url"], (res.get("format") or "").upper()
            if fmt not in ("CSV", "ZIP"):
                continue
            name = url.rsplit("/", 1)[-1]
            if not (name.startswith("menores") or "menor" in name.lower()):
                continue
            # el host interno de los metadatos no resuelve desde fuera -> re-anclar
            if "/datosabiertos/portal/" in url:
                path = url.split("/datosabiertos/portal/", 1)[1]
                url = f"{PORTAL}/{path}"
            # preferir CSV plano sobre ZIP si hay ambos para el mismo periodo
            out.setdefault(name, url)
    return out


def download() -> None:
    DUMPS.mkdir(parents=True, exist_ok=True)
    resources = RESOURCES or discover()
    if not resources:
        sys.exit("discover() no encontró resources — revisar CKAN")
    for name, url in sorted(resources.items()):
        dest = DUMPS / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"skip {name} ({dest.stat().st_size >> 20} MB)")
            continue
        print(f"GET {url}")
        try:
            data = _get(url)
            dest.write_bytes(data)
            print(f"  ok {len(data) >> 20} MB")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {name}: {type(e).__name__}: {e}")


def _rows_from_csv(fh) -> "csv.DictReader":
    text = io.TextIOWrapper(fh, encoding="iso-8859-1", newline="")
    return csv.DictReader(text, delimiter="|")


def _num(s: str | None) -> str | None:
    """Formatos MIXTOS por año: 2018-2024 punto decimal (39831.53), 2025+ coma (39171,83)."""
    s = (s or "").strip()
    if not s:
        return None
    if "," in s:                                          # coma decimal (+ puntos de millar)
        return s.replace(".", "").replace(",", ".")
    if s.count(".") == 1 and len(s.rsplit(".", 1)[1]) <= 2:
        return s                                          # punto decimal
    return s.replace(".", "")                             # puntos de millar


def _date(s: str | None) -> str | None:
    s = (s or "").strip()
    if len(s) == 10 and s[2] == "/" and s[5] == "/":     # dd/mm/aaaa -> aaaa-mm-dd
        return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    if "T" in s:                                          # ISO datetime -> fecha
        return s[:10]
    return s or None


def normalize() -> None:
    INDEX.mkdir(parents=True, exist_ok=True)
    files = sorted(DUMPS.iterdir()) if DUMPS.exists() else []
    if not files:
        sys.exit("no hay dumps — corre `download` primero")
    for path in files:
        shard = INDEX / f"{path.stem}.jsonl"
        done = INDEX / f"{path.stem}.done"
        if done.exists():
            print(f"skip {path.name}")
            continue
        n = 0
        with shard.open("w") as out:
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as z:
                    for member in z.namelist():
                        if not member.lower().endswith(".csv"):
                            continue
                        with z.open(member) as fh:
                            n += _write_rows(_rows_from_csv(fh), path.name, out)
            elif path.suffix.lower() == ".csv":
                with path.open("rb") as fh:
                    n += _write_rows(_rows_from_csv(fh), path.name, out)
            else:
                print(f"  ignoro {path.name} (formato)")
        done.write_text(f"{n}\n")
        print(f"{path.name}: {n} registros")


def _write_rows(reader, src: str, out) -> int:
    n = 0
    for row in reader:
        g = lambda k: (row.get(k) or "").strip() or None  # noqa: E731 - campos padded
        rec = {
            "src": src,
            "id": g("ID_EXPEDIENTE") or g("NUM_EXPEDIENTE"),
            "folder_id": g("NUM_EXPEDIENTE"),
            "organo": g("ORGANO_CONTRATACION"),
            "title": g("TITULO"),
            "procedure": "MENOR",           # dataset íntegro de menores (PROCEDIMIENTO=MENOR)
            "status": g("ESTADO"),
            "budget": _num(g("VALOR_ESTIMADO")),
            "amount": _num(g("IMPORTE_ADJUDICACION_SIN_IVA")),
            "amount_iva": _num(g("IMPORTE_ADJUDICACION_CON_IVA")),
            "n_tenders": g("NUM_LICITADORES_PRESENTADOS"),
            "winner_nif": g("NIF_ADJUDICATARIO"),
            "winner_name": g("ADJUDICATARIO_DENOMINACION"),
            "winner_city": g("LUGAR_EJECUCION_DENOMINACION"),
            "award_date": _date(g("FECHA_ADJUDICACION")),
            "tipo": g("TIPO_CONTRATO"),
        }
        if not rec["winner_name"] and not rec["winner_nif"]:
            continue
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
    return n


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "download":
        download()
    elif cmd == "normalize":
        normalize()
    elif cmd == "discover":
        for name, url in sorted(discover().items()):
            print(name, "->", url)
    else:
        sys.exit("uso: _ccaa_andalucia_fetch.py {discover|download|normalize}")
