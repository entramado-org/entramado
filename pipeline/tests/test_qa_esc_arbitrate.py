"""Spec 063-B — árbitro Opus 5.5 + validación de Astra (sin modelos, red ni grafo).

El censo y el pack son los de `test_qa_esc_escalate` (Fuldo: resumen, rol, alias y arista), con una
tabla `chunks` mínima para que las citas del árbitro se puedan extraer. Los dos CLIs son un runner
falso: para el árbitro el `stream-json` de `claude -p` (con las búsquedas como bloques `tool_use`) y
para la validación los eventos de `codex exec` más el fichero de `--output-last-message`.
"""
import json
from pathlib import Path
import unittest

from src import qa_esc as S
from src import qa_esc_arbitrate as A
from src import qa_esc_escalate as X
from src.qa_esc_schema import arbitration_schema_for, schema_is_flat, validate_arbitration
from tests.test_qa_esc_escalate import (FULDO, PACK, U_EDGE, U_ID, U_ROLE, U_SUM, EscalateBase,
                                        codex_events, decision, manifest, unit)

DOC = 'informe-uco.pdf'
PAGE_TEXT = ('Fuldo Fíctez, asesor del ministro de Transportes, figura en el pago de 5.700 euros a '
             'Ficcionbar según el informe de la UCO.')
CITA = {'doc': DOC, 'pagina': '76', 'url': ''}
ALL = (U_SUM, U_ROLE, U_ID, U_EDGE)


def arb(uid, choice='verificar', citas=None, sostenido=True, al_dueno=False, text='', motivo='sostenido',
        aliases=()):
    return {'unit_id': uid, 'choice_id': choice, 'text': text, 'sostenido': sostenido,
            'evidence': f'[{DOC}, p.76] «pago de 5.700 euros a Ficcionbar»',
            'citas': [dict(CITA)] if citas is None else citas, 'al_dueno': al_dueno, 'motivo': motivo,
            'aliases': list(aliases)}


def arbitration(*rows):
    return {'units': list(rows)}


def verdicts(**by_unit):
    return {'units': [{'unit_id': uid, 'veredicto': v, 'motivo': f'motivo {v}'} for uid, v in by_unit.items()]}


def search(term='Fuldo'):
    return f"python3 {S.EV_PATH} '{term}'"


class FakeCLIs:
    """`claude -p` (árbitro) y `codex exec` (validación): una respuesta por llamada, en orden."""

    def __init__(self, arbiter=(), validator=(), searches=(), foreign=()):
        self.arbiter, self.validator = list(arbiter), list(validator)
        self.searches, self.foreign = list(searches), list(foreign)
        self.calls = {'claude': [], 'astra': []}

    def __call__(self, cmd, stdin_text, timeout, stdout_path, stderr_path, cwd):
        Path(stderr_path).write_text('')
        if 'codex' in cmd:
            self.calls['astra'].append(stdin_text)
            result = self.validator.pop(0) if self.validator else None
            Path(stdout_path).write_text(codex_events())
            if result is not None:
                Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(result))
            return 0, None
        self.calls['claude'].append({'cmd': cmd, 'stdin': stdin_text})
        result = self.arbiter.pop(0) if self.arbiter else None
        events = [{'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': f'toolu_{i}', 'name': name, 'input': {'command': command}}]}}
            for i, (name, command) in enumerate([('Bash', c) for c in self.searches] + list(self.foreign))]
        events.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'stop_reason': 'end_turn',
                       'num_turns': 3, 'total_cost_usd': 1.25,
                       'modelUsage': {'claude-opus-5-5': {'inputTokens': 10, 'outputTokens': 2000,
                                                          'cacheReadInputTokens': 50000}},
                       'permission_denials': [], 'structured_output': result})
        Path(stdout_path).write_text(''.join(json.dumps(e) + '\n' for e in events))
        return 0, None


