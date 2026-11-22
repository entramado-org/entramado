"""Spec 063 T9 + 063-B — escalado a Astra, acuerdo dual Opus+Astra y tandas (sin modelos, red ni grafo).

Astra es un runner falso: los eventos JSON de `codex exec` más el fichero de `--output-last-message`
(el runner también sabe imitar el `stream-json` de `claude -p`, igual que `test_qa_esc_opus.py`). El censo es el mínimo de CONTRACTS.md (`units/objects/tiers/events`
+ `esc_packs/esc_readings`) sobre un tmpdir, y los packs son manifests escritos a mano.
"""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from src import qa_esc_apply as A
from src import qa_esc_escalate as E
from src.qa_integral import canonical, connect, digest

U_SUM, U_ROLE, U_ID, U_EDGE = (c * 64 for c in 'abcd')
PACK = 'E-fuldo000001'
FULDO = 'Person:fuldo-fictez'
BRIEF = 'BRIEF 063 de prueba'
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['entity_id'],
          'properties': {'entity_id': {'type': 'string'}}}

CENSUS_SCHEMA = '''
CREATE TABLE objects (object_id TEXT PRIMARY KEY, object_kind TEXT NOT NULL, ref TEXT NOT NULL,
  entity TEXT, type TEXT NOT NULL, provenance TEXT NOT NULL, technical INTEGER NOT NULL,
  content_hash TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(object_kind, ref));
CREATE TABLE units (unit_id TEXT PRIMARY KEY, object_id TEXT NOT NULL REFERENCES objects,
  kind TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL, technical INTEGER NOT NULL,
  risk INTEGER NOT NULL, prior TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
  resolution TEXT, UNIQUE(object_id,kind,field));
CREATE TABLE tiers (unit_id TEXT PRIMARY KEY REFERENCES units, tier TEXT NOT NULL, rank REAL NOT NULL);
CREATE TABLE events (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, unit_id TEXT, event TEXT NOT NULL,
  payload TEXT NOT NULL);
CREATE TABLE esc_packs (pack_id TEXT PRIMARY KEY, entity TEXT, kind TEXT, idx INTEGER, total INTEGER,
  unit_ids TEXT, pack_path TEXT, pack_sha256 TEXT, overlay_ref TEXT, riesgo TEXT, clase TEXT,
  route TEXT, state TEXT, batch TEXT, created_at TEXT);
CREATE TABLE esc_readings (attempt_id TEXT PRIMARY KEY, pack_id TEXT, side TEXT, result TEXT,
  receipt TEXT, at TEXT, UNIQUE(pack_id, side));
'''

EDGE_VALUE = {'start': FULDO, 'end': 'Company:ficcionbar', 'type': 'PAID',
              'props': {'source_kind': 'judicial', 'source_doc': 'informe-uco.pdf', 'source_page': 76,
                        'source_quote': 'pago de 5.700 euros a Ficcionbar'}}
# La unidad de identidad es el bloque entero que guarda el censo (`qa_dual`), nunca un alias suelto.
IDENTITY_VALUE = {'type': 'Person', 'identity': {'name': 'Fuldo Fíctez', 'entity_id': FULDO,
                                                 'aliases': ['Fuldo', 'Fuldo el de Ficcionbar']}}
UNIT_VALUES = {U_SUM: 'Fuldo Fíctez cobró 5.700 euros de Mengano de Tal el 20 de junio de 2026.',
               U_ROLE: 'Asesor del ministro de Transportes.', U_ID: IDENTITY_VALUE,
               U_EDGE: EDGE_VALUE}


def pack_unit(unit_id, kind, riesgo='alto'):
    edge = None
    if kind == 'edge':
        props = EDGE_VALUE['props']
        edge = {'type': EDGE_VALUE['type'], 'start_entity': FULDO, 'start_name': 'Fuldo Fíctez',
                'end_entity': EDGE_VALUE['end'], 'end_name': 'Ficcionbar', 'source_kind': 'judicial',
                'source_doc': props['source_doc'], 'source_page': 76,
                'source_quote': props['source_quote'], 'status': '', 'firme': '',
                'status_fuente_tipo': '', 'amount': 5700.0, 'currency': 'EUR', 'date': '',
                'note': '', 'public_label': ''}
    return {'unit_id': unit_id, 'tier': 'T0', 'kind': kind, 'field': f'/{kind}', 'riesgo': riesgo,
            'value': UNIT_VALUES[unit_id], 'subject': FULDO, 'edge': edge}


U_E1, U_E2, U_E3, U_E4 = (c * 64 for c in 'efgh')


def edge_manifest():
    """Un sub-pack de 4 aristas y nada más: la forma de los hubs del piloto (12 por sub-pack)."""
    out = manifest()
    units = []
    for uid in (U_E1, U_E2, U_E3, U_E4):
        row = copy.deepcopy(pack_unit(U_EDGE, 'edge'))
        row['unit_id'] = uid
        units.append(row)
    out['units'] = units
    return out


def edge_op(unit_id, action, **over):
    op = {'unit_id': unit_id, 'action': action, 'new_type': '', 'prop': '', 'value': '', 'status': '',
          'evidence': 'informe UCO p.76', 'reason': 'la cita no sostiene la relación'}
    op.update(over)
    return op


def manifest(pack_id=PACK, index=1, total=1):
    return {'pack_id': pack_id, 'kind': 'entity', 'index': index, 'total': total,
            'overlay_ref': 'worktree',
            'entity': {'id': FULDO, 'type': 'Person', 'name': 'Fuldo Fíctez', 'aliases': ['Fuldo'],
                       'is_pseudonym': False, 'pseudonym_note': '', 'anonymized': False,
                       'a5_familiar': False, 'degree': 12, 'context_role': 'asesor', 'family_of': [],
                       'estado_row': None, 'feed_items': [], 'tiers_pending': ['T0'], 'flags': {},
                       'classification': {'clase': 'figura_publica', 'riesgo': 'alto', 'source': 'reglas'}},
            'current': {'summary': {'text': UNIT_VALUES[U_SUM], 'sources': [], 'origin': 'node',
                                    'updated': '2026-09-01'},
                        'role': {'text': UNIT_VALUES[U_ROLE], 'origin': 'node'}},
            'units': [pack_unit(U_SUM, 'summary'), pack_unit(U_ROLE, 'attribute'),
                      pack_unit(U_ID, 'identity'), pack_unit(U_EDGE, 'edge')],
            'vigencia': '', 'sources': [], 'warnings': []}


def unit(unit_id, verdict='verificado', label='ok', evidence='informe UCO p.76', confidence='alta',
         sources=None):
    return {'unit_id': unit_id, 'verdict': verdict, 'label': label, 'evidence': evidence,
            'confidence': confidence, 'requested_sources': sources or []}


def decision(units=None, **over):
    out = {'entity_id': FULDO,
           'units': units or [unit(U_SUM), unit(U_ROLE), unit(U_ID), unit(U_EDGE)],
           'summary': {'action': 'none', 'resumen': '', 'sources': [], 'reason': ''},
           'role': {'action': 'none', 'role': '', 'reason': ''}, 'alias_deny': [],
           'estado': {'action': 'none', 'status': '', 'fecha': '', 'doc': '', 'url': '', 'tipo': '',
                      'nota': ''},
           'edges': [], 'editorial': [], 'notes': ''}
    out.update(over)
    return out


