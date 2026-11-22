#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Auditoría OFFLINE de la distribución de cabeceras (medios) en TODA la capa de prensa.

Sin Neo4j, sin red: solo lee YAML del repo. Recorre las tres fuentes editoriales que
citan medios de prensa como fuente:

  a. pipeline/data/ultima_hora/feed.yaml         (items:,     campo `outlet`)  — feed editorial
  b. pipeline/data/ultima_hora/ultima_hora.yaml   (relations:, campo `outlet`) — overlay de grafo
                                                    (source_kind='ultima_hora')
  c. pipeline/data/press/*.yaml (todos)           (relations:, campo `outlet`) — capa de grafo
                                                    (source_kind='prensa')

y cuenta cuántos ítems/relaciones cita cada cabecera, por capa y combinado.

Atribución «Origen (vía Medio)»: algunos `outlet` del feed vienen como «EFE (vía Infobae)» —
el origen real de la noticia (agencia o medio con la exclusiva) republicado por otro medio
(convención documentada en las instrucciones de edición del propio feed.yaml). Se separa con
la regex `^(?P<origen>.+?)\\s*\\(vía\\s+(?P<via>.+?)\\)$` en dos dimensiones: «medio de
origen» (el origen si hay vía, si no el outlet entero) y «medio de lectura» (el vía si
existe, si no el outlet entero). Ese patrón hoy solo aparece en el feed (no en el overlay ni
en la capa prensa), así que el bloque origen/lectura se calcula solo ahí. Se clasifican como
agencia los orígenes {EFE, Europa Press, Servimedia, Reuters, AFP}.

Decisión de diseño: el enunciado pide contar también el "dominio de url" del feed. El
`outlet` es el campo obligatorio y lo que puebla las tablas de cabeceras; el dominio de la
URL se usa solo como dato de diagnóstico en la sección «Avisos» cuando un ítem no tiene
`outlet` (hoy 0 casos en el feed) — no abre una tabla propia porque el enunciado de "Salida
por defecto" no la pide.

Determinista: mismo input -> mismo output byte a byte (sin timestamps, orden de ficheros vía
`sorted(glob(...))`, tablas ordenadas por count desc y nombre asc).

Uso:
  uv run pipeline/scripts/press_outlet_audit.py            # tablas markdown por stdout
  uv run pipeline/scripts/press_outlet_audit.py --json      # mismas estructuras en JSON
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml

SIN_OUTLET = "(sin outlet)"
AGENCIES = {"EFE", "Europa Press", "Servimedia", "Reuters", "AFP"}
VIA_RE = re.compile(r"^(?P<origen>.+?)\s*\(vía\s+(?P<via>.+?)\)$")


def find_repo_root() -> Path:
    """Busca hacia arriba (desde este fichero y desde cwd) el directorio que contenga
    `pipeline/data` — funciona tanto ejecutado desde la raíz del repo como desde pipeline/."""
    starts = [Path(__file__).resolve(), Path.cwd().resolve()]
    seen: set[Path] = set()
    for start in starts:
        for candidate in (start, *start.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "pipeline" / "data").is_dir():
                return candidate
    sys.exit("no encuentro la raíz del repo (ningún ancestro contiene pipeline/data)")


def parse_via(outlet: str) -> tuple[str, str]:
    """«EFE (vía Infobae)» -> ("EFE", "Infobae"); sin patrón -> (outlet, outlet)."""
    m = VIA_RE.match(outlet.strip())
    if m:
        return m.group("origen").strip(), m.group("via").strip()
    return outlet, outlet


def domain_of(url: str | None) -> str:
    if not url:
        return "(sin url)"
    netloc = urlsplit(url).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or "(sin url)"


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- recolección por capa

def collect_feed(root: Path) -> dict:
    path = root / "pipeline" / "data" / "ultima_hora" / "feed.yaml"
    items = load_yaml(path).get("items") or []
    counter: Counter = Counter()
    sin_outlet = 0
    sin_outlet_domains: list[str] = []
    origenes: Counter = Counter()
    lecturas: Counter = Counter()
    agencia_items = 0
    for it in items:
        outlet = (it.get("outlet") or "").strip()
        if not outlet:
            sin_outlet += 1
            sin_outlet_domains.append(domain_of(it.get("url")))
            counter[SIN_OUTLET] += 1
            origenes[SIN_OUTLET] += 1
            lecturas[SIN_OUTLET] += 1
            continue
        counter[outlet] += 1
        origen, via = parse_via(outlet)
        origenes[origen] += 1
        lecturas[via] += 1
        if origen in AGENCIES:
            agencia_items += 1
    return {
        "label": "feed última hora",
        "path": str(path.relative_to(root)),
        "total": len(items),
        "counter": counter,
        "sin_outlet": sin_outlet,
        "sin_outlet_domains": sin_outlet_domains,
        "origenes": origenes,
        "lecturas": lecturas,
        "agencia_items": agencia_items,
    }


def collect_relations(root: Path, path: Path, label: str) -> dict:
    relations = load_yaml(path).get("relations") or []
    counter: Counter = Counter()
    sin_outlet = 0
    for rel in relations:
        outlet = (rel.get("outlet") or "").strip() if isinstance(rel, dict) else ""
        if not outlet:
            sin_outlet += 1
            counter[SIN_OUTLET] += 1
            continue
        counter[outlet] += 1
    return {
        "label": label,
        "path": str(path.relative_to(root)),
        "total": len(relations),
        "counter": counter,
        "sin_outlet": sin_outlet,
    }


def collect_press(root: Path) -> dict:
    dir_path = root / "pipeline" / "data" / "press"
    total = 0
    counter: Counter = Counter()
    sin_outlet = 0
    files = sorted([*dir_path.glob("*.yaml"), *dir_path.glob("*.yml")])
    for f in files:
        relations = load_yaml(f).get("relations") or []
        total += len(relations)
        for rel in relations:
            outlet = (rel.get("outlet") or "").strip() if isinstance(rel, dict) else ""
            if not outlet:
                sin_outlet += 1
                counter[SIN_OUTLET] += 1
                continue
            counter[outlet] += 1
    return {
        "label": "capa prensa",
        "path": str(dir_path.relative_to(root)) + "/*.yaml",
        "files": [str(f.relative_to(root)) for f in files],
        "total": total,
        "counter": counter,
        "sin_outlet": sin_outlet,
    }


# ---------------------------------------------------------------- render

def sorted_rows(counter: Counter) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def render_table(counter: Counter, col1: str = "cabecera", col2: str = "ítems") -> str:
    rows = sorted_rows(counter)
    lines = [f"| {col1} | {col2} |", "|---|---|"]
    for name, n in rows:
        lines.append(f"| {name} | {n} |")
    return "\n".join(lines)


def render_markdown(layers: list[dict], combinada: Counter, feed: dict) -> str:
    out: list[str] = []
    out.append("# Auditoría de cabeceras — capa de prensa\n")
    for layer in layers:
        out.append(f"## {layer['label']} (`{layer['path']}`)\n")
        out.append(render_table(layer["counter"]))
        out.append("")
        out.append(f"Total ítems: {layer['total']} — cabeceras distintas: "
                    f"{len([k for k in layer['counter'] if k != SIN_OUTLET])}\n")
    out.append("## Combinada (las tres capas)\n")
    out.append(render_table(combinada))
    out.append("")
    out.append(f"Total ítems: {sum(combinada.values())} — cabeceras distintas: "
                f"{len([k for k in combinada if k != SIN_OUTLET])}\n")

    out.append("## Medios de origen vs medios de lectura (solo feed última hora)\n")
    out.append("### Medio de origen\n")
    out.append(render_table(feed["origenes"], col1="origen"))
    out.append("")
    out.append("### Medio de lectura\n")
    out.append(render_table(feed["lecturas"], col1="lectura"))
    out.append("")
    out.append(f"Ítems del feed con atribución de agencia ({', '.join(sorted(AGENCIES))}): "
                f"{feed['agencia_items']}\n")

    out.append("## Avisos\n")
    any_warning = False
    for layer in layers:
        if layer["sin_outlet"]:
            any_warning = True
            out.append(f"- {layer['label']}: {layer['sin_outlet']} ítem(s) sin `outlet`.")
    if feed["sin_outlet_domains"]:
        out.append("  dominios de esos ítems (feed): "
                    + ", ".join(feed["sin_outlet_domains"]))
    if not any_warning:
        out.append("- Ninguno: todas las capas tienen `outlet` en el 100% de sus ítems.")
    return "\n".join(out) + "\n"


def build_json(layers: list[dict], combinada: Counter, feed: dict) -> dict:
    def layer_json(layer: dict) -> dict:
        d = {
            "path": layer["path"],
            "total": layer["total"],
            "outlets_distintos": len([k for k in layer["counter"] if k != SIN_OUTLET]),
            "conteo": dict(layer["counter"]),
            "sin_outlet": layer["sin_outlet"],
        }
        if "files" in layer:
            d["files"] = layer["files"]
        return d

    return {
        "por_capa": {
            "feed_ultima_hora": layer_json(layers[0]),
            "overlay_ultima_hora": layer_json(layers[1]),
            "capa_prensa": layer_json(layers[2]),
        },
        "combinada": {
            "total": sum(combinada.values()),
            "outlets_distintos": len([k for k in combinada if k != SIN_OUTLET]),
            "conteo": dict(combinada),
        },
        "feed_origen": dict(feed["origenes"]),
        "feed_lectura": dict(feed["lecturas"]),
        "agencias": {
            "definicion": sorted(AGENCIES),
            "items_con_atribucion_de_agencia": feed["agencia_items"],
        },
        "avisos": {
            "feed_ultima_hora_sin_outlet": layers[0]["sin_outlet"],
            "overlay_ultima_hora_sin_outlet": layers[1]["sin_outlet"],
            "capa_prensa_sin_outlet": layers[2]["sin_outlet"],
            "dominios_sin_outlet_feed": feed["sin_outlet_domains"],
        },
    }


def main() -> None:
    as_json = "--json" in sys.argv[1:]
    root = find_repo_root()

    feed = collect_feed(root)
    overlay = collect_relations(
        root, root / "pipeline" / "data" / "ultima_hora" / "ultima_hora.yaml",
        "overlay última hora")
    press = collect_press(root)
    layers = [feed, overlay, press]

    combinada: Counter = Counter()
    for layer in layers:
        combinada.update(layer["counter"])

    if as_json:
        print(json.dumps(build_json(layers, combinada, feed),
                          ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(layers, combinada, feed))


if __name__ == "__main__":
    main()
