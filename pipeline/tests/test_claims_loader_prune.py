"""claims_loader: retirar la ÚLTIMA afirmación también poda (revisión Codex 063 tramo 1)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.src import claims_loader


def _run(yaml_text: str | None):
    with tempfile.TemporaryDirectory() as d:
        if yaml_text is not None:
            Path(d, "claims.yaml").write_text(yaml_text, encoding="utf-8")
        driver = mock.Mock()
        with mock.patch.object(claims_loader.neo4j_loader, "get_driver", return_value=driver), \
                mock.patch.object(claims_loader, "load") as load, \
                mock.patch.object(claims_loader, "prune") as prune, \
                mock.patch("sys.argv", ["claims_loader", "--dir", d]):
            claims_loader.main()
        return load, prune


class PruneOnEmptyTest(unittest.TestCase):
    def test_lista_vacia_explicita_poda_todo(self):
        load, prune = _run("claims: []\n")
        load.assert_not_called()
        prune.assert_called_once()
        self.assertEqual(prune.call_args.args[1:], ([], []))

    def test_sin_ficheros_no_poda(self):
        load, prune = _run(None)
        prune.assert_not_called()

    def test_fichero_sin_clave_claims_no_poda(self):
        load, prune = _run("otra_cosa: 1\n")
        prune.assert_not_called()

    def test_entradas_invalidas_no_podan(self):
        # Hay entradas pero ninguna valida: es un overlay roto, no una retirada.
        load, prune = _run("claims:\n  - {texto: sin id}\n")
        prune.assert_not_called()


if __name__ == "__main__":
    unittest.main()
