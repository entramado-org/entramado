"""Controlled vocabulary of the graph (spec 069): loader, invariants and the helpers every
decision point uses. `pipeline/data/ontologia/ontologia.yaml` is the ONLY source of entity types,
relation types, procedural statuses, provenance layers (source_kind), canonical crimes and defect
labels. Code derives its enums and sets from here; prompts and Jev questions render the term
cards (`fichas_prompt`); validators check every LLM output with `validar` and route doubt with
`ante_duda`. Nothing else may hold a literal list of these terms (`ontologia_check.py` enforces it).

Stdlib + PyYAML only, and no import of `config`: the API image and CI load it too.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "ontologia" / "ontologia.yaml"

SECCIONES = ("tipos_entidad", "tipos_relacion", "estados_procesales", "capas", "delitos",
             "etiquetas_defecto")
_FICHA = ("definicion", "incluye", "excluye")
_EJEMPLOS = ("ejemplo_si", "ejemplo_no")
# Required fields per section (spec 069 glossary: «ficha de término»).
CAMPOS = {
    "tipos_entidad": _FICHA,
    "tipos_relacion": ("clase", "direccion", "simetrica", "solo_loader", "lleva_estado", "sujeto", "objeto", *_FICHA,
                       *_EJEMPLOS, "sinonimos"),
    "estados_procesales": ("orden", "fase", "admite_prensa", "exige_fuente", *_FICHA, *_EJEMPLOS, "sinonimos"),
    "capas": ("rango", *_FICHA, *_EJEMPLOS),
    "delitos": ("articulos", *_FICHA, *_EJEMPLOS, "sinonimos"),
    "etiquetas_defecto": ("clase", *_FICHA, *_EJEMPLOS),
}
CLASES = ("acusatoria", "no_acusatoria")
# Owner decision 2026-10-07 (spec 069 §Decisiones 1): doubt on an accusatory term is never decided
# by a machine. The YAML cannot relax it.
POLITICA_FIJA = {"acusatoria": "revision"}
_BOOLS = {"tipos_relacion": ("simetrica", "solo_loader", "lleva_estado"),
          "estados_procesales": ("admite_prensa",)}
FASES = ("abierta", "cerrada", "condena")
# Kind of source a curated status row must cite (registry `fuente.tipo`). The phase says how serious
# a status is; this says what can prove it (Codex re-review of #15: a TC annulment is a sentencia).
FUENTES_EXIGIDAS = ("ninguna", "acto_acusatorio", "sentencia")
ACCIONES_DUDA = ("revision", "mantener")
# Statuses are procedural facts about a person: always the accusatory doubt class.
CLASE_ESTADO = "acusatoria"


class OntologiaError(ValueError):
    """The YAML breaks an invariant. Carries every problem found, not just the first."""

    def __init__(self, problemas: list[str]):
        self.problemas = problemas
        super().__init__("ontología inválida:\n- " + "\n- ".join(problemas))


def norm(s: str) -> str:
    """Case-, accent- and whitespace-insensitive key for free-text terms (crimes, statuses)."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def norm_rel(s: str) -> str:
    """Key for relation types: the LLM writes `se reunió con`, `SE_REUNIO_CON`, `se-reunio-con`…"""
    return re.sub(r"[\s\-]+", "_", norm(s)).upper()


