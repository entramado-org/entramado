#!/usr/bin/env python3
"""Spec 070 (AC1): the public tree never pairs a court pseudonym with the company or public body that
was withdrawn from it, nor with an alias outside the allowlist.

    python3 pipeline/scripts/seudonimos_check.py                 # this tree; exit 1 on any finding
    python3 pipeline/scripts/seudonimos_check.py --repo DIR      # another tree (e.g. an export of main)
    python3 pipeline/scripts/seudonimos_check.py --snapshot DIR  # a graph export (nodes/relationships.jsonl)
                                                                 # against the allowlist: Path A gate

Runs `pipeline/src/seudonimos.py` (stdlib + PyYAML: no Neo4j, no pipeline environment) over the
`deny`/`fix`/`alias_deny`/`splits` rules of every `relations/*.yaml|yml`, the rows of the public
corrections register, the prose of every `context/*.yaml` (no civil row names a pseudonym), the roster
(it names no civil) and `seudonimos_permitidos.yaml` itself. The report gives file:line and reason,
never the endpoints nor a retired alias: the CI log is public. An invalid YAML is reported by line and
column only, since the parser's message quotes the broken line; any other error, by its type only. The
tree mode needs git (the public documents are the ones git tracks under `data/`); without it, exit 1.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import seudonimos  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    try:
        return _comprobar(argv)
    except seudonimos.ErrorDeForma as err:   # a roster or an inventory YAML that cannot be read as such: exit 2
        for motivo in err.motivos:
            print(f'FORMA  {motivo}')
        return 2
    except SystemExit:
        raise   # the library's stops are already neutral (file, position, reason): their exit code goes through
    except Exception as err:   # anything else may quote the YAML: one line, no traceback (the CI log is public)
        print(f'seudonimos-check: error inesperado ({type(err).__name__}); sin detalle, el log del CI es público')
        return 1


def _comprobar(argv: list[str] | None) -> int:
    ap = argparse.ArgumentParser(description='Spec 070: seudónimos judiciales frente a la lista de permitidas')
    ap.add_argument('--repo', type=Path, default=seudonimos.REPO, help='raíz del árbol (por defecto, este repo)')
    ap.add_argument('--snapshot', type=Path, help='export del grafo a verificar (nodes.jsonl + relationships.jsonl)')
    args = ap.parse_args(argv)
    for clase, rel in (('SEUDONIMOS', seudonimos.PSEUDONYMS_REL), ('PERMITIDOS', seudonimos.PERMITIDOS_REL)):
        path = args.repo / rel
        if path.exists():
            try:
                seudonimos.leer_yaml(path.read_text(encoding='utf-8'), rel)
            except SystemExit as err:   # file and position, never the text
                print(f'{clase}  {err.code}')
                return 1
    if args.snapshot:
        ids = seudonimos.ids_seudonimos(args.repo / seudonimos.PSEUDONYMS_REL)
        data = seudonimos.cargar_permitidos(args.repo / seudonimos.PERMITIDOS_REL)
        # The allowlist is the reference: an invalid one (same validation as the tree check, public documents
        # included: this runs on the Mac, with git; without it, a neutral stop) would make the graph look compliant.
        publicos = seudonimos.documentos_publicos(args.repo)
        personas = seudonimos.personas_contexto(args.repo)
        civiles = seudonimos.indice_civiles(personas, ids, data)
        invalida = [motivo for _, _, motivo in seudonimos.validar_permitidos(data, ids, publicos=publicos,
                                                                              civiles=civiles)]
        for motivo in invalida:
            print(f'PERMITIDOS  {motivo}')
        if invalida:
            print(f'seudonimos-check: {seudonimos.PERMITIDOS_REL}: {len(invalida)} problemas; snapshot sin verificar')
            return 1
        cuentas = {}
        problemas = seudonimos.verificar_snapshot(args.snapshot, ids, data, cuentas, personas, publicos,
                                                  seudonimos.extremos_por_nombre(args.repo, ids), repo=args.repo)
        for problema in problemas:
            print(f'SNAPSHOT  {problema}')
        for aviso in cuentas['avisos']:   # informative (AC91): the loader resolves by id
            print(f'AVISO  {aviso}')
        print(f'seudonimos-check: {args.snapshot}: {cuentas["extremos_sin_nodo"]} extremo(s) de regla o postura '
              'escritos por nombre sin nodo en el export (informativo)')
        print(f'seudonimos-check: {args.snapshot}: {cuentas["org_sin_entity_id"]} nodo(s) Company/Organization sin '
              'entity_id (informativo)')
        print(f'seudonimos-check: {args.snapshot}: {len(data["aristas"])} aristas permitidas '
              f'({sum(1 for e in data["aristas"] if e.get("docs"))} acotadas por documento) y '
              f'{sum(len(e["permitidos"]) for e in data["alias"])} alias permitidos; {len(problemas)} problemas')
        return 1 if problemas else 0
    hallazgos = seudonimos.auditar(args.repo)
    # AC192: the context loader skips a relation without an https source by design (fail-closed 061): a notice with
    # its position and the count, on stderr — never silent, and never a finding.
    avisos = seudonimos.problemas_contexto(seudonimos.documentos_contexto(args.repo))[1]
    for aviso in avisos:
        print(f'AVISO  {aviso}', file=sys.stderr)
    if avisos:
        print(f'seudonimos-check: {len(avisos)} relación(es) de contexto sin fuente https: no se cargan (spec 061; '
              'aviso)', file=sys.stderr)
    for h in hallazgos:
        print(f'{h.fichero}:{h.linea}: {h.clase}: {h.motivo}')
    por_clase = ', '.join(f'{clase} {n}' for clase, n in sorted(Counter(h.clase for h in hallazgos).items()))
    print(f'seudonimos-check: {len(hallazgos)} hallazgos' + (f' ({por_clase})' if hallazgos else ''))
    return 1 if hallazgos else 0


if __name__ == '__main__':
    sys.exit(main())