def rewrite(text, units=None, reason='sin fuente para el importe'):
    """Una decisión que reescribe el resumen (la unidad de resumen queda corregida)."""
    return decision(units=units or [unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE), unit(U_ID),
                                    unit(U_EDGE)],
                    summary={'action': 'replace', 'resumen': text, 'sources': ['informe-uco.pdf p.76'],
                             'reason': reason})


# ── runners falsos ────────────────────────────────────────────────────────────

def tool_use(command, name='Bash', uid=None):
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': uid or f'toolu_{abs(hash(command)) % 10 ** 8}', 'name': name,
         'input': {'command': command}}]}}


def claude_stream(result, *events):
    final = {'type': 'result', 'subtype': 'success', 'is_error': False, 'stop_reason': 'end_turn',
             'num_turns': 4, 'total_cost_usd': 0.42,
             'modelUsage': {'claude-opus-5-5': {'inputTokens': 5, 'outputTokens': 900}},
             'permission_denials': [], 'structured_output': result}
    return ''.join(json.dumps(e) + '\n' for e in (*events, final))


def codex_events(*items, model='gpt-6-astra'):
    events = [{'type': 'thread.started', 'model': model}]
    for item in items:
        events.append({'type': 'item.completed', 'item': item})
    events.append({'type': 'turn.completed',
                   'usage': {'input_tokens': 900, 'output_tokens': 300, 'cached_input_tokens': 10,
                             'reasoning_output_tokens': 120}})
    return ''.join(json.dumps(e) + '\n' for e in events)


def shell(command):
    return {'type': 'command_execution', 'command': command}


class Runner:
    """Un solo runner para los dos lados: escribe lo que dejaría cada CLI y guarda el comando."""

    def __init__(self, claude_result=None, astra_result=None, astra_items=(), stderr='', code=0,
                 error=None):
        self.claude_result, self.astra_result = claude_result, astra_result
        self.astra_items, self.stderr, self.code, self.error = astra_items, stderr, code, error
        self.calls = {}

    def __call__(self, cmd, stdin_text, timeout, stdout_path, stderr_path, cwd):
        side = 'astra' if 'codex' in cmd else 'claude'
        self.calls[side] = {'cmd': cmd, 'stdin': stdin_text, 'cwd': Path(cwd),
                            'cwd_contents': sorted(p.name for p in Path(cwd).iterdir())}
        Path(stderr_path).write_text(self.stderr)
        if side == 'claude':
            Path(stdout_path).write_text(claude_stream(self.claude_result))
        else:
            Path(stdout_path).write_text(codex_events(*self.astra_items))
            if self.astra_result is not None:
                out = Path(cmd[cmd.index('--output-last-message') + 1])
                out.write_text(json.dumps(self.astra_result, ensure_ascii=False))
        return self.code, self.error


class EscalateBase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='qa-esc-escalate-'))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / 'audit'
        (self.root / 'esc/packs').mkdir(parents=True)
        self.manifest = manifest()
        (self.root / 'esc/packs' / f'{PACK}.json').write_text(canonical(self.manifest))
        self._census()

    def _census(self):
        db = sqlite3.connect(self.root / 'census.sqlite')
        db.executescript(CENSUS_SCHEMA)
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                   ('o-node', 'node', 'n:1', FULDO, 'Person', 'unspecified', 0, 'h',
                    canonical({'labels': ['Person'], 'props': {'name': 'Fuldo Fíctez'}})))
        for uid, kind in ((U_SUM, 'summary'), (U_ROLE, 'attribute'), (U_ID, 'identity'),
                          (U_EDGE, 'edge')):
            db.execute('INSERT INTO units(unit_id,object_id,kind,field,value,technical,risk,prior)'
                       ' VALUES (?,?,?,?,?,0,100,"[]")',
                       (uid, 'o-node', kind, f'/{kind}', canonical(UNIT_VALUES[uid])))
            db.execute('INSERT INTO tiers VALUES (?,?,?)', (uid, 'T0', 1.0))
        db.execute('INSERT INTO esc_packs(pack_id,entity,kind,idx,total,unit_ids,overlay_ref,riesgo,'
                   'clase,route,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (PACK, FULDO, 'entity', 1, 1, json.dumps([U_SUM, U_ROLE, U_ID, U_EDGE]),
                    'worktree', 'alto', 'figura_publica', 'dueno', 'tanda', '2026-09-21T00:00:00+00:00'))
        db.commit()
        db.close()

    def conn(self):
        conn = connect(self.root)
        self.addCleanup(conn.close)
        return conn

    def reading(self, side, result, receipt=None):
        conn = self.conn()
        conn.execute('INSERT OR REPLACE INTO esc_readings VALUES (?,?,?,?,?,?)',
                     (f'att-{side}', PACK, side, json.dumps(result) if result else None,
                      json.dumps(receipt or {'side': side, 'error': None}), '2026-09-21T00:00:00+00:00'))
        conn.commit()

    def state(self, unit_id):
        row = self.conn().execute('SELECT state, resolution FROM units WHERE unit_id=?',
                                  (unit_id,)).fetchone()
        return row['state'], row['resolution']


