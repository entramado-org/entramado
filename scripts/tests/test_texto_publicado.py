"""El texto que se escanea por PII es el que la web muestra, no el YAML tal como está escrito (spec 077)."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
sys.path.insert(0, os.path.dirname(AQUI))
import texto_publicado  # noqa: E402

ESCANER = os.path.join(RAIZ, "pipeline", "scripts", "pii_tree_scan.py")
# Número ficticio, escrito a trozos para que este fichero no lo contenga en una línea.
TROZOS = ("655", "443", "322")
UNA = "titulo: Llamen al %s %s %s para saber más\n" % TROZOS
DOS = "titulo: Llamen al %s\n  %s %s para saber más\n" % TROZOS


class TextoPublicadoTest(unittest.TestCase):
    def escanea(self, yaml_del_item, volcar):
        with tempfile.TemporaryDirectory() as d:
            feed = os.path.join(d, "feed.yaml")
            with open(feed, "w", encoding="utf-8") as f:
                f.write("items:\n- iso: '2026-11-20'\n  " + yaml_del_item.replace("\n  ", "\n    "))
            objetivo = feed
            if volcar:
                salida = io.StringIO()
                with contextlib.redirect_stdout(salida):
                    texto_publicado.main([feed])
                objetivo = os.path.join(d, "texto.txt")
                with open(objetivo, "w", encoding="utf-8") as f:
                    f.write(salida.getvalue())
            return subprocess.run([sys.executable, ESCANER, objetivo], capture_output=True).returncode

    def test_un_titular_en_una_linea_se_rechaza(self):
        self.assertNotEqual(self.escanea(UNA, volcar=True), 0)

    def test_un_titular_partido_en_dos_lineas_se_rechaza_igual(self):
        self.assertNotEqual(self.escanea(DOS, volcar=True), 0)

    def test_control_el_yaml_partido_tal_cual_escapa_al_escaner(self):
        # Si esto deja de cumplirse, el escáner ya une líneas y el volcado sobra.
        self.assertEqual(self.escanea(DOS, volcar=False), 0)

    def test_control_un_titular_sin_identificadores_pasa(self):
        self.assertEqual(self.escanea("titulo: El juez cita a declarar a un exministro\n", volcar=True), 0)


if __name__ == "__main__":
    unittest.main()
