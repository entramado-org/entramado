"""Spec 063 — decision schema and validator (no models, no graph)."""
import copy
import unittest

from src import qa_esc_schema as S

U_SUM, U_ROLE, U_ID, U_EDGE, U_ED = ('a' * 64, 'b' * 64, 'c' * 64, 'd' * 64, 'e' * 64)
# La forma real de una unidad de identidad del censo (`qa_dual`): el bloque entero, no un alias suelto.
IDENTITY = {'type': 'Person', 'identity': {'name': 'Fuldo Fíctez', 'entity_id': 'Person:fuldo-fictez',
                                           'aliases': ['Fuldo', 'Fuldo el de Ficcionbar']}}


def pack(**over):
    p = {'pack_id': 'E-000000000001', 'kind': 'entity', 'index': 1, 'total': 1,
         'entity': {'id': 'Person:fuldo-fictez', 'name': 'Fuldo Fíctez', 'is_pseudonym': False},
         'units': [{'unit_id': U_SUM, 'kind': 'summary'}, {'unit_id': U_ROLE, 'kind': 'attribute'},
                   {'unit_id': U_ID, 'kind': 'identity', 'value': IDENTITY},
                   {'unit_id': U_EDGE, 'kind': 'edge', 'edge': {'type': 'ACCUSED_OF', 'source_kind': 'judicial'}},
                   {'unit_id': U_ED, 'kind': 'editorial'}]}
    p.update(over)
    return p


def unit(uid, verdict='verificado', label='ok', **over):
    u = {'unit_id': uid, 'verdict': verdict, 'label': label, 'evidence': 'auto p.3', 'confidence': 'alta',
         'requested_sources': []}
    u.update(over)
    return u


def decision(**over):
    d = {'entity_id': 'Person:fuldo-fictez',
         'units': [unit(U_SUM), unit(U_ROLE), unit(U_ID), unit(U_EDGE), unit(U_ED)],
         'summary': {'action': 'none', 'resumen': '', 'sources': [], 'reason': ''},
         'role': {'action': 'none', 'role': '', 'reason': ''}, 'alias_deny': [],
         'estado': {'action': 'none', 'status': '', 'fecha': '', 'doc': '', 'url': '', 'tipo': '', 'nota': ''},
         'edges': [], 'editorial': [], 'notes': ''}
    d.update(over)
    return d


def set_unit(d, uid, **over):
    d = copy.deepcopy(d)
    for u in d['units']:
        if u['unit_id'] == uid:
            u.update(over)
    return d


class SchemaTest(unittest.TestCase):
    def test_schema_is_flat_and_closed(self):
        sc = S.schema_for(pack())
        self.assertTrue(S.schema_is_flat(sc))
        self.assertEqual(sc['properties']['entity_id']['enum'], ['Person:fuldo-fictez'])
        self.assertEqual(set(sc['properties']['units']['items']['properties']['unit_id']['enum']),
                         {U_SUM, U_ROLE, U_ID, U_EDGE, U_ED})
        self.assertEqual(sc['properties']['edges']['items']['properties']['unit_id']['enum'], [U_EDGE])
        self.assertIn('', sc['properties']['estado']['properties']['status']['enum'])

    def test_schema_without_edges_keeps_string_unit_id(self):
        p = pack(units=[{'unit_id': U_SUM, 'kind': 'summary'}])
        sc = S.schema_for(p)
        self.assertEqual(sc['properties']['edges']['items']['properties']['unit_id'], {'type': 'string'})