class AstraNeedsToolsTest(unittest.TestCase):
    def test_a_retirada_asks_for_the_searcher(self):
        self.assertTrue(E.astra_needs_tools(decision(units=[unit(U_SUM, 'corregido', 'retirada')])))

    def test_zero_hits_in_the_evidence_asks_for_the_searcher(self):
        for evidence in ('0 apariciones en el corpus', '[corpus] 0 hits', 'no aparece en el sumario',
                         'sin fuente que lo sostenga'):
            with self.subTest(evidence=evidence):
                self.assertTrue(E.astra_needs_tools(decision(units=[unit(U_SUM, evidence=evidence)])))

    def test_a_prose_removal_asks_for_the_searcher(self):
        removal = decision(summary={'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'x'})
        self.assertTrue(E.astra_needs_tools(removal))

    def test_a_clean_decision_does_not(self):
        self.assertFalse(E.astra_needs_tools(decision()))
        self.assertFalse(E.astra_needs_tools(None))


class RunDualTest(EscalateBase):
    def dual(self, runner, astra_tools='auto', opus=None):
        return E.run_dual(self.root, PACK, self.manifest, 'PACK CIEGO', 'PACK CON HERRAMIENTA',
                          SCHEMA, BRIEF, 'high', astra_tools=astra_tools, opus_decision=opus,
                          timeout=30, runner=runner)

    def test_only_astra_reads_and_stamps_the_pack_hash(self):
        runner = Runner(claude_result=decision(), astra_result=decision())
        out = self.dual(runner)
        # 063-B: Fable salió del dual; ningún `claude -p` se lanza aquí.
        self.assertEqual(sorted(out), [E.ASTRA_SIDE])
        self.assertNotIn('claude', runner.calls)
        for side, (result, receipt) in out.items():
            self.assertEqual(result['entity_id'], FULDO, side)
            self.assertEqual(receipt['pack_sha256'], digest(self.manifest), side)
            self.assertEqual(receipt['brief_sha256'], digest(BRIEF), side)
            self.assertEqual((receipt['pack_id'], receipt['side'], receipt['error']), (PACK, side, None))
        self.assertEqual(out[E.ASTRA_SIDE][1]['model'], 'gpt-6-astra')
        # Sin retiradas Astra va ciego: recibe el markdown sin herramienta y sin shell.
        self.assertIn('PACK CIEGO', runner.calls['astra']['stdin'])
        self.assertNotIn('PACK CON HERRAMIENTA', runner.calls['astra']['stdin'])
        self.assertIn('--disable', runner.calls['astra']['cmd'])
        self.assertIn('shell_tool', runner.calls['astra']['cmd'])

    def test_auto_gives_astra_the_searcher_only_when_opus_retires(self):
        runner = Runner(claude_result=decision(), astra_result=decision())
        opus = decision(units=[unit(U_SUM, 'corregido', 'retirada'), unit(U_ROLE), unit(U_ID),
                               unit(U_EDGE)])
        out = self.dual(runner, opus=opus)
        self.assertIn('PACK CON HERRAMIENTA', runner.calls['astra']['stdin'])
        self.assertNotIn('shell_tool', runner.calls['astra']['cmd'])
        self.assertTrue(out[E.ASTRA_SIDE][1]['tools'])

    def test_never_and_always_override_the_heuristic(self):
        opus = decision(units=[unit(U_SUM, 'corregido', 'retirada'), unit(U_ROLE), unit(U_ID),
                               unit(U_EDGE)])
        runner = Runner(claude_result=decision(), astra_result=decision())
        self.dual(runner, astra_tools='never', opus=opus)
        self.assertIn('PACK CIEGO', runner.calls['astra']['stdin'])
        other = Runner(claude_result=decision(), astra_result=decision())
        self.dual(other, astra_tools='always')
        self.assertIn('PACK CON HERRAMIENTA', other.calls['astra']['stdin'])

    def test_a_side_that_fails_travels_in_the_receipt_and_never_raises(self):
        runner = Runner(claude_result=None, astra_result=None, code=1)
        out = self.dual(runner)
        for side, (result, receipt) in out.items():
            self.assertIsNone(result, side)
            self.assertIsNotNone(receipt['error'], side)

    def test_an_invalid_decision_voids_the_reading(self):
        bad = decision(units=[unit(U_SUM)])          # faltan tres unidades del pack
        runner = Runner(claude_result=bad, astra_result=bad)
        out = self.dual(runner)
        for side, (result, receipt) in out.items():
            self.assertIsNone(result, side)
            self.assertEqual(receipt['error'], 'invalid-decision', side)
            self.assertTrue(receipt['validation_errors'], side)


class AstraToolsTest(EscalateBase):
    def run_astra(self, runner, **over):
        kwargs = {'tools': True, 'root': self.root, 'timeout': 30, 'runner': runner,
                  'stamp': {'pack_id': PACK}}
        kwargs.update(over)
        return E.run_astra(self.base / 'att', self.manifest, 'PACK', SCHEMA, BRIEF, 'xhigh', **kwargs)

    def test_the_searcher_runs_from_a_directory_with_only_the_pack_and_the_tool(self):
        search = f'python3 {self.base}/x/qa_esc_ev.py -n 6 \'Fuldo\\s+Fíctez\''
        runner = Runner(astra_result=decision(), astra_items=[shell(search)])
        result, receipt = self.run_astra(runner)
        self.assertEqual(result['entity_id'], FULDO)
        cmd, call = receipt, runner.calls['astra']
        self.assertEqual(call['cwd_contents'], ['pack.md', 'qa_esc_ev.py'])
        self.assertEqual(call['cmd'][call['cmd'].index('-C') + 1], str(call['cwd']))
        self.assertEqual(call['cmd'][call['cmd'].index('-s') + 1], 'read-only')
        self.assertEqual(call['cmd'][0], 'env')
        self.assertIn(f'QA_ESC_ROOT={self.root}', call['cmd'])
        self.assertNotIn('shell_tool', call['cmd'])
        self.assertIn('qa_esc_ev.py', call['stdin'])
        self.assertIn('Máximo 8 búsquedas', call['stdin'])
        self.assertEqual(cmd['searches'], [search])
        self.assertEqual((cmd['model_resolved'], cmd['effort']), ('gpt-6-astra', 'high'))
        self.assertEqual(cmd['usage']['input'], 900)
        self.assertEqual((cmd['side'], cmd['error'], cmd['exit_code']), (E.ASTRA_SIDE, None, 0))
        self.assertGreaterEqual(cmd['elapsed_seconds'], 0)
        self.assertFalse(call['cwd'].exists())

    def test_a_command_outside_the_searcher_voids_the_reading(self):
        for command in ('curl https://example.org', "python3 qa_esc_ev.py 'x'; cat /etc/passwd",
                        'cat ../census.sqlite'):
            with self.subTest(command=command):
                runner = Runner(astra_result=decision(), astra_items=[shell(command)])
                result, receipt = self.run_astra(runner)
                self.assertIsNone(result)
                self.assertEqual(receipt['error'], 'unexpected-tool-use')
                self.assertTrue(receipt['tool_calls'])

    def test_a_relative_call_to_the_searcher_is_a_search(self):
        runner = Runner(astra_result=decision(),
                        astra_items=[shell("python3 qa_esc_ev.py 'Fuldo|Mengano'")])
        result, receipt = self.run_astra(runner)
        self.assertIsNotNone(result)
        self.assertEqual(len(receipt['searches']), 1)

    def test_more_than_twelve_searches_voids_the_reading(self):
        items = [shell(f"python3 qa_esc_ev.py 't{i}'") for i in range(13)]
        result, receipt = self.run_astra(Runner(astra_result=decision(), astra_items=items))
        self.assertIsNone(result)
        self.assertEqual(receipt['error'], 'tool-budget-exceeded')
        ok, receipt_ok = self.run_astra(Runner(astra_result=decision(), astra_items=items[:12]))
        self.assertIsNotNone(ok)
        self.assertIsNone(receipt_ok['error'])

    def test_quota_and_missing_json_are_reported_as_such(self):
        quota = Runner(astra_result=None, stderr='429 rate limit: you have hit your quota', code=1)
        result, receipt = self.run_astra(quota)
        self.assertIsNone(result)
        self.assertEqual(receipt['error'], 'quota')
        missing, receipt_m = self.run_astra(Runner(astra_result=None))
        self.assertIsNone(missing)
        self.assertEqual(receipt_m['error'], 'missing-or-invalid-json')
        timed, receipt_t = self.run_astra(Runner(astra_result=None, code=-1, error='reader-timeout'))
        self.assertIsNone(timed)
        self.assertEqual(receipt_t['error'], 'reader-timeout')

    def test_the_blind_side_keeps_the_receipt_shape(self):
        runner = Runner(astra_result=decision())
        result, receipt = self.run_astra(runner, tools=False)
        self.assertEqual(result['entity_id'], FULDO)
        for key in ('attempt_id', 'side', 'model', 'effort', 'schema_sha256', 'input_sha256',
                    'brief_sha256', 'pack_sha256', 'searches', 'elapsed_seconds', 'usage'):
            self.assertIn(key, receipt, key)
        self.assertEqual((receipt['side'], receipt['searches'], receipt['tools']),
                         (E.ASTRA_SIDE, [], False))
        self.assertIn('shell_tool', runner.calls['astra']['cmd'])


class AssertedFactsTest(unittest.TestCase):
    def test_amounts_are_normalized_across_formats(self):
        self.assertIn('importe:5700:eur', E.asserted_facts('cobró 5.700 €'))
        self.assertIn('importe:5700:eur', E.asserted_facts('cobró 5700 euros'))
        self.assertIn('importe:1400000:eur', E.asserted_facts('movió 1,4 millones de euros'))
        self.assertIn('importe:1400000:eur', E.asserted_facts('movió 1.400.000 euros'))
        self.assertEqual(E.asserted_facts('cobró 5.700 €') - {'importe:5700:eur'}, set())

    def test_a_bare_number_is_not_an_amount(self):
        self.assertEqual({f for f in E.asserted_facts('informe UCO, p. 76') if f.startswith('importe')},
                         set())

    def test_dates_land_in_iso_whatever_the_format(self):
        for text in ('el 20-06-2026', 'el 20/06/2026', 'el 20 de junio de 2026', 'el 2026-06-20'):
            with self.subTest(text=text):
                self.assertIn('fecha:2026-06-20', E.asserted_facts(text))

    def test_procedural_terms_keep_the_firmness(self):
        self.assertIn('proc:investigad', E.asserted_facts('está investigado por cohecho'))
        self.assertIn('proc:condenad-no-firme', E.asserted_facts('fue condenado no firme'))
        self.assertIn('proc:condenad', E.asserted_facts('fue condenado en sentencia firme'))
        self.assertIn('proc:sobrese', E.asserted_facts('se sobreseyó la causa'))

    def test_attributions_carry_outlet_and_date(self):
        self.assertIn('segun:infobae:2026-07-16',
                      E.asserted_facts('según Infobae (16-07-2026) la reunión existió'))
        # Agencia + medio donde se leyó: el paréntesis que no es fecha viaja normalizado.
        self.assertIn('segun:efe:via infobae', E.asserted_facts('según EFE (vía Infobae)'))

    def test_named_people_are_extracted_without_the_sentence_openers(self):
        facts = E.asserted_facts('Fuldo Fíctez se reunió con Mengano de Tal. Según Infobae, no consta.')
        self.assertIn('nombre:fuldo fictez', facts)
        self.assertIn('nombre:mengano de tal', facts)
        self.assertNotIn('nombre:segun infobae', facts)

    def test_two_wordings_of_the_same_facts_agree(self):
        one = 'Fuldo Fíctez cobró 5.700 € el 20 de junio de 2026, según Infobae (16-07-2026).'
        other = ('Según Infobae (16-07-2026), Fuldo Fíctez recibió 5700 euros el 20/06/2026.')
        self.assertEqual(E.asserted_facts(one), E.asserted_facts(other))

    def test_a_different_amount_or_status_breaks_the_equality(self):
        base = E.asserted_facts('cobró 5.700 € y está investigado')
        self.assertNotEqual(base, E.asserted_facts('cobró 7.500 € y está investigado'))
        self.assertNotEqual(base, E.asserted_facts('cobró 5.700 € y está imputado'))

    def test_decision_facts_mark_the_retired_units_and_the_actions(self):
        d = rewrite('Fuldo Fíctez fue asesor del ministro.',
                    units=[unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE, 'corregido', 'retirada'),
                           unit(U_ID), unit(U_EDGE)])
        facts = E.decision_facts(d)
        self.assertIn(f'retirada:{U_ROLE}', facts)
        self.assertIn('accion:summary:replace', facts)
        self.assertIn('nombre:fuldo fictez', facts)


class DualAgreementTest(unittest.TestCase):
    def agree(self, opus, astra, pack=None):
        return E.dual_agreement(pack or manifest(), {'opus': opus, E.ASTRA_SIDE: astra})

    def test_two_identical_readings_close_as_dual(self):
        out = self.agree(decision(), decision())
        self.assertEqual(out['mode'], 'dual')
        self.assertEqual(out['final']['entity_id'], FULDO)
        self.assertNotIn('_meta', out['final'])
        self.assertTrue(all(row['agree'] for row in out['per_unit'].values()))
        self.assertEqual((out['closed_units'], out['tanda_units']),
                         ([U_SUM, U_ROLE, U_ID, U_EDGE], []))
        self.assertEqual(out['reasons'], [])

    def test_same_facts_different_wording_closes_with_the_opus_text(self):
        opus = rewrite('Fuldo Fíctez cobró 5.700 € el 20-06-2026.')
        other = rewrite('El 20 de junio de 2026, Fuldo Fíctez recibió 5700 euros.')
        out = self.agree(opus, copy.deepcopy(other))
        self.assertEqual(out['mode'], 'dual')
        self.assertEqual(out['per_unit'][U_SUM]['mode'], 'redaccion')
        self.assertEqual(out['final']['summary']['resumen'], opus['summary']['resumen'])

    def test_a_different_confidence_is_still_only_wording(self):
        low = decision(units=[unit(U_SUM, confidence='media'), unit(U_ROLE), unit(U_ID), unit(U_EDGE)])
        out = self.agree(decision(), low)
        self.assertEqual(out['mode'], 'dual')
        self.assertEqual(out['per_unit'][U_SUM]['mode'], 'redaccion')
        self.assertEqual(out['per_unit'][U_ROLE]['mode'], 'exacto')

    def test_a_verdict_discrepancy_sends_only_that_unit_to_the_tanda(self):
        other = decision(units=[unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE), unit(U_ID),
                                unit(U_EDGE)])
        out = self.agree(decision(), other)
        self.assertEqual(out['mode'], 'parcial')
        self.assertEqual((out['closed_units'], out['tanda_units']),
                         ([U_ROLE, U_ID, U_EDGE], [U_SUM]))
        self.assertFalse(out['per_unit'][U_SUM]['agree'])
        self.assertIn('veredicto distinto', out['per_unit'][U_SUM]['reason'])
        self.assertEqual(out['per_unit'][U_SUM]['verdicts'][E.ASTRA_SIDE], 'corregido')
        self.assertEqual([u['unit_id'] for u in out['final']['units']], [U_ROLE, U_ID, U_EDGE])
        self.assertEqual(E.validate_decision(out['final'],
                                             E.restrict_pack(manifest(), out['closed_units'])), [])

    def test_a_different_label_class_sends_only_that_unit_to_the_tanda(self):
        cita = decision(units=[unit(U_SUM, 'corregido', 'cita'), unit(U_ROLE), unit(U_ID), unit(U_EDGE)])
        fondo = decision(units=[unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE), unit(U_ID),
                                unit(U_EDGE)])
        out = self.agree(cita, fondo)
        self.assertEqual((out['mode'], out['tanda_units']), ('parcial', [U_SUM]))
        self.assertIn('clase de etiqueta distinta', out['per_unit'][U_SUM]['reason'])

    def test_contradictory_evidence_sends_only_that_unit_to_the_tanda(self):
        cited = decision(units=[unit(U_SUM, evidence='informe UCO, p. 76'), unit(U_ROLE), unit(U_ID),
                                unit(U_EDGE)])
        empty = decision(units=[unit(U_SUM, evidence='0 apariciones en el corpus'), unit(U_ROLE),
                                unit(U_ID), unit(U_EDGE)])
        out = self.agree(cited, empty)
        self.assertEqual((out['mode'], out['tanda_units']), ('parcial', [U_SUM]))
        self.assertIn('contradicción de evidencia', out['per_unit'][U_SUM]['reason'])

    def test_a_rewrite_that_asserts_other_facts_keeps_the_prose_for_the_owner(self):
        opus = rewrite('Fuldo Fíctez cobró 5.700 €.')
        other = rewrite('Fuldo Fíctez cobró 7.500 €.')
        out = self.agree(opus, other)
        self.assertEqual((out['mode'], out['tanda_units']), ('parcial', [U_SUM]))
        self.assertIn('acción distinta', out['per_unit'][U_SUM]['reason'])
        # La reescritura no se aplica a medias: sale de la decisión y viaja a la tanda con su texto.
        self.assertEqual(out['final']['summary']['action'], 'none')
        self.assertEqual(out['deferred']['summary']['resumen'], opus['summary']['resumen'])
        self.assertEqual(E.validate_decision(out['final'],
                                             E.restrict_pack(manifest(), out['closed_units'])), [])

    def test_a_different_edge_action_only_costs_its_own_units(self):
        pack = edge_manifest()
        kept = [unit(U_E1), unit(U_E2)]
        changed = [unit(U_E3, 'corregido', 'arista'), unit(U_E4, 'corregido', 'arista')]
        opus = decision(units=kept + changed,
                        edges=[edge_op(U_E3, 'deny'), edge_op(U_E4, 'deny')])
        astra = decision(units=copy.deepcopy(kept + changed),
                         edges=[edge_op(U_E3, 'retype', new_type='ASSOCIATED_WITH'),
                                edge_op(U_E4, 'retype', new_type='ASSOCIATED_WITH')])
        out = self.agree(opus, astra, pack=pack)
        self.assertEqual(out['mode'], 'parcial')
        self.assertEqual((out['closed_units'], out['tanda_units']), ([U_E1, U_E2], [U_E3, U_E4]))
        self.assertEqual(out['final']['edges'], [])
        self.assertEqual([u['unit_id'] for u in out['final']['units']], [U_E1, U_E2])
        self.assertEqual(E.validate_decision(out['final'],
                                             E.restrict_pack(pack, out['closed_units'])), [])

    def test_a_different_alias_deny_sends_identity_to_the_tanda(self):
        units = [unit(U_SUM), unit(U_ROLE), unit(U_ID, 'corregido', 'alias'), unit(U_EDGE)]
        opus = decision(units=units, alias_deny=['Fuldo el de Ficcionbar'])
        astra = decision(units=copy.deepcopy(units), alias_deny=['Fuldo, el de Ficcionbar S.L.'])
        out = self.agree(opus, astra)
        self.assertEqual((out['mode'], out['tanda_units']), ('parcial', [U_ID]))
        self.assertEqual(out['final']['alias_deny'], [])
        self.assertEqual(out['deferred']['alias_deny'], ['Fuldo el de Ficcionbar'])
        self.assertEqual(E.validate_decision(out['final'],
                                             E.restrict_pack(manifest(), out['closed_units'])), [])

    def test_an_entity_action_anchored_in_an_owner_unit_never_closes_in_the_dual(self):
        """El dual lee el pack sin la identidad (espera al dueño): su `alias_deny` no se cierra aquí."""
        units = [unit(U_SUM), unit(U_ROLE), unit(U_ID, 'corregido', 'alias'), unit(U_EDGE)]
        opus = decision(units=units, alias_deny=['Fuldo el de Ficcionbar'])
        scope = E.restrict_pack(manifest(), [U_SUM, U_ROLE, U_EDGE])
        astra = decision(units=[unit(U_SUM), unit(U_ROLE), unit(U_EDGE)])
        out = E.dual_agreement(scope, {'opus': opus, E.ASTRA_SIDE: astra}, full=manifest())
        self.assertEqual((out['mode'], out['closed_units']), ('dual', [U_SUM, U_ROLE, U_EDGE]))
        self.assertEqual(out['final']['alias_deny'], [])
        self.assertEqual(out['deferred']['alias_deny'], ['Fuldo el de Ficcionbar'])
        self.assertEqual([u['unit_id'] for u in out['final']['units']], [U_SUM, U_ROLE, U_EDGE])

    def test_a_missing_reader_never_closes(self):
        out = E.dual_agreement(manifest(), {'opus': decision(), E.ASTRA_SIDE: None})
        self.assertEqual((out['mode'], out['final']), ('arbitraje', None))
        self.assertEqual(out['closed_units'], [])
        self.assertIn(f'lado sin lectura válida: {E.ASTRA_SIDE}', out['reasons'])

    def test_a_fable_reading_is_ignored(self):
        """063-B: una lectura `fable-esc` del piloto no vota ni suple a Astra."""
        other = decision(units=[unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE), unit(U_ID),
                                unit(U_EDGE)])
        without = E.dual_agreement(manifest(), {'opus': decision(), 'fable-esc': decision()})
        self.assertEqual((without['mode'], without['closed_units']), ('arbitraje', []))
        disagree = E.dual_agreement(manifest(), {'opus': decision(), E.ASTRA_SIDE: other,
                                                 'fable-esc': decision()})
        self.assertEqual(disagree['tanda_units'], [U_SUM])
        self.assertNotIn('fable-esc', disagree['per_unit'][U_SUM]['verdicts'])


