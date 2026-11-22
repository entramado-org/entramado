"""Deterministic tests of the dual census and its text packets (spec 061), which the staged
audit (spec 063) builds on. No model call, no network, no graph. The 061 readers, gate and
loop were retired by spec 069 F5 together with their tests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.src import qa_dual
from pipeline.src import qa_dual_packets
from pipeline.src.qa_dual_packets import (AMPLIADO_LIMITS, AMPLIADO_RESOLUTION, DEFAULT_LIMITS,
                                          LEDE_CHARS, MAX_GROUP_UNITS, MAX_SOURCE_CHARS,
                                          OMISSION_MARK, TextPacketBuilder, anchor_terms, anchored_windows,
                                          group_units, windowed_text)
from pipeline.src.qa_integral import canonical, connect, digest, file_hash
from pipeline.src.qa_integral_sources import MAX_PAGE_CHARS, TRUNCATION_MARK, clip_page

PAGE = 'El original dice que Alejo pago 5.700 euros a la Empresa SA. Condena no firme.'
# Page 2 of the same document proves the third sentence of the summary; nothing else does.
PAGE2 = 'La sentencia fija una indemnizacion de 34.450 euros a INECO a cargo de Alejo Perez.'
SUMMARY = ('Alejo pago 5.700 euros a la Empresa SA. La sentencia no es firme. '
           'Indemnizo con 34.450 euros a INECO. Rige plenamente la presuncion de inocencia.')


class DualBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.snapshot = self.base / 'snapshot'
        self.snapshot.mkdir()
        self.pdf_dir = self.base / 'data'
        self.pdf_dir.mkdir()
        self.write_pdf()
        self.nodes = [
            {'ref': 'n1', 'labels': ['Person'], 'props': {
                'entity_id': 'Person:alejo', 'name': 'Alejo Pérez', 'aliases': ['Alejo Pérez'],
                'degree': 12, 'summary': SUMMARY, 'role': 'Exministro del ramo.',
                'summary_sources': ['https://example.com/fuente']}},
            {'ref': 'n2', 'labels': ['Person'], 'props': {
                'entity_id': 'Person:benito', 'name': 'Benito', 'degree': 2, 'is_pseudonym': True,
                'summary': 'Benito es un seudónimo judicial.'}},
            {'ref': 'n3', 'labels': ['Company'], 'props': {
                'entity_id': 'Company:empresa', 'name': 'Empresa SA', 'degree': 5,
                'summary': 'La empresa contrató a Alejo Pérez. Factura sin autor conocido.'}},
            {'ref': 'n4', 'labels': ['Crime'], 'props': {'entity_id': 'Crime:cohecho', 'name': 'cohecho'}},
            {'ref': 'c1', 'labels': ['Chunk'], 'props': {
                'chunk_id': 'ch1', 'doc_id': 'original.pdf', 'page_start': 1, 'text': PAGE}},
            {'ref': 'c2', 'labels': ['Chunk'], 'props': {
                'chunk_id': 'ch2', 'doc_id': 'sin-pdf.pdf', 'page_start': 3,
                'text': 'X' * 12000 + ' Alejo pago 5.700 euros segun el acta. ' + 'Y' * 6000}},
            # A chunk on page 2 that carries the audited figure, and one that only has
            # the name: mention order must not decide which travels in the packet.
            {'ref': 'c3', 'labels': ['Chunk'], 'props': {
                'chunk_id': 'ch3', 'doc_id': 'original.pdf', 'page_start': 2, 'text': PAGE2}},
            {'ref': 'c4', 'labels': ['Chunk'], 'props': {
                'chunk_id': 'ch4', 'doc_id': 'original.pdf', 'page_start': 5,
                'text': 'Alejo Perez comparecio. Sin cifras ni fechas relevantes.'}},
            {'ref': 'd1', 'labels': ['Document'], 'props': {'doc_id': 'original.pdf'}},
        ]
        judicial = {'source_doc': 'original.pdf', 'source_page': 1, 'source_chunk_id': 'ch1',
                    'source_kind': 'judicial', 'source_quote': 'El original dice que Alejo pago'}
        self.rels = [
            {'ref': 'r1', 'start': 'n1', 'end': 'n4', 'type': 'ACCUSED_OF',
             'props': {**judicial, 'status': 'investigado', 'article': 'art. 419 CP'}},
            {'ref': 'r2', 'start': 'n1', 'end': 'n3', 'type': 'PAID',
             'props': {**judicial, 'amount': 5700.0, 'currency': 'EUR'}},
            {'ref': 'r3', 'start': 'n1', 'end': 'n3', 'type': 'ASSOCIATED_WITH', 'props': {**judicial}},
            {'ref': 'r4', 'start': 'n1', 'end': 'c1', 'type': 'MENTIONED_IN', 'props': {}},
            {'ref': 'r5', 'start': 'n1', 'end': 'c2', 'type': 'MENTIONED_IN', 'props': {}},
            {'ref': 'r6', 'start': 'c1', 'end': 'd1', 'type': 'PART_OF', 'props': {}},
            {'ref': 'r9', 'start': 'n1', 'end': 'c4', 'type': 'MENTIONED_IN', 'props': {}},
            {'ref': 'r10', 'start': 'n1', 'end': 'c3', 'type': 'MENTIONED_IN', 'props': {}},
            {'ref': 'r7', 'start': 'n1', 'end': 'n3', 'type': 'APPOINTED_TO', 'props': {'boe_key': 'x'}},
        ]
        self.write_snapshot()
        artifact = self.base / 'seed.tgz'
        artifact.write_bytes(b'synthetic-dual-seed')
        self.seed = {'version': 'test-dual', 'sha256': file_hash(artifact), 'artifact_path': str(artifact)}
        self.root = self.base / 'audit' / 'run'
        self.manifest = qa_dual.census(self.root, self.snapshot, self.seed, self.base,
                                       {'PAID': 'Pagó a'}, editorial=False, t4_sample=100)

    def tearDown(self):
        self.tmp.cleanup()

    def write_pdf(self):
        import fitz
        doc = fitz.open()
        for i in range(6):
            page = doc.new_page()
            page.insert_text((40, 80), PAGE if i == 0 else PAGE2 if i == 1 else f'Pagina de relleno {i + 1}.')
            # A real page has a text layer worth reading; below MIN_PAGE_TEXT_CHARS the
            # builder treats it as scanned and falls back to the stored chunk.
            for line in range(8):
                page.insert_text((40, 110 + line * 18), f'Linea {line} del cuerpo de la pagina {i + 1}.')
        doc.save(self.pdf_dir / 'original.pdf')
        doc.close()

    def write_snapshot(self):
        metadata = {}
        for name, rows in [('nodes', self.nodes), ('relationships', self.rels)]:
            (self.snapshot / f'{name}.jsonl').write_text(''.join(canonical(r) + '\n' for r in rows))
            metadata[name] = {'count': len(rows), 'sha256': file_hash(self.snapshot / f'{name}.jsonl')}
        (self.snapshot / 'snapshot.json').write_text(canonical(metadata))

    def units(self, **where):
        db = connect(self.root)
        clause = ' AND '.join(f'{k}=?' for k in where) or '1=1'
        rows = [dict(r) for r in db.execute(
            f'SELECT u.*,t.tier,t.rank,o.type FROM units u JOIN tiers t USING(unit_id) '
            f'JOIN objects o USING(object_id) WHERE {clause} ORDER BY t.rank DESC,u.unit_id',
            tuple(where.values()))]
        db.close()
        return rows

    def state(self, uid):
        db = connect(self.root)
        value = db.execute('SELECT state,resolution FROM units WHERE unit_id=?', (uid,)).fetchone()
        db.close()
        return value['state'], value['resolution']

    def unit_like(self, kind, like):
        db = connect(self.root)
        uid = db.execute('SELECT unit_id FROM units WHERE kind=? AND value LIKE ?', (kind, like)).fetchone()[0]
        db.close()
        return kind, uid


class CensusTest(DualBase):
    def test_universe_is_bounded_and_tiered(self):
        tiers = {u['type']: u['tier'] for u in self.units(kind='edge')}
        self.assertEqual(tiers, {'ACCUSED_OF': 'T1', 'PAID': 'T2', 'ASSOCIATED_WITH': 'T4'})
        self.assertEqual([u['tier'] for u in self.units(kind='summary')], ['T0'] * 6)
        # Chunk/Document/technical links and the deterministic T3 layer never enter.
        self.assertFalse([u for u in self.units() if u['type'] in {'Chunk', 'Document', 'APPOINTED_TO'}])
        # 6 de Alejo (identidad, 4 frases de summary —una es salvaguarda—, rol) + 2 de
        # Benito + 1 frase de la empresa que nombra a una persona.
        self.assertEqual(self.manifest['tiers'], {'T0': 9, 'T1': 1, 'T2': 1, 'T4': 1})

    def test_only_context_prose_naming_a_person_enters(self):
        db = connect(self.root)
        rows = list(db.execute("SELECT u.value FROM units u JOIN objects o USING(object_id) "
                               "WHERE o.entity='Company:empresa' AND u.kind='summary'"))
        db.close()
        self.assertEqual([json.loads(r['value']).strip() for r in rows],
                         ['La empresa contrató a Alejo Pérez.'])

    def test_tiers_and_ids_are_deterministic(self):
        other = self.base / 'audit' / 'again'
        qa_dual.census(other, self.snapshot, self.seed, self.base, {'PAID': 'Pagó a'}, t4_sample=100)
        second = json.loads((other / 'manifest.json').read_text())
        self.assertEqual(second['universe_sha256'], self.manifest['universe_sha256'])
        db = connect(other)
        again = {r['unit_id']: (r['tier'], r['rank']) for r in db.execute('SELECT * FROM tiers')}
        db.close()
        self.assertEqual({u['unit_id']: (u['tier'], u['rank']) for u in self.units()}, again)

    def test_frozen_census_and_verifier(self):
        self.assertTrue(qa_dual.verify_census(self.root))
        with self.assertRaises(ValueError):
            qa_dual.census(self.root, self.snapshot, self.seed, self.base)
        db = connect(self.root)
        with db:
            db.execute("UPDATE units SET value='\"otra cosa\"' WHERE kind='summary'")
        db.close()
        with self.assertRaises(ValueError):
            qa_dual.verify_census(self.root)

    def test_editorial_units_come_from_files(self):
        repo = self.base / 'repo'
        (repo / 'pipeline/data/ultima_hora').mkdir(parents=True)
        (repo / 'pipeline/data/ultima_hora/feed.yaml').write_text(
            'items:\n  - iso: "2026-09-01"\n    headline: Titular\n    dek: Cuerpo del ítem.\n'
            '    url: https://example.com/n\n')
        rows = qa_dual._editorial_units(repo)
        self.assertEqual([r['layer'] for r in rows], ['feed'])
        self.assertEqual(rows[0]['value'], 'Titular\nCuerpo del ítem.')


class GroupingAndPacketTest(DualBase):
    def builder(self, fetch=False):
        return TextPacketBuilder(self.root, self.base, fetch=fetch)

    def groups(self, tier):
        db = connect(self.root)
        try:
            return group_units(db, tier)
        finally:
            db.close()

    def test_groups_never_exceed_the_cap_and_share_a_key(self):
        db = connect(self.root)
        oid = db.execute("SELECT object_id FROM units WHERE kind='summary' LIMIT 1").fetchone()[0]
        with db:
            for i in range(12):
                uid = digest(['extra', i])
                db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                           ' VALUES (?,?,?,?,?,0,100,"[]")', (uid, oid, 'summary', f'/summary@{i}:{i + 5}',
                                                              canonical(f'Frase numero {i}.')))
                db.execute('INSERT INTO tiers VALUES (?,?,?)', (uid, 'T0', 1.0))
        db.close()
        groups = self.groups('T0')
        self.assertTrue(all(len(g['unit_ids']) <= MAX_GROUP_UNITS for g in groups))
        self.assertTrue(all(g['group_key'].startswith(('entity:', 'item:')) for g in groups))
        # Todas las pendientes y solo las pendientes: la salvaguarda ya está cerrada.
        self.assertEqual(sum(len(g['unit_ids']) for g in groups),
                         len([u for u in self.units(tier='T0') if u['state'] == 'pending']))

    def test_edges_group_by_document_page(self):
        keys = {g['group_key'] for g in self.groups('T1')}
        self.assertEqual(keys, {'doc:original.pdf:1'})

    def test_packet_is_text_only_and_capped(self):
        builder = self.builder()
        try:
            packet = builder.build_group(self.groups('T1')[0])
        finally:
            builder.db.close()
        self.assertNotIn('image_locators', packet)
        self.assertNotIn('public_api', packet)
        self.assertFalse(list(self.root.rglob('*.png')))
        pdf = next(s for s in packet['sources'] if s.get('doc') == 'original.pdf')
        self.assertLessEqual(len(pdf['pages']), 3)
        self.assertIn('Alejo pago 5.700 euros', pdf['pages']['1'])
        self.assertTrue(all(len(t) <= MAX_PAGE_CHARS + len(TRUNCATION_MARK) for t in pdf['pages'].values()))

    def test_chunk_text_fallback_is_marked_and_windowed(self):
        builder = self.builder()
        try:
            packet = builder.build_group(self.groups('T0')[0])
        finally:
            builder.db.close()
        fallback = [s for s in packet['sources'] if s['status'] == 'chunk-text']
        self.assertEqual([s['chunk_id'] for s in fallback], ['ch2'])
        text = fallback[0]['pages']['3']
        # The audited figure sits 12k chars in: head-first truncation would have lost it.
        self.assertIn('Alejo pago 5.700 euros segun el acta', text)
        self.assertIn(OMISSION_MARK.strip(), text)
        self.assertFalse(fallback[0]['complete'])
        self.assertGreater(fallback[0]['omitted_chars'], 0)
        self.assertEqual(clip_page('abc', 2), 'ab' + TRUNCATION_MARK)

    def test_archived_url_keeps_the_windows_that_hold_the_audited_facts(self):
        """The 2026-09-09 pilot lost the `sentencia` section to a head-first cut and both
        readers disagreed about a source the packet had thrown away."""
        buried = ('El Tribunal Supremo fijo en su sentencia 418/2026 que Alejo pago 5.700 euros '
                  'a la Empresa SA, con responsabilidad civil de 9.500,54 euros.')
        archived = ('Ficha biografica de entrada. ' * 40 + 'RELLENO. ' * 3000
                    + buried + ' RELLENO. ' * 3000)
        with patch('pipeline.src.qa_dual_packets.archive_url') as fetched:
            fetched.return_value = {'source_id': 'u1', 'url': 'https://example.com/fuente',
                                    'status': 'accessible', 'pages': {'1': archived}}
            builder = self.builder(fetch=True)
            try:
                packet = builder.build_group(self.groups('T0')[0])
            finally:
                builder.db.close()
        source = next(s for s in packet['sources'] if s.get('url'))
        text = source['pages']['1']
        self.assertIn(buried, text)
        self.assertTrue(text.startswith('Ficha biografica de entrada.'))
        self.assertIn(OMISSION_MARK.strip(), text)
        self.assertFalse(source['complete'])
        from pipeline.src.qa_integral_sources import squash_ws
        # omissions are accounted over the whitespace-squashed archive (what the reader sees)
        self.assertEqual(source['omitted_chars'], len(squash_ws(archived)) - len(text.replace(OMISSION_MARK, '')))
        self.assertLessEqual(len(text) - text.count(OMISSION_MARK) * len(OMISSION_MARK), MAX_SOURCE_CHARS)

    def test_pdf_pages_left_out_or_clipped_are_declared_incomplete(self):
        builder = self.builder()
        try:
            packet = builder.build_group(self.groups('T1')[0])
        finally:
            builder.db.close()
        pdf = next(s for s in packet['sources'] if s.get('doc') == 'original.pdf')
        # Six-page PDF, page 1 cited: pages 1-2 kept, nothing clipped, nothing omitted.
        self.assertEqual(sorted(pdf['pages']), ['1', '2'])
        self.assertEqual((pdf['omitted_pages'], pdf['omitted_chars'], pdf['complete']), ([], 0, True))

    def test_corpus_pages_come_from_the_anchors_not_the_mention_order(self):
        """The 2026-09-09 pilot answered `uncertain` on facts the corpus proves: prose
        groups only carried summary_sources URLs and the first two mention chunks."""
        with patch('pipeline.src.qa_dual_packets.archive_url') as fetched:
            fetched.return_value = {'source_id': 'u1', 'url': 'https://example.com/fuente',
                                    'status': 'accessible', 'pages': {'1': 'Ficha sin cifras. ' * 200}}
            builder = self.builder(fetch=True)
            try:
                packet = builder.build_group(self.groups('T0')[0])
            finally:
                builder.db.close()
        corpus = [s for s in packet['sources'] if s['status'] in {'corpus-page', 'chunk-text'}]
        self.assertTrue(corpus)
        # The chunk carrying the audited figure wins over the one that only has the name.
        text = ' '.join(t for s in corpus for t in s['pages'].values())
        self.assertIn('34.450', text)
        # ... and every corpus source precedes every web source in the packet.
        first_url = next(i for i, s in enumerate(packet['sources']) if s.get('url'))
        self.assertTrue(all(i < first_url for i, s in enumerate(packet['sources'])
                            if s['status'] in {'corpus-page', 'chunk-text'}))
        self.assertLessEqual(len(corpus), qa_dual_packets.MAX_CORPUS_PAGES_PER_GROUP)

    def test_a_corpus_page_is_a_whole_original_page(self):
        builder = self.builder()
        try:
            packet = builder.build_group(self.groups('T0')[0])
        finally:
            builder.db.close()
        pages = [s for s in packet['sources'] if s['status'] == 'corpus-page']
        # Ordered by anchor score: pages 1 and 2 carry figures, page 5 only the name.
        # Rarity decides: 34.450 (p.2) beats 5.700 (p.1); the name-only page is last.
        self.assertEqual([s['page'] for s in pages], [2, 1, 5])
        page = next(s for s in pages if s['page'] == 2)
        self.assertEqual(page['doc'], 'original.pdf')
        self.assertEqual((page['omitted_chars'], page['complete']), (0, True))
        self.assertEqual(sorted(page['pages']), ['2'])               # page ±0, whole page
        self.assertIn('34.450', page['pages']['2'])

    def test_web_sources_are_ranked_by_anchors_and_share_one_budget(self):
        pages = {'https://example.com/vacia': 'Sin nada que ver. ' * 900,
                 'https://example.com/util': 'El decomiso fue de 34.450 euros. ' * 900}
        with patch('pipeline.src.qa_dual_packets.archive_url') as fetched:
            fetched.side_effect = lambda root, url, revision=None: {
                'source_id': digest(['url', url]), 'url': url, 'status': 'accessible',
                'pages': {'1': pages[url]}}
            builder = self.builder(fetch=True)
            builder.db.execute('UPDATE objects SET payload=replace(payload,'
                               '\'["https://example.com/fuente"]\','
                               '\'["https://example.com/vacia","https://example.com/util"]\')')
            builder.db.commit()
            try:
                group = self.groups('T0')[0]
                packet = builder.build_group({**group, 'group_id': 'T0-web'})
            finally:
                builder.db.close()
        urls = [s['url'] for s in packet['sources'] if s.get('url')]
        self.assertEqual(urls[0], 'https://example.com/util')
        web_chars = sum(len(t) for s in packet['sources'] if s.get('url') for t in s['pages'].values())
        self.assertLessEqual(web_chars, qa_dual_packets.MAX_WEB_CHARS_PER_GROUP)

    def test_the_pool_is_the_whole_document_not_only_mentioned_chunks(self):
        """The sentencia's header states «418/2026» without a MENTIONED_IN for the
        defendant: mentions alone could never reach it (pilot 1, 2026-09-09)."""
        db = connect(self.root)
        with db:  # a chunk of the SAME document where the entity is never mentioned
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                       ('ch5', 'original.pdf', 3, 3, 5,
                        'Sentencia num. 418/2026: se fija la indemnizacion de 34.450 euros.'))
        db.close()
        builder = self.builder()
        try:
            packet = builder.build_group(self.groups('T0')[0])
        finally:
            builder.db.close()
        pages = [s for s in packet['sources'] if s['status'] == 'corpus-page']
        self.assertIn(3, [s['page'] for s in pages])
        self.assertNotIn('ch5', builder.mentions['Person:alejo'])   # no MENTIONED_IN at all

    def test_a_chunk_that_names_the_entity_wins_a_close_call(self):
        db = connect(self.root)
        with db:  # same anchors, one mentioned and one not, in the same document
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                       ('ch6', 'original.pdf', 4, 4, 6, PAGE2))
        db.close()
        builder = self.builder()
        try:
            scored = builder.corpus_sources(['Person:alejo'],
                                            qa_dual_packets.anchor_terms([SUMMARY]))
        finally:
            builder.db.close()
        pages = [s['page'] for s in scored]
        # ch3 (p.2) is a direct mention, ch6 (p.4) is the same text without one.
        self.assertLess(pages.index(2), pages.index(4))

    def test_the_pool_is_capped_deterministically(self):
        builder = self.builder()
        try:
            with patch.object(qa_dual_packets, 'MAX_POOL_CHUNKS', 1):
                first = builder.corpus_sources(['Person:alejo'],
                                               qa_dual_packets.anchor_terms([SUMMARY]))
                again = builder.corpus_sources(['Person:alejo'],
                                               qa_dual_packets.anchor_terms([SUMMARY]))
        finally:
            builder.db.close()
        self.assertEqual([s['source_id'] for s in first], [s['source_id'] for s in again])
        self.assertLessEqual(len(first), 1)

    def test_a_scanned_page_falls_back_to_the_stored_chunk_text(self):
        """auto-zapatero.pdf extracts to nothing; the packet must not hand over a blank."""
        import fitz
        blank = self.pdf_dir / 'escaneado.pdf'
        doc = fitz.open()
        doc.new_page()
        doc.new_page().insert_text((40, 80), 'x')     # a text layer under the threshold
        doc.save(blank)
        doc.close()
        db = connect(self.root)
        with db:
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                       ('ch7', 'escaneado.pdf', 2, 2, 7,
                        'Texto OCR guardado: la indemnizacion de 34.450 euros consta en el auto. ' * 6))
            db.execute("INSERT INTO mentions VALUES ('Person:alejo','ch7')")
        db.close()
        builder = self.builder()
        try:
            sources = builder.corpus_sources(['Person:alejo'], qa_dual_packets.anchor_terms([SUMMARY]))
        finally:
            builder.db.close()
        scanned = next(s for s in sources if s['doc'] == 'escaneado.pdf')
        self.assertEqual((scanned['status'], scanned['page']), ('chunk-text', 2))
        self.assertIn('capa de texto vacía', scanned['note'].lower())
        self.assertIn('34.450', scanned['pages']['2'])

    def test_an_empty_page_never_costs_a_corpus_slot(self):
        import fitz
        blank = self.pdf_dir / 'vacio.pdf'
        doc = fitz.open()
        doc.new_page()
        doc.save(blank)
        doc.close()
        db = connect(self.root)
        with db:  # a chunk that scores but whose page has neither text layer nor stored text
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                       ('ch8', 'vacio.pdf', 1, 1, 8, '   '))
            db.execute("INSERT INTO mentions VALUES ('Person:alejo','ch8')")
        db.close()
        builder = self.builder()
        try:
            sources = builder.corpus_sources(['Person:alejo'], qa_dual_packets.anchor_terms([SUMMARY]))
        finally:
            builder.db.close()
        self.assertNotIn('vacio.pdf', [s['doc'] for s in sources])
        self.assertTrue(all(any(t.strip() for t in s['pages'].values()) for s in sources))

    def test_edge_groups_do_not_trigger_the_anchor_search(self):
        builder = self.builder()
        try:
            packet = builder.build_group(self.groups('T1')[0])
        finally:
            builder.db.close()
        # An edge carries its own doc and page; nothing is guessed for it.
        self.assertFalse([s for s in packet['sources'] if s['status'] == 'corpus-page'])

    def test_summary_sources_are_fetched_at_most_three_times(self):
        with patch('pipeline.src.qa_dual_packets.archive_url') as fetched:
            fetched.return_value = {'source_id': 'u', 'url': 'https://example.com/fuente',
                                    'status': 'accessible', 'pages': {'1': 'Texto archivado.'}}
            builder = self.builder(fetch=True)
            try:
                builder.build_group(self.groups('T0')[0])
            finally:
                builder.db.close()
        self.assertLessEqual(fetched.call_count, 3)

class AmpliadoProfileTest(DualBase):
    """Addendum 061-A: the second reading of a documentary uncertainty gets a wider
    packet, in groups of four, with the anchors the first pass asked for."""

    def builder(self, fetch=False):
        return TextPacketBuilder(self.root, self.base, fetch=fetch)

    def groups(self, tier):
        db = connect(self.root)
        try:
            return group_units(db, tier)
        finally:
            db.close()

    def mark(self, unit_ids, requested=()):
        """What the 061 requeue left behind: the resolution and the first reading."""
        db = connect(self.root)
        with db:
            db.executemany('UPDATE units SET resolution=? WHERE unit_id=?',
                           [(AMPLIADO_RESOLUTION, uid) for uid in unit_ids])
            for uid in unit_ids:
                db.execute('INSERT INTO events(at,unit_id,event,payload) VALUES (?,?,?,?)',
                           (qa_dual.now(), uid, 'relectura',
                            canonical({'requested_sources': list(requested)})))
        db.close()

    def test_the_profile_carries_the_addendum_limits_and_leaves_the_defaults_alone(self):
        # 061-C «cuerpo íntegro»: 60 k per source, 120 k of web per group, six URLs.
        self.assertEqual((AMPLIADO_LIMITS.source_chars, AMPLIADO_LIMITS.windows_per_source,
                          AMPLIADO_LIMITS.pages_per_source, AMPLIADO_LIMITS.corpus_pages_per_group,
                          AMPLIADO_LIMITS.web_chars_per_group, AMPLIADO_LIMITS.group_units,
                          AMPLIADO_LIMITS.summary_urls),
                         (60000, 12, 6, 12, 120000, 4, 6))
        self.assertEqual((DEFAULT_LIMITS.source_chars, DEFAULT_LIMITS.group_units,
                          DEFAULT_LIMITS.corpus_pages_per_group, DEFAULT_LIMITS.summary_urls),
                         (MAX_SOURCE_CHARS, MAX_GROUP_UNITS, qa_dual_packets.MAX_CORPUS_PAGES_PER_GROUP,
                          qa_dual_packets.MAX_SUMMARY_URLS))
        self.assertEqual(qa_dual_packets.limits_for(AMPLIADO_RESOLUTION), AMPLIADO_LIMITS)
        self.assertEqual(qa_dual_packets.limits_for(None), DEFAULT_LIMITS)
        self.assertEqual(qa_dual_packets.limits_for(None, 'ampliado'), AMPLIADO_LIMITS)
        self.assertEqual(qa_dual_packets.limits_for(None, 'normal'), DEFAULT_LIMITS)

    def test_a_source_that_fits_the_wider_cap_travels_whole(self):
        group = self.groups('T0')[0]
        builder = self.builder()
        try:
            narrow = builder.build_group(group)
            self.mark(group['unit_ids'])
            wide = builder.build_group({**group, 'group_id': 'T0-ampliado'})
        finally:
            builder.db.close()
        self.assertEqual((narrow['profile'], wide['profile']), ('normal', 'ampliado'))
        before = next(s for s in narrow['sources'] if s.get('chunk_id') == 'ch2')
        after = next(s for s in wide['sources'] if s.get('chunk_id') == 'ch2')
        # 18 k chars: cut to four windows under 9 000, whole and complete under 30 000.
        self.assertFalse(before['complete'])
        self.assertGreater(before['omitted_chars'], 0)
        self.assertEqual((after['complete'], after['omitted_chars']), (True, 0))
        self.assertNotIn(OMISSION_MARK.strip(), after['pages']['3'])
        self.assertGreater(len(after['pages']['3']), len(before['pages']['3']))

    def test_the_anchors_of_the_first_pass_reach_the_page_it_asked_for(self):
        db = connect(self.root)
        with db:  # a page of an already-cited document that the unit's own text never names
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                       ('ch9', 'sin-pdf.pdf', 4, 4, 9,
                        'Sentencia num. 512/2026: el decomiso asciende a 91.300 euros.'))
        db.close()
        group = self.groups('T0')[0]
        builder = self.builder()
        try:
            narrow = builder.build_group(group)
            self.mark(group['unit_ids'], ['STS 512/2026, decomiso de 91.300 euros'])
            wide = builder.build_group({**group, 'group_id': 'T0-ampliado'})
        finally:
            builder.db.close()
        self.assertNotIn('91.300', ' '.join(t for s in narrow['sources'] for t in s['pages'].values()))
        self.assertIn('91.300', ' '.join(t for s in wide['sources'] for t in s['pages'].values()))

    def test_requeued_units_group_in_fours_and_never_with_a_normal_one(self):
        db = connect(self.root)
        oid = db.execute("SELECT object_id FROM units WHERE kind='summary' LIMIT 1").fetchone()[0]
        with db:
            for i in range(9):
                uid = digest(['ampliada', i])
                db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior,'
                           'state,resolution) VALUES (?,?,?,?,?,0,100,"[]",?,?)',
                           (uid, oid, 'summary', f'/summary@{100 + i}:{105 + i}',
                            canonical(f'Frase re-encolada {i}.'), 'pending', AMPLIADO_RESOLUTION))
                db.execute('INSERT INTO tiers VALUES (?,?,?)', (uid, 'T0', 1.0))
        resolutions = {r['unit_id']: r['resolution'] for r in db.execute('SELECT unit_id,resolution FROM units')}
        db.close()
        groups = self.groups('T0')
        wide = [g for g in groups if resolutions[g['unit_ids'][0]] == AMPLIADO_RESOLUTION]
        self.assertEqual(sorted(len(g['unit_ids']) for g in wide), [1, 4, 4])
        for g in groups:
            profiles = {resolutions[uid] == AMPLIADO_RESOLUTION for uid in g['unit_ids']}
            self.assertEqual(len(profiles), 1)                     # never a mixed group
            self.assertLessEqual(len(g['unit_ids']),
                                 4 if profiles == {True} else MAX_GROUP_UNITS)
        # Same units, same key: only the profile keeps the two packets apart.
        self.assertEqual(len({g['group_id'] for g in groups}), len(groups))

    def test_a_scanned_page_asked_for_by_doc_and_page_travels_with_its_ocr_text(self):
        """auto-zapatero.pdf (D002): fitz returns nothing and the reader was handed a blank."""
        import fitz
        doc = fitz.open()
        doc.new_page()
        doc.new_page().insert_text((40, 80), 'x')      # a text layer under the threshold
        doc.save(self.pdf_dir / 'escaneado.pdf')
        doc.close()
        db = connect(self.root)
        with db:
            for cid, page in (('ch10', 1), ('ch11', 2)):
                db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                           (cid, 'escaneado.pdf', page, page, page,
                            f'Texto OCR de la pagina {page}: la indemnizacion de 34.450 euros consta en el auto.'))
        db.close()
        builder = self.builder()
        try:
            sources = builder._sources({'escaneado.pdf': {2}}, [], [])
        finally:
            builder.db.close()
        source = next(s for s in sources if s['doc'] == 'escaneado.pdf')
        self.assertEqual(sorted(source['pages']), ['1', '2'])
        self.assertIn('34.450', source['pages']['2'])
        self.assertIn('OCR', source['note'])
        self.assertEqual((source['status'], source['complete']), ('accessible', True))

    def test_a_page_with_no_text_anywhere_is_never_complete(self):
        import fitz
        doc = fitz.open()
        doc.new_page()
        doc.save(self.pdf_dir / 'vacio-sin-chunk.pdf')
        doc.close()
        builder = self.builder()
        try:
            sources = builder._sources({'vacio-sin-chunk.pdf': {1}}, [], [])
        finally:
            builder.db.close()
        source = next(s for s in sources if s['doc'] == 'vacio-sin-chunk.pdf')
        self.assertEqual((source['status'], source['complete']), ('text-layer-empty', False))
        self.assertFalse(source['pages']['1'].strip())


class WindowingTest(unittest.TestCase):
    def test_anchor_terms_are_figures_dates_and_names(self):
        terms = anchor_terms(['Alejo Pérez pagó 5.700 euros el 2026-05-03 a la Empresa SA.',
                              {'type': 'PAID', 'props': {'amount': 5700.0}}])
        self.assertIn('Alejo', terms)
        self.assertIn('5.700', terms)
        self.assertIn('2026', terms)
        # A long-form date travels whole, or it degrades to a useless bare year.
        self.assertIn('27 de noviembre de 2025',
                      anchor_terms(['Ingresó en prisión el 27 de noviembre de 2025.']))
        # Short words and lowercase noise never become anchors.
        self.assertNotIn('el', terms)
        self.assertNotIn('pagó', terms)
        self.assertEqual(terms, sorted(set(terms), key=lambda t: (-len(t), t)))

    def test_window_prefers_the_paragraph_with_the_fact_over_the_lede(self):
        fact = 'La sentencia 418/2026 condena por 34.450 euros.'
        text = 'Entradilla. ' * 30 + 'relleno ' * 4000 + fact + ' relleno ' * 4000
        kept, omitted = windowed_text(text, anchor_terms(['Importe 34.450 euros, sentencia 418/2026.']))
        self.assertIn(fact, kept)
        self.assertTrue(kept.startswith('Entradilla.'))
        self.assertGreater(omitted, 0)
        self.assertLessEqual(len(kept.replace(OMISSION_MARK, '')), MAX_SOURCE_CHARS)

    def test_short_text_is_never_cut(self):
        self.assertEqual(windowed_text('texto corto', ['algo']), ('texto corto', 0))
        self.assertEqual(anchored_windows('abc', [], cap=10), [(0, 3)])

    def test_without_anchors_the_lede_still_travels(self):
        text = 'A' * 50000
        kept, omitted = windowed_text(text, [])
        self.assertEqual(len(kept.replace(OMISSION_MARK, '')), LEDE_CHARS)
        self.assertEqual(omitted, 50000 - LEDE_CHARS)

    def test_windows_are_bounded_merged_and_deterministic(self):
        fact = ' HITO-{} 2026-05-0{} '
        text = ''.join('relleno ' * 500 + fact.format(i, i) for i in range(8))
        terms = anchor_terms([' '.join(f'HITO-{i}' for i in range(8))])
        first = windowed_text(text, terms)
        self.assertEqual(first, windowed_text(text, terms))
        kept = first[0].replace(OMISSION_MARK, '')
        self.assertLessEqual(len(kept), MAX_SOURCE_CHARS)
        self.assertGreaterEqual(first[0].count(OMISSION_MARK), 1)


class DefaultProfileAndVigenciaTest(DualBase):
    """Addendum 061-C: the wide profile as the loop's default, and the register row + feed
    of each entity travelling as one synthetic `vigencia` source both readers see alike."""

    def setUp(self):
        super().setUp()
        self.db = connect(self.root)
        (self.base / 'pipeline/data/accusations').mkdir(parents=True)
        (self.base / 'pipeline/data/ultima_hora').mkdir(parents=True)
        (self.base / 'pipeline/data/accusations/accusations.yaml').write_text(
            'accusations:\n'
            '- person: Alejo Pérez\n  entity_id: Person:alejo\n  doc: original.pdf\n  status: acusado\n'
            '  estado:\n    status: acusado\n    fecha: "2026-06-20"\n'
            '    fuente: {url: "https://example.com/auto-apertura", tipo: auto_apertura_juicio_oral}\n'
            '    nota: auto de apertura de juicio oral confirmado por dos fuentes\n'
            '  crimes: []\n')
        (self.base / 'pipeline/data/ultima_hora/feed.yaml').write_text(
            'items:\n'
            '- iso: "2026-07-16"\n  date: "2026-07-16"\n  headline: La Audiencia confirma la apertura\n'
            '  dek: Rechaza el recurso de la defensa.\n  outlet: Infobae\n'
            '  url: "https://example.com/audiencia"\n  entities: [Person:alejo]\n'
            '- iso: "2026-05-02"\n  date: "2026-05-02"\n  headline: Declara como investigado\n'
            '  outlet: EFE\n  url: "https://example.com/declara"\n  entities: [Person:alejo, Company:empresa]\n')

    def tearDown(self):
        self.db.close()
        super().tearDown()

    def test_the_profile_joins_the_group_identity(self):
        normal = group_units(self.db, 'T0')
        wide = group_units(self.db, 'T0', profile='ampliado')
        self.assertTrue(normal and wide)
        self.assertTrue({g['group_id'] for g in normal}.isdisjoint({g['group_id'] for g in wide}))
        self.assertTrue(all(len(g['unit_ids']) <= 4 for g in wide))
        with self.assertRaises(ValueError):
            group_units(self.db, 'T0', profile='gigante')

    def test_the_register_row_and_the_feed_travel_as_one_complete_source_per_entity(self):
        builder = TextPacketBuilder(self.root, self.base, fetch=False)
        try:
            group = next(g for g in group_units(self.db, 'T0') if g['group_key'] == 'entity:Person:alejo')
            packet = builder.build_group(group)
        finally:
            builder.db.close()
        vigencia = [s for s in packet['sources'] if s['status'] == 'vigencia']
        self.assertEqual(len(vigencia), 1)
        source = vigencia[0]
        self.assertEqual((source['doc'], source['complete'], source['format']),
                         ('vigencia:Person:alejo', True, 'vigencia'))
        self.assertNotIn('urls', source)
        text = source['pages']['1']
        self.assertIn('Alejo Pérez', text)
        self.assertIn('«acusado» con fecha 2026-06-20', text)
        self.assertIn('auto_apertura_juicio_oral', text)
        # Most recent item first, both items named, each with its outlet and URL.
        self.assertLess(text.index('2026-07-16'), text.index('2026-05-02'))
        self.assertIn('Infobae · La Audiencia confirma la apertura — Rechaza el recurso', text)
        self.assertIn('https://example.com/declara', text)
        # Its URLs join the web queue after the unit's own source (fetch=False leaves them as
        # not-fetched stubs): three fit under the normal cap, all of them under the wide one.
        urls = [s['url'] for s in packet['sources'] if s.get('url')]
        self.assertEqual(urls, ['https://example.com/fuente', 'https://example.com/auto-apertura',
                                'https://example.com/audiencia'])
        builder = TextPacketBuilder(self.root, self.base, fetch=False)
        try:
            wide = builder.build_group({**group, 'group_id': 'T0-ampliado', 'profile': 'ampliado'})
        finally:
            builder.db.close()
        self.assertEqual([s['url'] for s in wide['sources'] if s.get('url')],
                         ['https://example.com/fuente', 'https://example.com/auto-apertura',
                          'https://example.com/audiencia', 'https://example.com/declara'])

    def test_a_cited_url_keeps_its_slot_against_better_scoring_vigencia_articles(self):
        """With fetch=True the web queue is ranked by anchor score: the feed articles name the
        entity and its dates, so without a reserve they would evict the biography the unit cites."""
        def fake_archive(root, url, revision=None):
            hit = 'Alejo Pérez pagó 5.700 euros a la Empresa SA en 2026, dice la crónica. ' * 3
            return {'source_id': digest(['url', url]), 'url': url, 'status': 'accessible',
                    'pages': {'1': ('Biografía sin cifras.' if url.endswith('/fuente') else hit)}}
        builder = TextPacketBuilder(self.root, self.base, fetch=True)
        try:
            group = next(g for g in group_units(self.db, 'T0') if g['group_key'] == 'entity:Person:alejo')
            with patch.object(qa_dual_packets, 'archive_url', fake_archive):
                packet = builder.build_group(group)
        finally:
            builder.db.close()
        urls = [s['url'] for s in packet['sources'] if s.get('url')]
        self.assertEqual(len(urls), DEFAULT_LIMITS.summary_urls)
        self.assertEqual(urls[0], 'https://example.com/fuente')

    def test_an_entity_named_by_neither_layer_carries_no_vigencia_source(self):
        (self.base / 'pipeline/data/accusations/accusations.yaml').write_text('accusations: []\n')
        (self.base / 'pipeline/data/ultima_hora/feed.yaml').write_text('items: []\n')
        builder = TextPacketBuilder(self.root, self.base, fetch=False)
        try:
            packets = [builder.build_group(g) for g in group_units(self.db, 'T0')]
        finally:
            builder.db.close()
        self.assertFalse([s for p in packets for s in p['sources'] if s['status'] == 'vigencia'])

def tier_states(root, batch):
    db = connect(root)
    rows = dict(db.execute("SELECT u.state,count(*) FROM units u JOIN assignments a USING(unit_id) "
                           "WHERE a.batch_id=? GROUP BY 1", (batch,)))
    db.close()
    return rows


def unit_resolution(root, batch):
    db = connect(root)
    value = db.execute('SELECT u.resolution FROM units u JOIN assignments a USING(unit_id) '
                       'WHERE a.batch_id=? LIMIT 1', (batch,)).fetchone()[0]
    db.close()
    return value


class DisclaimerAndQueueTest(DualBase):
    """Pilot 2 (2026-09-09): 26 needs-human, most of them noise."""

    def test_safeguard_sentences_are_closed_by_the_census(self):
        db = connect(self.root)
        rows = [dict(r) for r in db.execute(
            "SELECT value,state,resolution,technical FROM units WHERE resolution=?",
            (qa_dual.DISCLAIMER_RESOLUTION,))]
        db.close()
        self.assertEqual([json.loads(r['value']).strip() for r in rows],
                         ['Rige plenamente la presuncion de inocencia.'])
        self.assertEqual((rows[0]['state'], rows[0]['technical']), ('verified', 1))
        # ... and never leaves in a batch.
        db = connect(self.root)
        try:
            grouped = [uid for g in group_units(db, 'T0') for uid in g['unit_ids']]
        finally:
            db.close()
        safeguard = self.unit_like('summary', '%presuncion%')[1]
        self.assertNotIn(safeguard, grouped)

    def test_reclassify_is_idempotent_and_never_overrides_a_verdict(self):
        db = connect(self.root)
        uid = self.unit_like('summary', '%presuncion%')[1]
        with db:   # a census built before the rule existed
            db.execute("UPDATE units SET state='pending',resolution=NULL,technical=0 WHERE unit_id=?", (uid,))
        db.close()
        first = qa_dual.reclassify_disclaimers(self.root)
        self.assertEqual((first['counts']['matched'], first['counts']['closed']), (1, 0 + 1))
        second = qa_dual.reclassify_disclaimers(self.root)
        self.assertEqual(second['counts']['closed'], 0)
        self.assertEqual(second['counts']['already-closed'], 1)
        self.assertTrue(qa_dual.verify_census(self.root))    # ids untouched
        db = connect(self.root)
        with db:
            db.execute("UPDATE units SET state='needs-human',resolution='parche-divergente' WHERE unit_id=?", (uid,))
        db.close()
        third = qa_dual.reclassify_disclaimers(self.root)
        self.assertEqual(third['counts']['skipped-already-judged'], 1)
        self.assertEqual(self.state(uid)[0], 'needs-human')

    def test_the_regex_only_catches_safeguards(self):
        for text in ('Rige plenamente la presunción de inocencia.',
                     'Su elevado grado en el grafo refleja su centralidad documental.',
                     'La cifra no implica responsabilidad penal acreditada.'):
            self.assertTrue(qa_dual.DISCLAIMER.search(text), text)
        for text in ('Fue condenado a cuatro años de prisión.',
                     'Pagó 5.700 euros a la empresa.'):
            self.assertFalse(qa_dual.DISCLAIMER.search(text), text)

    def test_risk_classes(self):
        rows = {'alto': {'kind': 'summary', 'type': 'Person', 'value': canonical('Fue condenado.')},
                'medio': {'kind': 'summary', 'type': 'Person', 'value': canonical('Nacio en Pamplona.')},
                'bajo': {'kind': 'summary', 'type': 'Company', 'value': canonical('Tiene sede social.')}}
        for expected, row in rows.items():
            self.assertEqual(qa_dual.risk_class(row), expected)


if __name__ == '__main__':
    unittest.main()
