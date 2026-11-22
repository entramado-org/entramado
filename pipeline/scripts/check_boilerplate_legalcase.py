#!/usr/bin/env python
"""Self-checking validator for the boilerplate-:LegalCase predicate (spec 024).

Asserts the DROP/KEEP frontier from specs/024-boilerplate-legalcase/spec.md against the
REAL predicate (src.cleanup.is_boilerplate_legalcase), then enumerates the offline facts
cache (pipeline/data/facts/*.jsonl) to report how many LegalCase mentions it would drop /
keep. Exits non-zero if any frontier case regresses. This is the repo-native "unit test"
(the project has no pytest harness); re-run after touching the predicate:

    cd pipeline && uv run python scripts/check_boilerplate_legalcase.py [--drops|--keeps]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cleanup import is_boilerplate_legalcase  # noqa: E402

# name -> expected is_boilerplate_legalcase() result
FRONTIER = {
    # --- DROP: statutes/regs/treaties (even when numbered) ---
    "Ley de Enjuiciamiento Criminal": True, "Real Decreto 54/05": True,
    "Orden TMA/263/2020": True, "Ley 9/2017": True, "Ley Orgánica 10/1995": True,
    "Código Penal": True, "Constitución Española": True, "Reglamento (CE) 2012/2002": True,
    "Real Decreto-ley 7/2020": True, "LECrim": True, "IRPF": True,
    "Convenio de Doble Imposición": True, "Presupuestos Generales del Estado de 2021": True,
    # --- DROP: article citations ---
    "artículo 428": True, "Artículo 570 bis CP": True,
    "artículo 503 de la Ley de Enjuiciamiento Criminal": True,
    # --- DROP: generic procedural terms / dated acts / placeholders ---
    "LegalCase": True, "causa especial": True, "auto de imputación": True,
    "Auto de imputación de Begoña Gómez": True, "Auto de 3 de marzo de 2026": True,
    "Auto de órgano judicial": True, "auto de PPA": True, "autos": True,
    "Resolución del Subsecretario": True, "Resolución de 2 de abril de 2020": True,
    "providencia": True, "prisión provisional": True, "mandamiento de entrada y registro": True,
    "recurso de apelación": True, "causa judicial": True, "proceso penal ordinario": True,
    "Evidencia A1.1": True, "Delincuencia Económica": True,
    "informe policial número NUM000": True,
    # --- KEEP: genuine judicial dockets ---
    "Causa Especial 20775/2020": False, "Diligencias Previas 65/2023": False,
    "Diligencias previas 1146/2024": False, "Pieza Separada 62/2025": False,
    "Diligencias Previas 77/2024": False, "20775/2020": False,
    # --- KEEP: numbered autos/resoluciones/informes ---
    "Auto 540/2025 de la Audiencia Provincial de Madrid": False,
    "Auto Supremo 59/2025": False,
    "auto de la Ilma. Audiencia Provincial de Madrid 445/2024": False,
    "Resolución nº 102/2017 Tribunal Administrativo Central de Recursos Contractuales": False,
    "Informe 211/2024": False, "UCO nº 211/2024": False, "informe 12-LO-5440": False,
    # --- KEEP: admin expedientes / codes / named cases / slugs / courts ---
    "044/20-ED": False, "300/2021/00652": False, "Expediente 3.19/27507.0250": False,
    "Operación Delorme": False, "auto-zapatero": False, "Auto-zapatero": False,
    "auto-prision-cerdan-2025-06-30": False, "JCI nº2": False,
    "Diligencias de Investigación Preprocesales 1/25": False,
    # --- KEEP: generic-contract descriptors (owner decision 2026-07-03) ---
    "contrato de mascarillas": False, "contrato de prestación de servicios": False,
    # --- KEEP: the covid hub (its split is spec 025) ---
    "Comisión de Investigación sobre los contratos públicos durante la pandemia de covid-19": False,
}


def main() -> int:
    fails = [f"{name!r}: got {is_boilerplate_legalcase(name)}, want {want}"
             for name, want in FRONTIER.items()
             if is_boilerplate_legalcase(name) != want]
    if fails:
        print("FRONTIER FAILURES:")
        for f in fails:
            print("  " + f)
        print(f"\n{len(fails)} frontier case(s) regressed.")
        return 1
    print(f"frontier: ALL {len(FRONTIER)} PASS")

    facts = Path(__file__).resolve().parents[1] / "data" / "facts"
    names: collections.Counter = collections.Counter()
    for f in sorted(facts.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for e in rec.get("entities", []):
                if e.get("type") == "LegalCase":
                    names[e.get("name", "")] += 1
    boiler = {n: c for n, c in names.items() if is_boilerplate_legalcase(n)}
    keep = {n: c for n, c in names.items() if not is_boilerplate_legalcase(n)}
    print(f"facts cache ({sum(names.values())} LegalCase mentions, {len(names)} distinct):")
    print(f"  DROP {len(boiler)} distinct / {sum(boiler.values())} mentions")
    print(f"  KEEP {len(keep)} distinct / {sum(keep.values())} mentions")
    which = "--drops" in sys.argv and boiler or "--keeps" in sys.argv and keep or {}
    for n, c in sorted(which.items(), key=lambda x: -x[1]):
        print(f"    {c:3} {n!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