class ExportTandaTest(EscalateBase):
    def setUp(self):
        super().setUp()
        self.opus = decision(units=[unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE),
                                    unit(U_ID), unit(U_EDGE, evidence='informe UCO, p. 76')],
                             summary={'action': 'replace', 'resumen': 'Fuldo Fíctez fue asesor.',
                                      'sources': [], 'reason': 'el importe no consta'})
        self.astra = decision(units=[unit(U_SUM), unit(U_ROLE, 'corregido', 'retirada'),
                                     unit(U_ID), unit(U_EDGE, evidence='0 apariciones en el corpus')],
                              role={'action': 'remove', 'role': '', 'reason': 'sin fuente'})
        self.reading('opus', self.opus)
        self.reading(E.ASTRA_SIDE, self.astra)
        self.reading('jev-route', {'answers': {'ruta': {'choice': 'dueno'}}})

    def export(self, date='2026-09-25'):
        return E.export_tanda(self.root, self.conn(), [PACK], date)

    def test_it_writes_the_three_blocks_with_the_readers_in_columns(self):
        md_path, json_path = self.export()
        text = md_path.read_text()
        self.assertIn('## A — discrepancias de veredicto', text)
        self.assertIn('## B — discrepancias de acción', text)
        self.assertIn('## C — sin discrepancia', text)
        self.assertIn(U_SUM[:8], text)
        self.assertIn('| opus | **corregido** | reescrita', text)
        self.assertIn('Propuestas:', text)
        self.assertIn('resumen replace', text)
        self.assertIn('jev-route', text)
        items = json.loads(json_path.read_text())
        self.assertEqual({i['unit_id'] for i in items}, {U_SUM, U_ROLE, U_ID, U_EDGE})
        blocks = {i['unit_id']: i['bloque'] for i in items}
        self.assertEqual(blocks[U_SUM], 'A')          # opus corrige, astra verifica
        self.assertEqual(blocks[U_ROLE], 'A')
        self.assertEqual(blocks[U_EDGE], 'B')         # mismo veredicto, evidencia contradictoria
        self.assertEqual(blocks[U_ID], 'C')
        item = next(i for i in items if i['unit_id'] == U_SUM)
        self.assertEqual((item['pack_id'], item['entity']), (PACK, FULDO))
        self.assertEqual([o['id'] for o in item['opciones']],
                         ['mantener-opus', 'adoptar-astra', 'retirar', 'verificar', 'otra'])
        self.assertIn('lectores', item['contexto'])

    def test_a_fable_reading_is_never_shown_to_the_owner(self):
        self.reading('fable-esc', decision(summary={'action': 'replace', 'resumen': 'TEXTO DE FABLE',
                                                    'sources': [], 'reason': 'x'}))
        md_path, json_path = self.export()
        self.assertNotIn('fable', md_path.read_text().lower())
        self.assertNotIn('TEXTO DE FABLE', json_path.read_text())

    def test_units_and_name_restrict_the_tanda(self):
        md_path, json_path = E.export_tanda(self.root, self.conn(), [PACK], '2026-09-25', units=[U_SUM],
                                            name='tanda-arbitraje-2026-09-25')
        self.assertEqual(json_path.name, 'tanda-arbitraje-2026-09-25.json')
        self.assertEqual([i['unit_id'] for i in json.loads(json_path.read_text())], [U_SUM])
        self.assertEqual(self.state(U_ROLE), ('pending', None))

    def test_it_marks_the_units_needs_human_without_touching_the_verified_ones(self):
        conn = self.conn()
        conn.execute("UPDATE units SET state='verified', resolution='opus-cerrada' WHERE unit_id=?",
                     (U_ID,))
        conn.commit()
        _md, json_path = self.export()
        self.assertEqual(self.state(U_SUM), ('needs-human', 'tanda-2026-09-25-pendiente'))
        self.assertEqual(self.state(U_ID), ('verified', 'opus-cerrada'))
        # Lo que ya cerró la auditoría ni se marca ni se le vuelve a preguntar al dueño.
        self.assertEqual({i['unit_id'] for i in json.loads(json_path.read_text())},
                         {U_SUM, U_ROLE, U_EDGE})
        events = [r['event'] for r in self.conn().execute('SELECT event FROM events')]
        self.assertEqual(sorted(events), ['esc-tanda'] * 3)
        # El informe se rehace en cada latido: no vuelve a anotar el evento ni cambia el estado.
        self.export()
        self.assertEqual(self.conn().execute('SELECT COUNT(*) FROM events').fetchone()[0], 3)
        self.assertEqual(self.state(U_ID), ('verified', 'opus-cerrada'))


