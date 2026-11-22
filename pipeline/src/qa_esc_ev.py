#!/usr/bin/env python3
"""Buscador de solo lectura sobre el corpus de la auditoría — la ÚNICA herramienta del lector (spec 063).

Copia stdlib de `specs/061-.../tools-T0e/ev.py` parametrizada por `QA_ESC_ROOT` (allí la raíz estaba
fija en el fuente) y sin ninguna escritura: el censo se abre `mode=ro` y las fuentes web sólo se leen.
Se ejecuta como script suelto —su ruta absoluta es el único prefijo que `--allowedTools` autoriza— así
que no importa nada del paquete y funciona desde un cwd vacío.

    python3 qa_esc_ev.py [-n MAX] [-w WIN] [--doc SUBSTR] [--nosrc] [--nocorpus] TERM [TERM ...]

Cada TERM es un regex (sin distinguir mayúsculas) y se busca en los trozos del corpus y en los cuerpos
web archivados; la salida es la misma que la de `ev.py` y es lo que el BRIEF describe al lector.
"""
import argparse
import contextlib
import glob
import io
import json
import os
from pathlib import Path
import re
import sqlite3

# Topes de la salida: por encima de ~30 k caracteres el CLI guarda el resultado en un fichero y el
# lector intenta leerlo con `cat` (lectura anulada en el piloto del 22-sep).
MAX_LIMIT, MAX_WINDOW, MAX_OUTPUT_CHARS = 6, 500, 20000

# Misma raíz que `tools-T0e/ev.py`, derivada del fichero para no depender del cwd.
DEFAULT_ROOT = str(Path(__file__).resolve().parents[1] / 'data/audit/dual/20260909-20260909-qa061')


def root():
    return os.environ.get('QA_ESC_ROOT') or DEFAULT_ROOT


def load_chunks(base, doc=None):
    conn = sqlite3.connect('file:' + base + '/census.sqlite?mode=ro', uri=True)
    try:
        query = 'select doc_id,page_start,page_end,text from chunks' + (' where doc_id like ?' if doc else '')
        return list(conn.execute(query, ((f'%{doc}%',) if doc else ())))
    finally:
        conn.close()


def load_sources(base):
    sources = []
    for path in glob.glob(base + '/sources/*.json'):
        with open(path) as f:
            source = json.load(f)
        if source.get('status') != 'accessible':
            continue
        pages = source.get('pages') or {}
        text = ' '.join(str(v) for k, v in sorted(pages.items(),
                                                  key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0))
        sources.append((source['url'], text))
    return sources


def norm(text):
    return re.sub(r'\s+', ' ', text)


def hits(rx, haystacks, label, limit, window):
    """`limit` ventanas como mucho en total, en el orden en que aparecen las fuentes."""
    found = 0
    for head, text in haystacks:
        for m in rx.finditer(text):
            start, end = max(0, m.start() - window), min(len(text), m.end() + window)
            print(f'  [{label} {head}] …{norm(text[start:end])}…')
            found += 1
            if found >= limit:
                return found
    return found


def search(terms, base=None, limit=4, window=220, doc=None, nosrc=False, nocorpus=False):
    base = base or root()
    chunks = [] if nocorpus else [(f'{d} p.{p1}-{p2}', t) for d, p1, p2, t in load_chunks(base, doc)]
    sources = [] if nosrc else load_sources(base)
    for term in terms:
        rx = re.compile(term, re.I | re.S)
        print(f'\n######## {term}')
        if not nocorpus and not hits(rx, chunks, 'corpus', limit, window):
            print('  [corpus] 0 hits')
        if not nosrc and not hits(rx, sources, 'web', limit, window):
            print('  [web] 0 hits')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('-n', type=int, default=4, dest='limit')
    ap.add_argument('-w', type=int, default=220, dest='window')
    ap.add_argument('--doc', default=None)
    ap.add_argument('--nosrc', action='store_true')
    ap.add_argument('--nocorpus', action='store_true')
    ap.add_argument('terms', nargs='+')
    a = ap.parse_args(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        search(a.terms, limit=min(a.limit, MAX_LIMIT), window=min(a.window, MAX_WINDOW), doc=a.doc,
               nosrc=a.nosrc, nocorpus=a.nocorpus)
    text = out.getvalue()
    if len(text) > MAX_OUTPUT_CHARS:
        text = (text[:MAX_OUTPUT_CHARS] + f'\n[… salida recortada a {MAX_OUTPUT_CHARS} caracteres: '
                'acota con --doc, menos términos o -n/-w más pequeños]\n')
    print(text, end='')


if __name__ == '__main__':
    main()
