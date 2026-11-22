#!/usr/bin/env python3
"""BOE recon sweep (spec 048, Fase 1, READ-ONLY — cero escrituras al grafo).

Barrido de la sección II.A del BOE («Nombramientos, situaciones e incidencias»)
2004-01-01 → hoy vía la API de datos abiertos de sumarios, y cruce gated contra la
lista objetivo de personas del grafo (exportada por `_boe_target_list.sh`).

Como el resto de `pipeline/scripts/_*.py` de recon (BDNS/PLACSP) va force-added:
el patrón del .gitignore lo excluye por defecto. Los crudos viven gitignored bajo
`pipeline/data/boe/_*`; lo único versionado del recon es `matches.candidate.yaml`
(curado a mano a partir de `_matches_raw.json`).

Subcomandos (reanudables):
  download [--since 2004-01-01] [--until hoy] [--threads 6]
      Sumario JSON por fecha → extrae los items de sección 2A a
      `_index/items-<año>.jsonl` (+ `_index/done_dates.txt` para reanudar).
  stats
      Conteos del índice (días cubiertos, items, por año).
  match [--min-degree 0]
      Cruce por nombre normalizado (canónico + alias multi-token) contra los
      títulos indexados → `_matches_raw.json` para curación manual.
      El gate anti-homónimo (≥2 señales) es HUMANO: esto solo genera candidatos.
  fetch-xml [--ids BOE-A-… …] [--ids-file ruta] [--match] [--force]  (spec 050)
      Descarga el XML de disposición (caché reanudable `_index/xml/<id>.xml`),
      parsea metadatos + cuerpo y extrae actos nombrar/promover/cesar (persona,
      cargo, párrafo original) → `_xml_extract.json`. Con --match cruza cada
      persona extraída contra los targets del grafo (los RD colectivos jun-2018
      nombran 17 ministros sin nombres en el título — solo el cuerpo los trae).
      INFORME para curación humana; jamás escribe boe.yaml.
  resolve-pre [--window-start 2004-01-01] [--max-candidates 8]  (spec 050)
      Lee las etapas `|pre` (cese sin nombramiento) de boe.yaml y propone
      candidatos de nombramiento en ventana: filtro de títulos por cargo +
      fallback RD colectivo, verificación SIEMPRE por cuerpo XML →
      `_pre_candidates.json` (etiqueta `sin_candidatos_en_ventana` = pre-2004
      presumible). Necesita PyYAML: correr `cd pipeline && uv run python
      scripts/_boe_recon_sweep.py resolve-pre`.
  match --export-vigia  (spec 051)
      Además del match, escribe `vigia_targets.json` (VERSIONADO: id/name/claves
      post-_key_ok, ligero) — el insumo de la rutina cloud recordatorio
      (`boe_vigia_check.py`); regenerarlo en cada ciclo de curación.
  novedades [--since auto] [--min-degree 0]  (spec 051, refresco)
      Descarga incremental (desde max(done_dates)+1) → match → resta lo ya
      cubierto (boe_ids de boe.yaml + matched/por_verificar de la semilla;
      entity_ids rechazados/sin_registro) → informe de curación
      `_novedades.md` con sugerencia mecánica por hit. Necesita PyYAML (uv run).

Uso: python3 pipeline/scripts/_boe_recon_sweep.py <subcomando> [opciones]
"""

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "pipeline" / "data" / "boe"
INDEX = DATA / "_index"
API = "https://www.boe.es/datosabiertos/api/boe/sumario/{yyyymmdd}"
UA = "sm-recon/048 (contacto: entramado.org; uso: investigación, volumen bajo)"


# ---------------------------------------------------------------- descarga

