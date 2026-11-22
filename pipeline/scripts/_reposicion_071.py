#!/usr/bin/env python3
"""Spec 071 (enmienda 2): Cypher para reponer las dos aristas que el arbitraje de la 063 conservó y que borró la
regla `deny` de su copia duplicada (la cita retirada era la de la conservada, o estaba dentro de ella).

    python3 pipeline/scripts/_reposicion_071.py [--root AUDIT] [--out FICHERO]

Lee del censo PRIVADO de la auditoría (`census.sqlite`, gitignored) las dos unidades por su unit_id completo y
emite un MERGE por arista: MATCH de los dos extremos por `entity_id` y MERGE de la relación con TODAS sus props
tal como las guarda el censo, sin añadir ninguna. Si falta una unidad (o no es una arista, o un valor no cabe en
una migración de `make qa-migrate`), no emite nada y sale con 1; sin censo, o con la salida dentro del repo, con
2. La salida lleva las citas del censo y no se versiona: va a stdout o a un fichero fuera del repo, y la aplica
la puerta del Path A conjunto (`REPOSICION=<fichero> pipeline/scripts/_qa063b_gate.sh …`) antes de sus dos
ciclos; `registro_check --snapshot` («gemela conservada ausente») prueba después que ninguna regla las vuelve a
borrar.

Emite además la deduplicación de las gemelas idénticas de `DEDUPLICAR` (spec 071): una retirada cuya
gemela se retipa no puede llevar regla `deny`, que mataría también la conservada; se borran todas sus copias menos
una, y la migración falla (y no aplica nada) si no queda exactamente una.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / 'pipeline/data/audit/dual/20260909-20260909-qa061'
# Las aristas que el arbitraje conservó y que la regla de su copia duplicada también borró.
UNIDADES = (
    '3d082c490990509ad51be687b6fe6e1f8136ee2921d91dac1d44d6197f50b1bb',  # R-2026-10-03-0189 y R-2026-10-03-0190
    '7240916bcdd0d8eaccebe376648374c2e23b318bd46f7d9e304a2830f9e23290',  # R-2026-10-03-0054
)
# Gemelas idénticas que el linaje lleva dos veces: (retirada, conservada, tipo al que la regla del retype lleva las dos).
DEDUPLICAR = (
    ('b736b08bed3490f120370a94dc17ff4a367577812bf82dd22b1a5e3ba6203910',  # retirada por la dual (duplicado exacto)
     '27716136923053bad866809cac48bf3e18d2d51b15c5e814b0b8890e2c301761', 'ASSOCIATED_WITH'),  # retipada por el arbitraje
)
_PROP = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_TIPO = re.compile(r'[A-Z][A-Z0-9_]*')


def _literal(value):
    """Un valor de prop como literal Cypher. `make qa-migrate` parte el fichero por «;» y quita las líneas que
    empiezan por «//»: un texto con «;» o con saltos de línea no cabe y se rechaza."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if ';' in value or '\n' in value or '\r' in value:
            raise ValueError('un texto con «;» o saltos de línea no cabe en la migración')
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
    if isinstance(value, list):
        return '[' + ', '.join(_literal(v) for v in value) + ']'
    raise ValueError(f'una prop de tipo {type(value).__name__}')


def merge(value):
    """El MERGE de una arista tal como la guarda el censo (`{'start', 'end', 'type', 'props'}`)."""
    start, end, rtype, props = value.get('start'), value.get('end'), value.get('type'), value.get('props') or {}
    if not (isinstance(start, str) and start and isinstance(end, str) and end
            and isinstance(rtype, str) and _TIPO.fullmatch(rtype)):
        raise ValueError('extremos o tipo de arista inválidos')
    if not props or not all(isinstance(k, str) and _PROP.fullmatch(k) for k in props):
        raise ValueError('props vacías o con un nombre inválido')
    mapa = ', '.join(f'{k}: {_literal(props[k])}' for k in sorted(props))
    return (f'MATCH (a {{entity_id: {_literal(start)}}}), (b {{entity_id: {_literal(end)}}})\n'
            f'MERGE (a)-[:{rtype} {{{mapa}}}]->(b);\n')


