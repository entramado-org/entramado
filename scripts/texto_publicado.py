#!/usr/bin/env python3
"""Texto que el sitio muestra, un campo por línea, tal como queda tras cargar el YAML (spec 077).

El escáner de PII lee ficheros línea a línea; un titular que el YAML reparte entre dos líneas se muestra en
la web como una sola. `publicar.sh` escanea esta salida además del árbol, para que la regla se aplique al
texto que de verdad se publica.

    python3 scripts/texto_publicado.py datos/feed.yaml datos/documentos.yaml > texto.txt
"""
import sys

import yaml


def campos(valor):
    if isinstance(valor, dict):
        for v in valor.values():
            yield from campos(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from campos(v)
    elif valor is not None:
        yield " ".join(str(valor).split())


def main(rutas):
    for ruta in rutas:
        with open(ruta, encoding="utf-8") as f:
            for campo in campos(yaml.safe_load(f)):
                print(campo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