class ArbitrateBase(EscalateBase):
    arb_on_setup = True

    def setUp(self):
        super().setUp()
        self.repo = self.base / 'repo'
        spec = self.repo / S.SPEC_DIR
        spec.mkdir(parents=True)
        (spec / A.ARB_BRIEF).write_text('BRIEF-ARB de prueba')
        (spec / A.VAL_BRIEF).write_text('BRIEF-VAL de prueba')
        S.save_state(self.root, S.initial_state())
        self.date = S._today()
        conn = self.conn()
        conn.execute('CREATE TABLE chunks (doc_id TEXT, page_start INTEGER, page_end INTEGER, text TEXT)')
        conn.execute('INSERT INTO chunks VALUES (?,?,?,?)', (DOC, 76, 76, PAGE_TEXT))
        conn.commit()
        (self.root / 'sources').mkdir()
        self.reading('opus', decision())
        self.reading(X.ASTRA_SIDE, decision(units=[unit(U_SUM, 'corregido', 'retirada'), unit(U_ROLE),
                                                   unit(U_ID), unit(U_EDGE)],
                                            summary={'action': 'remove', 'resumen': '', 'sources': [],
                                                     'reason': 'sin fuente'}))
        if self.arb_on_setup:
            self.to_arbitration()

    def to_arbitration(self, units=ALL, riesgo=None):
        conn = self.conn()
        with conn:
            if riesgo:
                conn.execute('UPDATE esc_packs SET riesgo=? WHERE pack_id=?', (riesgo, PACK))
            S.to_arbitration(conn, self.root, PACK, list(units), {u: 'acción distinta' for u in units}, 'arbitraje')

    def arbitrate(self, clis, **kwargs):
        return A.arbitrate(self.root, self.repo, runner=clis, **kwargs)

    def pack_state(self):
        return self.conn().execute('SELECT state FROM esc_packs WHERE pack_id=?', (PACK,)).fetchone()['state']

    def ledger(self):
        conn = self.conn()
        return [[tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('units', 'events', 'esc_packs')]

    def answers(self):
        path = self.root / 'esc/report' / f'arbitraje-{self.date}.answers.json'
        return {a['unit_id']: a for a in json.loads(path.read_text())}


class ArbiterClosesTest(ArbitrateBase):
    def test_valid_arbitration_closes_every_unit_with_the_arbiter_choice(self):
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, 'mantener-opus'), arb(U_ROLE), arb(U_ID),
                                             arb(U_EDGE, 'retirar', citas=[], sostenido=False))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_ID: 'valida'})],
                        searches=[search()])
        out = self.arbitrate(clis)
        self.assertEqual((out['arbitraje'], out['arbitrados'], out['packs']), (4, 1, {'closed': 1}))
        self.assertEqual(self.state(U_SUM), ('verified', f'arbitraje-{self.date}'))
        self.assertEqual(self.state(U_EDGE), ('verified', f'arbitraje-{self.date}-corregido'))
        self.assertEqual(self.pack_state(), 'closed')
        final = json.loads((self.root / 'esc/decisions' / f'{PACK}.arbitraje-{self.date}.json').read_text())
        self.assertEqual(final['_meta']['route'], 'arbitraje')
        self.assertEqual([e['action'] for e in final['edges']], ['deny'])
        # «mantener-opus» parte de la lectura de Opus, no de la de Astra (que retiraba el resumen).
        self.assertEqual(final['summary']['action'], 'none')
        # El árbitro corre con Opus 5.5 a xhigh y SOLO el buscador; el retirar no se valida.
        cmd = clis.calls['claude'][0]['cmd']
        self.assertEqual((cmd[cmd.index('--model') + 1], cmd[cmd.index('--effort') + 1]),
                         ('claude-opus-5-5', 'xhigh'))
        self.assertEqual(cmd[cmd.index('--allowedTools') + 1], f'Bash(python3 {S.EV_PATH}:*)')
        self.assertIn(U_SUM, clis.calls['astra'][0])
        self.assertNotIn(f'### {U_EDGE}', clis.calls['astra'][0])
        self.assertIn(PAGE_TEXT[:40], clis.calls['astra'][0])
        window = S.load_state(self.root)['window']
        self.assertEqual((window['usedClaude'], window['cachedClaude']), (2010, 50000))
        self.assertGreater(window['usedAstra'], 0)
        report = (self.root / 'esc/report' / f'arbitraje-{self.date}.md').read_text()
        self.assertIn('## Retiradas (1)', report)
        self.assertIn('**mantener-opus**', report)
        self.assertIn(f'{DOC} p.76', report)

    def test_the_decisions_pass_the_apply_gate(self):
        from src import qa_esc_apply
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, 'otra', text='Fuldo Fíctez fue asesor del ministro '
                                                                   '(informe UCO, p.76).'),
                                             arb(U_ROLE), arb(U_ID, 'retirar', citas=[], sostenido=False,
                                                              aliases=['Fuldo el de Ficcionbar']),
                                             arb(U_EDGE))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'no_valida', U_EDGE: 'valida'})])
        self.arbitrate(clis)
        decisions = qa_esc_apply.load_decisions(self.root)
        errors, coverage = qa_esc_apply.gate(self.conn(), decisions, self.root)
        self.assertEqual(errors, [])
        arbitraje = next(d for d in decisions if d['_meta']['resolution'] == f'arbitraje-{self.date}')
        self.assertEqual(arbitraje['alias_deny'], ['Fuldo el de Ficcionbar'])
        self.assertEqual(dict(coverage['por_resolucion']),
                         {f'arbitraje-{self.date}': 1, f'arbitraje-{self.date}-prudente': 1})

    def test_without_an_opus_reading_the_arbiter_reads_from_scratch(self):
        conn = self.conn()
        conn.execute("DELETE FROM esc_readings WHERE side='opus'")
        conn.commit()
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{u: 'valida' for u in ALL})])
        self.arbitrate(clis)
        self.assertIn('**opus**: sin lectura válida (decide desde cero)', clis.calls['claude'][0]['stdin'])
        self.assertEqual(self.pack_state(), 'closed')
        self.assertEqual(self.state(U_EDGE), ('verified', f'arbitraje-{self.date}'))
        # G7: la lectura del árbitro cuenta como lectura de Opus, no es un cierre «solo Jev».
        self.assertEqual(S.g7(self.conn())['cierres_solo_jev'], 0)

    def test_adopting_astra_starts_from_the_astra_reading(self):
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, 'adoptar-astra'), arb(U_ROLE), arb(U_ID), arb(U_EDGE))],
                        validator=[verdicts(**{u: 'valida' for u in ALL})])
        self.arbitrate(clis)
        final = json.loads((self.root / 'esc/decisions' / f'{PACK}.arbitraje-{self.date}.json').read_text())
        self.assertEqual(final['summary']['action'], 'remove')
        self.assertEqual(self.state(U_SUM), ('verified', f'arbitraje-{self.date}-corregido'))