def _fetch(date: dt.date, retries: int = 3) -> tuple[str, list[dict] | None]:
    """Devuelve (yyyymmdd, items-2A) — items=None si ese día no hay BOE."""
    ymd = date.strftime("%Y%m%d")
    req = urllib.request.Request(
        API.format(yyyymmdd=ymd),
        headers={"Accept": "application/json", "User-Agent": UA},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:  # día sin BOE (domingos y festivos señalados)
                return ymd, None
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    try:
        doc = json.loads(body)
    except json.JSONDecodeError:
        # los días sin BOE responden XML con <code>404</code> aunque pidas JSON
        if b"<code>404</code>" in body:
            return ymd, None
        raise
    if str(doc.get("status", {}).get("code")) == "404":
        return ymd, None

    def aslist(x):
        return x if isinstance(x, list) else [x] if x else []

    items = []
    for dia in aslist(doc["data"]["sumario"]["diario"]):
        for sec in aslist(dia.get("seccion")):
            if sec.get("codigo") != "2A":
                continue
            for dep in aslist(sec.get("departamento")):
                raw = list(aslist(dep.get("item")))
                for ep in aslist(dep.get("epigrafe")):
                    raw += aslist(ep.get("item"))
                for it in raw:
                    items.append({
                        "fecha": date.isoformat(),
                        "boe_id": it.get("identificador"),
                        "dep": dep.get("nombre"),
                        "titulo": it.get("titulo"),
                        "url_html": it.get("url_html"),
                        "url_xml": it.get("url_xml"),
                    })
    return ymd, items


def cmd_download(args) -> None:
    INDEX.mkdir(parents=True, exist_ok=True)
    done_path = INDEX / "done_dates.txt"
    done = set(done_path.read_text().split()) if done_path.exists() else set()
    since = dt.date.fromisoformat(args.since)
    until = dt.date.fromisoformat(args.until) if args.until else dt.date.today()
    dates = [since + dt.timedelta(days=i) for i in range((until - since).days + 1)]
    todo = [d for d in dates if d.strftime("%Y%m%d") not in done]
    print(f"ventana {since} → {until}: {len(dates)} días, {len(todo)} pendientes")

    n_ok = n_empty = n_items = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.threads) as pool, \
            open(done_path, "a") as done_f:
        futures = {pool.submit(_fetch, d): d for d in todo}
        year_files: dict[int, object] = {}
        try:
            for fut in as_completed(futures):
                d = futures[fut]
                try:
                    ymd, items = fut.result()
                except Exception as e:  # noqa: BLE001 — se reintenta en otra pasada
                    print(f"  ERROR {d}: {e}", file=sys.stderr)
                    continue
                if items is None:
                    n_empty += 1
                else:
                    n_ok += 1
                    n_items += len(items)
                    yf = year_files.get(d.year)
                    if yf is None:
                        yf = open(INDEX / f"items-{d.year}.jsonl", "a")
                        year_files[d.year] = yf
                    for it in items:
                        yf.write(json.dumps(it, ensure_ascii=False) + "\n")
                done_f.write(ymd + "\n")
                total = n_ok + n_empty
                if total % 250 == 0:
                    rate = total / (time.time() - t0)
                    print(f"  {total}/{len(todo)} días ({rate:.1f}/s) — "
                          f"{n_items} items 2A", flush=True)
        finally:
            for yf in year_files.values():
                yf.close()
    print(f"HECHO: {n_ok} días con BOE, {n_empty} sin BOE, {n_items} items 2A nuevos")


# ---------------------------------------------------------------- stats

def _iter_index():
    for path in sorted(INDEX.glob("items-*.jsonl")):
        with open(path) as fh:
            for line in fh:
                yield json.loads(line)


def cmd_stats(_args) -> None:
    done_path = INDEX / "done_dates.txt"
    done = set(done_path.read_text().split()) if done_path.exists() else set()
    per_year: dict[str, int] = {}
    dates = set()
    total = 0
    for it in _iter_index():
        total += 1
        per_year[it["fecha"][:4]] = per_year.get(it["fecha"][:4], 0) + 1
        dates.add(it["fecha"])
    print(f"días procesados: {len(done)} · días con items 2A: {len(dates)} · "
          f"items: {total}")
    for y in sorted(per_year):
        print(f"  {y}: {per_year[y]}")


# ---------------------------------------------------------------- match

_STOP = {"don", "dona", "de", "del", "la", "las", "los", "y", "e", "a",
         "señor", "senor", "señora", "senora", "d", "dª"}

# claves compuestas SOLO de vocabulario de cargo = nodos genéricos del grafo
# («Person:secretario-de-estado») que casan con cualquier título — fuera
# (gotcha PLACSP: los nodos genéricos ensucian el containment).
_CARGO_TOKENS = {
    "secretario", "secretaria", "estado", "presidente", "presidenta",
    "gobierno", "subdelegado", "subdelegada", "delegado", "delegada",
    "director", "directora", "general", "ministro", "ministra", "consejero",
    "consejera", "fiscal", "magistrado", "magistrada", "vocal", "jefe",
    "jefa", "vicepresidente", "vicepresidenta", "subsecretario",
    "subsecretaria", "letrado", "letrada", "interventor", "interventora",
    # carteras/dominios ministeriales (nodos tipo «ministra de hacienda»)
    "hacienda", "transportes", "justicia", "interior", "defensa", "industria",
    "agricultura", "cultura", "sanidad", "educacion", "trabajo", "economia",
    "presidencia", "exteriores", "transicion", "ecologica", "energia",
    "turismo", "ciencia", "universidades", "igualdad", "consumo", "inclusion",
    "migraciones", "seguridad", "social", "territorial", "digital", "agenda",
    "urbana", "movilidad", "fomento", "funcion", "publica",
}