class ValidatorTest(unittest.TestCase):
    def assertRejects(self, d, fragment, p=None):
        errors = S.validate_decision(d, p or pack())
        self.assertTrue(any(fragment in e for e in errors), f'expected {fragment!r} in {errors}')

    def test_clean_decision_is_accepted(self):
        self.assertEqual(S.validate_decision(decision(), pack()), [])

    def test_coverage_exact(self):
        d = decision(); d['units'] = d['units'][:-1]
        self.assertRejects(d, 'sin decisión')
        d = decision(); d['units'].append(unit(U_SUM))
        self.assertRejects(d, 'repetida')
        d = decision(); d['units'].append(unit('f' * 64))
        self.assertRejects(d, 'no pertenece al pack')

    def test_corregido_requires_change_and_vice_versa(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='reescrita')
        self.assertRejects(d, 'sin acción sobre summary')
        d = decision(summary={'action': 'replace', 'resumen': 'Texto nuevo (auto p.2).', 'sources': [], 'reason': 'x'})
        self.assertRejects(d, 'ninguna unidad summary es corregido')
        d = set_unit(decision(), U_SUM, verdict='corregido', label='ok')
        self.assertRejects(d, 'exige una etiqueta de cambio')
        d = set_unit(decision(), U_SUM, verdict='verificado', label='cita')
        self.assertRejects(d, 'verificado exige label ok')

    def test_incierto_requires_requested_sources(self):
        d = set_unit(decision(), U_SUM, verdict='incierto', label='ok')
        self.assertRejects(d, 'exige requested_sources')
        d = set_unit(decision(), U_SUM, verdict='incierto', label='ok', requested_sources=['UDEF 1907 p.98'])
        self.assertEqual(S.validate_decision(d, pack()), [])

    def test_estado_vocabulary(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='estado')
        d['estado'] = {'action': 'set', 'status': 'acusado', 'fecha': '2026-06-20', 'doc': 'auto.pdf', 'url': '',
                       'tipo': 'auto', 'nota': ''}
        self.assertRejects(d, 'exige un acto acusatorio')
        d['estado']['tipo'] = 'auto_apertura_juicio_oral'
        self.assertEqual(S.validate_decision(d, pack()), [])
        d['estado'].update(status='acusado', tipo='prensa')
        self.assertRejects(d, 'la prensa solo sostiene')
        d['estado'].update(status='imputado', tipo='prensa', fecha='20-06-2026')
        self.assertRejects(d, 'YYYY-MM-DD')
        d['estado'].update(fecha='2026-06-20', doc='', url='')
        self.assertRejects(d, 'exige doc o url')
        d2 = decision(); d2['estado'] = dict(d['estado'], doc='auto.pdf')
        self.assertRejects(d2, 'sin unidad con label estado')

    def test_vague_press_attribution(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='atrib')
        d['summary'] = {'action': 'replace', 'resumen': 'Según la prensa, cobró.', 'sources': [], 'reason': 'x'}
        self.assertRejects(d, 'según la prensa')
        d = set_unit(decision(), U_ED, verdict='corregido', label='reescrita')
        d['editorial'] = [{'unit_id': U_ED, 'action': 'replace', 'text': 'según los medios, x', 'reason': 'y'}]
        self.assertRejects(d, 'según la prensa')

    def test_pseudonym_only_remove(self):
        p = pack(); p['entity']['is_pseudonym'] = True
        d = set_unit(decision(), U_SUM, verdict='corregido', label='reescrita')
        d['summary'] = {'action': 'replace', 'resumen': 'x (auto p.1)', 'sources': [], 'reason': 'x'}
        self.assertRejects(d, 'nunca se reescribe', p)
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada')
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'seudónimo'}
        self.assertEqual(S.validate_decision(d, p), [])

    def test_alias_rules(self):
        d = set_unit(decision(), U_ID, verdict='corregido', label='alias')
        self.assertRejects(d, 'label alias sin alias_deny')
        d['alias_deny'] = ['Fuldo', 'Fuldo Fíctez']
        # Spec 071, AC21: el mensaje dice la posición, nunca el alias (los errores acaban en informes y logs).
        self.assertRejects(d, 'alias_deny[2] es el nombre civil de la entidad')
        self.assertFalse(any('Fuldo' in e for e in S.validate_decision(d, pack())))
        d['alias_deny'] = ['Fuldo']
        self.assertEqual(S.validate_decision(d, pack()), [])
        d = set_unit(decision(), U_SUM, verdict='corregido', label='alias')
        self.assertRejects(d, 'solo cabe en unidades')

    def test_edge_actions(self):
        base = set_unit(decision(), U_EDGE, verdict='corregido', label='arista')
        self.assertRejects(base, 'sin acción ≠ keep')
        d = copy.deepcopy(base)
        d['edges'] = [{'unit_id': U_EDGE, 'action': 'retype', 'new_type': 'MENTIONED_IN', 'prop': '', 'value': '',
                       'status': '', 'evidence': 'p.3', 'reason': 'x'}]
        self.assertRejects(d, 'new_type fuera del vocabulario')
        d['edges'][0]['new_type'] = 'ACCUSED_OF'
        self.assertRejects(d, 'retype al mismo tipo')
        d['edges'][0]['new_type'] = 'INVESTIGATED_BY'
        self.assertEqual(S.validate_decision(d, pack()), [])
        d['edges'][0].update(action='set_prop', prop='status', value='acusado')
        self.assertRejects(d, 'fuera de la lista cerrada')
        d['edges'][0].update(action='set_prop', prop='source_kind', value='contexto')
        self.assertEqual(S.validate_decision(d, pack()), [])
        p = pack(); p['units'][3]['edge']['source_kind'] = 'prensa'
        d['edges'][0].update(value='judicial')
        self.assertRejects(d, 'solo puede bajar', p)
        d['edges'][0].update(action='set_status', prop='', value='', status='culpable')
        self.assertRejects(d, 'status fuera de vocabulario')
        d['edges'][0].update(status='imputado')
        self.assertEqual(S.validate_decision(d, pack()), [])
        p = pack(); p['units'][3]['edge']['type'] = 'PAID'
        self.assertRejects(d, 'set_status solo en', p)
        d = decision(); d['edges'] = [{'unit_id': U_EDGE, 'action': 'deny', 'new_type': '', 'prop': '', 'value': '',
                                       'status': '', 'evidence': 'p.3', 'reason': 'x'}]
        self.assertRejects(d, 'exige unidad corregida con label arista')

    def test_a_load_the_loader_would_discard_is_rejected_without_its_value(self):
        # Spec 071 AC78: la carga de la operación es la que escribe el loader (`alcance.carga`); una que descarta no es
        # «sin set». El motivo nombra la unidad, el prop y por qué, nunca el valor.
        centinela = 'VALOR_CENTINELA_SINTETICO'
        base = set_unit(decision(), U_EDGE, verdict='corregido', label='arista')
        for op, motivo in (({'action': 'set_prop', 'prop': 'source_page', 'value': centinela},
                            'source_page: no es un entero'),
                           ({'action': 'set_prop', 'prop': 'source_page', 'value': '0'},
                            'source_page: tiene que ser un entero ≥ 1'),
                           ({'action': 'retype', 'new_type': 'INVESTIGATED_BY', 'set': {'source_kind': 'judicial',
                                                                                         'note': centinela}},
                            'source_kind: solo puede degradar la capa de una arista judicial'),
                           # AC82: una clave con forma de identificador fuera del vocabulario cerrado, por su posición.
                           ({'action': 'set_prop', 'prop': centinela, 'value': 'x'},
                            'prop desconocido (posición 1): fuera de la lista cerrada del loader'),
                           ({'action': 'retype', 'new_type': 'INVESTIGATED_BY', 'set': {'note': 'x', centinela: 'y'}},
                            'prop desconocido (posición 2): fuera de la lista cerrada del loader')):
            with self.subTest(motivo=motivo):
                d = copy.deepcopy(base)
                d['edges'] = [{'unit_id': U_EDGE, 'new_type': '', 'prop': '', 'value': '', 'status': '',
                               'evidence': 'p.3', 'reason': 'x', **op}]
                errors = S.validate_decision(d, pack())
                self.assertIn(f'edges {U_EDGE[:8]}: operación con carga inválida ({motivo})', errors)
                self.assertNotIn(centinela, '\n'.join(errors))
        # Y la lista cerrada del propio esquema nombra igual: las del vocabulario, por su nombre; las demás, por su
        # posición.
        for prop, nombre in ((centinela, 'prop desconocido (posición 1)'), ('source_quote', "prop 'source_quote'")):
            with self.subTest(nombre=nombre):
                d = copy.deepcopy(base)
                d['edges'] = [{'unit_id': U_EDGE, 'action': 'set_prop', 'new_type': '', 'prop': prop, 'value': 'x',
                               'status': '', 'evidence': 'p.3', 'reason': 'x'}]
                self.assertIn(f'edges {U_EDGE[:8]}: {nombre} fuera de la lista cerrada {S.SET_PROPS}',
                              S.validate_decision(d, pack()))

    def test_no_value_out_of_the_vocabulary_reaches_an_error(self):
        # Spec 071 AC87: los errores del esquema acaban en informes y logs, y un valor mal puesto puede ser un nombre.
        # Un valor fuera del vocabulario se señala por su campo y, en una lista, por su posición; solo un valor del
        # vocabulario cerrado se nombra.
        c = 'PERSONA_CENTINELA_SINTETICA'
        arista = set_unit(decision(), U_EDGE, verdict='corregido', label='arista')

        def op(**campos):
            return {'unit_id': U_EDGE, 'action': 'retype', 'new_type': '', 'prop': '', 'value': '', 'status': '',
                    'evidence': 'p.3', 'reason': 'x', **campos}

        estado = {'action': 'set', 'status': c, 'fecha': '2026-06-20', 'doc': 'auto.pdf', 'url': '', 'tipo': c,
                  'nota': 'nota'}
        casos = (('entity_id', decision(entity_id=f'Person:{c}'), 'entity_id distinto del de la entidad del pack'),
                 ('unidad ajena', decision(units=decision()['units'] + [unit(c)]), 'units[6]: la unidad no pertenece'),
                 ('unidad repetida', decision(units=decision()['units'] + [unit(c), unit(c)]),
                  'units[7]: unidad repetida'),
                 ('verdict y label', set_unit(decision(), U_SUM, verdict=c, label=c),
                  f'unidad {U_SUM[:8]}: verdict, label fuera de vocabulario'),
                 ('summary.action', decision(summary={'action': c}), 'summary.action: valor fuera del vocabulario'),
                 ('role.action', decision(role={'action': c}), 'role.action: valor fuera del vocabulario'),
                 ('estado.action', decision(estado={'action': c}), 'estado.action: valor fuera del vocabulario'),
                 ('estado.status', decision(estado=estado), 'estado.status fuera de vocabulario'),
                 ('estado.tipo', decision(estado=estado), 'estado.tipo fuera de vocabulario'),
                 ('la prensa', decision(estado=dict(estado, tipo='prensa')), 'la prensa solo sostiene'),
                 ('arista ajena', dict(arista, edges=[op(unit_id=c)]), 'edges[1]: no es una arista del pack'),
                 ('action', dict(arista, edges=[op(action=c)]), f'edges {U_EDGE[:8]}: action: valor fuera del'),
                 ('new_type', dict(arista, edges=[op(new_type=c)]),
                  f'edges {U_EDGE[:8]}: new_type fuera del vocabulario'),
                 ('source_kind', dict(arista, edges=[op(action='set_prop', prop='source_kind', value=c)]),
                  f'edges {U_EDGE[:8]}: source_kind desconocido'),
                 ('editorial ajeno', decision(editorial=[{'unit_id': c, 'action': 'keep', 'text': '', 'reason': ''}]),
                  'editorial[1]: no es un ítem editorial del pack'),
                 ('editorial action', decision(editorial=[{'unit_id': U_ED, 'action': c, 'text': '', 'reason': ''}]),
                  f'editorial {U_ED[:8]}: action: valor fuera del vocabulario'))
        for caso, d, neutro in casos:
            with self.subTest(caso=caso):
                errors = S.validate_decision(copy.deepcopy(d), pack())
                self.assertIn(neutro, '\n'.join(errors))
                self.assertNotIn(c, '\n'.join(errors))
        fila = {'unit_id': U_SUM, 'choice_id': 'retirar', 'text': '', 'sostenido': False, 'evidence': 'x', 'citas': [],
                'al_dueno': False, 'motivo': '', 'aliases': []}
        arbitraje = S.validate_arbitration({'units': [dict(fila, unit_id=c), dict(fila, unit_id=c),
                                                      dict(fila, choice_id=c)]}, pack(), {})
        validacion = S.validate_validation({'units': [{'unit_id': c, 'veredicto': 'valida'},
                                                      {'unit_id': c, 'veredicto': 'valida'}]}, [U_SUM])
        for neutro in ('units[1]: la unidad no pertenece al pack', 'units[2]: unidad repetida',
                       f'unidad {U_SUM[:8]}: choice_id: valor fuera del vocabulario'):
            self.assertIn(neutro, '\n'.join(arbitraje))
        for neutro in ('units[1]: la unidad no pertenece a la validación', 'units[2]: unidad repetida'):
            self.assertIn(neutro, '\n'.join(validacion))
        self.assertNotIn(c, '\n'.join(arbitraje + validacion))

    def test_an_identity_is_never_withdrawn_without_alias_deny(self):
        # 071 (Codex): una identidad civil corregida sin alias_deny no escribía ninguna regla y el registro
        # decía «alias retirado» en falso.
        d = set_unit(decision(), U_ID, verdict='corregido', label='retirada')
        self.assertRejects(d, 'las identidades se retiran con label alias + alias_deny')
        d = set_unit(decision(), U_ID, verdict='corregido', label='reescrita')
        self.assertRejects(d, 'las identidades se retiran con label alias + alias_deny')
        d['alias_deny'] = ['  ']
        self.assertRejects(d, 'las identidades se retiran con label alias + alias_deny')
        d = set_unit(decision(), U_ID, verdict='corregido', label='alias')
        d['alias_deny'] = ['Fuldo el de Ficcionbar']
        self.assertEqual(S.validate_decision(d, pack()), [])

    def test_an_edge_is_never_withdrawn_by_its_label(self):
        # 071: `retirada` sin acción deny no borra nada del grafo, pero el registro la publicaba como suprimida.
        d = set_unit(decision(), U_EDGE, verdict='corregido', label='retirada')
        self.assertRejects(d, 'las aristas se retiran con label arista + action deny')
        d['edges'] = [{'unit_id': U_EDGE, 'action': 'deny', 'new_type': '', 'prop': '', 'value': '',
                       'status': '', 'evidence': 'p.3', 'reason': 'x'}]
        self.assertRejects(d, 'las aristas se retiran con label arista + action deny')
        self.assertEqual(S.validate_decision(set_unit(d, U_EDGE, label='arista'), pack()), [])

    def test_subpack_cannot_touch_entity_actions(self):
        p = pack(index=2, total=3)
        d = set_unit(decision(), U_SUM, verdict='corregido', label='reescrita')
        d['summary'] = {'action': 'replace', 'resumen': 'x (auto p.1)', 'sources': [], 'reason': 'x'}
        self.assertRejects(d, 'sub-pack que no es el primero', p)

    def test_changes_summary(self):
        d = set_unit(decision(), U_SUM, verdict='corregido', label='retirada')
        d['summary'] = {'action': 'replace', 'resumen': 'x (auto p.1)', 'sources': [], 'reason': 'x'}
        c = S.changes(d)
        self.assertTrue(c['destructive'])
        self.assertIn('retirada', c['labels'])
        self.assertFalse(S.changes(decision())['destructive'])


