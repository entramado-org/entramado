"""Generate the reviewable migration that makes every Crime node canonical (spec 069 T4.9).

Reads a seed EXPORT (`nodes.jsonl`, the qa-lint snapshot format) — never Neo4j — and classifies
each Crime node against the controlled ontology:

  * canonical name                  → untouched;
  * a synonym of a canonical crime  → MERGE into the canonical node (created by renaming when it
                                      does not exist): its relationships move, nothing is lost;
  * ambiguous or no canonical form  → NOT migrated: listed for the owner (the review queue).

Writes `<out>/qa069_crimes_<stamp>.cypher` plus a JSON report. Apply only through Path A (gated
seed rebuild + qa-lint + bump); the rollback is the previous seed.

    cd pipeline && uv run python -m scripts.gen_crime_canonical_migration --snapshot ../neo4j/<export> \
        [--mapear "revelación de secretos=revelación de datos reservados"] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src import config
from src.entity_resolution import entity_id
from src.ontologia import ontologia


def _lit(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def classify(nodes: list[dict], mapear: dict[str, str] | None = None) -> dict:
    """{'merge': [(from_id, from_name, to_id, to_name)], 'owner': [(id, name, motivo)], 'ok': n}.
    `mapear` = the owner's decisions for names the ontology cannot resolve alone (name → canonical)."""
    mapear = mapear or {}
    ont = ontologia()
    crimes = [(n["props"]["entity_id"], n["props"]["name"]) for n in nodes
              if "Crime" in (n.get("labels") or []) and n.get("props", {}).get("entity_id")]
    out = {"merge": [], "owner": [], "ok": 0}
    for eid, name in sorted(crimes):
        res = ont.validar("delitos", name)
        if res.ok and res.valor == name:
            out["ok"] += 1
        elif res.ok:
            out["merge"].append((eid, name, entity_id("Crime", res.valor), res.valor))
        elif name in mapear:
            canon = ont.validar("delitos", mapear[name])
            if not (canon.ok and canon.valor == mapear[name]):
                raise SystemExit(f"--mapear: «{mapear[name]}» no es un delito canónico")
            out["merge"].append((eid, name, entity_id("Crime", canon.valor), canon.valor))
        else:
            out["owner"].append((eid, name, res.motivo))
    return out


def aristas_a_repuntar(snapshot: Path, plan: dict) -> dict[str, int]:
    """Relationships attached to each node the migration merges (what `mergeNodes` re-points)."""
    ids = {m[0] for m in plan["merge"]}
    ref_to_id = {}
    for line in (snapshot / "nodes.jsonl").read_text(encoding="utf-8").splitlines():
        if line:
            n = json.loads(line)
            if n.get("props", {}).get("entity_id") in ids:
                ref_to_id[n["ref"]] = n["props"]["entity_id"]
    out = {i: 0 for i in ids}
    for line in (snapshot / "relationships.jsonl").read_text(encoding="utf-8").splitlines():
        if line:
            r = json.loads(line)
            for end in (r["start"], r["end"]):
                if end in ref_to_id:
                    out[ref_to_id[end]] += 1
    return out


def cypher(plan: dict) -> str:
    lines = ["// spec 069 T4.9 — Crime nodes → canonical ontology names. Generated; review before Path A.",
             f"// ontología v{ontologia().version}. Rollback: the previous seed."]
    for from_id, from_name, to_id, to_name in plan["merge"]:
        lines.append(f"// «{from_name}» → «{to_name}»")
        # Rename when the canonical node does not exist; otherwise merge the synonym into it.
        lines.append(
            f"MATCH (old:Crime {{entity_id: {_lit(from_id)}}}) "
            f"OPTIONAL MATCH (canon:Crime {{entity_id: {_lit(to_id)}}}) "
            f"WITH old, canon "
            f"CALL apoc.do.when(canon IS NULL, "
            # Inside apoc.do.when the map entries are PARAMETERS ($old, $canon), not variables.
            f"'WITH $old AS old SET old.entity_id = $to_id, old.name = $to_name RETURN old AS n', "
            f"'WITH $canon AS canon, $old AS old "
            f"CALL apoc.refactor.mergeNodes([canon, old], {{properties: \"discard\", mergeRels: true}}) "
            f"YIELD node RETURN node AS n', "
            f"{{old: old, canon: canon, to_id: {_lit(to_id)}, to_name: {_lit(to_name)}}}) "
            f"YIELD value RETURN value.n.entity_id AS crime;")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True, help="directorio del export (nodes.jsonl)")
    ap.add_argument("--out", default=str(config.WORK_DIR / "audit" / "patches"))
    ap.add_argument("--stamp", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true", help="solo imprime el conteo; no escribe ficheros")
    ap.add_argument("--mapear", action="append", default=[], metavar="NOMBRE=CANONICO",
                    help="decisión del dueño para un nombre ambiguo o sin forma canónica (repetible)")
    args = ap.parse_args()
    nodes = [json.loads(l) for l in (Path(args.snapshot) / "nodes.jsonl").read_text(encoding="utf-8").splitlines() if l]
    mapear = dict(m.split("=", 1) for m in args.mapear)
    plan = classify(nodes, mapear)
    rel = aristas_a_repuntar(Path(args.snapshot), plan)
    print(f"canónicos {plan['ok']} · a fusionar {len(plan['merge'])} nodos Crime · "
          f"{sum(rel.values())} aristas a repuntar · para el dueño {len(plan['owner'])}")
    for m in plan["merge"]:
        print(f"  fusionar «{m[1]}» → «{m[3]}» ({rel[m[0]]} aristas)")
    for o in plan["owner"]:
        print(f"  dueño: «{o[1]}» — {o[2]}")
    if args.dry_run:
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"qa069_crimes_{args.stamp}.cypher").write_text(cypher(plan), encoding="utf-8")
    (out / f"qa069_crimes_{args.stamp}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"escrito {out / f'qa069_crimes_{args.stamp}.cypher'}")


if __name__ == "__main__":
    main()
