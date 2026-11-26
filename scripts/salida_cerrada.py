#!/usr/bin/env python3
"""Lo que se publica es una lista cerrada de ficheros (spec 077).

`wrangler deploy` sube todo `web/out`, y Next copia ahí todo `web/public`. Un fichero suelto (un PDF, una
imagen) quedaría accesible por URL sin que ninguna página lo enlace ni ningún escáner lo lea. Esta puerta
falla si `web/public` o `web/out` contienen algo fuera de lo esperado.

    python3 scripts/salida_cerrada.py web/public web/out
"""
import os
import re
import sys

PUBLIC = {"_headers"}
# Fuera de `_next/`: las páginas, sus cargas de navegación (.txt) y cuatro ficheros con nombre propio.
RAIZ = re.compile(r"(?:[a-z0-9_-]+|__next\.[A-Za-z0-9_.$-]+)\.(?:html|txt)|[a-z0-9_-]+/__next\.[A-Za-z0-9_.$-]+\.txt")
CON_NOMBRE = {"_headers", "icon.svg", "opengraph-image.png", "robots.txt", "sitemap.xml"}
# Dentro de `_next/`: solo lo que genera el build (código, estilos y sus recursos estáticos).
NEXT = re.compile(r"_next/[A-Za-z0-9_./~$-]+\.(?:js|css|png|svg|woff2)")


def ficheros(base):
    for carpeta, _, nombres in os.walk(base):
        for nombre in nombres:
            yield os.path.relpath(os.path.join(carpeta, nombre), base).replace(os.sep, "/")


def problemas(public, out):
    malos = [f"web/public: {f}" for f in sorted(ficheros(public)) if f not in PUBLIC]
    for f in sorted(ficheros(out)):
        if f in CON_NOMBRE or RAIZ.fullmatch(f) or NEXT.fullmatch(f):
            continue
        malos.append(f"web/out: {f}")
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