class MergeTandaTest(EscalateBase):
    date = '2026-09-25'

    def setUp(self):
        super().setUp()
        self.opus = decision()
        self.astra = rewrite('Fuldo Fíctez fue asesor del ministro, según Infobae (16-07-2026).')
        self.reading('opus', self.opus)
        self.reading(E.ASTRA_SIDE, self.astra)

    def merge(self, *answers):
        path = self.base / 'answers.json'
        path.write_text(json.dumps([{'pack_id': PACK, **a} for a in answers], ensure_ascii=False))
        return E.merge_tanda_answers(self.root, self.conn(), path, self.date)

    def final(self):
        return json.loads((self.root / 'esc/decisions' / f'{PACK}.json').read_text())

    def test_keeping_opus_closes_the_pack_with_meta_and_verified_units(self):
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'mantener-opus'}), [PACK])
        final = self.final()
        self.assertEqual(final['_meta']['resolution'], f'humano-{self.date}')
        self.assertEqual(final['_meta']['route'], 'dueno')
        self.assertEqual(final['_meta']['pack_id'], PACK)
        self.assertEqual(sorted(final['_meta']['sides']), ['astra-esc', 'opus'])
        self.assertEqual(final['_meta']['unit_ids'], [U_SUM, U_ROLE, U_ID, U_EDGE])
        self.assertEqual(final['_meta']['overlay_ref'], 'worktree')
        for uid in (U_SUM, U_ROLE, U_ID, U_EDGE):
            self.assertEqual(self.state(uid), ('verified', f'humano-{self.date}'))
        row = self.conn().execute('SELECT state, route FROM esc_packs WHERE pack_id=?',
                                  (PACK,)).fetchone()
        self.assertEqual((row['state'], row['route']), ('closed', 'dueno'))

    def test_adopting_astra_brings_its_unit_and_its_entity_action(self):
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'adoptar-astra'}), [PACK])
        final = self.final()
        self.assertEqual(final['summary']['action'], 'replace')
        self.assertEqual(final['summary']['resumen'], self.astra['summary']['resumen'])
        adopted = next(u for u in final['units'] if u['unit_id'] == U_SUM)
        self.assertEqual((adopted['verdict'], adopted['label']), ('corregido', 'reescrita'))
        self.assertEqual(self.state(U_SUM), ('verified', f'humano-{self.date}-corregido'))
        self.assertEqual(self.state(U_ROLE), ('verified', f'humano-{self.date}'))

    def test_adopting_a_reader_without_a_reading_is_an_error_and_does_not_close(self):
        conn = self.conn()
        conn.execute('DELETE FROM esc_readings WHERE side=?', (E.ASTRA_SIDE,))
        conn.commit()
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'adoptar-astra'}), [])
        errors = (self.root / 'esc/report' / f'tanda-{self.date}-errores.md').read_text()
        self.assertIn('no hay lectura válida de astra-esc', errors)
        self.assertFalse((self.root / 'esc/decisions' / f'{PACK}.json').exists())
        self.assertEqual(self.state(U_SUM)[0], 'pending')

    def test_retiring_removes_the_prose_and_denies_the_edge(self):
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'retirar'},
                                    {'unit_id': U_EDGE, 'choice_id': 'retirar'}), [PACK])
        final = self.final()
        self.assertEqual(final['summary']['action'], 'remove')
        self.assertEqual([e['action'] for e in final['edges']], ['deny'])
        edge_unit = next(u for u in final['units'] if u['unit_id'] == U_EDGE)
        self.assertEqual((edge_unit['verdict'], edge_unit['label']), ('corregido', 'arista'))
        summary_unit = next(u for u in final['units'] if u['unit_id'] == U_SUM)
        self.assertEqual(summary_unit['label'], 'retirada')
        self.assertEqual(self.state(U_EDGE), ('verified', f'humano-{self.date}-corregido'))

    def test_retiring_an_identity_denies_the_named_aliases(self):
        self.assertEqual(self.merge({'unit_id': U_ID, 'choice_id': 'retirar',
                                     'aliases': [' Fuldo el de Ficcionbar ']}), [PACK])
        final = self.final()
        self.assertEqual(final['alias_deny'], ['Fuldo el de Ficcionbar'])
        self.assertEqual(next(u for u in final['units'] if u['unit_id'] == U_ID)['label'], 'alias')

    def opus_proposing_to_retire(self, *aliases):
        self.reading('opus', decision(units=[unit(U_SUM), unit(U_ROLE), unit(U_ID, 'corregido', 'alias'), unit(U_EDGE)],
                                      alias_deny=list(aliases)))

    def test_the_owner_answer_replaces_the_aliases_opus_proposed_to_retire(self):
        # AC281: Opus proponía retirar B y el dueño eligió solo A: se retira A, no A y B (`apply_alias_deny` retira todo
        # `alias_deny`). La decisión que llega al validador y a `materialize` lleva solo lo elegido.
        propuesto, elegido = 'Fuldo el de Ficcionbar', 'Fuldo'
        self.opus_proposing_to_retire(propuesto)
        self.assertEqual(self.merge({'unit_id': U_ID, 'choice_id': 'retirar', 'aliases': [elegido]}), [PACK])
        final = self.final()
        self.assertEqual(final['alias_deny'], [elegido])
        self.assertEqual(E.validate_decision({k: v for k, v in final.items() if k != '_meta'}, self.manifest), [])
        rows = A.materialize([{**final, '_pack': self.manifest}], '063 tanda', {})['alias_deny']
        self.assertEqual([row['drop'] for row in rows], [[elegido]])

    def test_the_aliases_the_owner_selects_are_exactly_what_alias_deny_holds(self):
        a, b = 'Fuldo', 'Fuldo el de Ficcionbar'
        unit_ = next(u for u in self.manifest['units'] if u['unit_id'] == U_ID)
        cases = [([a], [a]), ([b], [b]), ([a, b], [a, b]), ([b, a], [b, a]),     # el orden de la respuesta
                 ([a, f' {a} ', a], [a]), ([b, a, b, a], [b, a])]                # sin repetidos
        for propuesto in ([], [a], [b], [a, b]):
            for elegido, esperado in cases:
                with self.subTest(propuesto=propuesto, elegido=elegido):
                    base = decision(units=[unit(U_SUM), unit(U_ROLE), unit(U_ID, 'corregido', 'alias'), unit(U_EDGE)],
                                    alias_deny=list(propuesto))
                    problems = E._apply_answer(base, {}, unit_, {'unit_id': U_ID, 'choice_id': 'retirar',
                                                                 'aliases': elegido}, self.date, [])
                    self.assertEqual(problems, [])
                    self.assertEqual(base['alias_deny'], esperado)
                    self.assertEqual(E.validate_decision(base, self.manifest), [])

    def test_retiring_an_identity_without_naming_its_aliases_changes_nothing(self):
        # 071: la unidad es el bloque entero (nombre, alias, id); su JSON acababa como «alias» denegado.
        unit_ = next(u for u in self.manifest['units'] if u['unit_id'] == U_ID)
        cases = [({'choice_id': 'retirar'}, 'exige `aliases: [...]` con los alias a retirar (la identidad tiene 2 alias)'),
                 ({'choice_id': 'retirar', 'aliases': []}, 'exige `aliases: [...]`'),
                 ({'choice_id': 'retirar', 'aliases': ['Fuldo', 'Fuldo Fíctez']},
                  'un alias pedido no está en la identidad')]
        for answer, needle in cases:
            for base in (decision(), decision(units=[unit(U_SUM), unit(U_ROLE), unit(U_EDGE)])):
                with self.subTest(answer=answer, units=len(base['units'])):
                    before = copy.deepcopy(base)
                    problems = E._apply_answer(base, {}, unit_, {'unit_id': U_ID, **answer}, self.date, [])
                    self.assertEqual(len(problems), 1)
                    self.assertIn(needle, problems[0])
                    self.assertNotIn('Ficcionbar', problems[0])
                    self.assertEqual(base, before)
        self.assertEqual(self.merge({'unit_id': U_ID, 'choice_id': 'retirar'}), [])
        self.assertIn('retirar una identidad exige',
                      (self.root / 'esc/report' / f'tanda-{self.date}-errores.md').read_text())
        self.assertFalse((self.root / 'esc/decisions' / f'{PACK}.json').exists())

    def test_an_identity_unit_without_its_block_is_an_error_not_a_fallback(self):
        unit_ = {**next(u for u in self.manifest['units'] if u['unit_id'] == U_ID), 'value': 'Fuldo'}
        base = decision()
        before = copy.deepcopy(base)
        problems = E._apply_answer(base, {}, unit_, {'unit_id': U_ID, 'choice_id': 'retirar',
                                                     'aliases': ['Fuldo']}, self.date, [])
        self.assertTrue(problems and 'bloque `identity`' in problems[0], problems)
        self.assertEqual(base, before)

    def test_verifying_without_change_drops_the_action_of_that_unit(self):
        self.reading('opus', self.astra)                       # Opus proponía reescribir el resumen
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'verificar'}), [PACK])
        final = self.final()
        self.assertEqual(final['summary']['action'], 'none')
        self.assertEqual(next(u for u in final['units'] if u['unit_id'] == U_SUM)['verdict'],
                         'verificado')
        self.assertEqual(self.state(U_SUM), ('verified', f'humano-{self.date}'))

    def test_free_text_becomes_the_new_summary_with_a_note(self):
        text = 'Fuldo Fíctez fue asesor del ministro de Transportes (informe UCO, p.76).'
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'otra', 'text': text}), [PACK])
        final = self.final()
        self.assertEqual((final['summary']['action'], final['summary']['resumen']), ('replace', text))
        self.assertIn(U_SUM[:8], final['notes'])
        self.assertEqual(next(u for u in final['units'] if u['unit_id'] == U_SUM)['label'], 'reescrita')

    def test_free_text_without_text_does_not_close_the_pack(self):
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'otra'}), [])
        self.assertIn('exige texto',
                      (self.root / 'esc/report' / f'tanda-{self.date}-errores.md').read_text())

    def test_an_answer_that_breaks_the_schema_is_reported_not_written(self):
        # Un resumen con «según la prensa» vago no pasa el validador de la decisión.
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'otra',
                                     'text': 'Cobró dinero según la prensa.'}), [])
        errors = (self.root / 'esc/report' / f'tanda-{self.date}-errores.md').read_text()
        self.assertIn('medio nominal', errors)
        self.assertFalse((self.root / 'esc/decisions' / f'{PACK}.json').exists())

    def test_a_partial_pack_closes_its_tanda_units_in_a_file_of_its_own(self):
        conn = self.conn()
        conn.execute("UPDATE units SET state='verified', resolution='triple-cerrada' "
                     'WHERE unit_id IN (?,?)', (U_ROLE, U_ID))
        conn.commit()
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'retirar'},
                                    {'unit_id': U_EDGE, 'choice_id': 'mantener-opus'}), [PACK])
        self.assertFalse((self.root / 'esc/decisions' / f'{PACK}.json').exists())
        final = json.loads((self.root / 'esc/decisions'
                            / f'{PACK}.humano-{self.date}.json').read_text())
        self.assertEqual(final['_meta']['unit_ids'], [U_SUM, U_EDGE])
        self.assertEqual([u['unit_id'] for u in final['units']], [U_SUM, U_EDGE])
        self.assertEqual(self.state(U_ROLE), ('verified', 'triple-cerrada'))
        self.assertEqual(self.state(U_SUM), ('verified', f'humano-{self.date}-corregido'))
        # Y una respuesta sobre una unidad que el acuerdo triple ya cerró no entra en la decisión.
        self.assertEqual(self.merge({'unit_id': U_ROLE, 'choice_id': 'retirar'}), [])

    def test_a_resolution_closes_only_the_answered_units_in_a_file_of_its_own(self):
        """063-B: el árbitro fusiona con `resolution=`; lo no respondido NO se cierra con Opus."""
        res = f'arbitraje-{self.date}'
        answers = [{'unit_id': U_SUM, 'choice_id': 'retirar', 'evidence': '[auto, p.3] no consta',
                    'resolution': res},
                   {'unit_id': U_ROLE, 'choice_id': 'retirar', 'resolution': f'{res}-prudente'}]
        path = self.base / 'answers.json'
        path.write_text(json.dumps([{'pack_id': PACK, **a} for a in answers]))
        self.assertEqual(E.merge_tanda_answers(self.root, self.conn(), path, self.date, resolution=res), [PACK])
        final = json.loads((self.root / 'esc/decisions' / f'{PACK}.{res}.json').read_text())
        self.assertEqual((final['_meta']['resolution'], final['_meta']['route']), (res, 'arbitraje'))
        self.assertEqual(final['_meta']['unit_ids'], [U_SUM])
        self.assertEqual(next(u for u in final['units'] if u['unit_id'] == U_SUM)['evidence'],
                         '[auto, p.3] no consta')
        self.assertEqual(self.state(U_SUM), ('verified', f'{res}-corregido'))
        for uid in (U_ROLE, U_ID, U_EDGE):
            self.assertEqual(self.state(uid), ('pending', None))
        self.assertEqual(self.conn().execute('SELECT state FROM esc_packs WHERE pack_id=?',
                                             (PACK,)).fetchone()['state'], 'tanda')
        events = {r['event'] for r in self.conn().execute('SELECT event FROM events')}
        self.assertEqual(events, {'corregido-arbitraje'})
        # La segunda resolución no pisa la primera y cierra solo lo suyo.
        self.assertEqual(E.merge_tanda_answers(self.root, self.conn(), path, self.date,
                                               resolution=f'{res}-prudente'), [PACK])
        self.assertEqual(self.state(U_ROLE), ('verified', f'{res}-prudente-corregido'))
        self.assertTrue((self.root / 'esc/decisions' / f'{PACK}.{res}.json').exists())

    def test_the_arbiter_can_decide_without_an_opus_reading(self):
        conn = self.conn()
        conn.execute("DELETE FROM esc_readings WHERE side='opus'")
        conn.commit()
        path = self.base / 'answers.json'
        path.write_text(json.dumps([{'pack_id': PACK, 'unit_id': U_EDGE, 'choice_id': 'verificar',
                                     'evidence': '[informe UCO, p.76] pago'},
                                    {'pack_id': PACK, 'unit_id': U_ROLE, 'choice_id': 'mantener-opus'}]))
        res = f'arbitraje-{self.date}'
        self.assertEqual(E.merge_tanda_answers(self.root, self.conn(), path, self.date, resolution=res), [])
        self.assertIn('no hay lectura de Opus que mantener',
                      (self.root / 'esc/report' / f'{res}-errores.md').read_text())
        path.write_text(json.dumps([{'pack_id': PACK, 'unit_id': U_EDGE, 'choice_id': 'verificar',
                                     'evidence': '[informe UCO, p.76] pago'}]))
        self.assertEqual(E.merge_tanda_answers(self.root, self.conn(), path, self.date, resolution=res), [PACK])
        self.assertEqual(self.state(U_EDGE), ('verified', res))
        # El dueño, en cambio, sigue necesitando la lectura de Opus de la que partir.
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'retirar'}), [])

    def test_fable_readings_in_the_ledger_are_ignored(self):
        self.reading('fable-esc', self.astra)
        self.assertEqual(sorted(E._decisions(E.readings_of(self.conn(), PACK))), ['astra-esc', 'opus'])
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'adoptar-fable'}), [])
        self.assertIn("opción desconocida 'adoptar-fable'",
                      (self.root / 'esc/report' / f'tanda-{self.date}-errores.md').read_text())
        self.assertEqual(self.merge({'unit_id': U_SUM, 'choice_id': 'mantener-opus'}), [PACK])
        self.assertEqual(sorted(self.final()['_meta']['sides']), ['astra-esc', 'opus'])

    def test_historic_triple_and_new_dual_and_arbiter_closures_count_as_closed(self):
        conn = self.conn()
        for uid, resolution in ((U_SUM, 'triple-cerrada'), (U_ROLE, 'dual-cerrada-corregido'),
                                (U_ID, f'arbitraje-{self.date}-prudente-corregido')):
            conn.execute("UPDATE units SET state='verified', resolution=? WHERE unit_id=?", (resolution, uid))
        conn.execute("UPDATE units SET resolution='arbitraje-pendiente' WHERE unit_id=?", (U_EDGE,))
        conn.commit()
        self.assertEqual(E.closed_units(conn, [U_SUM, U_ROLE, U_ID, U_EDGE]), {U_SUM, U_ROLE, U_ID})

    def test_merging_twice_does_not_duplicate_events(self):
        answer = {'unit_id': U_SUM, 'choice_id': 'retirar'}
        self.merge(answer)
        first = [dict(r) for r in self.conn().execute('SELECT * FROM events ORDER BY seq')]
        self.assertEqual(self.merge(answer), [PACK])
        second = [dict(r) for r in self.conn().execute('SELECT * FROM events ORDER BY seq')]
        self.assertEqual(len(first), 4)
        self.assertEqual([e['unit_id'] for e in first], [e['unit_id'] for e in second])
        self.assertEqual(sorted({e['event'] for e in second}),
                         ['corregido-humano', 'verificado-humano'])


