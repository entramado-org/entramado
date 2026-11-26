"""Un fichero no previsto en web/public o en web/out impide publicar (spec 077)."""
import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

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
                with open(destino, "w") as f:
                    f.write(ruta)
        self.d = d.name
        os.makedirs(os.path.join(d.name, "app"))
        with open(os.path.join(d.name, "app", "opengraph-image.alt.txt"), "w") as f:
            f.write("alt")
        for nombre, valor in (("CABECERAS", "_headers"), ("TEXTO_ALT", "alt")):
            otro = mock.patch.object(salida_cerrada, nombre, hashlib.sha256(valor.encode()).hexdigest())
            otro.start()
            self.addCleanup(otro.stop)
        parche = mock.patch.object(salida_cerrada, "GRAFICOS", {hashlib.sha256(n.encode()).hexdigest()
                                                                 for n in ("icon.svg", "opengraph-image.png")})
        parche.start()
        self.addCleanup(parche.stop)
        return self._problemas()

    def _problemas(self):
        d = type("D", (), {"name": self.d})
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
        self.arbol(["_headers"], BUENOS)
        os.makedirs(os.path.join(self.d, "static"))
        self.assertIn("web/static: no debe existir", self._problemas())

    def test_una_imagen_con_el_mismo_nombre_y_otros_bytes_se_rechaza(self):
        self.arbol(["_headers"], BUENOS)
        with open(os.path.join(self.d, "out", "opengraph-image.png"), "w") as f:
            f.write("otra imagen")
        self.assertEqual(self._problemas(), ["web/out: opengraph-image.png (imagen distinta de la aprobada)"])

    def test_una_imagen_generada_en_next_con_otros_bytes_se_rechaza(self):
        self.assertEqual(self.arbol(["_headers"], BUENOS + ["_next/static/media/captura.png"]),
                         ["web/out: _next/static/media/captura.png (imagen distinta de la aprobada)"])

    def test_un_enlace_a_un_directorio_en_public_se_rechaza(self):
        self.arbol(["_headers"], BUENOS)
        fuera = os.path.join(self.d, "fuera", "ultima-hora")
        os.makedirs(fuera)
        open(os.path.join(fuera, "__next.copia.txt"), "w").close()
        os.symlink(fuera, os.path.join(self.d, "public", "ultima-hora"))
        self.assertEqual(self._problemas(), ["web/public: ultima-hora"])

    def test_una_carpeta_o_un_enlace_en_public_o_en_out_se_rechazan(self):
        self.arbol(["_headers"], BUENOS)
        os.makedirs(os.path.join(self.d, "public", "ultima-hora"))
        os.symlink(os.path.join(self.d, "out", "index.html"), os.path.join(self.d, "out", "contribuir.html"))
        self.assertEqual(self._problemas(), ["web/public: ultima-hora", "web/out: contribuir.html (enlace simbólico)"])


    def test_cabeceras_texto_alternativo_y_entorno(self):
        self.arbol(["_headers"], BUENOS)
        with open(os.path.join(self.d, "out", "_headers"), "a") as f:
            f.write("\n/*\n  Refresh: 0; url=https://example.invalid\n")
        with open(os.path.join(self.d, "app", "opengraph-image.alt.txt"), "w") as f:
            f.write("otra frase")
        open(os.path.join(self.d, ".env.production"), "w").close()
        self.assertEqual(self._problemas(), ["web/.env.production: no debe existir",
                                             "web/app/opengraph-image.alt.txt (texto distinto del aprobado)",
                                             "web/out: _headers (cabeceras distintas de las aprobadas)"])

    def test_las_huellas_fijadas_son_las_del_arbol(self):
        web = os.path.join(os.path.dirname(os.path.dirname(AQUI)), "web")
        self.assertEqual(salida_cerrada.huella(os.path.join(web, "public", "_headers")), salida_cerrada.CABECERAS)
        self.assertEqual(salida_cerrada.huella(os.path.join(web, "app", "opengraph-image.alt.txt")), salida_cerrada.TEXTO_ALT)
        for nombre in ("opengraph-image.png", "icon.svg"):
            self.assertIn(salida_cerrada.huella(os.path.join(web, "app", nombre)), salida_cerrada.GRAFICOS)


if __name__ == "__main__":
    unittest.main()
