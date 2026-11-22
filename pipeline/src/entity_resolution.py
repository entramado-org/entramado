"""Hybrid entity canonicalization: seed -> fuzzy -> embedding merge.

Produces a stable `entity_id` per canonical entity and rewrites relation
endpoints to those ids. Label-scoped (never merges across entity types).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import structlog
from rapidfuzz import fuzz

from . import config, embeddings
from .entity_ids import entity_id, slug as _slug, strip_accents as _strip_accents  # noqa: F401 — re-exported

log = structlog.get_logger()

_HONORIFICS = re.compile(
    r"^(sr\.?|sra\.?|srta\.?|d\.?|d[ñn]a\.?|don|do[ñn]a|excmo\.?|ilmo\.?|"
    r"ministro|ministra|presidente|presidenta|se[ñn]or|se[ñn]ora)\s+",
    re.IGNORECASE,
)


def _norm_key(name: str) -> str:
    """Match key: lowercase, accent-free, honorific-free."""
    s = name.strip().lower()
    s = _HONORIFICS.sub("", s)
    s = _strip_accents(s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _alias_belongs(alias: str, canonical: str, etype: str) -> bool:
    """For named entities (Person/Org/Company/Court), an alias must share the
    canonical's LAST significant token (surname) or be a sub/superstring of the
    canonical name. Without this guard the LLM occasionally stuffs co-occurring
    names ("Aznar" as alias of "Ábalos") into the aliases array."""
    if etype not in {"Person", "Organization", "Company", "Court"}:
        return True
    a, c = _norm_key(alias), _norm_key(canonical)
    if not a or not c:
        return False
    if a in c or c in a:
        return True
    a_tokens = {t for t in a.split() if len(t) >= 3}
    c_tokens = [t for t in c.split() if len(t) >= 3]
    if not c_tokens:
        return True
    return c_tokens[-1] in a_tokens


def _never_merge(keys_a: set[str], keys_b: set[str]) -> bool:
    """True si algún par (a∈keys_a, b∈keys_b) está vetado en config.NEVER_MERGE."""
    for x, y in config.NEVER_MERGE:
        if (x in keys_a and y in keys_b) or (y in keys_a and x in keys_b):
            return True
    return False


def _bare_name_conflict(etype: str, keys_a: set[str], keys_b: set[str]) -> bool:
    """Spec 059 (anti-de-anonimización): a Person whose every surface form is a
    SINGLE token (a bare first name — the CENDOJ anonymisation roster: «David»,
    «Benito», «Felix»…) must never be auto-folded into a multi-token civil name
    («David Sánchez», «Félix Bolaños»). rapidfuzz.token_set_ratio scores that pair
    100 (subset), so without this guard every pseudonym that shares a first name
    with a real figure in the batch merged silently (findings/cripto-misatribucion-
    david-sanchez, pseudonyms.yaml «remanentes de conflación»). Only an explicit
    config.SEED_ALIASES entry may unite a bare name with a full one."""
    if etype != "Person":
        return False
    a_bare = all(len(k.split()) == 1 for k in keys_a)
    b_bare = all(len(k.split()) == 1 for k in keys_b)
    return a_bare != b_bare


class Resolver:
    def __init__(self):
        # type -> list of clusters; each cluster: {canonical, key, keys:set, aliases:set, count}
        self.clusters: dict[str, list[dict]] = defaultdict(list)

    def _find_fuzzy(self, etype: str, key: str) -> dict | None:
        best, best_score = None, 0
        for cl in self.clusters[etype]:
            if _never_merge({key}, cl["keys"]) or _bare_name_conflict(etype, {key}, cl["keys"]):
                continue
            score = max(fuzz.token_set_ratio(key, k) for k in cl["keys"])
            if score > best_score:
                best, best_score = cl, score
        return best if best_score >= config.FUZZY_AUTOMERGE else None

    def add(self, etype: str, name: str, aliases: list[str], role: str | None, count: int = 1):
        key = _norm_key(name)
        if not key:
            return
        seed = config.SEED_ALIASES.get(key)
        name = " ".join(name.split())  # OCR line breaks inside surface names, same as aliases
        canonical = seed or name
        # Collapse OCR line breaks / runs of whitespace inside aliases («César Adalid SILES\nBAZÁN»),
        # then strip aliases that don't actually belong to this canonical entity.
        aliases = [" ".join(a.split()) for a in aliases if a]
        aliases = [a for a in aliases if a and _alias_belongs(a, canonical, etype)]

        cluster = self._find_fuzzy(etype, key)
        if cluster is None and seed:
            # try to attach to an existing cluster already named for this seed
            for cl in self.clusters[etype]:
                if cl["canonical"] == seed:
                    cluster = cl
                    break

        if cluster is None:
            cluster = {
                "canonical": canonical,
                "keys": {key},
                "aliases": set(a for a in aliases if a),
                "count": count,
                "seeded": bool(seed),
            }
            if name != canonical:
                cluster["aliases"].add(name)
            self.clusters[etype].append(cluster)
        else:
            cluster["keys"].add(key)
            cluster["aliases"].update(a for a in aliases if a)
            cluster["aliases"].add(name)
            cluster["count"] += count
            if seed:
                cluster["canonical"] = seed
                cluster["seeded"] = True

    def _merge_embeddings(self):
        """Second pass: merge cluster reps within the embedding gray band."""
        for etype, clusters in self.clusters.items():
            if len(clusters) < 2:
                continue
            reps = [c["canonical"] for c in clusters]
            try:
                vecs = embeddings.embed_texts(reps)
            except Exception as e:  # noqa: BLE001
                log.warning("embed_merge_skipped", type=etype, error=str(e))
                return
            merged: list[int] = []
            for i in range(len(clusters)):
                if i in merged:
                    continue
                for j in range(i + 1, len(clusters)):
                    if j in merged:
                        continue
                    if _never_merge(clusters[i]["keys"], clusters[j]["keys"]):
                        continue
                    if _bare_name_conflict(etype, clusters[i]["keys"], clusters[j]["keys"]):
                        continue
                    cos = sum(a * b for a, b in zip(vecs[i], vecs[j]))
                    if cos >= config.EMBED_AUTOMERGE:
                        # merge j into i
                        clusters[i]["keys"].update(clusters[j]["keys"])
                        clusters[i]["aliases"].update(clusters[j]["aliases"])
                        clusters[i]["aliases"].add(clusters[j]["canonical"])
                        clusters[i]["count"] += clusters[j]["count"]
                        merged.append(j)
            self.clusters[etype] = [c for k, c in enumerate(clusters) if k not in merged]

    def finalize(self) -> tuple[dict, dict]:
        """Returns (registry by entity_id, name_key -> entity_id lookup)."""
        self._merge_embeddings()
        registry: dict[str, dict] = {}
        name_lookup: dict[str, str] = {}
        for etype, clusters in self.clusters.items():
            for cl in clusters:
                canonical = cl["canonical"]
                eid = entity_id(etype, canonical)
                aliases = sorted({" ".join(a.split()) for a in cl["aliases"] if a and a.strip()} - {canonical})
                registry[eid] = {
                    "entity_id": eid,
                    "type": etype,
                    "canonical_name": canonical,
                    "aliases": aliases,
                    "mentions": cl["count"],
                }
                for k in cl["keys"]:
                    # prefer Person on collision, else first writer wins
                    if k not in name_lookup or etype == "Person":
                        name_lookup[k] = eid
        return registry, name_lookup


def resolve(fact_records: list[dict]) -> tuple[dict, list[dict]]:
    """Build the registry and rewrite relation endpoints to entity_ids."""
    resolver = Resolver()

    # Count surface forms to pick representative canonical names sensibly.
    counts: Counter[tuple[str, str]] = Counter()
    meta: dict[tuple[str, str], dict] = {}
    for rec in fact_records:
        for e in rec["entities"]:
            etype, name = e["type"], e["name"]
            counts[(etype, name)] += 1
            m = meta.setdefault((etype, name), {"aliases": set(), "role": None})
            m["aliases"].update(e.get("aliases") or [])
            if e.get("role") and not m["role"]:
                m["role"] = e["role"]

    # Add most-frequent surface forms first so they win as canonical.
    for (etype, name), c in counts.most_common():
        m = meta[(etype, name)]
        resolver.add(etype, name, sorted(m["aliases"]), m["role"], count=c)

    registry, name_lookup = resolver.finalize()

    def lookup(name: str) -> str | None:
        return name_lookup.get(_norm_key(name))

    # Rewrite records: attach entity_id to entities, source_id/target_id to relations.
    resolved: list[dict] = []
    for rec in fact_records:
        new = dict(rec)
        new["entities"] = []
        for e in rec["entities"]:
            eid = lookup(e["name"])
            if eid:
                new["entities"].append({**e, "entity_id": eid})
        new["relations"] = []
        for r in rec["relations"]:
            sid, tid = lookup(r["source"]), lookup(r["target"])
            if sid and tid and sid != tid:
                new["relations"].append({**r, "source_id": sid, "target_id": tid})
        resolved.append(new)

    # Persist registry for inspection / cross-run stability.
    config.REGISTRY_PATH.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("entity_resolution", entities=len(registry))
    return registry, resolved
