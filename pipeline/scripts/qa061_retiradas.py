"""Retiradas aprobadas de la spec 061 (AC8) — Path A, SOLO contra un Neo4j EFÍMERO.

El dueño aprobó cinco retiradas sin lectura caso a caso. Tres las aplica este script
sobre el efímero (`_qa061_dev.sh`), después de los loaders y antes del diff
`alledges-061-{pre,post}`; las otras dos (prosa de seudónimos y meta-texto) viajan en
el overlay `summaries_qa061.yaml` / `roles_qa061.yaml`, y aquí solo se imprimen sus
entity_ids para que el humano compruebe que el overlay las cubre.

  1. huérfanas   — Person con 0 aristas de dominio (L18): DETACH DELETE.
  2. ultima_hora — ACCUSED_OF de prensa con `date` anterior a `--as-of` menos 90 días
                   (L11) SALVO que la persona tenga fila `estado` documentada.
  3. contexto    — aristas `source_kind='contexto'` sin `source_url` (L17).
  4. ADIF        — `src.qa_integral_adif_date.apply` (corrección heredada de la 060).

La lista concreta la manda la salida de `qa_lint`: nada se borra que no esté en su
JSONL. Cada operación va en su propia transacción y verifica primero que el recuento
GLOBAL vivo de su capa coincide con el censo del lint; si un loader movió la capa
después del lint, aborta sin escribir — vuelve a correr `make qa-lint` y revisa.

Uso (dentro de Path A, con el efímero levantado; NUNCA contra producción):
    python pipeline/scripts/qa061_retiradas.py --lint pipeline/data/audit/dual/lint-20260909
    python pipeline/scripts/qa061_retiradas.py --lint … --apply
    python pipeline/scripts/qa061_retiradas.py --rollback …/retiradas-<ts>.json --apply
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
from urllib.parse import urlparse


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import qa_integral_adif_date, ultima_hora_loader  # noqa: E402
from src.qa_lint import STALE_DAYS, TECH_RELS, as_date  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
OPERATIONS = ('orphans', 'ultima_hora', 'contexto', 'adif')

COUNT = {
    'orphans': ('MATCH (n:Person) WHERE COUNT { (n)-[r]-() WHERE NOT type(r) IN $tech } = 0 '
                'RETURN count(n) AS n'),
    'ultima_hora': "MATCH ()-[r:ACCUSED_OF]->() WHERE r.source_kind = 'ultima_hora' RETURN count(r) AS n",
    'contexto': ("MATCH ()-[r]->() WHERE r.source_kind = 'contexto' AND r.source_url IS NULL "
                 'RETURN count(r) AS n'),
}
READ_NODE = """
MATCH (n:Person {entity_id: $entity_id})
OPTIONAL MATCH (n)-[r]-(m)
RETURN labels(n) AS labels, properties(n) AS props,
       collect(CASE WHEN r IS NULL THEN NULL ELSE {
           type: type(r), props: properties(r), outgoing: startNode(r) = n,
           other_key: CASE WHEN m.entity_id IS NOT NULL THEN 'entity_id'
                           WHEN m.chunk_id IS NOT NULL THEN 'chunk_id' ELSE 'doc_id' END,
           other_value: coalesce(m.entity_id, m.chunk_id, m.doc_id)} END) AS edges
