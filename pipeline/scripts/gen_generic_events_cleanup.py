"""Genera migración + rollback para eliminar los nodos basura :Event de sustantivo genérico.

Son artefactos de extracción: nodos :Event cuyo nombre es un sustantivo genérico (contrato,
registro, software, SWIFT, cuenta bancaria, emergencia, audiencia, diligencias…) o un mal-tipado
(Event:logrono duplica Location:logrono). Sus aristas (INVOLVES / PARTICIPATED_IN / OCCURRED_AT +
los pocos PARTY_TO/OWNS/RECEIVED/WORKS_FOR donde el nodo genérico es el OBJETO) son co-ocurrencia
boilerplate: el actor está "party to a contract" / "owns software" con un sustantivo genérico como
objeto, no con un referente concreto. CADA atribución real persiste en el nodo propio del actor y
en el texto de los :Chunk (que quedan intactos). Borrar estos nodos NO pierde ninguna atribución real.

Verificado arista-por-arista (volcado de evidencia, precedente Crime:delito / PR #79):
las 24 aristas de tipo "semántico" tienen siempre al nodo genérico como extremo objeto y citan
boilerplate ("contrato entre Idella y Aldesa", "tres obras adjudicadas a ACCIONA", "jefa de
gabinete del subsecretario", regiones que co-ocurren con la palabra "emergencia"…).

DETACH DELETE solo elimina las relaciones DE cada nodo (incl. los MENTIONED_IN→:Chunk); los
:Chunk/:Document y su texto quedan intactos. El rollback recrea cada nodo + TODAS sus relaciones
con sus propiedades → 100% reversible.

Lista derivada del grafo vivo (`MATCH (n:Event)` + blocklist normalizado de sustantivos genéricos
y sus variantes plural/singular). Reproducible: re-correr regenera idéntico mientras el grafo no
cambie. Uso: `cd pipeline && uv run python scripts/gen_generic_events_cleanup.py <stamp>`.
"""
import os
import sys
from neo4j import GraphDatabase

STAMP = sys.argv[1] if len(sys.argv) > 1 else "manual"
OUT = "data/audit/patches"

# 56 nodos :Event de sustantivo genérico / mal-tipados (set confirmado por el dueño +
# 27 variantes del mismo ruido). Logroño es un mal-tipado (dup de Location:logrono).
EIDS = [
    "Event:8-expedientes",
    "Event:actividad-contratada",
    "Event:apertura-de-cuenta",
    "Event:audiencia",
    "Event:auditoria",
    "Event:autorizacion-de-vuelos",
    "Event:autorizacion-judicial",
    "Event:autorizaciones-administrativas",
    "Event:bloqueo-de-cuenta",
    "Event:bloqueo-de-cuentas",
    "Event:cadena-de-custodia",
    "Event:cambio-de-divisas",
    "Event:cancelacion-de-cuenta",
    "Event:cheques",
    "Event:clonado",
    "Event:contratacion",
    "Event:contratacion-publica",
    "Event:contrataciones-directas",
    "Event:contrato",
    "Event:contrato-de-comisionista",
    "Event:correo-electronico",
    "Event:correos-institucionales",
    "Event:correos-particulares",
    "Event:cuenta-bancaria",
    "Event:cuentas-corrientes",
    "Event:dacion-de-cuenta",
    "Event:depositos-de-cuentas",
    "Event:diligencias",
    "Event:diligencias-previas",
    "Event:documentacion",
    "Event:documentacion-bancaria",
    "Event:documento-de-identificacion",
    "Event:emergencia",
    "Event:expediente",
    "Event:ingresos-en-efectivo",
    "Event:licitacion",
    "Event:licitaciones",
    "Event:logrono",
    "Event:numero-de-cuenta",
    "Event:obra-de-emergencia",
    "Event:obra-publica",
    "Event:prision-provisional",
    "Event:prohibicion-de-contratar",
    "Event:reclamacion",
    "Event:registro",
    "Event:reintegros",
    "Event:reunion",
    "Event:reunion-de-trabajo",
    "Event:reuniones",
    "Event:software",
    "Event:subsecretaria",
    "Event:swift",
    "Event:transferencia-bancaria",
    "Event:transferencias",
    "Event:tres-reclamaciones",
    "Event:volcado",
]


