#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""READ-ONLY informe «Señal CCAA» (spec 054, Fase 1) — insumo del gate humano.

Agrega, por comunidad autónoma, dónde hay señal de contratación sobre el reparto del grafo,
SIN descargar nada de ninguna plataforma autonómica. Fuentes (todas ya en disco):
  - hits del cruce de menores (spec 045, _cruce/) y de los cruces nuevos (_cruce_lic/,
    _cruce_agr/): CCAA por CP del órgano (organo_zip) y, en agregadas, por dominio de la
    plataforma de origen (campo link);
  - sedes registrales (provincia) de las empresas del overlay mercantil ya matched.

  PLACSP_DATA=<...>/pipeline/data/contratacion uv run pipeline/scripts/_ccaa_signal_report.py

Salida: markdown por stdout (se pega en el findings). Cero escrituras fuera de stdout.
"""
import json
import os
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

import yaml

DATA = Path(os.environ.get("PLACSP_DATA")
            or Path(__file__).resolve().parents[1] / "data" / "contratacion")

PROV_CCAA = {
    "01": "País Vasco", "02": "Castilla-La Mancha", "03": "C. Valenciana", "04": "Andalucía",
    "05": "Castilla y León", "06": "Extremadura", "07": "Baleares", "08": "Cataluña",
    "09": "Castilla y León", "10": "Extremadura", "11": "Andalucía", "12": "C. Valenciana",
    "13": "Castilla-La Mancha", "14": "Andalucía", "15": "Galicia", "16": "Castilla-La Mancha",
    "17": "Cataluña", "18": "Andalucía", "19": "Castilla-La Mancha", "20": "País Vasco",
    "21": "Andalucía", "22": "Aragón", "23": "Andalucía", "24": "Castilla y León",
    "25": "Cataluña", "26": "La Rioja", "27": "Galicia", "28": "Madrid", "29": "Andalucía",
    "30": "Murcia", "31": "Navarra", "32": "Galicia", "33": "Asturias",
    "34": "Castilla y León", "35": "Canarias", "36": "Galicia", "37": "Castilla y León",
    "38": "Canarias", "39": "Cantabria", "40": "Castilla y León", "41": "Andalucía",
    "42": "Castilla y León", "43": "Cataluña", "44": "Aragón", "45": "Castilla-La Mancha",
    "46": "C. Valenciana", "47": "Castilla y León", "48": "País Vasco",
    "49": "Castilla y León", "50": "Aragón", "51": "Ceuta", "52": "Melilla",
}
DOMAIN_CCAA = {
    "www.juntadeandalucia.es": "Andalucía", "contractaciopublica.cat": "Cataluña",
    "contratos-publicos.comunidad.madrid": "Madrid",
    "www.contratacion.euskadi.eus": "País Vasco", "www.contratosdegalicia.gal": "Galicia",
    "hacienda.navarra.es": "Navarra", "www.larioja.org": "La Rioja",
}
PROV_TO_CCAA_NAME = {  # provincias del overlay mercantil (texto libre) -> CCAA
    "NAVARRA": "Navarra", "MADRID": "Madrid", "VALENCIA": "C. Valenciana",
    "ALICANTE": "C. Valenciana", "BARCELONA": "Cataluña", "SEVILLA": "Andalucía",
    "MALAGA": "Andalucía", "MURCIA": "Murcia", "VIZCAYA": "País Vasco",
    "BIZKAIA": "País Vasco", "GIPUZKOA": "País Vasco", "GUIPUZCOA": "País Vasco",
    "ALAVA": "País Vasco", "ZARAGOZA": "Aragón", "BALEARES": "Baleares",
    "ILLES BALEARS": "Baleares", "LA CORUNA": "Galicia", "A CORUNA": "Galicia",
    "PONTEVEDRA": "Galicia", "ASTURIAS": "Asturias", "CANTABRIA": "Cantabria",
    "BADAJOZ": "Extremadura", "CACERES": "Extremadura", "LAS PALMAS": "Canarias",
    "TENERIFE": "Canarias", "SANTA CRUZ DE TENERIFE": "Canarias",
}


def _hits(cruce_dir: Path):
    for path in sorted(cruce_dir.glob("hits_*.jsonl")):
        with path.open() as f:
            for line in f:
                yield json.loads(line)


def _nucleo() -> set[str]:
    """Objetivos del núcleo curado del caso: los marcados is_mercantil en el export."""
    import csv
    out: set[str] = set()
    with (DATA / "_target_companies.tsv").open() as f:
        reader = csv.reader(f, skipinitialspace=True)
        next(reader)
        for row in reader:
            if len(row) >= 6 and row[5].strip().upper() == "TRUE":
                out.add(row[1])
    return out


def main() -> None:
    nucleo = _nucleo()
    by_ccaa_platform: Counter = Counter()          # agregadas: plataforma de origen
    ents_by_platform: defaultdict = defaultdict(set)
    nucleo_by_platform: defaultdict = defaultdict(set)
    nucleo_hits_platform: Counter = Counter()
    by_ccaa_organo = {"menores": Counter(), "licitaciones": Counter(), "agregadas": Counter()}
    nucleo_by_ccaa_lic: defaultdict = defaultdict(set)
    ents_by_ccaa: defaultdict = defaultdict(set)

    for name, sub in (("menores", "_cruce"), ("licitaciones", "_cruce_lic"),
                      ("agregadas", "_cruce_agr")):
        d = DATA / sub
        if not d.exists():
            print(f"<!-- aviso: {d} no existe, fuente omitida -->")
            continue
        for h in _hits(d):
            zone = (h.get("organo_zip") or "")[:2]
            ccaa = PROV_CCAA.get(zone)
            if ccaa:
                by_ccaa_organo[name][ccaa] += 1
                ents_by_ccaa[ccaa].add(h["entity_id"])
                if name == "licitaciones" and h["entity_id"] in nucleo:
                    nucleo_by_ccaa_lic[ccaa].add(h["entity_id"])
            if name == "agregadas":
                dom = urllib.parse.urlparse(h.get("link") or "").netloc
                pc = DOMAIN_CCAA.get(dom)
                if pc:
                    by_ccaa_platform[pc] += 1
                    ents_by_platform[pc].add(h["entity_id"])
                    if h["entity_id"] in nucleo:
                        nucleo_by_platform[pc].add(h["entity_id"])
                        nucleo_hits_platform[pc] += 1

    # sedes registrales de las empresas matched (overlay mercantil, versionado)
    sedes: Counter = Counter()
    merc = DATA.parent / "mercantil" / "mercantil.yaml"
    if merc.exists():
        doc = yaml.safe_load(merc.read_text())
        for c in doc.get("companies", []):
            prov = (c.get("provincia") or "").upper()
            ccaa = PROV_TO_CCAA_NAME.get(prov)
            if ccaa:
                sedes[ccaa] += 1

    print("## Señal CCAA — ranking para el gate de selección (spec 054)\n")
    print("La columna decisiva es **núcleo** = objetivos `is_mercantil` (empresas curadas "
          "del caso), no el total (que arrastra marcas genéricas tipo Telefónica).\n")
    print("| CCAA | hits núcleo (agr.) | núcleo (agr.) | núcleo órgano-CP lic. | "
          "hits plataforma agregada (tot.) | hits órgano-CP lic. (tot.) | sedes mercantil |")
    print("|---|---|---|---|---|---|---|")
    ccaas = set(by_ccaa_platform) | set(sedes)
    for c in by_ccaa_organo.values():
        ccaas |= set(c)
    rank = sorted(ccaas, key=lambda c: (
        -nucleo_hits_platform[c], -len(nucleo_by_ccaa_lic[c]), -by_ccaa_platform[c]))
    for c in rank:
        print(f"| {c} | {nucleo_hits_platform[c]} | {len(nucleo_by_platform[c])} | "
              f"{len(nucleo_by_ccaa_lic[c])} | {by_ccaa_platform[c]} | "
              f"{by_ccaa_organo['licitaciones'][c]} | {sedes[c]} |")
    print("\nNúcleo del caso con hits vía plataforma agregada, por CCAA:")
    for c in rank:
        if nucleo_by_platform[c]:
            print(f"- **{c}**: {', '.join(sorted(nucleo_by_platform[c]))}")
    print("\nNúcleo del caso adjudicatario de órganos de la CCAA (licitaciones PLACSP):")
    for c in rank:
        if nucleo_by_ccaa_lic[c]:
            print(f"- **{c}**: {', '.join(sorted(nucleo_by_ccaa_lic[c]))}")
    print("\n> Señales cualitativas fuera de tabla: la UPV/EHU publica sus contratos MENORES "
          "en la plataforma vasca (no en PLACSP ni en agregadas —dataset SinMenores—), "
          "verificado en la spec 053 (Globalia, 521 microcontratos); Navarra es la sede del "
          "racimo Servinabar. Los menores autonómicos NO están en ningún dataset PLACSP: son "
          "el hueco que cubriría el adaptador de la CCAA elegida.")


if __name__ == "__main__":
    main()