def validar_ontologia(raw: dict) -> None:
    """Raise OntologiaError with every broken invariant."""
    p: list[str] = []
    if not isinstance(raw.get("version"), int) or isinstance(raw.get("version"), bool):
        p.append("`version` debe ser un entero")
    duda = raw.get("politica_duda") or {}
    for c in CLASES:
        if (duda.get(c) or {}).get("accion") not in ACCIONES_DUDA:
            p.append(f"politica_duda.{c}.accion debe ser una de {ACCIONES_DUDA}")
    for c, accion in POLITICA_FIJA.items():
        if (duda.get(c) or {}).get("accion") != accion:
            p.append(f"politica_duda.{c}.accion es `{accion}` por decisión del dueño (spec 069)")
    for sec in SECCIONES:
        terms = raw.get(sec)
        if not isinstance(terms, dict) or not terms:
            p.append(f"falta la sección `{sec}`")
            continue
        for name, f in terms.items():
            if not isinstance(f, dict):
                p.append(f"{sec}.{name}: la ficha debe ser un bloque")
                continue
            for campo in CAMPOS[sec]:
                v = f.get(campo)
                if v is None or (isinstance(v, str) and not v.strip() and campo != "articulos"):
                    p.append(f"{sec}.{name}: falta `{campo}`")
            if not isinstance(f.get("incluye", []), list) or not isinstance(f.get("excluye", []), list):
                p.append(f"{sec}.{name}: `incluye` y `excluye` son listas")
            for campo in _BOOLS.get(sec, ()):
                if campo in f and not isinstance(f[campo], bool):
                    p.append(f"{sec}.{name}: `{campo}` debe ser un booleano YAML (true/false)")
            if "extraible" in f and not isinstance(f["extraible"], bool):
                p.append(f"{sec}.{name}: `extraible` debe ser un booleano YAML (true/false)")
    rels = raw.get("tipos_relacion") or {}
    ents = set(raw.get("tipos_entidad") or {})
    for name, f in rels.items():
        if not isinstance(f, dict):
            continue
        if f.get("clase") not in CLASES:
            p.append(f"tipos_relacion.{name}: `clase` debe ser una de {CLASES}")
        sim, dirn = f.get("simetrica"), str(f.get("direccion") or "")
        if sim is True and "↔" not in dirn:
            p.append(f"tipos_relacion.{name}: simétrica exige dirección «A ↔ B»")
        if sim is False and "→" not in dirn:
            p.append(f"tipos_relacion.{name}: no simétrica exige dirección «sujeto → objeto»")
        for lado in ("sujeto", "objeto"):
            if not f.get(lado):
                p.append(f"tipos_relacion.{name}.{lado}: al menos un tipo de entidad")
            bad = [t for t in f.get(lado) or [] if t not in ents]
            if bad:
                p.append(f"tipos_relacion.{name}.{lado}: tipos de entidad desconocidos {bad}")
        for par in f.get("pares_extra") or []:
            if not (isinstance(par, list) and len(par) == 2 and all(t in ents for t in par)):
                p.append(f"tipos_relacion.{name}.pares_extra: {par!r} no es un par de tipos de entidad")
        if "duda" in f and f["duda"] not in ACCIONES_DUDA:
            p.append(f"tipos_relacion.{name}.duda debe ser una de {ACCIONES_DUDA}")
        if f.get("clase") in POLITICA_FIJA and f.get("duda", POLITICA_FIJA[f["clase"]]) != POLITICA_FIJA[f["clase"]]:
            p.append(f"tipos_relacion.{name}.duda no puede relajar la política de su clase")
        if f.get("lleva_estado") is True and f.get("clase") != "acusatoria":
            p.append(f"tipos_relacion.{name}: una relación con estado procesal es acusatoria")
        if name != name.upper():
            p.append(f"tipos_relacion.{name}: el nombre va en mayúsculas")
    for sec, keyf in (("tipos_relacion", norm_rel), ("estados_procesales", norm), ("delitos", norm)):
        dueño: dict[str, str] = {}
        for name, f in (raw.get(sec) or {}).items():
            if not isinstance(f, dict):
                continue
            for s in [name, *(f.get("sinonimos") or [])]:
                k = keyf(s)
                if k in dueño and dueño[k] != name:
                    p.append(f"{sec}: «{s}» pertenece a la vez a {dueño[k]} y a {name}")
                dueño[k] = name
    for name, f in (raw.get("estados_procesales") or {}).items():
        if isinstance(f, dict) and f.get("fase") not in FASES:
            p.append(f"estados_procesales.{name}: `fase` debe ser una de {FASES}")
        if isinstance(f, dict) and "exige_fuente" in f and f["exige_fuente"] not in FUENTES_EXIGIDAS:
            p.append(f"estados_procesales.{name}: `exige_fuente` debe ser una de {FUENTES_EXIGIDAS}")
    delitos = raw.get("delitos") or {}
    for name, f in delitos.items():
        if isinstance(f, dict) and f.get("familia") is not None and f["familia"] not in delitos:
            p.append(f"delitos.{name}: `familia` {f['familia']!r} no es un delito canónico")
    claves = {norm(s) for n, f in delitos.items() if isinstance(f, dict) for s in [n, *(f.get("sinonimos") or [])]}
    ambiguos = raw.get("delitos_ambiguos") or {}
    if not isinstance(ambiguos, dict):
        p.append("delitos_ambiguos: un bloque nombre → [candidatos]")
        ambiguos = {}
    for amb, candidatos in ambiguos.items():
        if norm(amb) in claves:
            p.append(f"delitos_ambiguos: «{amb}» es también un término o sinónimo")
        if not candidatos or any(c not in delitos for c in candidatos):
            p.append(f"delitos_ambiguos.{amb}: los candidatos deben ser delitos canónicos")
    rangos = [f.get("rango") for f in (raw.get("capas") or {}).values() if isinstance(f, dict)]
    if len(rangos) != len(set(rangos)):
        p.append("capas: cada `rango` es único")
    if p:
        raise OntologiaError(p)


