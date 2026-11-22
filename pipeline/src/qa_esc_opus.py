"""Lector Claude headless de la auditoría escalonada (spec 063-B): Opus 5.5 lee y arbitra con búsqueda.

Un solo `run_claude` sirve al lector (`opus`/`opus-qc`) y al árbitro (`opus-arb`, `qa_esc_arbitrate`),
que sólo se diferencian en el esfuerzo, el BRIEF y el esquema: con `tools_prefix` el lector puede
ejecutar `Bash` y SÓLO comandos que empiecen por ese prefijo (la ruta absoluta de `qa_esc_ev.py`); sin
él va con `--tools ""` y cualquier intento de herramienta anula la lectura. Fable salió del flujo el
2026-09-23 (addendum 063-B): sus lecturas `fable-esc` quedan en el ledger como histórico. El comando
y el parseo vienen del `run_fable` histórico de la 061 (`git show acfeffe:pipeline/src/qa_dual_readers.py`),
y el lanzamiento, el guardián de procesos y el recibo se reutilizan tal cual de `qa_dual_readers`.

**Formato de salida: `stream-json`.** `--output-format json` entrega `structured_output`, `modelUsage`,
`permission_denials`, `num_turns` y `total_cost_usd`, pero NO los comandos Bash que el lector llegó a
ejecutar: sólo lista los denegados (verificado el 2026-09-21 contra `claude 2.1.278`). El contrato exige
`searches[]` con los comandos ejecutados y un tope de 12, así que hace falta el flujo de eventos, donde
cada llamada aparece como un bloque `tool_use` del evento `assistant` y el evento `result` final trae
exactamente los mismos campos que el formato `json`. `stream-json` exige además `--verbose`.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time

from .ontologia import ontologia
from .qa_dual_readers import QUOTA, _finish, _spawn, reader_guard  # noqa: F401  (los reexporta el orquestador)
from .qa_esc_schema import MAX_SEARCHES
from .qa_integral import canonical, digest, now, write_new

# `claude` hereda la sesión del conductor si no se limpia el entorno; `--bare` saltaría el llavero (401).
ENV_STRIP = ('ANTHROPIC_API_KEY', 'CLAUDECODE', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_CHILD_SESSION',
             'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_PID',
             'CLAUDE_CODE_BRIDGE_SESSION_ID')
MODELS = {'opus': 'claude-opus-5-5'}
# Lados de la 063 que lee este módulo (los de Astra y Jev viven en sus propios módulos).
SIDES = ('opus', 'opus-qc', 'opus-arb')
EFFORT_RANK = {'low': 0, 'medium': 1, 'high': 2, 'xhigh': 3, 'max': 4}
MIN_EFFORT = 'high'  # el dueño pidió esfuerzo alto o muy alto: nunca se lee por debajo
DEFAULT_EFFORTS = {'critico': 'xhigh', 'alto': 'high', 'medio': 'high', 'bajo': 'high'}
OUTPUT_FORMAT = 'stream-json'
# El CLI entrega la salida estructurada como una llamada a herramienta: no es una herramienta del lector.
INTERNAL_TOOLS = {'StructuredOutput'}
# Credenciales o sesión caída: no hay nada que reintentar, el bucle debe parar.
CONFIG = re.compile(r'\b401\b|not logged in|invalid api key|authentication_error|please run .?claude login',
                    re.IGNORECASE)


def expected_effort(riesgo):
    """Esfuerzo por defecto por riesgo: «xhigh» en crítico, «high» en el resto (el estado del runner puede subirlo)."""
    key = str(riesgo or '').strip().lower().replace('í', 'i')
    return DEFAULT_EFFORTS.get(key, MIN_EFFORT)


def floor_effort(effort):
    """Lo pedido nunca baja de «high» (AC2): un valor desconocido o menor se sube al suelo."""
    if effort not in EFFORT_RANK or EFFORT_RANK[effort] < EFFORT_RANK[MIN_EFFORT]:
        return MIN_EFFORT
    return effort


def effort_ok(actual, expected):
    """True si el esfuerzo con el que se leyó no es menor que el esperado."""
    if actual not in EFFORT_RANK or expected not in EFFORT_RANK:
        return False
    return EFFORT_RANK[actual] >= EFFORT_RANK[expected]


def command(system, model, effort, schema, *, tools_prefix=None, budget_usd=None, extra_env=None,
            fmt=OUTPUT_FORMAT):
    """El comando completo, con el entorno limpiado y la herramienta acotada al prefijo (o ninguna)."""
    cmd = ['env']
    for name in ENV_STRIP:
        cmd += ['-u', name]
    for name, value in sorted((extra_env or {}).items()):
        cmd.append(f'{name}={value}')
    cmd += ['claude', '-p', '--no-session-persistence', '--permission-prompts', 'none',
            '--strict-mcp-config', '--setting-sources', '', '--system-prompt', system,
            '--model', model, '--effort', effort, '--json-schema', canonical(schema),
            '--output-format', fmt]
    if fmt == 'stream-json':
        cmd.append('--verbose')
    if budget_usd is not None:
        cmd += ['--max-budget-usd', str(budget_usd)]
    if tools_prefix:
        cmd += ['--tools', 'Bash', '--allowedTools', f'Bash({tools_prefix}:*)']
    else:
        cmd += ['--tools', '']
    return cmd


def _tool_use(block):
    return {'id': block.get('id'), 'name': block.get('name'),
            'command': str((block.get('input') or {}).get('command') or '')}


def _denial(entry):
    return {'id': entry.get('tool_use_id'), 'tool': entry.get('tool_name'),
            'command': str((entry.get('tool_input') or {}).get('command') or '')}


def _from_result(data, out):
    out['result'] = data.get('structured_output') if isinstance(data.get('structured_output'), dict) else None
    out['model_usage'] = data.get('modelUsage') or {}
    out['num_turns'] = data.get('num_turns')
    out['cost_usd'] = data.get('total_cost_usd')
    out['stop_reason'] = data.get('stop_reason')
    out['denials'] = [_denial(d) for d in (data.get('permission_denials') or [])]
    if data.get('is_error'):
        out['error'] = str(data.get('subtype') or data.get('result') or 'reader-error')


def parse_output(text, fmt=OUTPUT_FORMAT):
    """Lo que el CLI deja en stdout: decisión, consumo, denegaciones y herramientas usadas.

    Con `json` no hay bloques `tool_use` (el CLI sólo publica los denegados), así que `tool_uses`
    queda vacío; con `stream-json` recoge cada llamada del lector en orden.
    """
    out = {'result': None, 'model_usage': {}, 'num_turns': None, 'cost_usd': None, 'stop_reason': None,
           'denials': [], 'tool_uses': [], 'error': None}
    if fmt == 'stream-json':
        seen_result = False
        for line in (text or '').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get('type')
            if kind == 'assistant':
                for block in (event.get('message') or {}).get('content') or []:
                    if block.get('type') == 'tool_use':
                        out['tool_uses'].append(_tool_use(block))
            elif kind == 'result':
                _from_result(event, out)
                seen_result = True
        if not seen_result:
            out['error'] = out['error'] or 'missing-or-invalid-json'
        return out
    try:
        _from_result(json.loads(text or ''), out)
    except json.JSONDecodeError:
        out['error'] = 'missing-or-invalid-json'
    return out


def resolve_model(model_usage, model):
    """El id que facturó la lectura: `--model` acepta alias («opus», «sonnet») y `modelUsage` va por id."""
    if model in (model_usage or {}):
        return model
    keys = list(model_usage or {})
    return keys[0] if len(keys) == 1 else None


def usage_of(model_usage, model):
    """El consumo del modelo que leyó; un paquete nuevo se factura como `cacheCreation`, no como input."""
    used = (model_usage or {}).get(resolve_model(model_usage, model)) or {}
    if not used:
        return None
    return {'input': used.get('inputTokens'), 'output': used.get('outputTokens'),
            'cached': used.get('cacheReadInputTokens'), 'cache_write': used.get('cacheCreationInputTokens'),
            'reasoning': used.get('thinkingTokens')}


def _attempt(attempt_dir, side, model, cmd, stdin_text, effort, schema):
    """Como `qa_dual_readers._attempt`, pero el modelo lo elige el llamador."""
    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    write_new(attempt_dir / 'command.json', cmd)
    with gzip.open(attempt_dir / 'input.txt.gz', 'wt') as f:
        f.write(stdin_text)
    # `brief_sha256` y `pack_sha256` los estampa el llamador (`stamp`), el único que los tiene.
    return {'attempt_id': attempt_dir.name, 'side': side, 'model': model, 'effort': effort,
            'schema_sha256': digest(schema), 'input_sha256': digest(stdin_text),
            'brief_sha256': None, 'pack_sha256': None, 'ontologia_version': ontologia().version,
            'started_at': now()}


_PUNCT = {';', '&', '&&', '|', '||', '(', ')', '<', '>', '>>', '<<', '|&', ';;'}


def shell_tokens(command):
    """Tokens de shell (comillas resueltas, operadores como tokens propios) o None si no parsea."""
    try:
        lexer = shlex.shlex(str(command or ''), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def is_plain_search(command, tools_prefix):
    """True si el comando es SOLO una llamada al buscador: `python3 <ruta permitida> args…` sin
    ningún operador de shell (`;`, `&`, `|`, redirecciones, subshells, backticks, `$(`), decidido
    sobre los TOKENS reales del shell y no sobre el texto — una comilla falsa no puede esconder
    un `; ls /` (revisión de seguridad 2026-09-21)."""
    if not tools_prefix:
        return False
    tokens = shell_tokens(command)
    prefix = shell_tokens(tools_prefix)
    if not tokens or not prefix or tokens[:len(prefix)] != prefix:
        return False
    for token in tokens[len(prefix):]:
        if token in _PUNCT or '`' in token or '$(' in token or token.startswith(('<', '>')):
            return False
    return True


# Separadores entre búsquedas que el lector encadena (`ev.py 'a' ; ev.py 'b'`): cada tramo se juzga solo.
_CHAIN = {';', '&&'}


def search_calls(command, tools_prefix):
    """Las búsquedas de un comando: una por tramo separado por `;` o `&&`, o None si algún tramo no es
    una búsqueda limpia. Encadenar dos búsquedas no amplía lo que el lector puede hacer (el piloto
    anuló 4 lecturas de Opus sólo por eso); cualquier otro operador sigue anulando."""
    tokens = shell_tokens(command)
    if not tokens or not tools_prefix:
        return None
    if tokens[-1] == ';':
        tokens = tokens[:-1]
    segments, current = [], []
    for token in tokens + [';']:
        if token not in _CHAIN:
            current.append(token)
            continue
        if not current:
            return None
        segments.append(shlex.join(current))
        current = []
    if not all(is_plain_search(seg, tools_prefix) for seg in segments):
        return None
    return [command] if len(segments) == 1 else segments


def _void(parsed, tools_prefix):
    """Motivo por el que la lectura no vale, y las herramientas ajenas para el recibo.

    Prioridad: herramienta ajena (o Bash fuera del prefijo) → denegación → tope de búsquedas.
    Un comando compuesto (`python3 ev.py 'x'; ls /`) empieza por el prefijo pero el CLI lo deniega:
    cae en «permission-denied», que también anula.
    """
    foreign, searches = [], []
    denied = {d['id'] for d in parsed['denials'] if d.get('id')}
    for call in parsed['tool_uses']:
        if call['name'] in INTERNAL_TOOLS:
            continue
        if call['name'] != 'Bash':
            foreign.append(call['name'])
        elif (calls := search_calls(call['command'], tools_prefix)) is None:
            foreign.append(f"Bash: {call['command'][:120]}")
        elif call['id'] not in denied:
            searches.extend(calls)
    error = None
    if foreign:
        error = 'unexpected-tool-use'
    elif parsed['denials']:
        error = 'permission-denied'
    elif len(searches) > MAX_SEARCHES:
        error = 'tool-budget-exceeded'
    return error, foreign, searches


def run_claude(attempt_dir, side, model, effort, system, schema, input_text, *, tools_prefix=None,
               timeout=1800, budget_usd=None, extra_env=None, stamp=None, runner=_spawn,
               fmt=OUTPUT_FORMAT):
    """Una lectura de un pack por un lado Claude; devuelve `(decisión | None, recibo)`.

    `stamp` son los hashes que sólo conoce el llamador (`brief_sha256`, `pack_sha256`) y se funden en
    el recibo antes de escribirlo. El lector corre en un tmpdir vacío 0700 que se borra al terminar:
    no tiene repo, ni censo, ni CLAUDE.md que leer.
    """
    attempt_dir = Path(attempt_dir)
    cmd = command(system, model, effort, schema, tools_prefix=tools_prefix, budget_usd=budget_usd,
                  extra_env=extra_env, fmt=fmt)
    receipt = _attempt(attempt_dir, side, model, cmd, input_text, effort, schema)
    receipt.update(stamp or {})
    receipt['output_format'] = fmt
    stdout = attempt_dir / ('stdout.jsonl' if fmt == 'stream-json' else 'stdout.json')
    stderr = attempt_dir / 'stderr.txt'
    start = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix=f'esc-{side}-'))
    os.chmod(work, 0o700)
    try:
        code, error = runner(cmd, input_text, timeout, stdout, stderr, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    text = stdout.read_text() if stdout.exists() else ''
    parsed = parse_output(text, fmt)
    error = error or parsed['error']
    if error != 'reader-timeout':
        void, foreign, searches = _void(parsed, tools_prefix)
        error = void or error
    else:
        foreign, searches = [], []
    if error is None and parsed['result'] is None:
        error = 'missing-structured-output'
    if error is not None and error not in ('unexpected-tool-use', 'permission-denied', 'tool-budget-exceeded'):
        # Sólo en el camino de fallo: el texto de una lectura buena puede hablar de cuotas sin serlo.
        haystack = (stderr.read_text() if stderr.exists() else '') + '\n' + str(error)
        if CONFIG.search(haystack):
            error = f'config: {error}'
        elif QUOTA.search(haystack):
            error = 'quota'
    receipt.update({'cost_usd': parsed['cost_usd'], 'num_turns': parsed['num_turns'],
                    'searches': searches, 'denials': parsed['denials'], 'stop_reason': parsed['stop_reason'],
                    'model_resolved': resolve_model(parsed['model_usage'], model)})
    usage = usage_of(parsed['model_usage'], model)
    return _finish(attempt_dir, receipt, parsed['result'], start,
                   code if error is None else -1, error, usage, foreign)