# claves compuestas SOLO de nombres de pila (alias tipo «Juan Carlos», «José
# Manuel») casan con miles de homónimos — fuera del match automático.
_GIVEN_NAMES = {
    "jose", "juan", "maria", "luis", "carlos", "miguel", "angel", "antonio",
    "manuel", "ignacio", "rosario", "pedro", "ana", "javier", "jesus",
    "francisco", "fernando", "rafael", "pablo", "david", "daniel", "alberto",
    "alejandro", "victor", "sergio", "jorge", "ramon", "vicente", "andres",
    "diego", "santiago", "emilio", "enrique", "felipe", "gonzalo", "isabel",
    "carmen", "pilar", "teresa", "dolores", "begona", "mercedes", "cristina",
    "elena", "laura", "marta", "paloma", "raquel", "rosa", "sara", "silvia",
    "susana", "beatriz", "monica", "nuria", "patricia", "eduardo", "ricardo",
    "roberto", "ruben", "salvador", "tomas", "valentin", "cesar", "oscar",
    "hector", "ivan", "adrian", "alvaro", "gabriel", "gerardo", "guillermo",
    "gustavo", "jaime", "joaquin", "jordi", "julian", "julio", "lorenzo",
    "marcos", "mariano", "martin", "mateo", "nicolas", "raul", "rodrigo",
    "samuel", "sebastian", "simon", "esther", "eva", "gloria", "irene",
    "julia", "lucia", "margarita", "mar", "nieves", "olga", "sonia",
    "veronica", "victoria", "yolanda", "concepcion", "inmaculada",
    "montserrat", "rocio", "soledad", "amparo", "aurora", "consuelo",
}


# apellidos de alta frecuencia: un par de 2 tokens hecho SOLO de nombres/
# apellidos comunes («manuel martinez», «gomez fernandez») casa con decenas de
# homónimos sin que exista gate posible → fuera del match automático (las
# personas reales conservan sus claves de nombre completo, 3+ tokens).
_COMMON_SURNAMES = {
    "garcia", "gonzalez", "fernandez", "rodriguez", "lopez", "martinez",
    "sanchez", "perez", "gomez", "martin", "jimenez", "ruiz", "hernandez",
    "diaz", "moreno", "alvarez", "munoz", "romero", "alonso", "gutierrez",
    "navarro", "torres", "dominguez", "vazquez", "ramos", "gil", "ramirez",
    "serrano", "blanco", "suarez", "molina", "morales", "ortega", "delgado",
    "castro", "ortiz", "rubio", "marin", "sanz", "iglesias", "nunez",
    "medina", "garrido", "cortes", "castillo", "santos", "lozano", "guerrero",
    "cano", "prieto", "mendez", "cruz", "calvo", "gallego", "vidal", "leon",
    "marquez", "herrera", "pena", "flores", "cabrera", "campos", "vega",
    "fuentes", "carrasco", "diez", "caballero", "reyes", "nieto", "aguilar",
    "pascual", "santana", "herrero", "montero", "lorenzo", "hidalgo",
    "gimenez", "ibanez", "ferrer", "duran", "santiago", "benitez", "vargas",
}


def _key_ok(key: str) -> bool:
    toks = key.split()
    if len(toks) < 2:
        return False
    if all(t in _CARGO_TOKENS for t in toks):
        return False
    if all(t in _GIVEN_NAMES for t in toks):
        return False
    if len(toks) == 2 and all(
            t in _GIVEN_NAMES or t in _COMMON_SURNAMES for t in toks):
        return False
    return True


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-zñ ]+", " ", s)
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks)


def _load_targets(min_degree: int) -> list[dict]:
    """Parsea el export de cypher-shell --format plain (CSV; aliases unidos
    con ' ~ ' por el .sh — puede traer saltos de línea embebidos)."""
    src = DATA / "_target_persons.tsv"
    # cypher-shell plain escapa comillas internas como \" (no al estilo CSV "")
    rows = list(csv.reader(open(src), skipinitialspace=True, escapechar="\\"))
    header, rows = rows[0], rows[1:]
    assert [h.strip() for h in header][:2] == ["id", "name"], header
    # Court-anonymisation pseudonyms (pseudonyms.yaml) are never BOE targets: a first-name
    # match would tie a civil appointment to an anonymised person (spec 059 / 063, 2026-10-04).
    pseudo = set(re.findall(r"^(?:- id|  rename_from): (\S+)", (REPO / "pipeline/data/context/pseudonyms.yaml").read_text(), re.M))
    out = []
    for r in rows:
        if len(r) != 5:
            continue
        pid, name, degree, role, aliases_raw = r[0], r[1], int(r[2]), r[3], r[4]
        if degree < min_degree or pid.strip() in pseudo:
            continue
        variants = {name} | {a.strip() for a in aliases_raw.split(" ~ ") if a.strip()}
        # Spec 071 AC199: un referente «sin identificar» (lo dice su rol) o sin ningún nombre de dos palabras o más no
        # es un objetivo: su clave no identifica a nadie concreto, y emparejarla con un nombramiento lo identificaría.
        if "sin identificar" in _norm(role) or not any(len(v.split()) >= 2 for v in variants):
            continue
        keys = set()
        for v in variants:
            v = " ".join(v.split())  # colapsa saltos de línea embebidos
            n = _norm(v)
            if _key_ok(n):  # mono-tokens/cargos/solo-nombres-de-pila fuera
                keys.add(n)
        if keys:
            out.append({"id": pid, "name": name, "degree": degree,
                        "role": role, "keys": sorted(keys)})
    return out