if __name__ == '__main__':
    unittest.main()


class UnanchoredTest(unittest.TestCase):
    """Piloto 2026-09-21: un sub-pack solo de aristas puede proponer acciones de entidad; se marcan «sin ancla»."""

    def test_entity_actions_without_units_of_that_kind_are_allowed_and_flagged(self):
        p = pack(units=[{'unit_id': U_EDGE, 'kind': 'edge', 'edge': {'type': 'ACCUSED_OF', 'source_kind': 'judicial'}}])
        p['entity']['is_pseudonym'] = True
        d = decision(units=[unit(U_EDGE, verdict='corregido', label='arista')])
        d['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'seudónimo: sin prosa'}
        d['role'] = {'action': 'remove', 'role': '', 'reason': 'seudónimo: sin rol'}
        d['alias_deny'] = ['D. Fulgencio']
        d['estado'] = {'action': 'set', 'status': 'investigado', 'fecha': '2025-09-18', 'doc': 'ATS.pdf', 'url': '',
                       'tipo': 'auto', 'nota': 'auto del instructor'}
        d['edges'] = [{'unit_id': U_EDGE, 'action': 'set_status', 'new_type': '', 'prop': '', 'value': '',
                       'status': 'investigado', 'evidence': 'ATS p.3', 'reason': 'estado del auto'}]
        self.assertEqual(S.validate_decision(d, p), [])
        self.assertTrue(S.changes(d, p)['unanchored'])
        d['estado']['status'] = 'imputado'
        self.assertTrue(any('solo puede ser investigado' in e for e in S.validate_decision(d, p)))

    def test_anchored_when_the_pack_carries_the_kind(self):
        d = decision(summary={'action': 'remove', 'resumen': '', 'sources': [], 'reason': 'x'})
        self.assertTrue(any('ninguna unidad summary es corregido' in e for e in S.validate_decision(d, pack())))
        self.assertFalse(S.changes(decision(), pack())['unanchored'])


class NormalizeTest(unittest.TestCase):
    """Piloto 2026-09-21: Astra etiquetaba `arista` las aristas que mantenía con `verificado`."""

    def test_verificado_arista_on_kept_edge_becomes_ok(self):
        d = set_unit(decision(), U_EDGE, verdict='verificado', label='arista')
        self.assertEqual(S.validate_decision(d, pack()), [])
        self.assertEqual(next(u['label'] for u in d['units'] if u['unit_id'] == U_EDGE), 'ok')

    def test_verificado_arista_with_a_real_edge_action_is_still_rejected(self):
        d = set_unit(decision(), U_EDGE, verdict='verificado', label='arista')
        d['edges'] = [{'unit_id': U_EDGE, 'action': 'deny', 'new_type': '', 'prop': '', 'value': '',
                       'status': '', 'evidence': 'p.3', 'reason': 'x'}]
        errors = S.validate_decision(d, pack())
        self.assertTrue(errors)
        self.assertEqual(next(u['label'] for u in d['units'] if u['unit_id'] == U_EDGE), 'arista')