@dataclass(frozen=True)
class Resultado:
    """Outcome of `validar`: `ok` with the canonical `valor`, or a rejection `motivo`."""
    ok: bool
    valor: str | None = None
    motivo: str = ""


class Ontologia:
    def __init__(self, raw: dict, sha256: str):
        validar_ontologia(raw)
        self.raw = raw
        self.version: int = raw["version"]
        self.sha256 = sha256
        self.politica_duda = {c: raw["politica_duda"][c]["accion"] for c in CLASES}
        r = raw["tipos_relacion"]
        self.tipos_entidad: tuple[str, ...] = tuple(raw["tipos_entidad"])
        self.tipos_entidad_extraibles = tuple(t for t, f in raw["tipos_entidad"].items()
                                              if f.get("extraible", True))
        self.tipos_relacion: tuple[str, ...] = tuple(r)
        self.tipos_extraibles = tuple(t for t, f in r.items() if not f["solo_loader"])
        self.simetricas = frozenset(t for t, f in r.items() if f["simetrica"])
        self.acusatorias = frozenset(t for t, f in r.items() if f["clase"] == "acusatoria")
        # Relation types that carry a procedural status (ACCUSED_OF, CONVICTED_OF, INVESTIGATED_BY).
        self.procesales: tuple[str, ...] = tuple(t for t, f in r.items() if f["lleva_estado"])
        self.sinonimos_relacion = {norm_rel(s): t for t, f in r.items() for s in f["sinonimos"]}
        e = raw["estados_procesales"]
        # File order = the procedural ladder (least → most serious).
        self.estados: tuple[str, ...] = tuple(e)
        self.estados_abiertos = frozenset(k for k, f in e.items() if f["fase"] == "abierta")
        self.estados_cerrados = frozenset(k for k, f in e.items() if f["fase"] == "cerrada")
        self.estados_condena = frozenset(k for k, f in e.items() if f["fase"] == "condena")
        self.escalera: tuple[str, ...] = tuple(k for k in e if e[k]["fase"] != "cerrada")
        self.estados_prensa = frozenset(k for k, f in e.items() if f["admite_prensa"])
        self.fuente_exigida = {k: f["exige_fuente"] for k, f in e.items()}
        self.rango_estado = {k: f["orden"] for k, f in e.items()}
        c = raw["capas"]
        self.capas: tuple[str, ...] = tuple(sorted(c, key=lambda k: c[k]["rango"]))
        self.rango_capa = {k: f["rango"] for k, f in c.items()}
        d = raw["delitos"]
        self.delitos: tuple[str, ...] = tuple(d)
        self._delito_por_clave = {norm(s): k for k, f in d.items() for s in [k, *f["sinonimos"]]}
        self.familia_delito = {k: f.get("familia") or k for k, f in d.items()}
        self._delitos_ambiguos = {norm(a): tuple(c) for a, c in (raw.get("delitos_ambiguos") or {}).items()}
        self._articulos = {k: _rangos(f["articulos"]) for k, f in d.items()}
        self._estado_por_clave = {norm(s): k for k, f in e.items() for s in [k, *f["sinonimos"]]}
        self.etiquetas_defecto: tuple[str, ...] = tuple(raw["etiquetas_defecto"])
        self.clase_etiqueta = {k: f["clase"] for k, f in raw["etiquetas_defecto"].items()}

    # --- canonicalisation ----------------------------------------------------------------
    def canon_relacion(self, valor) -> str | None:
        if not isinstance(valor, str) or not valor.strip():
            return None
        k = norm_rel(valor)
        if k in self.tipos_relacion:
            return k
        return self.sinonimos_relacion.get(k)

    def canon_estado(self, valor) -> str | None:
        return self._estado_por_clave.get(norm(valor)) if isinstance(valor, str) else None

    def canon_delito(self, valor, articulo=None) -> str | None:
        """Exact match on the normalised name or a synonym — never a substring. An ambiguous name
        resolves only through the cited article (`articulo`, e.g. «art. 417.1 CP»)."""
        if not isinstance(valor, str):
            return None
        candidatos = self._delitos_ambiguos.get(norm(valor))
        if candidatos:
            n = _num_articulo(articulo)
            hits = [c for c in candidatos if n is not None and any(a <= n <= b for a, b in self._articulos[c])]
            return hits[0] if len(hits) == 1 else None
        return self._delito_por_clave.get(norm(valor))

    def parece_delito(self, valor) -> bool:
        """Heuristic for graph cleanup only: the name CONTAINS a canonical crime or synonym
        («delito de cohecho»). A topic («la corrupción») does not."""
        n = norm(valor)
        return bool(n) and any(k in n for k in self._delito_por_clave)

    def canon_capa(self, valor) -> str | None:
        """`None` in the graph is the judicial layer."""
        if valor is None:
            return "judicial"
        return valor if isinstance(valor, str) and valor in self.rango_capa else None

    def extremos_ok(self, tipo: str, sujeto: str, objeto: str) -> bool:
        """Are these entity types admitted at the ends of `tipo` (sujeto × objeto, plus `pares_extra`)?"""
        f = self.raw["tipos_relacion"][tipo]
        return ((sujeto in f["sujeto"] and objeto in f["objeto"])
                or [sujeto, objeto] in (f.get("pares_extra") or []))

    # --- validation ----------------------------------------------------------------------
    def validar(self, seccion: str, valor, *, extraccion: bool = False, articulo=None) -> Resultado:
        """Check one LLM output against the vocabulary. Never raises on a malformed value: any
        non-term (wrong JSON type included) is a rejection with its reason. `extraccion=True` also
        rejects what the extractor must not emit (`solo_loader` relations, `extraible: false`
        entities)."""
        if seccion == "tipos_relacion":
            t = self.canon_relacion(valor)
            if t is None:
                return Resultado(False, motivo=f"tipo de relación fuera de vocabulario: {valor!r}")
            if extraccion and t not in self.tipos_extraibles:
                return Resultado(False, motivo=f"{t} solo lo escribe su loader, no el extractor")
            return Resultado(True, t)
        if seccion == "estados_procesales":
            t = self.canon_estado(valor)
            return Resultado(True, t) if t else Resultado(False, motivo=f"estado fuera de vocabulario: {valor!r}")
        if seccion == "delitos":
            t = self.canon_delito(valor, articulo)
            if t is None and isinstance(valor, str) and norm(valor) in self._delitos_ambiguos:
                return Resultado(False, motivo=f"nombre de delito ambiguo (falta o no encaja el artículo): {valor!r}")
            return Resultado(True, t) if t else Resultado(False, motivo=f"delito sin forma canónica: {valor!r}")
        if seccion == "capas":
            t = self.canon_capa(valor)
            return Resultado(True, t) if t else Resultado(False, motivo=f"capa fuera de vocabulario: {valor!r}")
        if seccion == "tipos_entidad" and extraccion and valor in self.raw[seccion] \
                and valor not in self.tipos_entidad_extraibles:
            return Resultado(False, motivo=f"{valor} no es una entidad que el extractor pueda emitir")
        if seccion in ("tipos_entidad", "etiquetas_defecto"):
            ok = isinstance(valor, str) and valor in self.raw[seccion]
            return Resultado(True, valor) if ok else Resultado(False, motivo=f"{seccion}: {valor!r} fuera de vocabulario")
        raise KeyError(seccion)

    def ante_duda(self, termino: str) -> str:
        """`revision` or `mantener` for a doubtful decision about `termino` (a relation type, or
        any procedural status)."""
        if termino in self.rango_estado or termino == "estado":
            return self.politica_duda[CLASE_ESTADO]
        t = self.canon_relacion(termino)
        if t is None:
            return "revision"  # unknown term: never decide it alone
        f = self.raw["tipos_relacion"][t]
        return f.get("duda") or self.politica_duda[f["clase"]]

    # --- prompt rendering ----------------------------------------------------------------
    def ficha(self, seccion: str, termino: str) -> dict:
        return self.raw[seccion][termino]

    def definiciones(self, seccion: str, terminos=None) -> str:
        """One line `term = definition; …` — the compact form for a Jev question."""
        sec = self.raw[seccion]
        return "; ".join(f"{t} = {sec[t]['definicion'].rstrip('.')}" for t in (terminos or sec)) + "."

    def fichas_prompt(self, seccion: str, terminos=None) -> str:
        """Term cards in plain-language form, ready to paste into a prompt or a Jev question."""
        sec = self.raw[seccion]
        out = []
        for t in terminos if terminos is not None else sec:
            f = sec[t]
            head = f"- {t}"
            if seccion == "tipos_relacion":
                head += f" ({f['direccion']})"
            out.append(f"{head}: {f['definicion']}")
            if f.get("incluye"):
                out.append("  Incluye: " + "; ".join(f["incluye"]) + ".")
            if f.get("excluye"):
                out.append("  Excluye: " + "; ".join(f["excluye"]) + ".")
            if f.get("ejemplo_si"):
                out.append(f"  Sí: {f['ejemplo_si']}")
            if f.get("ejemplo_no"):
                out.append(f"  No: {f['ejemplo_no']}")
        return "\n".join(out)