class PrudentTest(ArbitrateBase):
    def test_no_valida_retires_the_unit_prudently(self):
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{U_SUM: 'no_valida', U_ROLE: 'valida', U_ID: 'valida',
                                               U_EDGE: 'valida'})])
        out = self.arbitrate(clis)
        self.assertEqual((out['prudente'], out['arbitraje']), (1, 3))
        self.assertEqual(self.state(U_SUM), ('verified', f'arbitraje-{self.date}-prudente-corregido'))
        self.assertEqual(self.state(U_ROLE), ('verified', f'arbitraje-{self.date}'))
        prudent = json.loads((self.root / 'esc/decisions'
                              / f'{PACK}.arbitraje-{self.date}-prudente.json').read_text())
        self.assertEqual(prudent['summary']['action'], 'remove')
        self.assertEqual(prudent['_meta']['unit_ids'], [U_SUM])
        self.assertEqual(self.answers()[U_SUM]['choice_id'], 'retirar')

    def test_a_citation_that_is_not_in_the_corpus_is_no_valida_without_calling_astra(self):
        ghost = [{'doc': 'no-existe.pdf', 'pagina': '3', 'url': ''}]
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, citas=ghost), arb(U_ROLE, citas=ghost),
                                             arb(U_ID, 'retirar', citas=[], sostenido=False,
                                                 aliases=['Fuldo el de Ficcionbar']),
                                             arb(U_EDGE, citas=[{'doc': DOC, 'pagina': '99', 'url': ''}]))])
        out = self.arbitrate(clis)
        self.assertEqual(clis.calls['astra'], [])
        self.assertEqual((out['prudente'], out['arbitraje']), (3, 1))
        for uid in (U_SUM, U_ROLE, U_EDGE):
            self.assertEqual(self.state(uid)[1], f'arbitraje-{self.date}-prudente-corregido')
        self.assertEqual(self.state(U_ID), ('verified', f'arbitraje-{self.date}-corregido'))
        self.assertEqual(self.answers()[U_ID]['aliases'], ['Fuldo el de Ficcionbar'])
        self.assertIn('cita no encontrada', self.answers()[U_SUM]['evidence'])

    def test_a_prudent_withdrawal_of_an_identity_names_no_alias_and_goes_to_the_owner(self):
        # 071: la retirada prudente no sabe QUÉ alias retira; antes denegaba el bloque entero como alias.
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM), arb(U_ROLE), arb(U_ID, citas=[
            {'doc': '', 'pagina': '', 'url': 'https://x.org/n'}]), arb(U_EDGE))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_EDGE: 'valida'})])
        out = self.arbitrate(clis)
        # La fusión prudente no valida (sin alias que retirar) y la unidad pasa al dueño (`fusion-invalida`).
        self.assertEqual((out.get('prudente', 0), out['dueno'], out['arbitraje']), (0, 1, 3))
        self.assertEqual(self.state(U_ID), ('needs-human', f'tanda-{self.date}-pendiente'))
        self.assertEqual(self.state(U_SUM), ('verified', f'arbitraje-{self.date}'))
        self.assertFalse((self.root / 'esc/decisions' / f'{PACK}.arbitraje-{self.date}-prudente.json').exists())
        self.assertIn('retirar una identidad exige',
                      (self.root / 'esc/report' / f'arbitraje-{self.date}-prudente-errores.md').read_text())

    def test_the_arbiter_over_the_search_budget_twice_goes_to_the_owner(self):
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))] * 2,
                        searches=[search(f't{i}') for i in range(13)])
        out = self.arbitrate(clis)
        self.assertEqual(len(clis.calls['claude']), 2)
        self.assertEqual(clis.calls['astra'], [])
        # Árbitro nulo = fallo técnico, no juicio sobre el dato: nada se retira solo (dueño, 28-09).
        self.assertEqual((out.get('prudente', 0), out['dueno']), (0, 4))
        for uid in ALL:
            self.assertEqual(self.state(uid), ('needs-human', f'tanda-{self.date}-pendiente'))
        receipt = json.loads(self.conn().execute('SELECT receipt FROM esc_readings WHERE side=?',
                                                 (A.ARB_SIDE,)).fetchone()['receipt'])
        self.assertEqual(receipt['error'], 'tool-budget-exceeded')
        # Calidad del lector: no alimenta el disyuntor.
        self.assertEqual(S.load_state(self.root)['consecutiveFailures'], 0)

    def test_a_foreign_command_voids_the_arbiter(self):
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))] * 2,
                        foreign=[('Bash', 'cat /etc/passwd')])
        self.arbitrate(clis)
        receipt = json.loads(self.conn().execute('SELECT receipt FROM esc_readings WHERE side=?',
                                                 (A.ARB_SIDE,)).fetchone()['receipt'])
        self.assertEqual(receipt['error'], 'unexpected-tool-use')
        self.assertEqual(self.state(U_SUM), ('needs-human', f'tanda-{self.date}-pendiente'))

    def test_one_null_arbiter_is_retried_in_the_same_pass(self):
        clis = FakeCLIs(arbiter=[{'units': []}, arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{u: 'valida' for u in ALL})])
        out = self.arbitrate(clis)
        self.assertEqual((len(clis.calls['claude']), out['arbitraje']), (2, 4))

    def test_astra_null_twice_goes_to_the_owner(self):
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))], validator=[{'units': []}, {'units': []}])
        out = self.arbitrate(clis)
        self.assertEqual((len(clis.calls['astra']), out.get('prudente', 0), out['dueno']), (2, 0, 4))
        self.assertEqual(self.state(U_ROLE), ('needs-human', f'tanda-{self.date}-pendiente'))


