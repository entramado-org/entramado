"""Persistent correction for the ADIF receipt date; invoke only during reviewed Path A.

No default connection or CLI: the caller supplies the isolated driver. The exact
before/after maps are checked transactionally; rerunning converges, drift aborts.
"""
MATCH = """MATCH (a {entity_id:$source})-[r:RECEIVED]->(b {entity_id:$target})
WHERE r.source_chunk_id=$chunk AND r.source_doc=$doc
"""
PARAMS = {'source':'Organization:administrador-de-infraestructuras-ferroviarias',
          'target':'Event:mascarillas', 'chunk':'0130bbc1ef7a590f',
          'doc':'20240819_informe_audit_ejec_om_adquis_mascarillas_an.pdf'}
BEFORE = {'date':'26 de marzo de 2020','date_norm':'2020','date_year':2020,
          'source_chunk_id':PARAMS['chunk'],'source_doc':PARAMS['doc'],
          'source_kind':'judicial','source_page':54,'source_quote':'mascarillas adquiridas por ADIF'}
# Retain the already recorded annual precision; remove only the unsupported day.
AFTER = {k:v for k,v in BEFORE.items() if k != 'date'}


def apply(driver, *, rollback=False):
    expected, desired = (AFTER, BEFORE) if rollback else (BEFORE, AFTER)
    def mutate(tx):
        rows=list(tx.run(MATCH+'RETURN properties(r) AS props', **PARAMS))
        if len(rows)!=1:
            raise ValueError(f'ADIF correction expects one edge; found {len(rows)}')
        props=dict(rows[0]['props'])
        if props == desired:return 0
        if props != expected:raise ValueError('ADIF edge drifted from reviewed before/after maps')
        count=tx.run(MATCH+'AND properties(r)=$expected SET r=$desired RETURN count(r) AS n',
                     **PARAMS,expected=expected,desired=desired).single()['n']
        if count!=1:raise ValueError('ADIF correction precondition changed')
        return count
    with driver.session() as session:
        return session.execute_write(mutate)
