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
DOC = {"organismo": "Senado", "designacion": "Diario de Sesiones del Senado. Comisiones, núm. 758 (XV Legislatura)", "fecha": "2026-01-02", "url": "https://www.senado.es/ds/DS_C_15_758.PDF"}

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
    "documento que enlaza a la portada del organismo": lambda d: d["documentos"][0].update(url="https://www.senado.es/web/index.html"),
    "documento que enlaza a otro número": lambda d: d["documentos"][0].update(url="https://www.senado.es/ds/DS_C_15_756.PDF"),
    "documento sin número en su designación": lambda d: d["documentos"][0].update(designacion="Diario de Sesiones"),
    "diario de otra serie con el mismo número": lambda d: d["documentos"][0].update(organismo="Congreso de los Diputados", designacion="Diario de Sesiones del Congreso de los Diputados. Comisiones de Investigación, núm. 8 (XV Legislatura)", url="https://www.congreso.es/public_oficiales/L15/CONG/DS/CO/DSCD-15-CO-8.PDF"),
    "diario de otra legislatura": lambda d: d["documentos"][0].update(url="https://www.senado.es/ds/DS_C_14_758.PDF"),
    "organismo desconocido": lambda d: d["documentos"][0].update(organismo="Otro"),
    "titular vacío": lambda d: d["items"][0].update(titulo=" "),
    "fecha futura": lambda d: d["items"][0].update(iso="2999-01-01"),
    "host sin punto": lambda d: d["items"][0].update(url="https://localhost/pieza"),
    "misma pieza con parámetros": lambda d: d["items"].append(dict(d["items"][0], url=d["items"][0]["url"] + "/?utm=1")),
    "misma pieza con la ruta codificada": lambda d: (d["items"][0].update(url="https://medio.example/España/pieza"), d["items"].append(dict(d["items"][0], url="https://medio.example/Espa%C3%B1a/pieza"))),
    "titular de dos líneas": lambda d: d["items"][0].update(titulo="Un titular\ny texto propio"),
    "titular que enlaza a un PDF adjunto": lambda d: d["items"][0].update(url="https://medio.example/uploads/sentencia.PDF"),
    "titular que enlaza a una imagen": lambda d: d["items"][0].update(url="https://medio.example/img/foto.jpg?w=800"),
    "url de portada": lambda d: d["items"][0].update(url="https://medio.example/"),
    "url que resuelve a la portada": lambda d: d["items"][0].update(url="https://medio.example/hemeroteca/.."),
    "url de barras que resuelve a la portada": lambda d: d["items"][0].update(url="https://medio.example//.."),
    "titular que enlaza a un documento oficial": lambda d: d["items"][0].update(url=d["documentos"][0]["url"]),
    "url que resuelve a la portada, codificada": lambda d: d["items"][0].update(url="https://medio.example/hemeroteca/%2E%2E"),
    "año cero": lambda d: d["items"][0].update(iso="0000-01-01"),
    "fecha de semana ISO": lambda d: d["items"][0].update(iso="2026-W47-5"),
    "url sin host con ruta": lambda d: d["items"][0].update(url="https:///pieza"),
    "url sin barras": lambda d: d["items"][0].update(url="https:medio.example/pieza"),
    "url con una sola barra": lambda d: d["items"][0].update(url="https:/medio.example/pieza"),
    "url con credenciales": lambda d: d["items"][0].update(url="https://a@medio.example/pieza"),
    "url con barra invertida": lambda d: d["items"][0].update(url="https://medio.example\\@otro.example/pieza"),
}

# Raw YAML that the two parsers used to read differently (YAML 1.1 vs 1.2).
YAML_CRUDO = {
    "fecha sin comillas": "items:\n- iso: 2026-11-20\n  titulo: Un titular\n  medio: Medio\n  url: https://medio.example/pieza\n",
    "clave repetida": "items:\n- iso: '2026-11-20'\n  titulo: Uno\n  titulo: Otro\n  medio: Medio\n  url: https://medio.example/pieza\n",
    "titular booleano": "items:\n- iso: '2026-11-20'\n  titulo: No\n  medio: Medio\n  url: https://medio.example/pieza\n",
}


