#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["certifi", "pyyaml"]
# ///
"""READ-ONLY PLACSP recon sweep (specs 045 + 054, Fase 1). Zero graph writes.

A diferencia de BDNS (API de búsqueda por nombre), PLACSP publica dumps: ZIPs de ficheros
ATOM/CODICE. El cruce se invierte — descargar dumps, indexar adjudicatarios en local, cruzar
la lista objetivo del grafo (pipeline/data/contratacion/_target_*.tsv, de
_placsp_target_list.sh) contra el índice:

  uv run pipeline/scripts/_placsp_recon_sweep.py download [--dataset=D] [--only=SUBSTR]
  uv run pipeline/scripts/_placsp_recon_sweep.py index    [--dataset=D] [--only=SUBSTR]
  uv run pipeline/scripts/_placsp_recon_sweep.py match    [--dataset=D]

Datasets (--dataset, default `menores` = comportamiento spec 045 intacto):
  menores       sindicacion_1143  contratos menores en perfiles PLACSP (2018→)
  licitaciones  sindicacion_643   licitaciones NO menores en perfiles PLACSP (2012→)
  agregadas     sindicacion_1044  licitaciones de plataformas agregadas CCAA/EELL (2016→)

Nombres de fichero verificados en vivo 2026-07-19 (páginas de datasets de hacienda.gob.es;
ojo: 643 es «...PerfilesContratanteCompleto3», Contratante en singular). En licitaciones/
agregadas la misma licitación se RE-PUBLICA en cada cambio de estado → el match dedupe por
versión (folder_id+adjudicatario, gana el updated más reciente). `--only` filtra por
substring del nombre de ZIP (p.ej. --only=202506 para la muestra de 1 mes).

Todo bajo pipeline/data/contratacion/_* (gitignored; sufijos _lic/_agr por dataset);
raíz de datos redirigible con PLACSP_DATA (gotcha worktrees). Reanudable: download salta
ZIPs ya bajados, index salta ZIPs con marker .done, match es una pasada local re-ejecutable.
TLS: la cadena FNMT del servidor no está en el bundle de curl/macOS -> certifi (sí la trae).
"""
import concurrent.futures
import csv
import io
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

import certifi
import yaml

SIND = "https://contrataciondelsectorpublico.gob.es/sindicacion"
DATASETS = {
    "menores": {
        "base": f"{SIND}/sindicacion_1143", "prefix": "contratosMenoresPerfilesContratantes",
        "years": range(2018, 2026), "suffix": "", "versioned": False,
    },
    "licitaciones": {
        "base": f"{SIND}/sindicacion_643", "prefix": "licitacionesPerfilesContratanteCompleto3",
        "years": range(2012, 2026), "suffix": "_lic", "versioned": True,
    },
    "agregadas": {
        "base": f"{SIND}/sindicacion_1044", "prefix": "PlataformasAgregadasSinMenores",
        "years": range(2016, 2026), "suffix": "_agr", "versioned": True,
    },
    # CCAA (sub-barrido B, gate 2026-07-19): el índice lo generan los adaptadores
    # _ccaa_<nombre>_fetch.py (download+normalize al mismo esquema); aquí solo `match`.
    **{f"ccaa_{n}": {"base": "", "prefix": "", "years": (),
                     "suffix": f"_ccaa_{n}", "versioned": False}
       for n in ("andalucia", "navarra", "galicia", "euskadi")},
}
DATA = Path(os.environ.get("PLACSP_DATA")
            or Path(__file__).resolve().parents[1] / "data" / "contratacion")