class OwnerTest(ArbitrateBase):
    def owner(self, clis, uid):
        self.arbitrate(clis)
        self.assertEqual(self.state(uid), ('needs-human', f'tanda-{self.date}-pendiente'))
        self.assertEqual(self.pack_state(), 'tanda')
        items = json.loads((self.root / 'esc/report' / f'tanda-arbitraje-{self.date}.json').read_text())
        self.assertIn(uid, {i['unit_id'] for i in items})
        return {i['unit_id']: i for i in items}

    def test_exculpatory_text_goes_to_the_owner(self):
        text = 'Fuldo Fíctez fue investigado; la causa se archivó en 2026 (auto p.3).'
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, 'otra', text=text), arb(U_ROLE), arb(U_ID), arb(U_EDGE))],
                        validator=[verdicts(**{U_ROLE: 'valida', U_ID: 'valida', U_EDGE: 'valida'})])
        items = self.owner(clis, U_SUM)
        # Lo demás cierra; el dueño solo ve la exculpatoria, con la elección del árbitro delante.
        self.assertEqual(set(items), {U_SUM})
        self.assertEqual(items[U_SUM]['contexto']['arbitro']['choice_id'], 'otra')
        self.assertEqual(self.state(U_ROLE), ('verified', f'arbitraje-{self.date}'))

    def test_exculpatory_current_text_goes_to_the_owner_even_to_retire(self):
        conn = self.conn()
        conn.execute('UPDATE units SET value=? WHERE unit_id=?',
                     (json.dumps('Absuelto por la Audiencia Nacional.'), U_ROLE))
        conn.commit()
        manifest_ = manifest()
        manifest_['units'][1]['value'] = 'Absuelto por la Audiencia Nacional.'
        (self.root / 'esc/packs' / f'{PACK}.json').write_text(json.dumps(manifest_))
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM), arb(U_ROLE, 'retirar', citas=[], sostenido=False),
                                             arb(U_ID), arb(U_EDGE))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ID: 'valida', U_EDGE: 'valida'})])
        self.owner(clis, U_ROLE)

    def test_the_ruling_wording_absolvemos_and_its_nouns_are_exculpatory(self):
        # «absuelt» no casaba con el fallo en mayúsculas; retiró una absolución sin el dueño (23-09).
        for text in ('ABSOLVEMOS LIBREMENTE A LOS ACUSADOS', 'pronunciamiento absolutorio',
                     'pide la libre absolución', 'la Audiencia lo excluyó del proceso principal',
                     'le levantaron la imputación', 'la esposa no está imputada'):
            self.assertTrue(A._exculpatory(text), text)
        self.assertFalse(A._exculpatory('Fuldo Fíctez figura en el pago de 5.700 euros'))

    def test_a_favorable_status_on_the_edge_goes_to_the_owner(self):
        man = manifest()
        edge = next(u for u in man['units'] if u['unit_id'] == U_EDGE)
        edge['edge']['status'] = 'absuelto'
        self.assertIn('exculpatorio', A.fixed_owner_reasons(edge, man, {}, 'alto'))
        edge['edge']['status'] = 'investigado'
        self.assertNotIn('exculpatorio', A.fixed_owner_reasons(edge, man, {}, 'alto'))

    def test_critical_risk_goes_to_the_owner(self):
        conn = self.conn()
        conn.execute("UPDATE esc_packs SET riesgo='critico' WHERE pack_id=?", (PACK,))
        conn.commit()
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))])
        out = self.arbitrate(clis)
        self.assertEqual(out['dueno'], 4)
        # Lo que va al dueño en cualquier caso no se le paga al árbitro ni a Astra.
        self.assertEqual(clis.calls, {'claude': [], 'astra': []})
        for uid in ALL:
            self.assertEqual(self.state(uid), ('needs-human', f'tanda-{self.date}-pendiente'))

    def test_a_procedural_status_change_goes_to_the_owner(self):
        self.reading('opus', decision(units=[unit(U_SUM), unit(U_ROLE), unit(U_ID),
                                             unit(U_EDGE, 'corregido', 'estado')],
                                      estado={'action': 'set', 'status': 'imputado', 'fecha': '2026-06-01',
                                              'doc': DOC, 'url': '', 'tipo': 'auto', 'nota': ''}))
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_ID: 'valida'})])
        self.owner(clis, U_EDGE)

    def test_a_relative_named_in_the_published_text_goes_to_the_owner(self):
        text = 'Hacienda investiga al expresidente, a su esposa y a sus hijas (LD 03-07-2026).'
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, 'otra', text=text), arb(U_ROLE), arb(U_ID), arb(U_EDGE))],
                        validator=[verdicts(**{U_ROLE: 'valida', U_ID: 'valida', U_EDGE: 'valida'})])
        items = self.owner(clis, U_SUM)
        self.assertEqual(set(items), {U_SUM})
        self.assertEqual(self.state(U_ROLE), ('verified', f'arbitraje-{self.date}'))

    def test_the_orchestrator_list_sends_a_decided_unit_to_the_owner(self):
        path = self.root / A.OWNER_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({U_EDGE[:8]: 'arista duplicada'}))
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_ID: 'valida'})])
        items = self.owner(clis, U_EDGE)
        self.assertEqual(set(items), {U_EDGE})
        # Se aplica tras el árbitro: su lectura es la misma que sin la lista (no se repaga).
        self.assertEqual(len(clis.calls['claude']), 1)

    def test_al_dueno_and_identity_without_verificar_go_to_the_owner(self):
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM, al_dueno=True), arb(U_ROLE),
                                             arb(U_ID, 'mantener-opus'), arb(U_EDGE))],
                        validator=[verdicts(**{U_ROLE: 'valida', U_EDGE: 'valida'})])
        items = self.owner(clis, U_ID)
        self.assertEqual(set(items), {U_SUM, U_ID})