def _num_articulo(articulo) -> int | None:
    m = re.search(r"\d+", str(articulo or ""))
    return int(m.group()) if m else None


def _rangos(articulos: str) -> list[tuple[int, int]]:
    """«419-427, 446» → [(419, 427), (446, 446)]; «570 bis-570 quater» → [(570, 570)]."""
    out = []
    for part in str(articulos or "").split(","):
        nums = [int(x) for x in re.findall(r"\d+", part)]
        if nums:
            out.append((min(nums), max(nums)))
    return out


@lru_cache(maxsize=None)
def _load(path: str) -> Ontologia:
    data = Path(path).read_bytes()
    return Ontologia(yaml.safe_load(data), hashlib.sha256(data).hexdigest())


def ontologia(path: str | Path | None = None) -> Ontologia:
    """The cached ontology. `ONTOLOGIA_PATH` overrides the default (the API image)."""
    return _load(str(path or os.environ.get("ONTOLOGIA_PATH") or DEFAULT_PATH))


_TRUE = {"true", "si", "yes", "1"}
_FALSE = {"false", "no", "0"}


def parse_bool(valor) -> bool:
    """Strict boolean parse of an LLM output. Raises ValueError on anything unrecognised, so the
    caller sends it to the review queue instead of guessing (`bool("false")` is True)."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        k = norm(valor)
        if k in _TRUE:
            return True
        if k in _FALSE:
            return False
    raise ValueError(f"booleano no reconocido: {valor!r}")