def _export_vigia(targets: list[dict]) -> None:
    """Escribe el insumo VERSIONADO de la rutina cloud (spec 051): solo id/name/keys.
    Derivado del grafo vía _target_persons.tsv → puede quedar rancio entre ciclos de
    curación (falsos negativos temporales del recordatorio, nunca escrituras)."""
    out = DATA / "vigia_targets.json"
    with open(out, "w") as fh:
        json.dump(
            {"generado_desde": "_target_persons.tsv (export read-only del grafo)",
             "targets": [{"id": t["id"], "name": t["name"], "keys": t["keys"]}
                         for t in targets]},
            fh, ensure_ascii=False, indent=1)
    print(f"vigia_targets: {len(targets)} personas → {out}")


def cmd_match(args) -> None:
    targets = _load_targets(args.min_degree)
    if getattr(args, "export_vigia", False):
        _export_vigia(targets)
    print(f"objetivo: {len(targets)} personas (min_degree={args.min_degree})")
    # prefiltro por primer token → lista de (clave, target)
    by_first: dict[str, list[tuple[str, dict]]] = {}
    for t in targets:
        for k in t["keys"]:
            by_first.setdefault(k.split()[0], []).append((k, t))

    hits: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()  # (person, boe_id) — varias claves
    n_items = 0                         # pueden casar la misma disposición
    for it in _iter_index():
        n_items += 1
        nt = _norm(it["titulo"] or "")
        padded = f" {nt} "
        seen_tokens = set(nt.split())
        for first, pairs in by_first.items():
            if first not in seen_tokens:
                continue
            for key, t in pairs:
                if f" {key} " in padded:
                    if (t["id"], it["boe_id"]) in seen:
                        continue
                    seen.add((t["id"], it["boe_id"]))
                    h = hits.setdefault(t["id"], {
                        "name": t["name"], "degree": t["degree"],
                        "role": t["role"][:200], "disposiciones": []})
                    h["disposiciones"].append({
                        "matched_key": key,
                        "fecha": it["fecha"],
                        "boe_id": it["boe_id"],
                        "dep": it["dep"],
                        "titulo": it["titulo"],
                        "url_html": it["url_html"],
                    })
    out = DATA / "_matches_raw.json"
    with open(out, "w") as fh:
        json.dump({"n_items_index": n_items, "n_targets": len(targets),
                   "hits": hits}, fh, ensure_ascii=False, indent=1)
    n_disp = sum(len(h["disposiciones"]) for h in hits.values())
    print(f"items índice: {n_items} · personas con hits: {len(hits)} · "
          f"disposiciones: {n_disp} → {out}")


# ---------------------------------------------------------------- novedades (spec 051)

def _covered() -> tuple[set, set, dict]:
    """Cobertura actual desde los YAML COMMITTEADOS: (boe_ids cubiertos,
    entity_ids descartados, {entity_id: trayectoria de boe.yaml}). Necesita PyYAML."""
    try:
        import yaml  # noqa: PLC0415
    except ModuleNotFoundError:
        sys.exit("novedades necesita PyYAML: "
                 "cd pipeline && uv run python scripts/_boe_recon_sweep.py novedades")
    ids: set[str] = set()
    trays: dict[str, dict] = {}
    doc = yaml.safe_load((DATA / "boe.yaml").read_text(encoding="utf-8")) or {}
    for t in doc.get("trayectorias") or []:
        trays[t["entity_id"]] = t
        for c in t.get("cargos") or []:
            for k in ("boe_id_nombramiento", "boe_id_cese"):
                if c.get(k):
                    ids.add(c[k])
        for i in t.get("incidencias") or []:
            if i.get("boe_id"):
                ids.add(i["boe_id"])
    mc = yaml.safe_load((DATA / "matches.candidate.yaml").read_text(encoding="utf-8")) or {}
    for section in ("matched", "por_verificar"):
        for m in mc.get(section) or []:
            for d in m.get("disposiciones") or []:
                if d.get("boe_id"):
                    ids.add(d["boe_id"])
    descartados = {m["entity_id"] for section in ("rechazados", "sin_registro_destacados")
                   for m in (mc.get(section) or []) if m.get("entity_id")}
    return ids, descartados, trays


