"""The step «Nivel del paso largo» of `checks.yml` (spec 076): what the long pipeline step does.

Writes `nivel=omitir|rapido|completo` to `$GITHUB_OUTPUT` from what the scope step (`ci_scope.py`) left, comparing
exact words (an expression of the workflow would ignore case). The workflow skips the long step only on `omitir` and
passes the word to the tests as `SM_NIVEL`; the tests marked `@lento` skip themselves only on the exact `rapido`.
Stdlib only.
"""
from __future__ import annotations

import os
import sys


def tier_from_scope(outcome, pesado, lento) -> str:
    """`omitir` only for a scope step that succeeded and wrote `pesado=false` and `lento=false`; `rapido` only for
    one that succeeded and wrote `pesado=true` and `lento=false`. Anything else (a missing output, another spelling,
    a failed step) is `completo`."""
    if (outcome, lento) == ('success', 'false') and pesado in ('true', 'false'):
        return 'omitir' if pesado == 'false' else 'rapido'
    return 'completo'


def main() -> int:
    tier = tier_from_scope(os.environ.get('ALCANCE'), os.environ.get('PESADO'), os.environ.get('LENTO'))
    print(f'nivel: {tier}')
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
        handle.write(f'nivel={tier}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