if __name__ == '__main__':
    unittest.main()


class ShellWrapTest(unittest.TestCase):
    """Piloto 2026-09-21: codex envuelve cada comando en `/bin/zsh -lc "…"`; sigue siendo una búsqueda."""

    def test_wrapped_search_is_a_search(self):
        from src.qa_esc_escalate import is_search, unwrap_shell
        inner = "python3 /var/folders/x/esc-astra-abc/qa_esc_ev.py -n 6 'Fuldo|Mengano' --doc uco"
        self.assertEqual(unwrap_shell(f'/bin/zsh -lc "{inner}"'), inner)
        self.assertTrue(is_search(f'/bin/zsh -lc "{inner}"'))
        self.assertTrue(is_search('bash -c "python3 /tmp/x/qa_esc_ev.py -n 4 Fuldo"'))
        self.assertFalse(is_search('/bin/zsh -lc "ls /tmp"'))
        self.assertFalse(is_search(f'/bin/zsh -lc "{inner}; cat /etc/passwd"'))


class UnwrapBypassTest(unittest.TestCase):
    """Revisión de seguridad 2026-09-21: el desenvoltorio solo acepta exactamente `zsh -lc "<cmd>"`."""

    def test_early_closing_quote_is_not_unwrapped(self):
        from src.qa_esc_escalate import is_search, unwrap_shell
        ev = 'python3 /tmp/esc-astra-x/qa_esc_ev.py'
        evil = f'/bin/zsh -lc "{ev} \'a\'"; ls /; /bin/zsh -lc "{ev} \'b\'"'
        self.assertFalse(is_search(evil))
        self.assertNotEqual(unwrap_shell(evil), f"{ev} 'a'\"; ls /; /bin/zsh -lc \"{ev} 'b'")
        self.assertTrue(is_search(f'/bin/zsh -lc "{ev} -n 4 \'Fuldo|Mengano\'"'))
        self.assertFalse(is_search(f'/bin/zsh -lc "{ev} \'a\' | head"'))
        self.assertFalse(is_search('/bin/zsh -lc "cat pack.md"'))