class FableIgnoredTest(ArbitrateBase):
    def test_fable_readings_never_reach_the_arbiter_nor_its_choices(self):
        self.reading('fable-esc', decision(units=[unit(U_SUM, 'corregido', 'reescrita'), unit(U_ROLE),
                                                  unit(U_ID), unit(U_EDGE)],
                                           summary={'action': 'replace', 'resumen': 'TEXTO DE FABLE',
                                                    'sources': [], 'reason': 'x'}))
        self.assertEqual(set(A.readers_of(self.conn(), PACK)), {'opus', X.ASTRA_SIDE})
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{u: 'valida' for u in ALL})])
        self.arbitrate(clis)
        stdin = clis.calls['claude'][0]['stdin']
        self.assertNotIn('fable', stdin.lower())
        self.assertNotIn('TEXTO DE FABLE', stdin)
        schema = arbitration_schema_for(manifest())
        self.assertNotIn('adoptar-fable', json.dumps(schema))
        self.assertTrue(schema_is_flat(schema))
        # Sin lectura de Astra, «adoptar-astra» no vale aunque haya una de Fable.
        readers = {'opus': decision(), X.ASTRA_SIDE: None}
        errors = validate_arbitration(arbitration(arb(U_SUM, 'adoptar-astra'), arb(U_ROLE), arb(U_ID),
                                                  arb(U_EDGE)), manifest(), readers)
        self.assertTrue(any('adoptar-astra sin lectura válida' in e for e in errors))


