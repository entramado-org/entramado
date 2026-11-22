"""Spec 071 AC199(c): el generador del catálogo del vigía (`_boe_recon_sweep.py`, que escribe `vigia_targets.json`) no
incluye un referente «sin identificar» ni a quien no tiene ningún nombre de varias palabras: su clave no identifica a
nadie concreto. Un nombre de una palabra con un alias completo, sí."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

SWEEP = Path(__file__).resolve().parents[1] / 'scripts/_boe_recon_sweep.py'


def generador():
    spec = importlib.util.spec_from_file_location('boe_recon_sweep', SWEEP)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class CatalogoTest(unittest.TestCase):

    def objetivos(self, filas):
        """Los ids que `_load_targets` da para estas filas (id, nombre, grado, rol, alias), escritas como las exporta
        `_boe_target_list.sh` (el formato plain de cypher-shell, con los alias unidos por « ~ »)."""
        B = generador()
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            (raiz / 'pipeline/data/context').mkdir(parents=True)
            (raiz / 'pipeline/data/context/pseudonyms.yaml').write_text('pseudonyms: []\n', encoding='utf-8')
            lineas = ['"id", "name", "degree", "role", "aliases"']
            lineas += [f'"{i}", "{n}", {g}, "{r}", "{a}"' for i, n, g, r, a in filas]
            (raiz / '_target_persons.tsv').write_text('\n'.join(lineas) + '\n', encoding='utf-8')
            B.DATA, B.REPO = raiz, raiz
            return {t['id'] for t in B._load_targets(0)}

    def test_an_unidentified_referent_is_not_a_target(self):
        # Rojo→verde: el rol que lo marca «sin identificar» lo saca, aunque su nombre tenga varias palabras.
        ids = self.objetivos([('Person:referente-ficticio', 'Referente Ficticio Ejemplar', 5,
                               'Mencionado en un informe ficticio, sin identificar.', ''),
                              ('Person:persona-ficticia', 'Persona Ficticia Ejemplar', 5, '', '')])
        self.assertEqual(ids, {'Person:persona-ficticia'})

    def test_a_one_word_name_needs_a_full_alias(self):
        # Guardas: un nombre de una palabra con un alias de varias palabras se mantiene; sin ninguno, cae.
        ids = self.objetivos([('Person:con-alias', 'Ejemplar', 5, '', 'Ejemplar Ficticio Completo ~ '),
                              ('Person:sin-alias', 'Ejemplar', 5, '', '')])
        self.assertEqual(ids, {'Person:con-alias'})


if __name__ == '__main__':
    unittest.main()