# se fijan en __main__ según --dataset (default menores = rutas históricas del 045)
BASE, PREFIX, YEARS, VERSIONED = "", "", [], False
DUMPS, INDEX, CRUCE = DATA / "_dumps", DATA / "_index", DATA / "_cruce"
ONLY = None                              # --only=SUBSTR filtra ZIPs en download/index
MONTHS_2026 = [f"2026{m:02d}" for m in range(1, 8)]  # año corriente: mensuales
MIN_NAME_LEN = 10                        # gate: nombre normalizado mínimo para cruce
MIN_MONO_LEN = 8                         # pasada mono-token: longitud mínima del token
MAX_SAMPLE_PER_TARGET = 200              # cap de detalle por objetivo (too_generic va a stats)

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# formas jurídicas y ruido que se quitan ANTES de comparar nombres
LEGAL_TOKENS = {
    "SL", "SLU", "SLL", "SLP", "SA", "SAU", "SAL", "SC", "SCP", "SCOOP", "COOP", "AIE",
    "SOCIEDAD", "LIMITADA", "ANONIMA", "UNIPERSONAL", "LABORAL", "PROFESIONAL",
    "ESPAÑA", "ESPANA", "SPAIN", "GROUP", "GRUPO", "THE",
}
UTE_RE = re.compile(r"\b(UTE|UNION TEMPORAL DE EMPRESAS?)\b")
STOPWORDS = {"DE", "LA", "EL", "Y", "LOS", "LAS", "DEL", "E", "EN"}


def _content_tokens(norm_name: str) -> list[str]:
    return [t for t in norm_name.split() if t not in STOPWORDS]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r"[^A-Z0-9Ñ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_company(s: str) -> str:
    toks = [t for t in norm(s).split() if t not in LEGAL_TOKENS]
    return " ".join(toks)


