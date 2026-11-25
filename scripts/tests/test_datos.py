"""Tests of the closed schema and of the link check (spec 077).

    cd scripts && python -m unittest discover tests
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import enlaces  # noqa: E402
import validar_datos  # noqa: E402
from titulares import Pieza, _Page  # noqa: E402

ITEM = {"iso": "2026-11-20", "titulo": "Un titular", "medio": "Medio", "url": "https://medio.example/pieza"}
DOC = {"organismo": "Senado", "designacion": "Diario", "fecha": "2026-01-02", "url": "https://www.senado.es/a.PDF"}

# Each case must be rejected by BOTH validators (Python and the web build).
INVALIDOS = {
    "campo de más": lambda d: d["items"][0].update(dek="texto propio"),
    "campo caso": lambda d: d["items"][0].update(caso="caso-uno"),
    "falta el medio": lambda d: d["items"][0].pop("medio"),
    "url sin host": lambda d: d["items"][0].update(url="https://"),
    "url http": lambda d: d["items"][0].update(url="http://medio.example/pieza"),
    "mes 13": lambda d: d["items"][0].update(iso="2026-13-01"),
    "30 de febrero": lambda d: d["items"][0].update(iso="2026-02-30"),
    "url repetida": lambda d: d["items"].append(dict(d["items"][0])),
    "documento en dominio ajeno": lambda d: d["documentos"][0].update(url="https://otro.example/a.pdf"),
    "organismo desconocido": lambda d: d["documentos"][0].update(organismo="Otro"),
    "titular vacío": lambda d: d["items"][0].update(titulo=" "),
}


def _write(folder: pathlib.Path, mutate=None) -> None:
    data = {"items": [dict(ITEM)], "documentos": [dict(DOC)]}
    if mutate:
        mutate(data)
    for name, key in (("feed", "items"), ("documentos", "documentos")):
        (folder / f"{name}.yaml").write_text(yaml.safe_dump({key: data[key]}, allow_unicode=True), encoding="utf-8")


def _python(folder: pathlib.Path) -> bool:
    return not validar_datos.validar(str(folder / "feed.yaml"), str(folder / "documentos.yaml"))


def _web(folder: pathlib.Path) -> bool:
    done = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", "await import('./lib/datos.ts')"],
        cwd=ROOT / "web", env={"PATH": __import__("os").environ["PATH"], "DATOS_DIR": str(folder)}, capture_output=True,
    )
    return done.returncode == 0


class EsquemaCerrado(unittest.TestCase):
    def test_los_datos_validos_pasan_los_dos_validadores(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(pathlib.Path(tmp))
            self.assertTrue(_python(pathlib.Path(tmp)))
            self.assertTrue(_web(pathlib.Path(tmp)))

    def test_cada_dato_invalido_lo_rechazan_los_dos_validadores(self):
        for name, mutate in INVALIDOS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                _write(pathlib.Path(tmp), mutate)
                self.assertFalse(_python(pathlib.Path(tmp)), "python lo acepta")
                self.assertFalse(_web(pathlib.Path(tmp)), "el build lo acepta")

    def test_los_datos_del_repositorio_son_validos(self):
        self.assertTrue(_python(ROOT / "datos"))


class Titulares(unittest.TestCase):
    def test_un_titular_con_comillas_interiores_se_lee_entero(self):
        page = _Page()
        page.feed("""<meta property="og:title" content="El juez del 'caso X' prorroga"><meta content='Dice "sí"' property="x">
                     <h1 class="t">El juez del <i>'caso X'</i> prorroga</h1><meta property="article:published_time" content="2026-11-20T10:00:00+01:00">""")
        self.assertEqual(page.meta["og:title"], "El juez del 'caso X' prorroga")
        self.assertEqual(page.h1s, ["El juez del 'caso X' prorroga"])


class Enlaces(unittest.TestCase):
    def test_un_h1_corregido_con_metadatos_antiguos_se_marca(self):
        pieza = Pieza(status="200", h1="Un titular (corregido)", og_title="Un titular", title="Un titular | Medio", published="2026-11-20")
        self.assertIn("TITULAR CAMBIADO", enlaces.revisar_item(ITEM, pieza))

    def test_un_titular_ampliado_se_marca(self):
        pieza = Pieza(status="200", h1="Un titular, según desmiente el juzgado", published="2026-11-20")
        self.assertIn("TITULAR CAMBIADO", enlaces.revisar_item(ITEM, pieza))

    def test_un_enlace_caido_se_marca(self):
        self.assertIn("ENLACE 404", enlaces.revisar_item(ITEM, Pieza(status="404")))

    def test_una_fecha_distinta_se_marca(self):
        self.assertIn("FECHA DISTINTA", enlaces.revisar_item(ITEM, Pieza(status="200", h1="Un titular", published="2026-11-19")))

    def test_una_pieza_igual_no_se_marca(self):
        self.assertIsNone(enlaces.revisar_item(ITEM, Pieza(status="200", h1="Un titular", published="2026-11-20")))


if __name__ == "__main__":
    unittest.main()
