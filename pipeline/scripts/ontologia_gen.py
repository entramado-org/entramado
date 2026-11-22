"""Render the artifacts derived from the controlled vocabulary (spec 069).

    python pipeline/scripts/ontologia_gen.py           # write them
    python pipeline/scripts/ontologia_gen.py --check   # exit 1 if any is stale (CI)
    python3 pipeline/scripts/ontologia_gen.py --fichas tipos_relacion --extraccion   # term cards
                                                       # for a prompt (Claude workflows load them with Bash)

Today: `web/lib/ontologia.generated.ts` (term unions + ranks for the web, `as const` so the
label maps can be typed exhaustively) and every Jev question file `pipeline/data/jev/*.json`, rendered
from its `*.tmpl.json` (the criteria take their definitions from the ontology). Stdlib + PyYAML only, like `src/ontologia.py`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))

from src.ontologia import ontologia  # noqa: E402

WEB_TS = REPO / "web" / "lib" / "ontologia.generated.ts"


def _arr(name: str, tipo: str, values) -> str:
    return (f"export const {name} = {json.dumps(list(values), ensure_ascii=False)} as const;\n"
            f"export type {tipo} = (typeof {name})[number];\n")


def _rec(name: str, tipo: str, mapping: dict) -> str:
    return f"export const {name}: Record<{tipo}, number> = {json.dumps(mapping, ensure_ascii=False)};\n"


def web_ts() -> str:
    o = ontologia()
    return "".join([
        "// GENERADO por pipeline/scripts/ontologia_gen.py desde pipeline/data/ontologia/ontologia.yaml.\n",
        "// No lo edites: cambia la ontología y regenera (`make ontologia`). CI falla si está desfasado.\n",
        f"export const ONTOLOGIA_VERSION = {o.version};\n\n",
        "// Tipos de nodo que existen en el grafo (MoneyAmount nunca es nodo).\n",
        _arr("TIPOS_ENTIDAD", "TipoEntidad", o.tipos_entidad_extraibles), "\n",
        _arr("TIPOS_RELACION", "TipoRelacion", o.tipos_relacion), "\n",
        "// Orden = escalera procesal del fichero; ESTADO_RANGO = gravedad para elegir el estado titular.\n",
        _arr("ESTADOS", "Estado", o.estados),
        _rec("ESTADO_RANGO", "Estado", o.rango_estado), "\n",
        "// Capas de procedencia (source_kind), de más a menos autoritativa.\n",
        _arr("CAPAS", "Capa", o.capas),
        _rec("CAPA_RANGO", "Capa", o.rango_capa),
    ])


JEV_DIR = REPO / "pipeline" / "data" / "jev"
_MARCA = re.compile(r"\{\{(escalera|definiciones:[a-z_]+(?::[\w,]+)?)\}\}")


def render_plantilla(text: str) -> str:
    """Fill a Jev question template: `{{escalera}}` and `{{definiciones:SECCION[:t1,t2]}}`."""
    o = ontologia()

    def sub(m):
        key = m.group(1)
        if key == "escalera":
            return " < ".join(o.escalera)
        _, seccion, *terms = key.split(":")
        return o.definiciones(seccion, terms[0].split(",") if terms else None)
    out = _MARCA.sub(sub, text)
    if "{{" in out:
        raise ValueError(f"marcador de plantilla desconocido: {out[out.index('{{'):][:60]!r}")
    return out


def _jev(tmpl: Path):
    def render() -> str:
        data = json.loads(render_plantilla(tmpl.read_text(encoding="utf-8")))
        return json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    return render


ARTIFACTS = {WEB_TS: web_ts,
             **{t.with_name(t.name.replace(".tmpl.json", ".json")): _jev(t)
                for t in sorted(JEV_DIR.glob("*.tmpl.json"))}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="falla si algún artefacto está desfasado")
    ap.add_argument("--fichas", metavar="SECCION", help="imprime las fichas de una sección para un prompt")
    ap.add_argument("--extraccion", action="store_true", help="con --fichas tipos_relacion: sin los solo_loader")
    args = ap.parse_args()
    if args.fichas:
        o = ontologia()
        terms = o.tipos_extraibles if (args.extraccion and args.fichas == "tipos_relacion") else None
        print(f"Vocabulario controlado v{o.version} ({args.fichas}):")
        print(o.fichas_prompt(args.fichas, terms))
        return 0
    stale = []
    for path, render in ARTIFACTS.items():
        new = render()
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != new:
                stale.append(path.relative_to(REPO))
        else:
            path.write_text(new, encoding="utf-8")
            print(f"escrito {path.relative_to(REPO)}")
    if stale:
        print("desfasados (ejecuta `make ontologia`):", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
