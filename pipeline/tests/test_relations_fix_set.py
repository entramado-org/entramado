"""`fix: {set: …}` del overlay de relaciones (spec 061, T1.3).

Sin grafo: se valida la whitelist de props, la regla de degradación de
`source_kind` y el orden retype → set → swap con un driver falso.
"""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from pipeline.src import relations_loader
from pipeline.src.relations_loader import _RETYPE_Q, _SET_Q, _SWAP_Q, _prepare_deny, _prepare_fix, apply_deny, apply_fix
from pipeline.src.seudonimos import ErrorDeForma


class FakeResult:
    def __init__(self, n): self._n = n

    def single(self): return {"n": self._n}


class FakeSession:
    def __init__(self, counts): self.counts = list(counts); self.calls = []

    def run(self, query, **params):
        self.calls.append((str(query), params))
        return FakeResult(self.counts.pop(0) if self.counts else 0)

    def __enter__(self): return self

    def __exit__(self, *exc): return False


class FakeDriver:
    def __init__(self, session): self._session = session

    def session(self): return self._session


def _row(**extra):
    row = {"source": "Fulana de Tal", "source_type": "Person",
           "target": "Empresa Ficticia SL", "target_type": "Company",
           "type": "PAID", "reason": "qa061-1"}
    row.update(extra)
    return row


# Spec 070 (AC143/AC144): los motivos con que para el loader una fila que antes se saltaba.
FORMA = "corrección con un `set` que no es un mapa con propiedades"
PROPS = "corrección con un `set` con una propiedad fuera de su lista"
CAPA = "corrección con un `set` cuya capa no degrada una arista judicial"
PAGINA = "corrección con un `set` cuya página no es un entero ≥ 1"
KINDS = "corrección con un `set` cuya capa no baja de la de sus `kinds`"
SWAP = "corrección con un `set` de capa y un swap (van en dos reglas)"
TIPO = "corrección con un `set` cuya propiedad no es del tipo admitido"
NO_BOOLEANO = "una corrección cuyo `swap_direction` no es un booleano"