def _sugerencia(entity_id: str, titulo: str, trays: dict) -> str:
    """Sugerencia mecánica para el informe de curación (no escribe nada)."""
    t = trays.get(entity_id)
    es_cese = bool(_RE_CESE.search(titulo))
    if t is None:
        return "posible TRAYECTORIA NUEVA (persona sin bloque en boe.yaml)"
    if es_cese:
        abiertas = [c["boe_key"] for c in t.get("cargos") or []
                    if not c.get("fecha_cese")]
        if abiertas:
            nt = f" {_norm(titulo)} "
            candidatas = [k for k in abiertas
                          if f" {_norm(k.split('|')[0].replace('-', ' '))} " in nt] or abiertas
            return f"posible CIERRE de etapa abierta: {', '.join(candidatas)}"
        return "cese sin etapa abierta que casar — revisar a mano"
    return "posible ETAPA NUEVA en trayectoria existente"


def cmd_novedades(args) -> None:
    covered_ids, descartados, trays = _covered()
    done_path = INDEX / "done_dates.txt"
    done = sorted(done_path.read_text().split()) if done_path.exists() else []
    if args.since == "auto":
        if not done:
            sys.exit("índice vacío: corre `download` completo primero")
        last = max(done)  # max REAL: los threads escriben desordenado
        since = (dt.date(int(last[:4]), int(last[4:6]), int(last[6:8]))
                 + dt.timedelta(days=1)).isoformat()
    else:
        since = args.since
    today = dt.date.today().isoformat()
    if since > today:
        print("0 novedades (índice ya al día, nada que descargar)")
        return
    print(f"descarga incremental {since} → {today}…")
    dl = argparse.Namespace(since=since, until=None, threads=args.threads)
    cmd_download(dl)

    # match SOLO sobre los items nuevos (fecha >= since)
    targets = _load_targets(args.min_degree)
    by_first: dict[str, list[tuple[str, dict]]] = {}
    for t in targets:
        for k in t["keys"]:
            by_first.setdefault(k.split()[0], []).append((k, t))
    hits = []
    seen: set[tuple[str, str]] = set()
    for it in _iter_index():
        if it["fecha"] < since:
            continue
        nt = _norm(it["titulo"] or "")
        padded = f" {nt} "
        toks = set(nt.split())
        for first, pairs in by_first.items():
            if first not in toks:
                continue
            for key, t in pairs:
                if f" {key} " in padded and (t["id"], it["boe_id"]) not in seen:
                    seen.add((t["id"], it["boe_id"]))
                    hits.append((t, it))

    nuevas = [(t, it) for t, it in hits
              if t["id"] not in descartados and it["boe_id"] not in covered_ids]
    out = DATA / "_novedades.md"
    with open(out, "w") as fh:
        fh.write(f"# Novedades BOE II.A — {since} → {today}\n\n")
        fh.write(f"hits nuevos tras el diff de cobertura: **{len(nuevas)}** "
                 f"(matcheados {len(hits)}, cubiertos/descartados {len(hits) - len(nuevas)})\n\n")
        for t, it in sorted(nuevas, key=lambda x: x[1]["fecha"], reverse=True):
            fh.write(f"## {t['name']} (`{t['id']}`)\n"
                     f"- {it['fecha']} · [{it['boe_id']}]({it['url_html']})\n"
                     f"- {it['titulo']}\n"
                     f"- **Sugerencia**: {_sugerencia(t['id'], it['titulo'] or '', trays)}\n\n")
        if not nuevas:
            fh.write("Sin novedades: todo lo matcheado ya está cubierto o descartado.\n")
    print(f"{len(nuevas)} novedades → {out}")


# ---------------------------------------------------------------- xml (spec 050)

XML_DIR = INDEX / "xml"
XML_URL = "https://www.boe.es/diario_boe/xml.php?id={boe_id}"

# Verbos de acto sobre el TÍTULO/párrafo normalizado. El orden importa: un
# párrafo de cese («… el cese de don N como Ministro…») también contiene
# vocabulario de cargo — se clasifica por el verbo, no por el cargo.
_RE_CESE = re.compile(r"\b(cese|cesa|cesar)\b", re.I)
_RE_NOMBRA = re.compile(r"\b(nombrar|nombra|nombran|nombramiento|promover|promueve|asciende|ascender)\b", re.I)
# Persona tras don/doña: corta en coma/punto/punto y coma o en « como ». Mayúscula
# inicial admitida («Don Francisco…» en las listas de los RD colectivos del CGPJ);
# el nombre en sí sigue anclado a inicial mayúscula (sin re.I global).
_RE_PERSONA = re.compile(
    r"\b(?:[Dd]on|[Dd]oña|D\.ª|D\.)\s+([A-ZÁÉÍÓÚÜÑ][^,.;:()]*?)(?=\s+como\s|[,.;:()]|$)")