"""
DELETE_ORPHAN = """
MATCH (n:Person {entity_id: $entity_id})
WHERE COUNT { (n)-[r]-() WHERE NOT type(r) IN $tech } = 0
DETACH DELETE n RETURN count(*) AS n
"""
# Una consulta por capa: el predicado de capa viaja EN el Cypher, nunca en parámetros,
# para que un par de entidades con aristas de otra capa jamás entre en el borrado.
MATCH_EDGE = {
    'ultima_hora': """
        MATCH (a {entity_id: $start})-[r:ACCUSED_OF]->(b {entity_id: $end})
        WHERE r.source_kind = 'ultima_hora'
          AND coalesce(r.date, '') = $date AND coalesce(r.url, '') = $url
    """,
    'contexto': """
        MATCH (a {entity_id: $start})-[r]->(b {entity_id: $end})
        WHERE type(r) = $type AND r.source_kind = 'contexto' AND r.source_url IS NULL
    """,
}
RESTORE_EDGE = 'MATCH (a {%s: $start}), (b {%s: $end}) CREATE (a)-[r:`%s`]->(b) SET r = $props RETURN count(*) AS n'


def now():
    return datetime.now(timezone.utc).isoformat()


def jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def registered_defendants():
    """Personas con fila de registro en accusations.yaml (spec 061 §3.1).

    Misma definición que el cargador de última hora: si difieren, este script retira aristas
    que `make ultima-hora` vuelve a crear en el ciclo siguiente (063, 2026-09-28: una fila
    `estado` con fuente de prensa y sin `doc` contaba para el cargador y no para aquí)."""
    return ultima_hora_loader.registered_defendants()


class Snapshot:
    """Solo lo que las retiradas necesitan del export congelado: aristas por clave semántica."""

    def __init__(self, path):
        path = Path(path)
        self.ids = {}
        with (path / 'nodes.jsonl').open() as f:
            for line in f:
                record = json.loads(line)
                self.ids[record['ref']] = record['props'].get('entity_id')
        self.rels = {}
        self.ultima_hora_accusations = 0
        with (path / 'relationships.jsonl').open() as f:
            for line in f:
                record = json.loads(line)
                self.rels[record['ref']] = record
                if record['type'] == 'ACCUSED_OF' and record['props'].get('source_kind') == 'ultima_hora':
                    self.ultima_hora_accusations += 1

    def edge(self, ref):
        record = self.rels[ref]
        return {'type': record['type'], 'start': self.ids.get(record['start']),
                'end': self.ids.get(record['end']), 'props': record['props']}


def plan_orphans(lint):
    return [{'entity_id': row['entity_id'], 'detail': row['detail']}
            for row in jsonl(lint / 'L18.jsonl') if row['entity_id']]


def plan_ultima_hora(lint, snap, registered, as_of):
    """Solo las aristas caducadas de personas SIN fila de registro (el registro manda)."""
    horizon = as_of - timedelta(days=STALE_DAYS)
    keys = {}
    for row in jsonl(lint / 'L11.jsonl'):
        edge = snap.edge(row['ref'])
        when = as_date(edge['props'].get('date'))
        if edge['start'] in registered or when is None or when >= horizon:
            continue
        key = (edge['start'], edge['end'], str(edge['props'].get('date') or ''),
               str(edge['props'].get('url') or ''))
        keys[key] = keys.get(key, 0) + 1
    return [{'start': s, 'end': e, 'type': 'ACCUSED_OF', 'params': {'date': d, 'url': u}, 'expected': n}
            for (s, e, d, u), n in sorted(keys.items())]


def plan_contexto(lint, snap):
    keys = {}
    for row in jsonl(lint / 'L17.jsonl'):
        edge = snap.edge(row['ref'])
        if edge['props'].get('source_url'):
            continue
        key = (edge['start'], edge['end'], edge['type'])
        keys[key] = keys.get(key, 0) + 1
    return [{'start': s, 'end': e, 'type': t, 'params': {'type': t}, 'expected': n}
            for (s, e, t), n in sorted(keys.items())]


def read_targets(session, plan):
    """Lee del grafo lo que se va a borrar ANTES de tocarlo: el diario nace del grafo real."""
    journal = {'orphans': [], 'ultima_hora': [], 'contexto': []}
    for item in plan['orphans']:
        row = session.run(READ_NODE, entity_id=item['entity_id']).single()
        if row is None:
            raise ValueError(f'{item["entity_id"]}: ya no existe en el grafo')
        edges = [e for e in row['edges'] if e]
        domain = sorted({e['type'] for e in edges} - TECH_RELS)
        if domain:
            raise ValueError(f'{item["entity_id"]}: ya no es huérfana ({", ".join(domain)})')
        journal['orphans'].append({'entity_id': item['entity_id'], 'labels': sorted(row['labels']),
                                   'props': dict(row['props']), 'edges': edges})
    for name in ('ultima_hora', 'contexto'):
        for item in plan[name]:
            rows = list(session.run(MATCH_EDGE[name] + ' RETURN properties(r) AS props',
                                    start=item['start'], end=item['end'], **item['params']))
            if len(rows) != item['expected']:
                raise ValueError(f'{name} {item["start"]}→{item["end"]} ({item["type"]}): '
                                 f'{len(rows)} aristas vivas, el lint censó {item["expected"]}')
            journal[name].append({**item, 'props': [dict(r['props']) for r in rows]})
    return journal


def apply_removals(driver, plan, journal, census):
    """Una transacción por operación; el recuento global de la capa se verifica dentro."""
    applied = {}

    def guard(tx, name):
        live = tx.run(COUNT[name], tech=sorted(TECH_RELS)).single()['n']
        if live != census[name]:
            raise ValueError(f'{name}: la capa viva tiene {live} y el lint censó {census[name]}; '
                             'vuelve a correr qa-lint sobre el estado actual')

    if plan['orphans']:
        def orphans(tx):
            guard(tx, 'orphans')
            done = sum(tx.run(DELETE_ORPHAN, entity_id=item['entity_id'],
                              tech=sorted(TECH_RELS)).single()['n'] for item in journal['orphans'])
            if done != len(journal['orphans']):
                raise ValueError(f'huérfanas: se borraron {done} de {len(journal["orphans"])}')
            return done
        with driver.session() as session:
            applied['orphans'] = session.execute_write(orphans)
    for name in ('ultima_hora', 'contexto'):
        if not plan[name]:
            continue

        def edges(tx, name=name):
            guard(tx, name)
            done = 0
            for item in journal[name]:
                deleted = tx.run(MATCH_EDGE[name] + ' DELETE r RETURN count(*) AS n',
                                 start=item['start'], end=item['end'],
                                 **item['params']).single()['n']
                if deleted != item['expected']:
                    raise ValueError(f'{name} {item["start"]}→{item["end"]}: borró {deleted}, '
                                     f'esperaba {item["expected"]}')
                done += deleted
            return done
        with driver.session() as session:
            applied[name] = session.execute_write(edges)
    return applied


def restore(driver, journal):
    """Restituye exactamente lo borrado: nodos con sus props y sus aristas técnicas, luego aristas."""
    done = {'orphans': 0, 'orphan_edges': 0, 'ultima_hora': 0, 'contexto': 0}
    for item in journal.get('orphans') or []:
        labels = item['labels']
        if not all(NAME.fullmatch(l) for l in labels):
            raise ValueError(f'{item["entity_id"]}: etiquetas no representables {labels}')

        def node(tx, item=item, labels=labels):
            tx.run(f'MERGE (n:`{"`:`".join(labels)}` {{entity_id: $entity_id}}) SET n = $props',
                   entity_id=item['entity_id'], props=item['props'])
            restored = 0
            for edge in item['edges']:
                if not NAME.fullmatch(edge['type']):
                    raise ValueError(f'tipo de arista no representable: {edge["type"]}')
                keys = (('entity_id', edge['other_key']) if edge['outgoing']
                        else (edge['other_key'], 'entity_id'))
                values = ((item['entity_id'], edge['other_value']) if edge['outgoing']
                          else (edge['other_value'], item['entity_id']))
                restored += tx.run(RESTORE_EDGE % (keys[0], keys[1], edge['type']),
                                   start=values[0], end=values[1], props=edge['props']).single()['n']
            return restored
        with driver.session() as session:
            done['orphan_edges'] += session.execute_write(node)
            done['orphans'] += 1
    for name in ('ultima_hora', 'contexto'):
        for item in journal.get(name) or []:
            def edges(tx, item=item):
                return sum(tx.run(RESTORE_EDGE % ('entity_id', 'entity_id', item['type']),
                                  start=item['start'], end=item['end'],
                                  props=props).single()['n'] for props in item['props'])
            with driver.session() as session:
                done[name] += session.execute_write(edges)
    return done


def overlay_crosscheck(lint):
    """L6/L7 no se retiran aquí: se listan para cotejar el overlay summaries_qa061.yaml."""
    return {check: sorted({f"{r['entity_id']}#{r['field']}" for r in jsonl(lint / f'{check}.jsonl')
                           if r['entity_id']})
            for check in ('L06', 'L07')}


def private_host(uri):
    host = urlparse(uri).hostname or ''
    try:
        address = ipaddress.ip_address(socket.gethostbyname(host))
    except (OSError, ValueError):
        # Nombres internos de las máquinas Apple container (`neo4j`, `sm_neo4j_editqa061`).
        return '.' not in host
    return address.is_private or address.is_loopback


def preview(plan, census, live, crosscheck):
    print(f"huérfanas L18      : {len(plan['orphans'])} nodos "
          f"(censo {census['orphans']} / vivo {live['orphans']})")
    for item in plan['orphans'][:10]:
        print(f"    - {item['entity_id']}: {item['detail']}")
    for name, label in (('ultima_hora', 'última hora L11'), ('contexto', 'contexto L17   ')):
        total = sum(i['expected'] for i in plan[name])
        print(f"{label}    : {total} aristas en {len(plan[name])} pares "
              f"(capa: censo {census[name]} / vivo {live[name]})")
        for item in plan[name][:10]:
            extra = ' '.join(f'{k}={v}' for k, v in item['params'].items() if v and k != 'type')
            print(f"    - {item['start']} -[{item['type']}]-> {item['end']} ×{item['expected']} {extra}")
    print('ADIF               : qa_integral_adif_date.apply (1 arista, before/after revisados)')
    for check, items in crosscheck.items():
        print(f"{check} (overlay, NO se retira aquí): {len(items)} entradas — comprobar que "
              'summaries_qa061/roles_qa061 las cubren')
        for item in items[:10]:
            print(f'    · {item}')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lint', type=Path, help='directorio de salida de qa_lint')
    p.add_argument('--snapshot', type=Path, help='export congelado (por defecto: el del manifest del lint)')
    p.add_argument('--as-of', help='fecha de corte (por defecto: la del manifest del lint)')
    p.add_argument('--out', type=Path, help='dónde escribir el diario (por defecto: el dir del lint)')
    p.add_argument('--neo4j-uri', default=os.environ.get('NEO4J_URI', 'bolt://localhost:7689'))
    p.add_argument('--user', default=os.environ.get('NEO4J_USERNAME', 'neo4j'))
    p.add_argument('--password', default=os.environ.get('NEO4J_PASSWORD', 'neo4j'))
    p.add_argument('--only', default='', help='subconjunto: ' + ','.join(OPERATIONS))
    p.add_argument('--apply', action='store_true', help='ejecuta (por defecto: dry-run)')
    p.add_argument('--rollback', type=Path, help='deshace un diario retiradas-<ts>.json')
    p.add_argument('--allow-remote', action='store_true', help='permite un bolt no privado (jamás en prod)')
    args = p.parse_args()
    only = {o.strip() for o in args.only.split(',') if o.strip()} or set(OPERATIONS)
    if unknown := only - set(OPERATIONS):
        p.error(f'operaciones desconocidas: {sorted(unknown)}')
    if args.apply and not (args.allow_remote or private_host(args.neo4j_uri)):
        p.error(f'{args.neo4j_uri} no es un host privado; las retiradas son Path A sobre el efímero')

    from neo4j import GraphDatabase  # lazy: importar el driver no debe costar en --help
    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.user, args.password))
    try:
        if args.rollback:
            journal = json.loads(args.rollback.read_text())
            print(f'rollback de {args.rollback} ({journal.get("created_at")}, '
                  f'estado {journal.get("status")}):')
            for name in ('orphans', 'ultima_hora', 'contexto'):
                print(f'  {name}: {len(journal.get(name) or [])} entradas')
            if journal.get('adif'):
                print('  adif: 1 arista (inversa de qa_integral_adif_date)')
            if not args.apply:
                print('dry-run — nada escrito. Añade --apply para restituir.')
                return 0
            done = restore(driver, journal)
            if journal.get('adif'):
                done['adif'] = qa_integral_adif_date.apply(driver, rollback=True)
            print(json.dumps(done, ensure_ascii=False, indent=2))
            return 0

        if not args.lint:
            p.error('--lint es obligatorio salvo con --rollback')
        manifest = json.loads((args.lint / 'manifest.json').read_text())
        summary = json.loads((args.lint / 'summary.json').read_text())
        snapshot = args.snapshot or Path(manifest['snapshot'])
        as_of = date.fromisoformat(args.as_of or manifest['as_of'])
        snap = Snapshot(snapshot)
        registered = registered_defendants()
        plan = {'orphans': plan_orphans(args.lint) if 'orphans' in only else [],
                'ultima_hora': (plan_ultima_hora(args.lint, snap, registered, as_of)
                                if 'ultima_hora' in only else []),
                'contexto': plan_contexto(args.lint, snap) if 'contexto' in only else []}
        census = {'orphans': summary['L18']['count'], 'contexto': summary['L17']['count'],
                  'ultima_hora': snap.ultima_hora_accusations}
        with driver.session() as session:
            live = {name: session.run(query, tech=sorted(TECH_RELS)).single()['n']
                    for name, query in COUNT.items()}
        preview(plan, census, live, overlay_crosscheck(args.lint))
        if not args.apply:
            print('\ndry-run — nada escrito. Añade --apply (efímero Path A) para ejecutar.')
            return 0
        with driver.session() as session:
            journal = read_targets(session, plan)
        journal.update({'created_at': now(), 'lint': str(args.lint.resolve()),
                        'snapshot': str(Path(snapshot).resolve()), 'as_of': as_of.isoformat(),
                        'neo4j_uri': args.neo4j_uri, 'census': census, 'live_before': live,
                        'status': 'pending'})
        out = args.out or args.lint
        out.mkdir(parents=True, exist_ok=True)
        path = out / f'retiradas-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json'
        # El diario se publica ANTES de borrar: sin él no hay rollback posible.
        path.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n')
        applied = apply_removals(driver, plan, journal, census)
        if 'adif' in only:
            applied['adif'] = qa_integral_adif_date.apply(driver)
            journal['adif'] = bool(applied['adif'])
        journal['status'] = 'applied'
        journal['applied'] = applied
        path.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n')
        print(f'\ndiario: {path}')
        print(json.dumps(applied, ensure_ascii=False, indent=2))
        return 0
    finally:
        driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
