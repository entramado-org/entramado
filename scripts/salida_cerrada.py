#!/usr/bin/env python3
"""Lo que se publica es una lista cerrada de ficheros (spec 077).

`wrangler deploy` sube todo `web/out`, y Next copia ahí todo `web/public`. Un fichero suelto (un PDF, una
imagen) quedaría accesible por URL sin que ninguna página lo enlace ni ningún escáner lo lea. Esta puerta
falla si `web/public` o `web/out` contienen algo fuera de lo esperado.

    python3 scripts/salida_cerrada.py web/public web/out
"""
import hashlib
import os
import re
import sys

PUBLIC = {"_headers"}
# Fuera de `_next/`: las páginas de este sitio, sus cargas de navegación (.txt) y cinco ficheros con nombre propio.
# Las páginas van por su nombre: una carpeta cualquiera (p. ej. `static/`, que Next también copia) no entra.
PAGINAS = ("index", "404", "_not-found", "aviso-legal", "contribuir", "documentos", "ultima-hora")
_P = "(?:" + "|".join(re.escape(p) for p in PAGINAS) + ")"
_CARGA = r"__next\.[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)*\.txt"
RAIZ = re.compile(rf"{_P}\.(?:html|txt)|{_CARGA}|{_P}/{_CARGA}")
CON_NOMBRE = {"_headers", "icon.svg", "opengraph-image.png", "robots.txt", "sitemap.xml"}
# Dentro de `_next/`: solo lo que genera el build (código, estilos y sus recursos estáticos).
NEXT = re.compile(r"_next/[A-Za-z0-9_./~$-]+\.(?:js|css|png|svg|woff2)")


# Los dos recursos gráficos, por su contenido: ningún escáner lee una imagen, así que cambiarla es un cambio
# deliberado que también cambia esta huella (SHA-256).
GRAFICOS = {
    "87339eee09ff15984cabf5415a0006c4385550c9e580caf77930e558cfe42bf6",  # opengraph-image.png
    "19d4769ad46ca3dd94454529b1043e49f6dde0c49424786c4c12fc9879dfa042",  # icon.svg
}


def huella(ruta):
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def entradas(base):
    """(ruta relativa, es_enlace) de todo lo que cuelga de `base`, sin seguir enlaces simbólicos."""
    for carpeta, carpetas, nombres in os.walk(base, followlinks=False):
        for nombre in carpetas + nombres:
            ruta = os.path.join(carpeta, nombre)
            if os.path.islink(ruta) or not os.path.isdir(ruta):
                yield os.path.relpath(ruta, base).replace(os.sep, "/"), os.path.islink(ruta)


def problemas(public, out):
    base = os.path.dirname(os.path.abspath(public))
    static = os.path.join(base, "static")
    malos = []
    if os.path.lexists(static):
        malos.append("web/static: no debe existir")
    # web/public: exactamente un fichero regular, `_headers`. Ni carpetas ni enlaces.
    for nombre in sorted(os.listdir(public)):
        ruta = os.path.join(public, nombre)
        if nombre not in PUBLIC or os.path.islink(ruta) or not os.path.isfile(ruta):
            malos.append(f"web/public: {nombre}")
    for f, enlace in sorted(entradas(out)):
        if enlace:
            malos.append(f"web/out: {f} (enlace simbólico)")
        elif not (f in CON_NOMBRE or RAIZ.fullmatch(f) or NEXT.fullmatch(f)):
            malos.append(f"web/out: {f}")
        elif f.endswith((".png", ".svg")) and huella(os.path.join(out, f)) not in GRAFICOS:
            malos.append(f"web/out: {f} (imagen distinta de la aprobada)")
    return malos


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    if not os.path.isdir(argv[1]):
        print(f"salida_cerrada: no existe {argv[1]}", file=sys.stderr)
        return 1
    malos = problemas(*argv)
    for m in malos:
        print(f"salida_cerrada: fichero no previsto: {m}", file=sys.stderr)
    if not malos:
        print("salida cerrada: solo los ficheros previstos")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
