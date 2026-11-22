"""Export every physical graph record, in one read transaction, from seed-only Neo4j.

Only used inside the isolated container network by qa_integral_seed.sh.
"""
import hashlib
import json
from pathlib import Path
from neo4j import GraphDatabase

out = Path('/out')
queries = {
    'nodes': 'MATCH (n) RETURN elementId(n) AS ref, labels(n) AS labels, properties(n) AS props ORDER BY ref',
    'relationships': 'MATCH (a)-[r]->(b) RETURN elementId(r) AS ref, elementId(a) AS start, elementId(b) AS end, type(r) AS type, properties(r) AS props ORDER BY ref',
}
with GraphDatabase.driver('bolt://localhost:7687', auth=None) as driver:
    with driver.session(default_access_mode='READ') as session:
        with session.begin_transaction() as tx:
            counts = {}
            for kind, query in queries.items():
                path = out / f'{kind}.jsonl'
                if path.exists():
                    raise RuntimeError(f'refusing to overwrite {path}')
                count = 0
                with path.open('x') as f:
                    for record in tx.run(query):
                        f.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True, default=str) + '\n')
                        count += 1
                counts[kind] = {'count': count, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            (out / 'snapshot.json').write_text(json.dumps(counts, indent=2))
            print(json.dumps(counts))
