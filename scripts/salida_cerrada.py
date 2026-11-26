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
# Fuera de `_next/`: las páginas de este sitio, sus cargas de navegación (.txt) y cinco ficheros con nombre propio.
# Las páginas van por su nombre: una carpeta cualquiera (p. ej. `static/`, que Next también copia) no entra.
PAGINAS = ("index", "404", "_not-found", "aviso-legal", "contribuir", "documentos", "ultima-hora")
_P = "(?:" + "|".join(re.escape(p) for p in PAGINAS) + ")"
_CARGA = r"__next\.[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)*\.txt"
RAIZ = re.compile(rf"{_P}\.(?:html|txt)|{_CARGA}|{_P}/{_CARGA}")
CON_NOMBRE = {"_headers", "icon.svg", "opengraph-image.png", "robots.txt", "sitemap.xml"}
# Dentro de `_next/`: solo lo que genera el build (código, estilos y sus recursos estáticos).
NEXT = re.compile(r"_next/[A-Za-z0-9_./~$-]+\.(?:js|css|png|svg|woff2)")


def ficheros(base):
    for carpeta, _, nombres in os.walk(base):
        for nombre in nombres:
            yield os.path.relpath(os.path.join(carpeta, nombre), base).replace(os.sep, "/")


def problemas(public, out):
    base = os.path.dirname(os.path.abspath(public))
    malos = [f"web/static: {f}" for f in sorted(ficheros(os.path.join(base, "static")))]
    malos += [f"web/public: {f}" for f in sorted(ficheros(public)) if f not in PUBLIC]
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