# Rules only the Python gate enforces; it runs in `prebuild`, before every build of the site.
SOLO_PYTHON = {"titular que enlaza a un PDF adjunto", "titular que enlaza a una imagen", "diario de otra serie con el mismo número", "diario de otra legislatura", "documento que enlaza a la portada del organismo", "documento que enlaza a otro número", "documento sin número en su designación", "url de barras que resuelve a la portada", "titular que enlaza a un documento oficial", "titular de dos líneas", "url de portada", "url que resuelve a la portada", "url que resuelve a la portada, codificada", "año cero", "misma pieza con la ruta codificada"}


def _write(folder: pathlib.Path, mutate=None) -> None:
    data = {"items": [dict(ITEM)], "documentos": [dict(DOC)]}
    if mutate:
        mutate(data)
    for name, key in (("feed", "items"), ("documentos", "documentos")):
        (folder / f"{name}.yaml").write_text(yaml.safe_dump({key: data[key]}, allow_unicode=True), encoding="utf-8")


def _python(folder: pathlib.Path) -> bool:
    try:
        return not validar_datos.validar(str(folder / "feed.yaml"), str(folder / "documentos.yaml"))
    except SystemExit:
        return False


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
                if name not in SOLO_PYTHON:
                    self.assertFalse(_web(pathlib.Path(tmp)), "el build lo acepta")

    def test_el_build_valida_con_python_antes_de_construir(self):
        scripts = __import__("json").loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))["scripts"]
        self.assertIn("validar_datos.py ../datos/feed.yaml ../datos/documentos.yaml", scripts["prebuild"])

    def test_el_yaml_que_cada_parser_leia_distinto_lo_rechazan_los_dos(self):
        for name, raw in YAML_CRUDO.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                _write(pathlib.Path(tmp))
                (pathlib.Path(tmp) / "feed.yaml").write_text(raw, encoding="utf-8")
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
        pieza = Pieza(status="200", final_url=ITEM["url"], h1="Un titular (corregido)", og_title="Un titular", title="Un titular | Medio", published="2026-11-20")
        self.assertIn("TITULAR CAMBIADO", enlaces.revisar_item(ITEM, pieza))

    def test_un_titular_ampliado_se_marca(self):
        pieza = Pieza(status="200", final_url=ITEM["url"], h1="Un titular, según desmiente el juzgado", published="2026-11-20")
        self.assertIn("TITULAR CAMBIADO", enlaces.revisar_item(ITEM, pieza))

    def test_una_redireccion_a_otro_sitio_se_marca(self):
        pieza = Pieza(status="200", final_url="https://otro.example/portada", h1="Un titular", published="2026-11-20")
        self.assertIn("ENLACE REDIRIGIDO", enlaces.revisar_item(ITEM, pieza))

    def test_un_h1_corregido_no_se_tapa_con_og_title_en_ningun_medio(self):
        item = dict(ITEM, url="https://www.eldebate.com/espana/pieza.html")
        pieza = Pieza(status="200", final_url=item["url"], h1="Un titular rectificado", og_title="Un titular", published="2026-11-20")
        self.assertIn("TITULAR CAMBIADO", enlaces.revisar_item(item, pieza))

    def test_una_fecha_que_no_se_puede_leer_se_marca(self):
        self.assertIn("FECHA NO LEGIBLE", enlaces.revisar_item(ITEM, Pieza(status="200", final_url=ITEM["url"], h1="Un titular")))

    def test_un_h1_oculto_no_tapa_el_titular_visible(self):
        for oculto in ('<h1 hidden>Anterior</h1>', '<template><h1>Anterior</h1></template>', '<div aria-hidden="true"><h1>Anterior</h1></div>'):
            with self.subTest(oculto):
                page = _Page()
                page.feed(oculto + "<article><h1>Rectificado</h1></article>")
                self.assertEqual(page.article_h1s or page.h1s, ["Rectificado"])

    def test_una_region_oculta_con_cierres_opcionales_no_tapa_el_resto(self):
        for oculto in ("<ul hidden><li>a<li>b</ul>", '<div aria-hidden="true"><p>x<div>y</div></div>'):
            with self.subTest(oculto):
                page = _Page()
                page.feed(oculto + "<article><h1>Titular</h1></article>")
                self.assertEqual(page.article_h1s, ["Titular"])

    def test_un_nombre_que_el_medio_oculta_dentro_del_h1_no_se_lee(self):
        page = _Page()
        page.feed('<h1>Se investiga a A<span style="display:none"> y a B</span></h1>')
        self.assertEqual(page.h1s, ["Se investiga a A"])

    def test_la_fecha_de_publicacion_gana_a_la_de_actualizacion(self):
        page = _Page()
        page.feed('<time class="updated" datetime="2026-11-20">20</time><time itemprop="datePublished" datetime="2026-11-18">18</time>')
        self.assertEqual(page.meta.get("datePublished"), "2026-11-18")
        self.assertNotIn("time", page.meta)

    def test_una_redireccion_que_cambia_el_valor_de_la_consulta_se_marca(self):
        item = dict(ITEM, url="https://medio.example/pieza?path=/noticia")
        pieza = Pieza(status="200", final_url="https://medio.example/pieza?path=/noticia/", h1="Un titular", published="2026-11-20")
        self.assertIn("ENLACE REDIRIGIDO", enlaces.revisar_item(item, pieza))

    def test_el_h1_del_articulo_gana_al_del_logo(self):
        page = _Page()
        page.feed("<h1>Medio</h1><article><h1>Un titular</h1></article>")
        self.assertEqual(page.article_h1s, ["Un titular"])

    def test_dos_titulares_visibles_distintos_se_marcan(self):
        pieza = Pieza(status="200", final_url=ITEM["url"], h1="Un titular", otros_h1=["Rectificado"], published="2026-11-20")
        self.assertIn("VARIOS TITULARES", enlaces.revisar_item(ITEM, pieza))

    def test_la_fecha_de_modificacion_no_es_la_de_publicacion(self):
        page = _Page()
        page.feed('<time itemprop="dateModified" datetime="2026-11-21T09:00:00+01:00">21</time><h1>Un titular</h1>')
        self.assertNotIn("time", page.meta)

    def test_una_redireccion_que_cambia_la_consulta_se_marca(self):
        item = dict(ITEM, url="https://medio.example/articulo?id=17")
        pieza = Pieza(status="200", final_url="https://medio.example/articulo?id=32", h1="Un titular", published="2026-11-20")
        self.assertIn("ENLACE REDIRIGIDO", enlaces.revisar_item(item, pieza))

    def test_la_fecha_visible_en_time_se_lee(self):
        page = _Page()
        page.feed('<h1>Un titular</h1><time class="d" datetime="2026-10-23T14:10:22+02:00">23 oct</time>')
        self.assertEqual(page.meta["time"][:10], "2026-10-23")

    def test_una_redireccion_dentro_del_mismo_sitio_se_marca(self):
        pieza = Pieza(status="200", final_url="https://medio.example/pieza-con-otro-slug", h1="Un titular", published="2026-11-20")
        self.assertIn("ENLACE REDIRIGIDO", enlaces.revisar_item(ITEM, pieza))

    def test_un_enlace_caido_se_marca(self):
        self.assertIn("ENLACE 404", enlaces.revisar_item(ITEM, Pieza(status="404")))

    def test_una_fecha_distinta_se_marca(self):
        self.assertIn("FECHA DISTINTA", enlaces.revisar_item(ITEM, Pieza(status="200", final_url=ITEM["url"], h1="Un titular", published="2026-11-19")))

    def test_una_pieza_igual_no_se_marca(self):
        self.assertIsNone(enlaces.revisar_item(ITEM, Pieza(status="200", final_url=ITEM["url"], h1="Un titular", published="2026-11-20")))


if __name__ == "__main__":
    unittest.main()