class ValidateArbitrationTest(unittest.TestCase):
    readers = {'opus': decision(), X.ASTRA_SIDE: decision()}

    def errors(self, *rows):
        return validate_arbitration(arbitration(*rows), manifest(), self.readers)

    def test_a_complete_arbitration_is_accepted(self):
        self.assertEqual(self.errors(*(arb(u) for u in ALL)), [])

    def test_otra_on_an_edge_is_invalid(self):
        errors = self.errors(arb(U_SUM), arb(U_ROLE), arb(U_ID), arb(U_EDGE, 'otra', text='algo'))
        self.assertTrue(any('«otra» no cabe en una unidad edge' in e for e in errors))

    def test_the_other_rules(self):
        cases = {
            'otra sin texto': ([arb(U_SUM, 'otra')], '«otra» exige text'),
            'no sostenido': ([arb(U_SUM, sostenido=False)], 'no sostenido con verificar'),
            'sin citas': ([arb(U_SUM, citas=[])], 'verificar sin citas'),
            'cita rota': ([arb(U_SUM, citas=[{'doc': DOC, 'pagina': '', 'url': ''}])], 'cita sin doc+pagina'),
            'repetida': ([arb(U_SUM), arb(U_SUM)], 'repetida'),
        }
        for name, (rows, needle) in cases.items():
            with self.subTest(name):
                errors = self.errors(*rows, *(arb(u) for u in (U_ROLE, U_ID, U_EDGE)))
                self.assertTrue(any(needle in e for e in errors), errors)
        missing = self.errors(arb(U_SUM))
        self.assertTrue(any('sin decisión del árbitro' in e for e in missing))
        # Retirar sin citas y sin sostener es lo prudente: vale.
        self.assertEqual(self.errors(arb(U_SUM, 'retirar', citas=[], sostenido=False),
                                     *(arb(u) for u in (U_ROLE, U_ID, U_EDGE))), [])

    def test_retiring_an_identity_names_its_aliases(self):
        # 071: la unidad de identidad es el bloque entero; lo que se deniega son alias suyos, con nombre.
        others = [arb(u) for u in (U_SUM, U_ROLE, U_EDGE)]
        retire = dict(choice='retirar', citas=[], sostenido=False)
        cases = [((), 'retirar una identidad exige `aliases: [...]`'),
                 (('Fuldo Fíctez',), 'un alias pedido no está en la identidad')]
        for aliases, needle in cases:
            with self.subTest(aliases=aliases):
                errors = self.errors(arb(U_ID, aliases=aliases, **retire), *others)
                self.assertTrue(any(needle in e for e in errors), errors)
        self.assertEqual(self.errors(arb(U_ID, aliases=['Fuldo'], **retire), *others), [])
        self.assertIn('aliases', arbitration_schema_for(manifest())['properties']['units']['items']['required'])

    def test_the_validation_contract(self):
        from src.qa_esc_schema import validate_validation, validation_schema_for
        self.assertTrue(schema_is_flat(validation_schema_for([U_SUM, U_ROLE])))
        self.assertEqual(validate_validation(verdicts(**{U_SUM: 'valida', U_ROLE: 'no_valida'}), [U_SUM, U_ROLE]), [])
        errors = validate_validation({'units': [{'unit_id': U_SUM, 'veredicto': 'no_valida', 'motivo': ''}]},
                                     [U_SUM, U_ROLE])
        self.assertTrue(any('no_valida sin motivo' in e for e in errors))
        self.assertTrue(any('sin veredicto' in e for e in errors))

    def test_keeping_an_uncertain_reading_is_invalid(self):
        uncertain = decision(units=[unit(U_SUM, 'incierto', sources=['auto p.3']), unit(U_ROLE), unit(U_ID),
                                    unit(U_EDGE)])
        errors = validate_arbitration(arbitration(arb(U_SUM, 'mantener-opus'), arb(U_ROLE), arb(U_ID), arb(U_EDGE)),
                                      manifest(), {'opus': uncertain, X.ASTRA_SIDE: None})
        self.assertTrue(any('lectura incierta' in e for e in errors))


class DryRunAndIdempotencyTest(ArbitrateBase):
    def test_dry_run_writes_answers_and_report_without_touching_the_ledger(self):
        before = self.ledger()
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{U_SUM: 'no_valida', U_ROLE: 'valida', U_ID: 'valida',
                                               U_EDGE: 'valida'})])
        out = self.arbitrate(clis, dry_run=True)
        self.assertTrue(out['dry_run'])
        self.assertEqual(self.ledger(), before)
        self.assertFalse((self.root / 'esc/decisions').exists())
        answers = self.answers()
        self.assertEqual(answers[U_SUM]['resolution'], f'arbitraje-{self.date}-prudente')
        self.assertEqual(answers[U_ROLE]['resolution'], f'arbitraje-{self.date}')
        self.assertIn('DRY-RUN', (self.root / 'esc/report' / f'arbitraje-{self.date}.md').read_text())
        # La pasada real reutiliza las lecturas ya pagadas: ningún CLI se vuelve a lanzar.
        again = FakeCLIs()
        out = self.arbitrate(again)
        self.assertEqual(again.calls, {'claude': [], 'astra': []})
        self.assertEqual(self.state(U_SUM)[1], f'arbitraje-{self.date}-prudente-corregido')
        self.assertEqual(self.pack_state(), 'closed')

    def test_the_step_is_idempotent(self):
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{u: 'valida' for u in ALL})])
        self.arbitrate(clis)
        after = self.ledger()
        again = FakeCLIs()
        out = self.arbitrate(again)
        self.assertEqual(again.calls, {'claude': [], 'astra': []})
        self.assertNotIn('arb-pending', out['packs'])
        self.assertEqual(self.ledger(), after)

    def test_quota_leaves_the_pack_waiting(self):
        class Quota(FakeCLIs):
            def __call__(self, cmd, stdin_text, timeout, stdout_path, stderr_path, cwd):
                Path(stdout_path).write_text('')
                Path(stderr_path).write_text('429 rate limit: you have hit your quota')
                return 1, None
        before = self.ledger()
        out = self.arbitrate(Quota())
        self.assertEqual(out['parada'], 'quota')
        self.assertEqual(self.pack_state(), 'arb-pending')
        self.assertEqual(self.ledger(), before)
        self.assertIsNotNone(S.load_state(self.root)['window']['pausedUntil'])