def norm_nif(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# ---------------------------------------------------------------- download

def download() -> None:
    DUMPS.mkdir(parents=True, exist_ok=True)
    # anuales cerrados + mensuales del año corriente (JAMÁS el anual del año corriente:
    # duplicaría con los mensuales, gotcha 045)
    names = [f"{PREFIX}_{y}.zip" for y in YEARS] + \
            [f"{PREFIX}_{m}.zip" for m in MONTHS_2026]
    if ONLY:
        names = [n for n in names if ONLY in n]
    for name in names:
        dest = DUMPS / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"skip {name} (ya bajado, {dest.stat().st_size >> 20} MB)")
            continue
        url = f"{BASE}/{name}"
        part = dest.with_suffix(".part")
        print(f"GET {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r, \
                    part.open("wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            part.rename(dest)
            print(f"  ok {dest.stat().st_size >> 20} MB")
        except Exception as e:  # noqa: BLE001 - un ZIP que falle no aborta el resto
            part.unlink(missing_ok=True)
            print(f"  ERROR {name}: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- index

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_member(zip_path: str, member: str) -> list[dict]:
    """Parse one .atom file inside a zip -> compact records, one per TenderResult."""
    out: list[dict] = []
    with zipfile.ZipFile(zip_path) as z, z.open(member) as fh:
        # iterparse en streaming; limpiamos cada <entry> al cerrarse para no retener el árbol
        for _event, el in ET.iterparse(io.BufferedReader(fh), events=("end",)):
            if _local(el.tag) != "entry":
                continue
            rec_base: dict = {"src": f"{Path(zip_path).name}/{member}"}
            results: list[dict] = []
            for node in el.iter():
                t = _local(node.tag)
                if t == "id" and "id" not in rec_base:
                    rec_base["id"] = (node.text or "").rsplit("/", 1)[-1]
                elif t == "link" and "link" not in rec_base:
                    rec_base["link"] = node.attrib.get("href", "")
                elif t == "updated" and "updated" not in rec_base:
                    rec_base["updated"] = (node.text or "")[:10]
                elif t == "ContractFolderID":
                    rec_base["folder_id"] = node.text
                elif t == "ContractFolderStatusCode" and "status" not in rec_base:
                    rec_base["status"] = node.text
                elif t == "ProcedureCode" and "procedure" not in rec_base:
                    rec_base["procedure"] = node.text          # 1 abierto, 3/4 negociado…
                elif t == "UrgencyCode" and "urgency" not in rec_base:
                    rec_base["urgency"] = node.text            # 1 ordinaria, 2 urgencia, 3 emergencia
                elif t == "TenderResult":
                    results.append(_parse_result(node))
            # órgano y objeto: primer Party del LocatedContractingParty + ProcurementProject
            for node in el.iter():
                t = _local(node.tag)
                if t == "LocatedContractingParty":
                    rec_base["organo"], rec_base["organo_nif"], rec_base["organo_zip"] = \
                        _party_name_nif(node)
                elif t == "ProcurementProject":
                    for sub in node.iter():
                        st = _local(sub.tag)
                        if st == "Name" and "title" not in rec_base:
                            rec_base["title"] = sub.text
                        elif st == "ItemClassificationCode" and "cpv" not in rec_base:
                            rec_base["cpv"] = sub.text
                        elif st == "TaxExclusiveAmount" and "budget" not in rec_base:
                            rec_base["budget"] = sub.text      # presupuesto s/IVA → % de baja
                        elif st == "EstimatedOverallContractAmount" \
                                and "budget_est" not in rec_base:
                            rec_base["budget_est"] = sub.text  # valor estimado (con prórrogas)
                    break
            for res in results:
                out.append({**rec_base, **res})
            el.clear()
    return out


def _party_name_nif(node) -> tuple[str | None, str | None, str | None]:
    name = nif = zone = None
    for sub in node.iter():
        t = _local(sub.tag)
        if t == "ParentLocatedParty":  # no bajar a la jerarquía administrativa
            break
        if t == "ID" and sub.attrib.get("schemeName") == "NIF" and nif is None:
            nif = sub.text
        elif t == "Name" and name is None:
            name = sub.text
        elif t == "PostalZone" and zone is None:
            zone = sub.text               # CP del órgano → proxy provincia/CCAA (ranking 054)
    return name, nif, zone


def _parse_result(node) -> dict:
    res: dict = {}
    in_winner = False
    for sub in node.iter():
        t = _local(sub.tag)
        if t == "ProcurementProjectLotID" and "lot" not in res:
            res["lot"] = sub.text
        elif t == "AwardDate":
            res["award_date"] = sub.text
        elif t == "ResultCode":
            res["result_code"] = sub.text
        elif t == "ReceivedTenderQuantity":
            res["n_tenders"] = sub.text
        elif t == "WinningParty":
            in_winner = True
        elif in_winner and t == "ID" and sub.attrib.get("schemeName") == "NIF" \
                and "winner_nif" not in res:
            res["winner_nif"] = sub.text
        elif in_winner and t == "Name" and "winner_name" not in res:
            res["winner_name"] = sub.text
        elif in_winner and t == "CityName" and "winner_city" not in res:
            res["winner_city"] = sub.text
        elif t == "PayableAmount":
            res["amount"] = sub.text
        elif t == "TaxExclusiveAmount" and "amount" not in res:
            res["amount"] = sub.text
    return res


def index() -> None:
    INDEX.mkdir(parents=True, exist_ok=True)
    zips = sorted(DUMPS.glob("*.zip"))
    if ONLY:
        zips = [z for z in zips if ONLY in z.name]
    if not zips:
        sys.exit(f"no hay ZIPs en {DUMPS.name}/ — corre `download` primero")
    for zp in zips:
        shard = INDEX / f"{zp.stem}.jsonl"
        done = INDEX / f"{zp.stem}.done"
        if done.exists():
            print(f"skip {zp.name} (indexado)")
            continue
        with zipfile.ZipFile(zp) as z:
            members = [m for m in z.namelist() if m.endswith(".atom")]
        n = 0
        with shard.open("w") as out, concurrent.futures.ProcessPoolExecutor() as pool:
            futures = [pool.submit(_parse_member, str(zp), m) for m in members]
            for fut in concurrent.futures.as_completed(futures):
                for rec in fut.result():
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
        done.write_text(f"{n}\n")
        print(f"{zp.name}: {len(members)} atom, {n} resultados")


# ---------------------------------------------------------------- match

def _load_targets() -> tuple[list[dict], list[dict]]:
    companies, persons = [], []
    with (DATA / "_target_companies.tsv").open() as f:
        reader = csv.reader(f, skipinitialspace=True)
        next(reader)
        for row in reader:
            if row:
                label, eid, name, nif, degree, mercantil = row
                companies.append({"entity_id": eid, "name": name, "label": label,
                                  "nif": norm_nif(nif), "degree": int(degree),
                                  "norm": norm_company(name)})
    with (DATA / "_target_persons.tsv").open() as f:
        reader = csv.reader(f, skipinitialspace=True)
        next(reader)
        for row in reader:
            if row:
                eid, name, degree = row
                persons.append({"entity_id": eid, "name": name, "degree": int(degree),
                                "norm": norm(name)})
    return companies, persons


def _load_extra_nifs() -> dict[str, str]:
    """entity_id -> NIF desde las capas curadas ya versionadas (BDNS + mercantil)."""
    nifs: dict[str, str] = {}
    for rel in ("subvenciones/matches.candidate.yaml", "mercantil/matches.candidate.yaml",
                "mercantil/mercantil.yaml", "contratacion/matches.candidate.yaml"):
        path = DATA.parent / rel
        if not path.exists():
            continue
        docs = yaml.safe_load(path.read_text())
        stack = [docs]
        while stack:  # busca pares entity_id/nif en cualquier nivel
            cur = stack.pop()
            if isinstance(cur, dict):
                eid, nif = cur.get("entity_id"), cur.get("nif")
                if eid and nif:
                    nifs.setdefault(eid, norm_nif(str(nif)))
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
    return nifs


def match() -> None:
    CRUCE.mkdir(parents=True, exist_ok=True)
    companies, persons = _load_targets()
    extra = _load_extra_nifs()
    for c in companies:
        if not c["nif"] and c["entity_id"] in extra:
            c["nif"] = extra[c["entity_id"]]
    by_nif = {c["nif"]: c for c in companies if c["nif"]}
    comp_by_norm: dict[str, dict] = {}
    for c in companies:
        if len(c["norm"]) >= MIN_NAME_LEN and len(_content_tokens(c["norm"])) >= 2:
            comp_by_norm.setdefault(c["norm"], c)
    pers_by_norm = {p["norm"]: p for p in persons if len(p["norm"]) >= MIN_NAME_LEN}
    contains_targets = [c for c in comp_by_norm.values()]  # para substring y UTE
    # pasada mono-token supletoria (obligatoria en spec 054; gotcha GLOBALIA/SERVINABAR
    # del 045): empresas cuyo nombre útil es UN solo token distintivo quedan fuera del
    # gate >=2 tokens — se cruzan por pertenencia exacta del token, nunca por substring
    mono_targets = []
    for c in companies:
        toks = _content_tokens(c["norm"])
        if len(toks) == 1 and len(toks[0]) >= MIN_MONO_LEN:
            mono_targets.append({**c, "tok": toks[0]})

    shards = sorted(INDEX.glob("*.jsonl"))
    if not shards:
        sys.exit(f"no hay shards en {INDEX.name}/ — corre `index` primero")

    seen: set[tuple] = set()           # dedup global por (entry id, winner)
    stats = Counter()
    hit_counts: Counter = Counter()    # entity_id -> nº hits (para too_generic)
    outs = {k: (CRUCE / f"hits_{k}.jsonl").open("w")
            for k in ("nif", "name", "ute", "mono")}
    # datasets versioned: cada cambio de estado re-publica la licitación con entry id
    # nuevo → quedarse con la versión más reciente por (expediente, adjudicatario, lote)
    pending: dict[tuple, tuple[str, dict, dict, str]] = {}

    def _write(kind: str, target: dict, rec: dict, why: str) -> None:
        hit_counts[target["entity_id"]] += 1
        stats[f"hits_{kind}"] += 1
        if hit_counts[target["entity_id"]] <= MAX_SAMPLE_PER_TARGET:
            outs[kind].write(json.dumps(
                {"entity_id": target["entity_id"], "target_name": target["name"],
                 "match": why, **rec}, ensure_ascii=False) + "\n")

    def emit(kind: str, target: dict, rec: dict, why: str) -> None:
        if not VERSIONED:
            _write(kind, target, rec, why)
            return
        stats["hits_pre_dedup_version"] += 1
        vkey = (kind, target["entity_id"], rec.get("folder_id") or rec.get("id"),
                norm_nif(rec.get("winner_nif") or "") or norm(rec.get("winner_name") or ""),
                rec.get("lot"))
        cur = pending.get(vkey)
        if cur is None or (rec.get("updated") or "") > (cur[2].get("updated") or ""):
            pending[vkey] = (kind, target, rec, why)

    for shard in shards:
        with shard.open() as f:
            for line in f:
                rec = json.loads(line)
                stats["records"] += 1
                wname, wnif = rec.get("winner_name"), norm_nif(rec.get("winner_nif") or "")
                if not wname and not wnif:
                    stats["sin_adjudicatario"] += 1
                    continue
                key = (rec.get("id"), wnif or wname)
                if key in seen:
                    stats["dups"] += 1
                    continue
                seen.add(key)
                wnorm = norm(wname or "")
                wnorm_c = norm_company(wname or "")
                if wnif:
                    stats["con_nif"] += 1
                if UTE_RE.search(wnorm):
                    stats["utes"] += 1
                    wtoks = set(wnorm_c.split())
                    for t in contains_targets:
                        if t["norm"] in wnorm_c:
                            emit("ute", t, rec, f"miembro-en-denominacion-ute:{t['norm']}")
                    for t in mono_targets:
                        if t["tok"] in wtoks:
                            emit("ute", t, rec, f"miembro-mono-token-en-ute:{t['tok']}")
                    continue
                if wnif and wnif in by_nif:
                    emit("nif", by_nif[wnif], rec, f"nif-exacto:{wnif}")
                    continue
                t = comp_by_norm.get(wnorm_c) or pers_by_norm.get(wnorm)
                if t:
                    emit("name", t, rec, "nombre-normalizado-exacto")
                    continue
                # containment: objetivo dentro del nombre del adjudicatario (p.ej. + sufijos)
                for t in contains_targets:
                    if t["norm"] in wnorm_c:
                        emit("name", t, rec, f"nombre-contenido:{t['norm']}")
                        break
                else:
                    wtoks = set(wnorm_c.split())
                    for t in mono_targets:
                        if t["tok"] in wtoks:
                            emit("mono", t, rec, f"mono-token:{t['tok']}")
                            break

    if VERSIONED:  # volcado de las versiones ganadoras (una por expediente+adjudicatario+lote)
        for kind, target, rec, why in pending.values():
            _write(kind, target, rec, why)
    for fh in outs.values():
        fh.close()
    stats["targets_companies"] = len(companies)
    stats["targets_mono_token"] = len(mono_targets)
    stats["targets_companies_con_nif"] = len(by_nif)
    stats["targets_persons"] = len(persons)
    stats["targets_con_hits"] = len(hit_counts)
    too_generic = {k: v for k, v in hit_counts.items() if v > MAX_SAMPLE_PER_TARGET}
    (CRUCE / "stats.json").write_text(json.dumps(
        {"stats": dict(stats), "hits_por_objetivo": dict(hit_counts),
         "too_generic": too_generic}, ensure_ascii=False, indent=2))
    print(json.dumps(dict(stats), ensure_ascii=False, indent=2))
    if too_generic:
        print(f"too_generic (> {MAX_SAMPLE_PER_TARGET} hits): {too_generic}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dataset = "menores"
    for a in sys.argv[2:]:
        if a.startswith("--dataset="):
            dataset = a.split("=", 1)[1]
        elif a.startswith("--only="):
            ONLY = a.split("=", 1)[1]
        else:
            sys.exit(f"argumento no reconocido: {a}")
    if dataset not in DATASETS:
        sys.exit(f"dataset desconocido: {dataset} (opciones: {', '.join(DATASETS)})")
    cfg = DATASETS[dataset]
    BASE, PREFIX, VERSIONED = cfg["base"], cfg["prefix"], cfg["versioned"]
    YEARS = list(cfg["years"])
    DUMPS = DATA / f"_dumps{cfg['suffix']}"
    INDEX = DATA / f"_index{cfg['suffix']}"
    CRUCE = DATA / f"_cruce{cfg['suffix']}"
    if cmd == "download":
        download()
    elif cmd == "index":
        index()
    elif cmd == "match":
        match()
    else:
        sys.exit("uso: _placsp_recon_sweep.py {download|index|match} "
                 "[--dataset=menores|licitaciones|agregadas] [--only=SUBSTR]")
