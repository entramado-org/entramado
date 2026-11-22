"""Exporta el efímero del Path A 063 como nodes/relationships.jsonl (forma de neo4j/integral-*) para qa_lint,
y `canonico.tsv` (spec 070): el grafo entero con claves naturales en lugar de elementId, todas las
propiedades ordenadas salvo degree/centralidades (se recomputan después) y el embedding como su sha256,
ordenado, para comparar ciclos y seed base con `cmp`/`diff` en la puerta.

Uso: uv run python scripts/_qa063b_export_snap.py <outdir>  (vive en el repo: el scratchpad pierde ficheros).
"""
import hashlib
import json
import os
import sys

# Las recalcula el Path A tras la puerta (compute_degree, make centralidad): no son un cambio de datos.
EXCLUIDAS = {'degree', 'betweenness', 'closeness', 'community', 'community_label'}
_CLAVES = ('chunk_id', 'doc_id', 'claim_id')


def clean(v):
    if isinstance(v, list):
        return [clean(x) for x in v]
    if hasattr(v, 'iso_format'):
        return v.iso_format()
    return v


def _props(props):
    out = {k: v for k, v in props.items() if k not in EXCLUIDAS and k != 'embedding'}
    if 'embedding' in props:
        out['embedding_sha256'] = hashlib.sha256(json.dumps(props['embedding']).encode()).hexdigest()
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


def clave(labels, props):
    """Clave natural de un nodo: su entity_id, el id de chunk/documento/claim o, si no hay, el hash."""
    if props.get('entity_id'):
        return props['entity_id']
    for key in _CLAVES:
        if props.get(key):
            return f'{(labels or ["?"])[0]}:{props[key]}'
    return '~' + hashlib.sha256((json.dumps(sorted(labels)) + _props(props)).encode()).hexdigest()[:16]


def linea_nodo(labels, props):
    return '\t'.join(('N', clave(labels, props), json.dumps(sorted(labels)), _props(props)))


def linea_arista(start, rtype, end, props):
    return '\t'.join(('R', start, rtype, end, _props(props)))


def main(out):
    from neo4j import GraphDatabase

    os.makedirs(out, exist_ok=True)
    d = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://192.168.64.3:7689'), auth=('neo4j', 'neo4j'))
    claves, canonico = {}, []
    with d.session() as s, open(f'{out}/nodes.jsonl', 'w') as f:
        n = 0
        for r in s.run('MATCH (n) RETURN elementId(n) AS ref, labels(n) AS labels, properties(n) AS props'):
            props = {k: clean(v) for k, v in r['props'].items()}
            f.write(json.dumps({'labels': r['labels'], 'props': props, 'ref': r['ref']},
                               ensure_ascii=False, sort_keys=True) + '\n'); n += 1
            claves[r['ref']] = clave(r['labels'], props)
            canonico.append(linea_nodo(r['labels'], props))
    with d.session() as s, open(f'{out}/relationships.jsonl', 'w') as f:
        m = 0
        for r in s.run('MATCH (a)-[r]->(b) RETURN elementId(r) AS ref, elementId(a) AS start, elementId(b) AS end, '
                       'type(r) AS type, properties(r) AS props'):
            props = {k: clean(v) for k, v in r['props'].items()}
            f.write(json.dumps({'end': r['end'], 'props': props, 'ref': r['ref'],
                                'start': r['start'], 'type': r['type']}, ensure_ascii=False, sort_keys=True) + '\n'); m += 1
            canonico.append(linea_arista(claves[r['start']], r['type'], claves[r['end']], props))
    with open(f'{out}/canonico.tsv', 'w') as f:
        f.writelines(line + '\n' for line in sorted(canonico))
    json.dump({'nodes': {'count': n}, 'relationships': {'count': m}}, open(f'{out}/snapshot.json', 'w'), indent=2)
    print(n, m)


if __name__ == '__main__':
    main(sys.argv[1])