class LoopTest(ArbitrateBase):
    def test_the_heartbeat_fires_arbitrate_with_the_default_n_on_an_old_state(self):
        state = S.load_state(self.root)
        state['perFire'].pop('arbitrate')                    # estado del piloto, anterior a la 063-B
        S.save_state(self.root, state)
        calls = []
        out = S.step(self.root, self.repo, spawn=lambda root, action, n: calls.append((action, n)) or 999999)
        self.assertEqual(out['action'], 'ARBITRATE')
        self.assertEqual(calls, [('ARBITRATE', S.PER_FIRE['arbitrate'])])

    def test_the_cli_runs_arbitrate_under_the_lock_with_its_flags(self):
        from unittest.mock import patch
        seen = {}

        def fake(root, repo, n=None, dry_run=False, from_tanda=False):
            seen.update(n=n, dry_run=dry_run, from_tanda=from_tanda, locked=not S.lock_free(root))
            return {'ok': True}

        with patch.object(A, 'arbitrate', fake):
            S.main(['--root', str(self.root), '--repo', str(self.repo), 'arbitrate', '--n', '3', '--dry-run',
                    '--from-tanda'])
        self.assertEqual(seen, {'n': 3, 'dry_run': True, 'from_tanda': True, 'locked': True})


class RouterOwnerUnitTest(ArbitrateBase):
    """Enrutado por unidad (2026-10-01): la identidad ya espera al dueño mientras el árbitro decide el resto."""
    arb_on_setup = False

    def setUp(self):
        super().setUp()
        conn = self.conn()
        with conn:
            conn.execute("UPDATE units SET state='needs-human', resolution='tanda-2026-10-01-pendiente' "
                         'WHERE unit_id=?', (U_ID,))
        self.to_arbitration(units=(U_SUM, U_ROLE, U_EDGE))

    def test_the_arbiter_closes_its_scope_and_the_pack_waits_for_the_owner(self):
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM), arb(U_ROLE), arb(U_EDGE))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_EDGE: 'valida'})])
        out = self.arbitrate(clis)
        self.assertEqual(out['arbitraje'], 3)
        self.assertNotIn(f'### {U_ID}', clis.calls['claude'][0]['stdin'])
        self.assertEqual(self.state(U_ID), ('needs-human', 'tanda-2026-10-01-pendiente'))
        # Ni `closed` sin el dueño ni `arb-pending` para siempre: a la tanda.
        self.assertEqual(self.pack_state(), 'tanda')
        self.assertEqual(S.decide(self.root)['action'], 'DONE')

    def test_an_arb_pending_pack_with_nothing_left_to_arbitrate_goes_to_the_tanda(self):
        conn = self.conn()
        with conn:
            conn.execute("UPDATE units SET state='verified', resolution='dual-cerrada' WHERE unit_id IN (?,?,?)",
                         (U_SUM, U_ROLE, U_EDGE))
        out = self.arbitrate(FakeCLIs())
        self.assertEqual(out['vacios'], 1)
        self.assertEqual(self.pack_state(), 'tanda')