def deduplicar(retirada, conservada, resultante):
    """La deduplicación de dos gemelas idénticas del censo: borra todas las copias con sus extremos, su dirección y
    todas sus props menos una, y luego exige que quede exactamente una (`apoc.util.validate` aborta la transacción de
    `make qa-migrate`). Casa el tipo original y el que deja el retype: corre antes de los ciclos, con el original, y
    tiene que valer igual sobre un linaje que ya la traiga retipada."""
    if (retirada.get('start'), retirada.get('end'), retirada.get('type'), retirada.get('props')) != \
            (conservada.get('start'), conservada.get('end'), conservada.get('type'), conservada.get('props')):
        raise ValueError('las gemelas a deduplicar no son idénticas en el censo')
    start, end, rtype, props = retirada.get('start'), retirada.get('end'), retirada.get('type'), retirada.get('props') or {}
    if not (isinstance(start, str) and start and isinstance(end, str) and end
            and all(isinstance(t, str) and _TIPO.fullmatch(t) for t in (rtype, resultante))):
        raise ValueError('extremos o tipo de arista inválidos')
    if not props or not all(isinstance(k, str) and _PROP.fullmatch(k) for k in props):
        raise ValueError('props vacías o con un nombre inválido')
    tipos = ', '.join(_literal(t) for t in sorted({rtype, resultante}))
    filtro = ' AND '.join([f'type(r) IN [{tipos}]'] + [f'r.{k} = {_literal(props[k])}' for k in sorted(props)])
    patron = f'MATCH (a {{entity_id: {_literal(start)}}})-[r]->(b {{entity_id: {_literal(end)}}})\nWHERE {filtro}\n'
    return (patron + 'WITH collect(r) AS copias\nFOREACH (x IN tail(copias) | DELETE x);\n'
            + patron + 'WITH count(r) AS n\n'
            "CALL apoc.util.validate(n <> 1, 'reposicion-071: la deduplicacion deja %d copias', [n])\nRETURN n;\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description='Spec 071: MERGE de las aristas conservadas que hay que reponer')
    ap.add_argument('--root', type=Path, default=ROOT, help='raíz privada de la auditoría (census.sqlite)')
    ap.add_argument('--out', type=Path, help='fichero de salida fuera del repo (por defecto, stdout)')
    args = ap.parse_args(argv)
    census = args.root / 'census.sqlite'
    if not census.is_file() or not census.stat().st_size:
        print(f'reposicion-071: falta el censo de la auditoría en {args.root}', file=sys.stderr)
        return 2
    if args.out and (args.out.resolve() == REPO or REPO in args.out.resolve().parents):
        print('reposicion-071: la salida lleva citas del censo y no puede quedar dentro del repo', file=sys.stderr)
        return 2
    conn = sqlite3.connect(census.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        filas = {uid: conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
                 for uid in UNIDADES + tuple(u for retirada, conservada, _ in DEDUPLICAR for u in (retirada, conservada))}
    finally:
        conn.close()
    faltan = [uid[:8] for uid, fila in filas.items() if fila is None or fila[0] != 'edge']
    if faltan:
        print(f'reposicion-071: no están en el censo como aristas: {", ".join(faltan)}; no se emite nada',
              file=sys.stderr)
        return 1
    try:
        texto = ''.join(f'// {uid[:8]}\n' + merge(json.loads(filas[uid][1])) for uid in UNIDADES)
        texto += ''.join(f'// deduplicación: {retirada[:8]} (gemela de {conservada[:8]})\n'
                         + deduplicar(json.loads(filas[retirada][1]), json.loads(filas[conservada][1]), resultante)
                         for retirada, conservada, resultante in DEDUPLICAR)
    except ValueError as exc:
        print(f'reposicion-071: {exc}; no se emite nada', file=sys.stderr)
        return 1
    if args.out:
        args.out.write_text(texto, encoding='utf-8')
    else:
        sys.stdout.write(texto)
    return 0


if __name__ == '__main__':
    sys.exit(main())