def cy_val(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(cy_val(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + s + "'"


def cy_map(props):
    return "{" + ", ".join(f"{k}: {cy_val(v)}" for k, v in props.items()) + "}"


d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", os.getenv("NEO4J_PASSWORD", "changeme")))

nodes = []   # (eid, labels, props)
all_rels = {}  # eid -> [rel dicts]
tot_sem = tot_men = 0
with d.session() as s:
    for eid in EIDS:
        node = s.run(
            "MATCH (c {entity_id:$e}) RETURN labels(c) AS labels, properties(c) AS props", e=eid
        ).single()
        if node is None:
            print(f"WARN: {eid} no existe (saltado)")
            continue
        nprops = dict(node["props"])
        nprops.pop("embedding", None)
        nodes.append((eid, list(node["labels"]), nprops))
        rels = []
        for x in s.run(
            """
            MATCH (c {entity_id:$e})-[r]-(o)
            RETURN type(r) AS rt,
                   startNode(r).entity_id = $e AS outgoing,
                   coalesce(o.entity_id, o.chunk_id) AS okey,
                   (o.entity_id IS NOT NULL) AS is_entity,
                   properties(r) AS rp
            """,
            e=eid,
        ):
            rels.append(dict(x))
        all_rels[eid] = rels
        tot_sem += sum(1 for r in rels if r["is_entity"])
        tot_men += sum(1 for r in rels if not r["is_entity"])

n = len(nodes)

# ---------- migration ----------
mig = []
mig.append("// GENERATED by gen_generic_events_cleanup.py — elimina los nodos basura :Event de sustantivo genérico.")
mig.append(f"// {n} nodos (sustantivos genéricos: contrato/registro/software/SWIFT/emergencia/audiencia… + Event:logrono mal-tipado).")
mig.append(f"// {tot_sem} aristas semánticas (co-ocurrencia boilerplate, verificadas) + {tot_men} MENTIONED_IN→:Chunk.")
mig.append("// DETACH DELETE solo borra SUS relaciones; los :Chunk/:Document quedan intactos. REVISABLE Y REVERSIBLE.")
mig.append("// Apply GATED: make qa-migrate FILE=… CONFIRM=1.  Tras aplicar: make centralidad && make healthcheck.")
mig.append("")
for eid, _, _ in nodes:
    mig.append(f"MATCH (c {{entity_id:'{eid}'}}) DETACH DELETE c;")
mig.append("")
open(f"{OUT}/manual_fix_generic_events_{STAMP}.cypher", "w").write("\n".join(mig))

# ---------- rollback ----------
rb = []
rb.append(f"// ROLLBACK de manual_fix_generic_events_{STAMP}.cypher — recrea los {n} nodos + todas sus relaciones.")
rb.append(f"// Recrea cada nodo con sus props y sus {tot_sem} aristas semánticas + {tot_men} MENTIONED_IN.")
rb.append("// (centralidad/community se recalculan con `make centralidad`; aquí se restauran tal cual).")
rb.append("")
for eid, labels, nprops in nodes:
    lbl = ":" + ":".join(labels)
    rb.append(f"CREATE (c{lbl} {cy_map(nprops)});")
rb.append("")
for eid, _, _ in nodes:
    for r in all_rels[eid]:
        if r["is_entity"]:
            match_o = f"(o {{entity_id:{cy_val(r['okey'])}}})"
        else:
            match_o = f"(o:Chunk {{chunk_id:{cy_val(r['okey'])}}})"
        rp = cy_map(r["rp"]) if r["rp"] else ""
        relpat = f"[:{r['rt']}{(' ' + rp) if rp else ''}]"
        pat = f"(c)-{relpat}->(o)" if r["outgoing"] else f"(c)<-{relpat}-(o)"
        rb.append(f"MATCH (c {{entity_id:'{eid}'}}), {match_o} CREATE {pat};")
rb.append("")
open(f"{OUT}/manual_fix_generic_events_{STAMP}.rollback.cypher", "w").write("\n".join(rb))

print(f"nodos={n}  semantic_edges={tot_sem}  mentioned_in={tot_men}")
print(f"wrote {OUT}/manual_fix_generic_events_{STAMP}.cypher (+ .rollback.cypher)")
d.close()