class FromTandaTest(ArbitrateBase):
    arb_on_setup = False

    def setUp(self):
        super().setUp()
        # El pack del piloto: en tanda, una unidad ya cerrada por el acuerdo triple histórico.
        conn = self.conn()
        with conn:
            conn.execute("UPDATE esc_packs SET state='tanda', route='dueno' WHERE pack_id=?", (PACK,))
            conn.execute("UPDATE units SET state='needs-human', resolution='tanda-2026-09-22-pendiente'")
            conn.execute("UPDATE units SET resolution='lado-sin-lectura-valida:astra-esc:invalid-decision' "
                         'WHERE unit_id=?', (U_EDGE,))
            conn.execute("UPDATE units SET state='verified', resolution='triple-cerrada' WHERE unit_id=?", (U_ID,))

    def test_tanda_packs_go_to_the_arbiter_and_closed_units_are_never_touched(self):
        self.assertEqual(X.closed_units(self.conn(), list(ALL)), {U_ID})
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM), arb(U_ROLE), arb(U_EDGE))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_EDGE: 'valida'})])
        out = self.arbitrate(clis, from_tanda=True)
        self.assertEqual(out['desde_tanda'], 1)
        stdin = clis.calls['claude'][0]['stdin']
        self.assertNotIn(f'### {U_ID}', stdin)
        self.assertIn('lado-sin-lectura-valida:astra-esc:invalid-decision', stdin)
        self.assertEqual(self.state(U_ID), ('verified', 'triple-cerrada'))
        self.assertEqual(self.state(U_SUM), ('verified', f'arbitraje-{self.date}'))
        self.assertEqual(self.pack_state(), 'closed')
        events = [r['event'] for r in self.conn().execute('SELECT event FROM events WHERE unit_id=?', (U_ID,))]
        self.assertEqual(events, [])

    def test_dry_run_from_tanda_does_not_move_anything(self):
        before = self.ledger()
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM), arb(U_ROLE), arb(U_EDGE))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida', U_EDGE: 'valida'})])
        out = self.arbitrate(clis, from_tanda=True, dry_run=True)
        self.assertEqual((out['desde_tanda'], out['arbitraje']), (0, 3))
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.pack_state(), 'tanda')

    def test_the_real_pass_after_a_dry_run_reuses_its_readings_and_merges(self):
        clis = FakeCLIs(arbiter=[arbitration(arb(U_SUM), arb(U_ROLE), arb(U_EDGE, al_dueno=True))],
                        validator=[verdicts(**{U_SUM: 'valida', U_ROLE: 'valida'})])
        self.arbitrate(clis, from_tanda=True, dry_run=True)
        out = self.arbitrate(clis, from_tanda=True)
        # El pack no se salta por tener ya lectura del árbitro, y esa lectura no se vuelve a pagar.
        self.assertEqual((out['desde_tanda'], out['arbitraje'], out['dueno']), (1, 2, 1))
        self.assertEqual((len(clis.calls['claude']), len(clis.calls['astra'])), (1, 1))
        self.assertEqual(self.state(U_SUM), ('verified', f'arbitraje-{self.date}'))
        # Lo que quedó para el dueño no vuelve al árbitro en la pasada siguiente.
        self.assertEqual(self.pack_state(), 'tanda')
        again = self.arbitrate(clis, from_tanda=True)
        self.assertEqual((again['desde_tanda'], again.get('arbitrados', 0)), (0, 0))


if __name__ == '__main__':
    unittest.main()


class ReopenTest(ArbitrateBase):
    def prudent_close(self):
        clis = FakeCLIs(arbiter=[arbitration(*(arb(u) for u in ALL))],
                        validator=[verdicts(**{U_SUM: 'no_valida', U_ROLE: 'valida', U_ID: 'valida',
                                               U_EDGE: 'valida'})])
        self.arbitrate(clis)
        return self.root / 'esc/decisions' / f'{PACK}.arbitraje-{self.date}-prudente.json'

    def test_reopen_moves_an_arbiter_closure_back_to_the_owner(self):
        prudent = self.prudent_close()
        self.assertTrue(prudent.exists())
        conn = self.conn()
        done = A.reopen_to_owner(self.root, conn, [U_SUM], 'absolución retirada sin el dueño', self.date)
        self.assertEqual(done, [U_SUM])
        # Era la única unidad de esa decisión: el fichero desaparece y el overlay ya no la retira.
        self.assertFalse(prudent.exists())
        self.assertEqual(self.state(U_SUM), ('needs-human', f'tanda-{self.date}-pendiente'))
        self.assertEqual(self.state(U_ROLE), ('verified', f'arbitraje-{self.date}'))
        self.assertEqual(self.pack_state(), 'tanda')
        event = conn.execute("SELECT payload FROM events WHERE unit_id=? AND event='esc-reabierta'",
                             (U_SUM,)).fetchone()
        self.assertIn('absolución retirada', event['payload'])

    def test_reopen_trims_a_shared_decision_and_keeps_the_rest(self):
        self.prudent_close()
        closed = self.root / 'esc/decisions' / f'{PACK}.arbitraje-{self.date}.json'
        before = json.loads(closed.read_text())
        self.assertIn(U_EDGE, before['_meta']['unit_ids'])
        A.reopen_to_owner(self.root, self.conn(), [U_EDGE], 'revisión', self.date)
        after = json.loads(closed.read_text())
        self.assertNotIn(U_EDGE, after['_meta']['unit_ids'])
        self.assertNotIn(U_EDGE, {u['unit_id'] for u in after['units'] + after['edges']})
        self.assertEqual(set(after['_meta']['unit_ids']), set(before['_meta']['unit_ids']) - {U_EDGE})

    def test_reopen_refuses_what_the_arbiter_did_not_close(self):
        self.prudent_close()
        conn = self.conn()
        conn.execute("UPDATE units SET resolution='humano-2026-09-17e' WHERE unit_id=?", (U_ROLE,))
        conn.commit()
        with self.assertRaises(ValueError):
            A.reopen_to_owner(self.root, conn, [U_ROLE], 'no', self.date)
        self.assertEqual(self.state(U_ROLE), ('verified', 'humano-2026-09-17e'))
