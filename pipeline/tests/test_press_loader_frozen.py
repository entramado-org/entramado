"""press_loader no se re-carga sobre el linaje (revisión Codex 063 tramo 1)."""
import unittest
from unittest import mock

from pipeline.src import press_loader


def _run(has_press: bool, *extra):
    driver = mock.Mock()
    with mock.patch.object(press_loader.neo4j_loader, "get_driver", return_value=driver), \
            mock.patch.object(press_loader.neo4j_loader, "compute_degree"), \
            mock.patch.object(press_loader, "lineage_has_press", return_value=has_press), \
            mock.patch.object(press_loader, "load") as load, \
            mock.patch("sys.argv", ["press_loader", *extra]):
        try:
            press_loader.main()
            code = 0
        except SystemExit as e:
            code = e.code
    return load, code


class FrozenTest(unittest.TestCase):
    def test_aborta_si_el_grafo_ya_tiene_prensa(self):
        load, code = _run(True)
        self.assertEqual(code, 1)
        load.assert_not_called()

    def test_grafo_sin_prensa_carga(self):
        load, code = _run(False)
        self.assertEqual(code, 0)
        load.assert_called_once()

    def test_override_explicito(self):
        load, code = _run(True, "--allow-lineage")
        load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
