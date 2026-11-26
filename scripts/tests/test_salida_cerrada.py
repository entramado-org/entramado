"""Un fichero no previsto en web/public o en web/out impide publicar (spec 077)."""
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import salida_cerrada  # noqa: E402

BUENOS = ["index.html", "index.txt", "404.html", "ultima-hora.html", "ultima-hora.txt", "__next._tree.txt",
          "ultima-hora/__next._full.txt", "_headers", "icon.svg", "opengraph-image.png", "robots.txt",
          "sitemap.xml", "_next/static/chunks/abc-123.js", "_next/static/css/x.css", "_next/static/media/a.woff2"]


class SalidaCerradaTest(unittest.TestCase):
    def arbol(self, public, out):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for base, rutas in (("public", public), ("out", out)):
            os.makedirs(os.path.join(d.name, base))
            for ruta in rutas:
                destino = os.path.join(d.name, base, ruta)
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                open(destino, "w").close()
        return salida_cerrada.problemas(os.path.join(d.name, "public"), os.path.join(d.name, "out"))

    def test_la_salida_prevista_pasa(self):
        self.assertEqual(self.arbol(["_headers"], BUENOS), [])

    def test_un_pdf_en_public_se_rechaza(self):
        self.assertEqual(self.arbol(["_headers", "auto.pdf"], BUENOS), ["web/public: auto.pdf"])

    def test_ficheros_no_previstos_en_out_se_rechazan(self):
        for malo in ("auto.pdf", "docs/auto.pdf", "foto.png", "datos.json", "_next/static/auto.pdf",
                     "_next/../auto.html.pdf", "pagina.html.pdf", "sub/pagina.html", "feed.yaml",
                     "static/__next._full.txt", "otra.html", "otra/__next._full.txt", "__next.txt"):
            with self.subTest(malo=malo):
                self.assertEqual(self.arbol(["_headers"], BUENOS + [malo.replace("_next/../", "")]),
                                 ["web/out: " + malo.replace("_next/../", "")])


    def test_la_carpeta_static_del_proyecto_se_rechaza(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for carpeta in ("public", "out", "static"):
            os.makedirs(os.path.join(d.name, carpeta))
        open(os.path.join(d.name, "static", "__next._full.txt"), "w").close()
        self.assertEqual(salida_cerrada.problemas(os.path.join(d.name, "public"), os.path.join(d.name, "out")),
                         ["web/static: __next._full.txt"])


if __name__ == "__main__":
    unittest.main()