class PrepareSetTest(unittest.TestCase):
    def para(self, fila, motivo):
        """Spec 070 (AC143/AC144): la fila, tras una válida, para el loader con su posición y su motivo (exit 2), sin
        valores; antes, un aviso y se descartaba (o se cargaba sin su `set`)."""
        with self.assertRaises(ErrorDeForma) as fin:
            _prepare_fix([_row(set={"amount": 1}), fila])
        self.assertEqual(fin.exception.code, 2)
        self.assertEqual(fin.exception.motivos, [f"fix[1]: {motivo}"])

    def sin_efecto(self, fila):
        self.para(fila, "regla fix sin efecto")

    def test_fila_solo_con_set_es_valida(self):
        rows = _prepare_fix([_row(set={"amount": 1200, "currency": "EUR"})])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["set"], {"amount": 1200, "currency": "EUR"})
        self.assertIsNone(rows[0]["corrected_type"])
        self.assertFalse(rows[0]["swap"])

    def test_una_regla_sin_efecto_para_el_loader(self):
        """Spec 070 (AC143): una fila que no puede hacer nada (sin retype a otro tipo, sin swap y sin un `set` que el
        loader aplique) para el loader con su posición: antes, un aviso y se descartaba. Con swap verdadero o con un
        `set` aplicable, vale aunque el tipo no cambie."""
        for fila in (_row(), _row(corrected_type="PAID"), _row(swap_direction=False),
                     _row(corrected_type="PAID", swap_direction=False), _row(set=None)):
            with self.subTest(claves=sorted(fila)):
                self.sin_efecto(fila)
        validas = (_row(swap_direction=True), _row(corrected_type="PAID", set={"note": "x"}),
                   _row(corrected_type="PAID", swap_direction=True))
        for fila in validas:
            with self.subTest(vale=sorted(fila)):
                self.assertEqual(len(_prepare_fix([fila])), 1)

    def test_swap_direction_solo_un_booleano(self):
        """Spec 070 (AC155): `swap_direction` es un booleano YAML. Una cadena («True», «False», «true», «yes»), un
        número o un contenedor paran el loader con su posición y sin el valor (antes, «yes» y 2 armaban el swap y
        «False» no); `true`, `false` y la clave ausente, no."""
        for valor in ("True", "False", "true", "yes", "1", 2, 1, 0, 1.0, [], {}, [True]):
            with self.subTest(valor=valor):
                self.para(_row(corrected_type="RECEIVED", swap_direction=valor), NO_BOOLEANO)
        for valor, swap in ((True, True), (False, False), (None, False)):
            with self.subTest(vale=valor):
                self.assertEqual(_prepare_fix([_row(corrected_type="RECEIVED", swap_direction=valor)])[0]["swap"],
                                 swap)

    def test_cada_propiedad_del_set_de_su_tipo(self):
        """Spec 070 (AC154): cada propiedad del `set` tiene su tipo: texto (`note`, `currency`), número no booleano
        (`amount`), una fecha que el loader normaliza (`date`), estado y capa de la ontología y página entera ≥ 1; un
        nulo borra la propiedad (salvo la capa, el estado y la página, que no se borran así: enmienda 89). Un mapa, una lista, un booleano o
        un número donde va texto paran el loader con su posición antes del driver (un SET de un mapa tras el retype
        dejaba la carga a medias); los de su tipo cargan."""
        malos = [("note", {}), ("note", []), ("note", 3), ("note", True), ("currency", ["EUR"]), ("amount", "1200"),
                 ("amount", True), ("amount", {}), ("date", "sin fecha"), ("date", ""), ("date", "3 de marzo"),
                 ("date", 20210304), ("date", {"y": 2021})]
        for prop, valor in malos:
            with self.subTest(prop=prop, valor=valor):
                self.para(_row(set={prop: valor}), TIPO)
        for prop, valor in (("source_kind", None), ("status", None), ("source_page", None)):
            with self.subTest(nulo=prop):
                self.para(_row(set={prop: valor}), CAPA if prop == "source_kind" else PAGINA if prop == "source_page"
                          else "corrección con un `set` cuyo estado no es de la ontología")
        buenos = [("note", "texto"), ("note", None), ("currency", "EUR"), ("amount", 1200), ("amount", 1200.5),
                  ("amount", None), ("date", "2021-03-04"), ("date", "2021"), ("date", "marzo de 2021"),
                  ("date", None), ("source_page", 3)]
        for prop, valor in buenos:
            with self.subTest(vale=prop, valor=valor):
                self.assertEqual(len(_prepare_fix([_row(set={prop: valor})])), 1)

    def test_prop_fuera_de_la_whitelist(self):
        # source_quote/source_doc son la EVIDENCIA: corregir un dato no la reescribe. Con un retype, la fila tampoco se
        # carga sin su `set` (AC144): para.
        for conjunto in ({"source_quote": "otra cita"}, {"amount": 1, "source_doc": "x.pdf"}):
            for extra in ({}, {"corrected_type": "RECEIVED"}):
                with self.subTest(conjunto=sorted(conjunto), **extra):
                    self.para(_row(set=conjunto, **extra), PROPS)

    def test_source_page_corrige_el_localizador(self):
        # 063: la cita de la condena de Koldo está en la p.220; la arista apuntaba a la 219 (fallo de Ábalos).
        rows = _prepare_fix([_row(set={"source_page": 220})])
        self.assertEqual(rows[0]["set"], {"source_page": 220})
        for bad in (0, -3, "220", 2.5, True):
            with self.subTest(bad=bad):
                self.para(_row(set={"source_page": bad}), PAGINA)

    def test_set_vacio_o_mal_formado(self):
        self.para(_row(set={}), FORMA)
        self.para(_row(set="amount=1"), FORMA)

    def test_source_kind_solo_a_la_baja(self):
        for kind in ("contrato", "acusacion", "prensa", "ultima_hora", "contexto"):
            rows = _prepare_fix([_row(set={"source_kind": kind})])
            self.assertEqual(len(rows), 1, kind)
        for kind in ("judicial", "boe", "mercantil", "inventado", ["prensa"]):
            with self.subTest(kind=kind):
                self.para(_row(set={"source_kind": kind}), CAPA)

    def test_set_de_capa_y_swap_no_conviven(self):
        self.para(_row(set={"source_kind": "prensa"}, swap_direction=True), SWAP)
        # …pero otras props sí sobreviven al swap (invert conserva propiedades).
        self.assertEqual(
            len(_prepare_fix([_row(set={"amount": 3}, swap_direction=True)])), 1)

    def test_set_sobrevive_a_un_corrected_type_redundante(self):
        rows = _prepare_fix([_row(corrected_type="PAID", set={"date": "2021-03-04"})])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["corrected_type"])

    def test_el_envoltorio_avisa_igual_y_descarta_en_los_mismos_casos(self):
        # Spec 071 enmienda 16: la validación vive en `alcance.preparar_set` (la misma con que las comprobaciones
        # leen la carga de una decisión) y `_prepare_set` la envuelve. El loader no cambia: el mismo aviso, con los
        # mismos campos que antes, y None; un bloque válido pasa sin avisar.
        from pipeline.src import relations_loader as RL

        class Registro:
            def __init__(self):
                self.avisos = []

            def warning(self, evento, **campos):
                self.avisos.append((evento, campos))

        permitidos = sorted({"amount", "currency", "date", "status", "note", "source_kind", "source_page"})
        degradar = "source_kind solo puede DEGRADAR la capa de una arista judicial"
        casos = (({}, ("fix_skip_set_shape", {"where": "w"})),
                 ("amount=1", ("fix_skip_set_shape", {"where": "w"})),
                 ({"note": "x", "source_quote": "otra cita"},
                  ("fix_skip_set_props", {"where": "w", "unknown": ["source_quote"], "allowed": permitidos})),
                 ({"source_kind": "judicial"},
                  ("fix_skip_set_kind", {"where": "w", "source_kind": "judicial", "note": degradar})),
                 ({"source_kind": "inventada"},
                  ("fix_skip_set_kind", {"where": "w", "source_kind": "inventada", "note": degradar})),
                 ({"status": "sospechoso"},
                  ("fix_skip_set_status", {"where": "w", "status": "sospechoso",
                                           "motivo": "estado fuera de vocabulario: 'sospechoso'"})),
                 ({"source_page": 0, "note": "x"},
                  ("fix_skip_set_page", {"where": "w", "source_page": 0,
                                         "note": "source_page debe ser un entero ≥ 1"})))
        registro, previo = Registro(), RL.log
        RL.log = registro
        try:
            # Fusión 070 ⟷ 071 (D2, 9-nov): en los mismos casos el loader PARA (spec 070 AC143/AC144) en vez de avisar y
            # descartar: exit 2, la posición y ningún valor, ni en el motivo ni en un aviso.
            for props, aviso in casos:
                with self.subTest(aviso=aviso[0]):
                    registro.avisos.clear()
                    with self.assertRaises(ErrorDeForma) as fin:
                        RL._prepare_set(props, "w")
                    self.assertEqual(fin.exception.code, 2)
                    self.assertTrue(all(m.startswith("w: ") for m in fin.exception.motivos), fin.exception.motivos)
                    self.assertEqual(registro.avisos, [])
            registro.avisos.clear()
            self.assertIsNone(RL._prepare_set(None, "w"))
            self.assertEqual(RL._prepare_set({"date": "2021-03-04", "status": "Acusado"}, "w"),
                             {"date": "2021-03-04", "status": "acusado", "date_norm": "2021-03-04", "date_year": 2021})
            self.assertEqual(registro.avisos, [])
        finally:
            RL.log = previo

    def test_retype_y_swap_siguen_funcionando_sin_set(self):
        rows = _prepare_fix([_row(corrected_type="RECEIVED", swap_direction=False)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["corrected_type"], "RECEIVED")
        self.assertFalse(rows[0]["swap"])
        self.assertIsNone(rows[0]["set"])


class MainSinEfectoTest(unittest.TestCase):
    def test_una_regla_sin_efecto_para_el_loader_con_exit_2(self):
        """Spec 070 (AC143): una regla fix sin efecto (un retype a su propio tipo sin swap) para el loader antes de
        abrir el driver, con exit 2 y su posición, sin valores (antes, un aviso y seguía)."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'relations.yaml').write_text(
                "fix:\n  - {source: Fulana de Tal, source_type: Person, target: Empresa Ficticia SL, "
                "target_type: Company, type: PAID, corrected_type: PAID, swap_direction: false}\n", encoding='utf-8')
            err = io.StringIO()
            with mock.patch.object(sys, 'argv', ['relations_loader', '--dir', tmp]), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as fin:
                relations_loader.main()
        self.assertEqual(fin.exception.code, 2)
        self.assertIn('relations: fix[0]: regla fix sin efecto', err.getvalue())
        self.assertNotIn('Fulana', err.getvalue())


class ApplySetTest(unittest.TestCase):
    def test_orden_retype_set_swap_y_conteos(self):
        session = FakeSession([2, 5, 1])
        rows = _prepare_fix([_row(set={"status": "investigado"})])
        out = apply_fix(FakeDriver(session), rows)
        self.assertEqual(out, {"retyped": 2, "props_set": 5, "swapped": 1})
        queries = [q for q, _ in session.calls]
        self.assertEqual(len(queries), 3)
        self.assertIn("apoc.refactor.setType", queries[0])
        self.assertIn("SET r += props", queries[1])
        self.assertIn("apoc.refactor.invert", queries[2])

    def test_set_casa_las_simetricas_en_cualquier_sentido(self):
        # 063 (2026-09-28): cuatro notas sobre ASSOCIATED_WITH guardadas B→A no se aplicaban.
        session = FakeSession([0, 1, 0])
        apply_fix(FakeDriver(session), _prepare_fix([_row(type="ASSOCIATED_WITH", set={"note": "x"})]))
        (retype_q, _), (set_q, set_params), (swap_q, _) = session.calls
        self.assertIn("-[r]-(b", set_q)
        self.assertIn("startNode(r) = a OR type(r) IN $symmetric", set_q)
        self.assertEqual(set(set_params["symmetric"]), {"FAMILY_OF", "ASSOCIATED_WITH", "MET_WITH"})
        # El retype y el swap siguen dirigidos: pueden convertir la arista en una dirigida.
        self.assertIn("-[r]->(b", retype_q)
        self.assertIn("-[r]->(b", swap_q)

    def test_la_consulta_solo_toca_aristas_extraidas(self):
        self.assertIn("r.source_kind IS NULL OR r.source_kind = 'judicial'", _SET_Q)
        self.assertIn("row.doc IS NULL OR r.source_doc = row.doc", _SET_Q)
        self.assertIn("quote_contains", _SET_Q)
        self.assertIn("r.qa_set_run = reason", _SET_Q)
        self.assertIn("head(collect(row.set))", _SET_Q)  # dedup por arista

    def test_corregir_date_recalcula_la_fecha_que_publica_la_api(self):
        # La API prioriza date_norm: corregir solo `date` dejaba visible la errónea (revisión Codex 063).
        row = _prepare_fix([_row(set={"date": "2023-11-21"})])[0]
        self.assertEqual(row["set"], {"date": "2023-11-21", "date_norm": "2023-11-21", "date_year": 2023})

    def test_kinds_amplia_la_capa_solo_si_la_fila_lo_pide(self):
        # spec 063: sin `kinds` la fila solo casa aristas extraídas; con `kinds` también las de esa capa.
        for q in (_RETYPE_Q, _SET_Q, _SWAP_Q):
            self.assertIn("r.source_kind IN coalesce(row.kinds, [])", q)
        self.assertIsNone(_prepare_fix([_row(set={"note": "x"})])[0]["kinds"])
        self.assertEqual(_prepare_fix([_row(set={"note": "x"}, kinds=["prensa"])])[0]["kinds"], ["prensa"])
        deny = _prepare_deny([_row(kinds=["ultima_hora", "acusacion"])])
        self.assertEqual(deny[0]["kinds"], ["ultima_hora", "acusacion"])
        self.assertIsNone(_prepare_deny([_row()])[0]["kinds"])

    def test_kinds_no_permite_ascender_la_capa(self):
        for kind in ("acusacion", "contrato", "prensa"):
            with self.subTest(kind=kind):
                PrepareSetTest.para(self, _row(kinds=["prensa"], set={"source_kind": kind}), KINDS)
        ok = _prepare_fix([_row(kinds=["prensa"], set={"source_kind": "ultima_hora"})])
        self.assertEqual(ok[0]["set"], {"source_kind": "ultima_hora"})

    def test_retirar_date_retira_tambien_la_normalizada(self):
        # Spec 070 (AC154): se retira con un nulo; un texto vacío no es una fecha y para el loader.
        row = _prepare_fix([_row(set={"date": None})])[0]
        self.assertEqual(row["set"], {"date": None, "date_norm": None, "date_year": None})
        PrepareSetTest.para(self, _row(set={"date": ""}), TIPO)

    def test_kinds_desconocido_aborta(self):
        with self.assertRaises(ErrorDeForma) as fin:
            _prepare_deny([_row(kinds=["inventada"])])
        self.assertEqual(fin.exception.motivos, ["deny[0]: capas desconocidas"])

    def test_deny_pasa_kinds_a_la_consulta(self):
        session = FakeSession([1])
        apply_deny(FakeDriver(session), _prepare_deny([_row(kinds=["prensa"])]))
        query, params = session.calls[0]
        self.assertIn("r.source_kind IN coalesce(row.kinds, [])", query)
        self.assertEqual(params["rows"][0]["kinds"], ["prensa"])

    def test_sin_filas_no_abre_sesion(self):
        session = FakeSession([])
        self.assertEqual(apply_fix(FakeDriver(session), []),
                         {"retyped": 0, "swapped": 0, "props_set": 0})
        self.assertEqual(session.calls, [])


class Enmienda36Test(unittest.TestCase):
    """Spec 070, enmienda 36 (AC162–AC165), en el loader de relaciones: sin driver."""

    def main_con(self, texto):
        """(código de salida, stderr) de `main` sobre un overlay con un fichero `texto`; el driver no se abre nunca."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'relations.yaml').write_text(texto, encoding='utf-8')
            err = io.StringIO()
            with mock.patch.object(sys, 'argv', ['relations_loader', '--dir', tmp]), \
                    mock.patch.object(relations_loader.neo4j_loader, 'get_driver',
                                      side_effect=AssertionError('el driver se abrió')), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as fin:
                relations_loader.main()
        return fin.exception.code, err.getvalue()

    def test_la_raiz_original_de_cada_fichero(self):
        """AC164: el loader lee la raíz de cada fichero tal cual (sin `or {}`): vacío, `null`, `false`, `0`, `''` o
        `[]` paran con exit 2 («la raíz no es un mapa») antes del driver, como en el árbol; antes, cero reglas."""
        for raiz in ('', 'null\n', 'false\n', '0\n', "''\n", '[]\n'):
            with self.subTest(raiz=raiz):
                codigo, err = self.main_con(raiz)
                self.assertEqual(codigo, 2)
                self.assertIn('pipeline/data/relations/relations.yaml: la raíz no es un mapa', err)

    def test_un_numero_que_el_grafo_no_guarda_para_antes_del_driver(self):
        """AC163: un entero del `set` fuera de los 64 bits con signo, o un flotante no finito, para el loader con exit
        2 y su posición antes de abrir el driver (antes, el codificador de Bolt fallaba con el driver abierto y los
        splits ya aplicados); el valor no sale. Los límites, sí caben."""
        regla = ("splits:\n  - {canonical: {type: Person, name: Fulana de Tal}, namesake: {type: Person, name: "
                 "Fulana Otra}, by_doc: auto.pdf}\nfix:\n  - {source: Fulana de Tal, source_type: Person, target: "
                 "Empresa Ficticia SL, target_type: Company, type: PAID, set: {%s: %s}}\n")
        for prop, valor in (('amount', '9223372036854775808'), ('amount', '-9223372036854775809'),
                            ('amount', '.inf'), ('amount', '.nan'), ('source_page', '9223372036854775808')):
            with self.subTest(prop=prop, valor=valor):
                codigo, err = self.main_con(regla % (prop, valor))
                self.assertEqual(codigo, 2)
                self.assertIn('relations: fix[0]: corrección con un `set` con un número que el grafo no guarda', err)
                self.assertNotIn(valor.lstrip('-.'), err)
        for prop, valor in (('amount', 2 ** 63 - 1), ('amount', -2 ** 63), ('amount', 1.5),
                            ('source_page', 2 ** 63 - 1)):
            with self.subTest(cabe=valor):
                self.assertEqual(_prepare_fix([_row(set={prop: valor})])[0]['set'][prop], valor)

    def test_kinds_ausente_o_nulo_es_el_alcance_por_defecto(self):
        """AC165: sin `kinds` (o nulo), solo aristas extraídas; `false`, `0`, `''`, `{}` o `[]` paran la regla deny o
        fix (`capas desconocidas`), en el loader y con la misma función en el árbol, y nunca se leen como el alcance por
        defecto."""
        self.assertIsNone(_prepare_deny([_row()])[0]['kinds'])
        self.assertIsNone(_prepare_deny([_row(kinds=None)])[0]['kinds'])
        for kinds in (False, 0, '', {}, []):
            with self.subTest(kinds=kinds):
                with self.assertRaises(ErrorDeForma) as deny:
                    _prepare_deny([_row(kinds=kinds)])
                self.assertEqual(deny.exception.motivos, ['deny[0]: capas desconocidas'])
                with self.assertRaises(ErrorDeForma) as fix:
                    _prepare_fix([_row(kinds=kinds, swap_direction=True)])
                self.assertEqual(fix.exception.motivos, ['fix[0]: capas desconocidas'])
                self.assertEqual(relations_loader.seudonimos.forma_deny(_row(kinds=kinds)), 'capas desconocidas')

    def test_cada_consulta_del_split_lleva_todas_sus_restricciones(self):
        """AC162: las tres consultas de un split (aristas que salen, que entran y menciones) llevan siempre `by_doc`,
        las citas de `by_quote` y las de `by_quote_exclude` a la vez: el documento ya no se ignora en las de cita, ni la
        exclusión en las de documento. Un criterio que el split no declara no filtra (nulo o vacío)."""
        canon = {'type': 'Person', 'name': 'Fulana de Tal'}
        casos = [({'by_doc': 'auto.pdf'}, {'by_doc': 'auto.pdf', 'inc': [], 'exc': []}),
                 ({'by_quote': ['Otra'], 'by_quote_exclude': ['Tal']},
                  {'by_doc': None, 'inc': ['otra'], 'exc': ['tal']}),
                 ({'by_doc': 'auto.pdf', 'by_quote': ['Otra'], 'by_quote_exclude': ['Tal']},
                  {'by_doc': 'auto.pdf', 'inc': ['otra'], 'exc': ['tal']}),
                 ({'by_doc': 'auto.pdf', 'by_quote_exclude': ['Tal']},
                  {'by_doc': 'auto.pdf', 'inc': [], 'exc': ['tal']})]
        for criterios, esperado in casos:
            with self.subTest(criterios=criterios):
                session = FakeSession([0, 1, 1, 1, 0])
                sp = {'canonical': canon, 'namesake': {'type': 'Person', 'name': 'Fulana Otra'}, **criterios}
                relations_loader.apply_split(FakeDriver(session), sp, 'splits[0]')
                movimientos = session.calls[1:4]
                self.assertEqual([q for q, _ in movimientos],
                                 [relations_loader._MOVE_OUT_Q, relations_loader._MOVE_IN_Q,
                                  relations_loader._MOVE_MENTIONS_Q])
                for query, params in movimientos:
                    self.assertEqual({k: params[k] for k in ('by_doc', 'inc', 'exc')}, esperado)
                    for filtro in ('$by_doc IS NULL OR', 'size($inc) = 0 OR any(p IN $inc', 'none(p IN $exc'):
                        self.assertIn(filtro, query)


if __name__ == "__main__":
    unittest.main()