def _fetch_xml(boe_id: str, force: bool = False) -> Path:
    """Descarga (o reutiliza de caché) el XML de una disposición."""
    XML_DIR.mkdir(parents=True, exist_ok=True)
    path = XML_DIR / f"{boe_id}.xml"
    if path.exists() and path.stat().st_size > 0 and not force:
        return path
    req = urllib.request.Request(
        XML_URL.format(boe_id=boe_id),
        headers={"Accept": "application/xml", "User-Agent": UA},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    path.write_bytes(body)
    return path


def _parse_disposicion(path: Path) -> dict:
    """Metadatos + párrafos del cuerpo de un XML de disposición del BOE."""
    try:
        # hardening XXE/billion-laughs si está instalado (uv run lo trae);
        # fallback stdlib aceptable: fuente única boe.es, script local de curación
        import defusedxml.ElementTree as ET  # noqa: PLC0415
    except ModuleNotFoundError:
        import xml.etree.ElementTree as ET  # noqa: PLC0415
    root = ET.parse(path).getroot()

    def txt(tag: str) -> str | None:
        el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else None

    def fecha_iso(tag: str) -> str | None:
        raw = txt(tag)
        if raw and len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        return raw

    parrafos = [" ".join(p.itertext()).strip()
                for p in root.findall(".//texto//p")]
    return {
        "boe_id": txt("identificador"),
        "titulo": txt("titulo"),
        "fecha_disposicion": fecha_iso("fecha_disposicion"),
        "fecha_publicacion": fecha_iso("fecha_publicacion"),
        "departamento": txt("departamento"),
        "rango": txt("rango"),
        "numero_oficial": txt("numero_oficial"),
        "parrafos": [p for p in parrafos if p],
    }


def _tipo(p: str) -> str | None:
    es_cese = bool(_RE_CESE.search(p))
    es_nombra = bool(_RE_NOMBRA.search(p))
    if es_cese and not es_nombra:
        return "cese"
    if es_nombra and not es_cese:
        return "nombramiento"
    if es_cese and es_nombra:
        return "mixto"
    return None


def _extract_actos(parrafos: list[str]) -> list[dict]:
    """Actos nombrar/promover/cesar de los párrafos (persona + cargo aproximado).

    Los RD COLECTIVOS ponen el verbo en un párrafo introductorio («… vengo en
    nombrar:») y cada acto en párrafos siguientes SIN verbo («Ministro de Fomento
    a don José Luis Ábalos Meco.») → el tipo del introductorio se hereda como
    contexto. Extracción por regex para INFORME de curación — el párrafo original
    viaja siempre; los formatos raros (acuerdos CGPJ, AEAT) se leen a mano."""
    actos = []
    contexto: str | None = None
    for p in parrafos:
        tipo = _tipo(p)
        personas = list(_RE_PERSONA.finditer(p))
        if tipo and not personas:
            contexto = tipo  # párrafo introductorio del colectivo
            continue
        if not personas:
            continue
        tipo = tipo or contexto
        if not tipo:
            continue
        for m in personas:
            nombre = " ".join(m.group(1).split())
            cargo = None
            if tipo == "cese":
                mc = re.search(r"\s+como\s+([^,.;]+)", p[m.end():])
                if mc:
                    cargo = " ".join(mc.group(1).split())
            else:
                # «[vengo en nombrar |se nombra ]CARGO a don/doña N» — el cargo es
                # el prefijo hasta « a don/doña», limpio de fórmulas de verbo
                mc = re.match(r"^(.*?)\s+a\s+(?:don|doña|D\.ª|D\.)\b", p)
                if mc:
                    cargo = re.sub(
                        r"^.*?(?:vengo en nombrar|se nombra|se promueve|nombramiento de|"
                        r"por el que se nombra|promover a la categoría de)\s*:?\s*",
                        "", mc.group(1), flags=re.I)
                    cargo = " ".join(cargo.split()) or None
            actos.append({"tipo": tipo, "nombre": nombre, "cargo": cargo, "parrafo": p})
    return actos


def cmd_fetch_xml(args) -> None:
    ids: list[str] = list(args.ids or [])
    if args.ids_file:
        ids += [l.strip() for l in Path(args.ids_file).read_text().split()
                if l.strip()]
    ids = sorted(dict.fromkeys(ids))
    if not ids:
        sys.exit("fetch-xml: pasa --ids o --ids-file")

    targets = _load_targets(0) if args.match else []
    out: dict[str, dict] = {}
    for boe_id in ids:
        try:
            meta = _parse_disposicion(_fetch_xml(boe_id, force=args.force))
        except Exception as e:  # noqa: BLE001 — informe, seguimos con el resto
            print(f"  ERROR {boe_id}: {e}", file=sys.stderr)
            out[boe_id] = {"error": str(e)}
            continue
        actos = _extract_actos(meta.pop("parrafos"))
        if targets:
            for a in actos:
                nn = _norm(a["nombre"])
                padded, toks = f" {nn} ", set(nn.split())
                hits = []
                for t in targets:
                    if any(f" {k} " in padded for k in t["keys"]):
                        hits.append({"entity_id": t["id"], "name": t["name"],
                                     "match_via": "contiguo"})
                    # el BOE intercala nombres registrales («Nadia MARÍA Calviño
                    # Santamaría») → fallback por subconjunto de tokens, marcado
                    elif any(set(k.split()) <= toks for k in t["keys"]):
                        hits.append({"entity_id": t["id"], "name": t["name"],
                                     "match_via": "tokens"})
                a["grafo_hits"] = hits
        out[boe_id] = {"meta": meta, "actos": actos}
        n_hits = sum(1 for a in actos if a.get("grafo_hits"))
        print(f"{boe_id}: {len(actos)} actos"
              + (f" · {n_hits} con hit en el grafo" if targets else ""))

    dst = DATA / "_xml_extract.json"
    with open(dst, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"→ {dst}")


# ---------------------------------------------------------------- resolve-pre (spec 050)

# títulos de disposición COLECTIVA (sin nombres): «… por el que se nombran
# Ministros del Gobierno», «… se nombran Vocales…» — el cuerpo trae los nombres.
_RE_COLECTIVO = re.compile(r"\bse nombran\b|\bnombramientos\b", re.I)


def _pre_stages() -> list[dict]:
    """Etapas `|pre` (cese sin nombramiento) del boe.yaml curado. Necesita PyYAML."""
    try:
        import yaml  # noqa: PLC0415 — solo este subcomando lo necesita
    except ModuleNotFoundError:
        sys.exit("resolve-pre necesita PyYAML: "
                 "cd pipeline && uv run python scripts/_boe_recon_sweep.py resolve-pre")
    doc = yaml.safe_load((DATA / "boe.yaml").read_text(encoding="utf-8")) or {}
    stages = []
    for t in doc.get("trayectorias") or []:
        for c in t.get("cargos") or []:
            if (c.get("boe_key") or "").endswith("|pre") and not c.get("fecha_nombramiento"):
                stages.append({
                    "entity_id": t["entity_id"],
                    "nombre_grafo": t.get("nombre_grafo"),
                    "boe_key": c["boe_key"],
                    "cargo": c.get("cargo"),
                    "organismo": c.get("organismo"),
                    "fecha_cese": c.get("fecha_cese"),
                })
    return stages


def cmd_resolve_pre(args) -> None:
    stages = _pre_stages()
    print(f"etapas |pre sin nombramiento: {len(stages)}")
    window_start = args.window_start

    # una pasada por el índice sirve para todas las etapas
    items = list(_iter_index())
    results = []
    for st in stages:
        cese = st["fecha_cese"] or "9999-12-31"
        cargo_key = _norm(st["cargo"] or "")
        # aquí NO aplica _key_ok: no barremos títulos a ciegas, verificamos el
        # cuerpo de candidatos ya filtrados por cargo → una clave 2-token común
        # («javier herrero») vale, marcada como débil para la curación
        n = _norm(st["nombre_grafo"] or "")
        persona_keys = {n} if len(n.split()) >= 2 else set()
        clave_debil = bool(persona_keys) and not _key_ok(n)
        if not persona_keys:
            results.append({**st, "estado": "sin_clave_de_persona", "candidatos": []})
            continue

        # pass A: título contiene el cargo + verbo de nombramiento, fecha < cese.
        # Fuera los nombramientos DE GABINETE de otro cargo («Director del
        # Gabinete del Ministro de X») salvo que la propia etapa sea de gabinete.
        def _title_ok(it) -> bool:
            nt = _norm(it["titulo"] or "")
            if f" {cargo_key} " not in f" {nt} ":
                return False
            if "gabinete" in nt.split() and "gabinete" not in cargo_key:
                return False
            return True
        cand_a = [it for it in items
                  if window_start <= it["fecha"] < cese
                  and cargo_key and _title_ok(it)
                  and _RE_NOMBRA.search(it["titulo"] or "")
                  and not _RE_CESE.search(it["titulo"] or "")]
        # pass B (RD colectivo): título plural sin nombres cuyo cargo-cabeza casa
        # EXACTO en alguna variante de género/plural («se nombran Ministros del
        # Gobierno» ↔ etapa «ministro de …»), ventana [cese-8a, cese]. Un stem
        # laxo no vale: «minist» casa «Ministerio» y entierra el colectivo bajo
        # cientos de resoluciones de «nombramientos» (cazado en el primer run).
        floor_b = max(window_start, f"{int(cese[:4]) - 8}{cese[4:]}")
        head = cargo_key.split()[0] if cargo_key else ""
        variants = {head}
        if head.endswith("o"):
            variants.add(head[:-1] + "a")
        elif head.endswith("a"):
            variants.add(head[:-1] + "o")
        variants |= {v + "s" for v in variants} | {v + "es" for v in variants}
        cand_b = [it for it in items
                  if floor_b <= it["fecha"] < cese
                  and _RE_COLECTIVO.search(it["titulo"] or "")
                  and head
                  and variants & set(_norm(it["titulo"] or "").split())
                  and not _RE_PERSONA.search(it["titulo"] or "")]
        # más cercanos al cese primero; cap de descargas XML por etapa
        ids_b = {it["boe_id"] for it in cand_b}
        pool, seen_ids = [], set()
        for it in (sorted(cand_a, key=lambda x: x["fecha"], reverse=True)[:args.max_candidates]
                   + sorted(cand_b, key=lambda x: x["fecha"], reverse=True)[:args.max_candidates]):
            if it["boe_id"] not in seen_ids:
                seen_ids.add(it["boe_id"])
                pool.append(it)

        candidatos = []
        for it in pool:
            try:
                meta = _parse_disposicion(_fetch_xml(it["boe_id"]))
            except Exception as e:  # noqa: BLE001
                print(f"  ERROR {it['boe_id']}: {e}", file=sys.stderr)
                continue
            cuerpo_norm = _norm(" ".join(meta["parrafos"]))
            cuerpo = f" {cuerpo_norm} "
            cuerpo_toks = set(cuerpo_norm.split())
            if any(f" {k} " in cuerpo for k in persona_keys):
                confianza = "cuerpo"
            # nombres registrales intercalados («Nadia MARÍA Calviño Santamaría»)
            elif any(set(k.split()) <= cuerpo_toks for k in persona_keys):
                confianza = "cuerpo_tokens"
            else:
                confianza = "solo_titulo"
            if confianza == "solo_titulo" and it["boe_id"] in ids_b:
                continue  # colectivo que no nombra a la persona → fuera
            candidatos.append({
                "boe_id": it["boe_id"],
                "fecha": it["fecha"],
                "titulo_xml": meta["titulo"],
                "url_html": it["url_html"],
                "confianza": confianza,
                "via": "colectivo" if it["boe_id"] in ids_b else "titulo_cargo",
            })
        estado = "candidatos" if candidatos else "sin_candidatos_en_ventana"
        results.append({**st, "estado": estado, "clave_debil": clave_debil,
                        "candidatos": candidatos})
        print(f"  {st['entity_id']} · {st['boe_key']}: {estado} ({len(candidatos)})")

    dst = DATA / "_pre_candidates.json"
    with open(dst, "w") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print(f"→ {dst}")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download")
    d.add_argument("--since", default="2004-01-01")
    d.add_argument("--until", default=None)
    d.add_argument("--threads", type=int, default=6)
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("stats")
    s.set_defaults(fn=cmd_stats)

    m = sub.add_parser("match")
    m.add_argument("--min-degree", type=int, default=0)
    m.add_argument("--export-vigia", action="store_true",
                   help="escribe vigia_targets.json (insumo VERSIONADO de la rutina cloud)")
    m.set_defaults(fn=cmd_match)

    nv = sub.add_parser("novedades")
    nv.add_argument("--since", default="auto",
                    help="'auto' = max(done_dates)+1; o fecha ISO")
    nv.add_argument("--min-degree", type=int, default=0)
    nv.add_argument("--threads", type=int, default=6)
    nv.set_defaults(fn=cmd_novedades)

    fx = sub.add_parser("fetch-xml")
    fx.add_argument("--ids", nargs="*", default=None)
    fx.add_argument("--ids-file", default=None)
    fx.add_argument("--match", action="store_true",
                    help="cruza personas extraídas contra _target_persons.tsv")
    fx.add_argument("--force", action="store_true", help="re-descarga ignorando caché")
    fx.set_defaults(fn=cmd_fetch_xml)

    rp = sub.add_parser("resolve-pre")
    rp.add_argument("--window-start", default="2004-01-01")
    rp.add_argument("--max-candidates", type=int, default=8,
                    help="cap de descargas XML por etapa y por vía")
    rp.set_defaults(fn=cmd_resolve_pre)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
