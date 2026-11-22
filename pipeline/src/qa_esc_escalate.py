"""Escalado dual de la 063-B: Astra ciega sobre el mismo pack que Opus, acuerdo dual y tandas del dueño.

Lo que el router manda `escalar` lo lee GPT-6-Astra por `codex exec` SOBRE EL MISMO PACK (mismo
`pack_sha256`) y sin ver la decisión de Opus. Astra sólo recibe herramienta si el estado lo pide
(`astra_needs_tools`/`astraTools`), y entonces corre en un tmpdir aislado que contiene
EXCLUSIVAMENTE el pack y una copia de `qa_esc_ev.py`: ni repo, ni censo, ni overlays (el buscador
llega al censo en solo lectura por `QA_ESC_ROOT`). Fable salió del dual el 2026-09-23 (addendum
063-B): sus lecturas `fable-esc` siguen en `esc_readings` como histórico y aquí no se leen nunca.

`dual_agreement` cierra una unidad sólo si Opus y Astra coinciden en veredicto, en clase de
etiqueta (`qa_esc_schema.LABEL_CLASS`), sin contradicción de evidencia y en la ACCIÓN sobre esa
unidad, y si las reescrituras afirman los MISMOS hechos (`asserted_facts`: importes normalizados,
fechas, términos procesales, atribuciones «según X (fecha)» y nombres propios). El acuerdo es por
unidad: un pack cierra las suyas aunque otras discrepen (`mode='parcial'`). Lo demás va al árbitro
(`qa_esc_arbitrate`); lo que el árbitro no puede cerrar va al dueño: `export_tanda` ordena las
unidades en los tres bloques de la ronda T0e (A veredicto · B acción y dudas · C unánime de riesgo
alto) y `merge_tanda_answers` convierte sus respuestas —o las del árbitro— en la decisión final validada.

Nada de esto toca Neo4j, el grafo ni producción: sólo el ledger de la 061 y `ROOT/esc/`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from .qa_dual import RISK_ORDER
from .qa_dual_readers import QUOTA, _spawn, run_astra as run_astra_blind
from .qa_esc import MACHINE_STATES
from .qa_esc_opus import is_plain_search, shell_tokens
from .qa_esc_schema import LABEL_CLASS, MAX_SEARCHES, aliases_to_retire, validate_decision
from .qa_integral import DUAL_MODELS, canonical, digest, now

ASTRA_SIDE = 'astra-esc'
OPUS_SIDES = ('opus', 'opus-qc')
# Los únicos lados cuyas lecturas cuentan: una fila `fable-esc` del piloto es histórico y se ignora.
READER_SIDES = (*OPUS_SIDES, ASTRA_SIDE)
JEV_SIDES = ('jev-class', 'jev-route')
ASTRA_MODEL = DUAL_MODELS['astra']
# `codex` sólo entiende estos tres esfuerzos: un `xhigh` de la 063 se lee como `high`.
CODEX_EFFORTS = ('low', 'medium', 'high')
EV_SOURCE = Path(__file__).resolve().with_name('qa_esc_ev.py')
_RISK = {'critico': -1, **RISK_ORDER}

# Evidencia que dice «esto no está en ninguna fuente» (la que obliga a darle herramienta a Astra).
NO_SOURCE = re.compile(r'0\s*aparici|0\s*hits|no\s+aparece|sin\s+fuente', re.I)
# Evidencia que SÍ cita: documento con página o atribución nominal fechada.
CITED = re.compile(r'p\.\s*\d+|seg[uú]n\s+[^()\n]{2,60}\(', re.I)

ASTRA_PREAMBLE = """Eres Astra, revisor editorial CIEGO de la auditoría escalonada 063 de El Entramado. No ves la decisión de
ningún otro lector y no debes intentar obtenerla. Trabajas en un directorio AISLADO que contiene sólo el paquete
(`pack.md`) y el buscador, con un shell de SOLO LECTURA y SIN RED: no puedes escribir ficheros ni consultar la web, y
no debes intentarlo.
La ÚNICA herramienta que tienes es el buscador del corpus:
    python3 {ev} [-n 6] [-w 300] [--doc SUBSTR] [--nosrc] [--nocorpus] 'regex' ['regex' …]
Busca cada término (regex, sin distinguir mayúsculas) en el corpus completo y en las fuentes web archivadas, y
devuelve ventanas de contexto con documento y página o URL. Máximo 8 búsquedas por pack: agrupa varios regex en una
llamada. Más de {max_searches} búsquedas, o CUALQUIER otro comando, anulan tu lectura entera. Antes de retirar un dato
busca siempre variantes (`\\s+` entre palabras, sin tildes, OCR, nombre comercial, cifra con y sin separadores).
No leas CLAUDE.md, skills, specs ni memoria del proyecto: el BRIEF que va a continuación es tu única instrucción y el
paquete tu único encargo. Lee el paquete entero y termina respondiendo ÚNICAMENTE con el JSON de decisión impuesto por
el esquema: todas las unidades del paquete exactamente una vez y `evidence` concreta y verificable en cada una."""


def pack_sha256(manifest):
    """El hash del manifest — idéntico a `qa_esc_packs.pack_sha256`, sin arrastrar el constructor."""
    return digest(manifest)


def astra_needs_tools(decision_opus):
    """True si la decisión escalada retira un dato o afirma que no hay fuente (AC7: Astra raciona búsqueda)."""
    if not isinstance(decision_opus, dict):
        return False
    for unit in decision_opus.get('units') or []:
        if unit.get('label') == 'retirada':
            return True
        if NO_SOURCE.search(str(unit.get('evidence') or '')):
            return True
    for key, field in (('summary', 'summary'), ('role', 'role')):
        if (decision_opus.get(key) or {}).get('action') == 'remove':
            return True
    return False


def _stamp(manifest, brief, stamp=None):
    """Los hashes que atan la lectura a ESTE pack; el llamador puede añadir o sobrescribir."""
    full = {'pack_id': manifest['pack_id'], 'pack_sha256': pack_sha256(manifest),
            'brief_sha256': digest(brief)}
    full.update(stamp or {})
    return full


def _check(result, manifest, receipt, validate=validate_decision, invalid='invalid-decision'):
    """Anula la lectura que no valida contra el esquema del pack (el recibo guarda por qué)."""
    errors = validate(result, manifest) if isinstance(result, dict) else ['sin resultado']
    receipt['validation_errors'] = errors
    if errors and receipt.get('error') is None:
        receipt['exit_code'], receipt['error'] = -1, invalid
    return None if receipt.get('error') else result


def _write_receipt(attempt_dir, receipt):
    path = Path(attempt_dir) / 'receipt.json'
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(canonical(receipt) + '\n')
    os.replace(temporary, path)


# ── Astra ─────────────────────────────────────────────────────────────────────

def astra_tools_command(work, schema_path, output, effort, root):
    """El comando de `tools-T0e/astra_t0e.py`, pero con `-C` en el tmpdir aislado y `QA_ESC_ROOT`.

    Diferencia con `qa_integral_runner.command`: aquí NO se deshabilita `shell_tool` (es la
    herramienta de búsqueda), y el sandbox `read-only` es lo que impide escribir y salir a la red.
    """
    return ['env', f'QA_ESC_ROOT={root}',
            'codex', 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
            '-C', str(work), '-s', 'read-only', '-m', ASTRA_MODEL,
            '-c', f'model_reasoning_effort="{effort}"', '-c', 'project_doc_max_bytes=0',
            '--disable', 'plugins', '--disable', 'apps', '--disable', 'multi_agent',
            '--disable', 'computer_use', '--disable', 'browser_use', '--disable', 'in_app_browser',
            '--disable', 'hooks', '--disable', 'view_image', '--disable', 'skill_search',
            '--enable', 'skip_host_skill_discovery', '-c', 'suppress_unstable_features_warning=true',
            '--output-schema', str(schema_path), '--output-last-message', str(output), '--json', '-']


def astra_effort(effort):
    """`codex` no conoce `xhigh`: el esfuerzo de la 063 se recorta al máximo que acepta."""
    return effort if effort in CODEX_EFFORTS else 'high'


_EV_CALL = re.compile(r'^\s*python3\s+(?:\./)?(?:\S*/)?qa_esc_ev\.py(?:\s|$)')
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_CHAINED = re.compile(r'[;&|`]|\$\(')


_SHELLS = re.compile(r'^(?:/bin/|/usr/bin/)?(?:zsh|bash|sh)$')


def unwrap_shell(command):
    """codex ejecuta cada comando como `/bin/zsh -lc "<cmd>"`: devuelve `<cmd>` SOLO si el comando
    entero son exactamente esos tres tokens de shell (una comilla que cierre antes de tiempo deja
    más tokens y no se desenvuelve); None si no parsea (revisión de seguridad 2026-09-21)."""
    tokens = shell_tokens(command)
    if tokens is None:
        return None
    if len(tokens) == 3 and _SHELLS.match(tokens[0]) and tokens[1] in ('-lc', '-c'):
        return tokens[2]
    return str(command or '')


def is_search(command):
    """True si el comando es una llamada al buscador `python3 …/qa_esc_ev.py args…` y nada más."""
    inner = unwrap_shell(command)
    if inner is None:
        return False
    tokens = shell_tokens(inner)
    if not tokens or len(tokens) < 2 or tokens[0] != 'python3' or not tokens[1].endswith('qa_esc_ev.py'):
        return False
    return is_plain_search(inner, f'python3 {tokens[1]}')


def _astra_events(path):
    events = []
    for line in (Path(path).read_text().splitlines() if Path(path).exists() else []):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _astra_parse(events):
    """Consumo, modelo resuelto, comandos ejecutados y herramientas ajenas del flujo de `codex --json`."""
    out = {'usage': None, 'model_resolved': None, 'searches': [], 'tool_calls': [], 'error': None}
    for event in events:
        kind = event.get('type')
        item = event.get('item') or {}
        if kind == 'thread.started':
            out['model_resolved'] = event.get('model') or out['model_resolved']
        elif kind == 'turn.completed':
            used = event.get('usage') or {}
            out['usage'] = {'input': used.get('input_tokens'), 'output': used.get('output_tokens'),
                            'cached': used.get('cached_input_tokens'),
                            'cache_write': used.get('cache_write_input_tokens'),
                            'reasoning': used.get('reasoning_output_tokens')}
        elif kind == 'item.completed':
            if item.get('type') == 'command_execution':
                out['searches'].append(str(item.get('command') or ''))
            elif item.get('type') not in ('agent_message', 'reasoning', 'error'):
                out['tool_calls'].append(str(item.get('type')))
        elif kind == 'error':
            out['error'] = out['error'] or event.get('message', 'runtime-error')
    return out


def _astra_void(parsed):
    """Motivo por el que la lectura con herramienta no vale: comando ajeno o presupuesto agotado."""
    foreign = [c for c in parsed['searches'] if not is_search(c)]
    if parsed['tool_calls'] or foreign:
        return 'unexpected-tool-use', parsed['tool_calls'] + [f'shell: {c[:120]}' for c in foreign]
    if len(parsed['searches']) > MAX_SEARCHES:
        return 'tool-budget-exceeded', []
    return None, []


def _run_astra_tools(attempt_dir, manifest, markdown, schema, brief, effort, stamp, root, timeout, runner,
                     side=ASTRA_SIDE, validate=validate_decision, invalid='invalid-decision'):
    """Astra con el buscador: tmpdir con SÓLO `pack.md` y `qa_esc_ev.py`, sandbox de solo lectura."""
    attempt_dir = Path(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='esc-astra-'))
    os.chmod(work, 0o700)
    (work / 'pack.md').write_text(markdown)
    shutil.copy(EV_SOURCE, work / EV_SOURCE.name)
    ev_path = work / EV_SOURCE.name
    schema_path, output = attempt_dir / 'schema.json', attempt_dir / 'result.json'
    schema_path.write_text(canonical(schema))
    effort = astra_effort(effort)
    cmd = astra_tools_command(work, schema_path, output, effort, root)
    preamble = ASTRA_PREAMBLE.format(ev=ev_path, max_searches=MAX_SEARCHES)
    stdin_text = f'{preamble}\n\n=== BRIEF ===\n\n{brief}\n\n=== PAQUETE ===\n\n{markdown}'
    receipt = {'attempt_id': attempt_dir.name, 'side': side, 'model': ASTRA_MODEL, 'effort': effort,
               'schema_sha256': digest(schema), 'input_sha256': digest(stdin_text),
               'output_format': 'codex-json', 'tools': True, 'started_at': now(),
               **_stamp(manifest, brief, stamp)}
    (attempt_dir / 'command.json').write_text(canonical(cmd) + '\n')
    (attempt_dir / 'input.txt').write_text(stdin_text)
    events_path, stderr_path = attempt_dir / 'events.jsonl', attempt_dir / 'stderr.txt'
    start = time.monotonic()
    try:
        code, error = runner(cmd, stdin_text, timeout, events_path, stderr_path, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    parsed = _astra_parse(_astra_events(events_path))
    error = error or parsed['error']
    foreign = []
    if error != 'reader-timeout':
        void, foreign = _astra_void(parsed)
        error = void or error
    try:
        result = json.loads(output.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        result, error = None, error or 'missing-or-invalid-json'
    if error is not None and error not in ('unexpected-tool-use', 'tool-budget-exceeded'):
        haystack = (stderr_path.read_text() if stderr_path.exists() else '') + '\n' + str(error)
        if QUOTA.search(haystack):
            error = 'quota'
    receipt.update({'exit_code': code if error is None else -1, 'error': error, 'usage': parsed['usage'],
                    'model_resolved': parsed['model_resolved'], 'searches': parsed['searches'],
                    'denials': [], 'tool_calls': foreign, 'cost_usd': None, 'num_turns': None,
                    'finding_sha256': digest(result) if result is not None else None,
                    'elapsed_seconds': round(time.monotonic() - start, 3), 'finished_at': now()})
    result = _check(result if receipt['error'] is None else None, manifest, receipt, validate, invalid)
    _write_receipt(attempt_dir, receipt)
    return result, receipt


def run_astra(attempt_dir, manifest, markdown, schema, brief, effort, tools=False, stamp=None, *,
              root=None, timeout=1800, runner=_spawn, side=ASTRA_SIDE, validate=validate_decision,
              invalid='invalid-decision'):
    """Una lectura de Astra sobre el pack; con `tools` recibe el buscador, sin ellas va ciega.

    Ciega delega en `qa_dual_readers.run_astra` (mismo comando del latido 061, shell deshabilitado)
    y luego normaliza el recibo a las claves de `qa_esc_opus`. `root` es la raíz de la auditoría:
    viaja como `QA_ESC_ROOT` para que el buscador abra `census.sqlite` en solo lectura. `side`,
    `validate` e `invalid` dejan reutilizarla con otro esquema (la validación `astra-val` de la 063-B).
    """
    attempt_dir = Path(attempt_dir)
    if tools:
        return _run_astra_tools(attempt_dir, manifest, markdown, schema, brief, astra_effort(effort),
                                stamp, root, timeout, runner, side, validate, invalid)
    effort = astra_effort(effort)
    result, receipt = run_astra_blind(attempt_dir, manifest, brief, schema, markdown, effort=effort,
                                      timeout=timeout, transport=runner)
    parsed = _astra_parse(_astra_events(attempt_dir / 'events.jsonl'))
    receipt.update({'side': side, 'model': ASTRA_MODEL, 'output_format': 'codex-json', 'tools': False,
                    'model_resolved': parsed['model_resolved'], 'searches': [], 'denials': [],
                    'cost_usd': None, 'num_turns': None, **_stamp(manifest, brief, stamp)})
    if parsed['searches'] and receipt.get('error') is None:
        # El comando del latido deshabilita el shell: si aparece uno, la lectura no es la pactada.
        receipt['exit_code'], receipt['error'] = -1, 'unexpected-tool-use'
        receipt['tool_calls'] = [f'shell: {c[:120]}' for c in parsed['searches']]
        result = None
    if receipt.get('error') and QUOTA.search(str(receipt.get('error'))):
        receipt['error'] = 'quota'
    result = _check(result, manifest, receipt, validate, invalid)
    _write_receipt(attempt_dir, receipt)
    return result, receipt


def run_dual(root, pack_id, manifest, markdown_blind, markdown_tools, schema, brief, effort,
             astra_tools='auto', opus_decision=None, *, timeout=1800, runner=_spawn, sides=None):
    """Astra sobre el MISMO pack que leyó Opus; devuelve `(resultado, recibo)` por lado.

    Nunca lanza: lo que falle viaja en el recibo (el orquestador decide si reintenta o manda el pack
    al árbitro como `lado-sin-lectura-valida`). `astra_tools='auto'` consulta `astra_needs_tools(opus_decision)`.
    """
    root = Path(root)
    tools = {'always': True, 'never': False}.get(astra_tools, astra_needs_tools(opus_decision))
    stamp = _stamp(manifest, brief, {'pack_id': pack_id})
    attempts = root / 'esc' / 'attempts'
    jobs = {
        ASTRA_SIDE: lambda d: run_astra(d, manifest, markdown_tools if tools else markdown_blind, schema,
                                        brief, effort, tools=tools, stamp=stamp, root=root,
                                        timeout=timeout, runner=runner),
    }

    def read(side):
        attempt_dir = attempts / uuid.uuid4().hex
        try:
            return jobs[side](attempt_dir)
        except Exception as exc:                                        # noqa: BLE001 — el fallo es dato
            return None, {'attempt_id': attempt_dir.name, 'side': side, 'exit_code': -1,
                          'error': f'{type(exc).__name__}: {exc}', 'started_at': now(),
                          'finished_at': now(), **stamp}

    # `sides` acota a los lados que faltan (el orquestador reutiliza las lecturas válidas ya pagadas).
    jobs = {side: job for side, job in jobs.items() if not sides or side in sides}
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {side: pool.submit(read, side) for side in jobs}
    out = {side: future.result() for side, future in futures.items()}
    for side, (_, receipt) in out.items():
        receipt.setdefault('tools', tools if side == ASTRA_SIDE else False)
    return out


# ── hechos afirmados y acuerdo dual ──────────────────────────────────────────

_ACCENTS = str.maketrans('áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')
MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7,
          'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
MAGNITUDES = {'mil': 1000, 'millon': 10 ** 6, 'millones': 10 ** 6, 'millardo': 10 ** 9,
              'millardos': 10 ** 9, 'mil millones': 10 ** 9}
CURRENCIES = {'€': 'eur', 'eur': 'eur', 'euro': 'eur', 'euros': 'eur', '$': 'usd', 'usd': 'usd',
              'dolar': 'usd', 'dolares': 'usd'}
AMOUNT = re.compile(r'(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*'
                    r'(mil millones|millones|millon|millardos|millardo|mil)?\s*(?:de\s+)?'
                    r'(€|\$|euros|euro|dolares|dolar|usd|eur)?')
DATE_ISO = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
DATE_DMY = re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b')
DATE_TEXT = re.compile(r'\b(\d{1,2})\s+de\s+(' + '|'.join(MONTHS) + r')\s+de\s+(\d{4})\b')
PROCESAL = re.compile(r'\b(investigad|imputad|procesad|acusad|condenad|absuelt|archivad|sobrese)\w*'
                      r'(\s+no\s+firmes?)?')
SEGUN = re.compile(r'seg[uú]n\s+([^()\n]{2,60}?)\s*\(([^)\n]{3,40})\)', re.I)
_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][\wÁÉÍÓÚÜÑáéíóúüñ'’-]*")
CONNECTORS = {'de', 'del', 'la', 'las', 'los', 'y', 'e', 'van', 'von', 'da', 'di', 'do'}
# Mayúsculas que abren frase o cláusula y no son nombre propio.
STOP_CAPS = {'segun', 'el', 'la', 'los', 'las', 'en', 'de', 'del', 'un', 'una', 'unos', 'unas', 'tras',
             'durante', 'al', 'a', 'este', 'esta', 'esto', 'estos', 'su', 'sus', 'no', 'si', 'como',
             'desde', 'hasta', 'para', 'por', 'con', 'sin', 'fue', 'era', 'es', 'the', 'tambien',
             'ademas', 'pero', 'cuando', 'mientras', 'aunque', 'ambos', 'entre', 'sobre', 'ni', 'o',
             'que', 'se', 'ha', 'han', 'habia'}


def _norm(text):
    return re.sub(r'\s+', ' ', str(text or '').translate(_ACCENTS)).strip().lower()


def _amounts(text):
    """Importes con su magnitud y su moneda; un número pelado (páginas, años) NO es un importe."""
    out = set()
    for number, magnitude, currency in AMOUNT.findall(text):
        if not magnitude and not currency and '.' not in number:
            continue
        clean = number
        if '.' in clean and ',' in clean:
            clean = clean.replace('.', '').replace(',', '.')
        elif '.' in clean:
            clean = clean.replace('.', '')
        else:
            clean = clean.replace(',', '.')
        try:
            value = float(clean) * MAGNITUDES.get(magnitude, 1)
        except ValueError:
            continue
        token = f'importe:{int(value)}' if value == int(value) else f'importe:{value:.2f}'
        out.add(f'{token}:{CURRENCIES[currency]}' if currency else token)
    return out


def _dates(text):
    out = {f'fecha:{y}-{m}-{d}' for y, m, d in DATE_ISO.findall(text)}
    out |= {f'fecha:{y}-{int(m):02d}-{int(d):02d}' for d, m, y in DATE_DMY.findall(text)}
    out |= {f'fecha:{y}-{MONTHS[m]:02d}-{int(d):02d}' for d, m, y in DATE_TEXT.findall(text)}
    return out


def _flush(run):
    while run and _norm(run[-1]) in CONNECTORS:
        run.pop()
    if sum(1 for w in run if w[0].isupper()) < 2:
        return set()
    return {'nombre:' + _norm(' '.join(run))}


def _names(text):
    """Secuencias de ≥2 palabras capitalizadas (con conectores «de/del/y»), sin las que abren frase.

    Cualquier signo entre dos palabras corta la secuencia: «Según Infobae, Koldo García…» son dos
    cosas distintas, no un nombre de cuatro palabras.
    """
    out, run, end = set(), [], None
    for match in _WORD.finditer(text):
        if end is not None and text[end:match.start()].strip():
            out |= _flush(run)
            run = []
        end = match.end()
        word = match.group(0)
        low = _norm(word)
        if word[0].isupper() and low not in STOP_CAPS:
            run.append(word)
        elif run and low in CONNECTORS:
            run.append(word)
        else:
            out |= _flush(run)
            run = []
    return out | _flush(run)


def asserted_facts(text):
    """Los hechos que un texto AFIRMA, normalizados, para comparar dos redacciones distintas.

    Determinista y sin modelo. Reglas (una etiqueta por hecho):
      · `importe:<n>[:<moneda>]` — «1,4 millones» → 1400000, «5.700 €»/«5700 euros» → 5700:eur;
        un número sin magnitud, moneda ni separador de millar (páginas, años) no es un importe.
      · `fecha:<AAAA-MM-DD>` — dd-mm-aaaa, dd/mm/aaaa, «20 de junio de 2026» y aaaa-mm-dd.
      · `proc:<término>` — investigad/imputad/procesad/acusad/condenad[-no-firme]/absuelt/archivad/sobrese.
      · `segun:<medio>:<fecha>` — atribución «según <Medio> (<fecha>)», con el medio normalizado.
      · `nombre:<nombre propio>` — ≥2 palabras capitalizadas seguidas (personas y organizaciones).
    Dos reescrituras con los mismos hechos difieren sólo en redacción (AC7).
    """
    text = str(text or '')
    flat = _norm(text)
    facts = _amounts(flat) | _dates(flat) | _names(text)
    for stem, firme in PROCESAL.findall(flat):
        facts.add(f'proc:{stem}-no-firme' if firme.strip() else f'proc:{stem}')
    for outlet, when in SEGUN.findall(text):
        dates = sorted(_dates(_norm(when)))
        facts.add(f'segun:{_norm(outlet)}:{dates[0][6:] if dates else _norm(when)}')
    return facts


def decision_facts(decision):
    """Los hechos y las acciones que una decisión afirma: reescrituras, retiradas y acciones de entidad."""
    summary, role = decision.get('summary') or {}, decision.get('role') or {}
    estado = decision.get('estado') or {}
    facts = {f'accion:summary:{summary.get("action", "none")}',
             f'accion:role:{role.get("action", "none")}',
             f'accion:estado:{estado.get("action", "none")}:{estado.get("status") or ""}',
             'accion:alias:' + canonical(sorted(str(a) for a in decision.get('alias_deny') or []))}
    if summary.get('action') == 'replace':
        facts |= asserted_facts(summary.get('resumen'))
    if role.get('action') == 'replace':
        facts |= asserted_facts(role.get('role'))
    for unit in decision.get('units') or []:
        if unit.get('label') == 'retirada':
            facts.add(f'retirada:{unit.get("unit_id")}')
    for edge in decision.get('edges') or []:
        if edge.get('action') and edge['action'] != 'keep':
            facts.add(f'arista:{edge.get("unit_id")}:{edge["action"]}')
    for item in decision.get('editorial') or []:
        if item.get('action') and item['action'] != 'keep':
            facts.add(f'editorial:{item.get("unit_id")}:{item["action"]}')
    return facts


def _unit_of(decision, unit_id):
    return next((u for u in decision.get('units') or [] if u.get('unit_id') == unit_id), None)


def restrict_pack(pack, unit_ids):
    """El mismo pack con `units` acotadas: lo que `validate_decision` ve en un cierre parcial."""
    keep = set(unit_ids or [])
    return {**pack, 'units': [u for u in pack['units'] if u['unit_id'] in keep]}


# Acciones de entidad y las unidades del pack que las ANCLAN (el `estado` ancla por etiqueta).
ANCHOR_KINDS = {'summary': ('summary', 'claim'), 'role': ('attribute',), 'alias_deny': ('identity',)}
PROSE_FIELD = {'summary': 'resumen', 'role': 'role'}


def _entity_actions(decision):
    """Qué acciones de entidad propone la decisión (las que hay que anclar en unidades cerradas)."""
    summary, role = decision.get('summary') or {}, decision.get('role') or {}
    estado = decision.get('estado') or {}
    return {'summary': (summary.get('action') or 'none') != 'none',
            'role': (role.get('action') or 'none') != 'none',
            'alias_deny': bool([a for a in decision.get('alias_deny') or [] if str(a).strip()]),
            'estado': (estado.get('action') or 'none') != 'none'}


def _anchors(pack, decision, group):
    """Las unidades del pack de las que cuelga esa acción de entidad."""
    if group == 'estado':
        labelled = {u.get('unit_id') for u in decision.get('units') or [] if u.get('label') == 'estado'}
        return [u['unit_id'] for u in pack['units'] if u['unit_id'] in labelled]
    return [u['unit_id'] for u in pack['units'] if u.get('kind') in ANCHOR_KINDS[group]]


def _blank_block(group):
    return {'summary': _none_summary, 'role': _none_role, 'alias_deny': list,
            'estado': _none_estado}[group]()


def _prose_key(decision, field):
    """La prosa propuesta, por HECHOS afirmados: dos redacciones de lo mismo dan la misma clave."""
    block = decision.get(field) or {}
    action = block.get('action') or 'none'
    if action == 'none':
        return f'{field}:none'
    return f'{field}:{action}:' + digest(sorted(asserted_facts(block.get(PROSE_FIELD[field]))))[:8]


def _edge_key(decision, unit_id):
    entry = next((e for e in decision.get('edges') or [] if e.get('unit_id') == unit_id), None) or {}
    action = entry.get('action') or 'keep'
    if action == 'keep':
        return 'arista:keep'
    return f'arista:{action}:' + '/'.join(_norm(entry.get(key)) for key in
                                          ('new_type', 'prop', 'value', 'status'))


def _editorial_key(decision, unit_id):
    entry = next((e for e in decision.get('editorial') or [] if e.get('unit_id') == unit_id), None) or {}
    action = entry.get('action') or 'keep'
    if action == 'keep':
        return 'editorial:keep'
    return f'editorial:{action}:' + digest(sorted(asserted_facts(entry.get('text'))))[:8]


def _alias_key(decision):
    return 'alias_deny:' + ','.join(sorted(_norm(a) for a in decision.get('alias_deny') or []
                                           if str(a).strip()))


def _estado_key(decision):
    estado = decision.get('estado') or {}
    if (estado.get('action') or 'none') == 'none':
        return 'estado:none'
    return f'estado:set:{_norm(estado.get("status"))}/{_norm(estado.get("tipo"))}'


def _action_key(decision, unit, estado):
    """Lo que ESTA lectura hace con ESTA unidad, comparable entre lectores e imprimible (AC7)."""
    kind, uid = unit.get('kind'), unit['unit_id']
    if kind in ('summary', 'claim'):
        key = _prose_key(decision, 'summary')
    elif kind == 'attribute':
        key = _prose_key(decision, 'role')
    elif kind == 'identity':
        key = _alias_key(decision)
    elif kind == 'edge':
        key = _edge_key(decision, uid)
    elif kind == 'editorial':
        key = _editorial_key(decision, uid)
    else:
        key, estado = '', True
    return ' · '.join(part for part in (key, _estado_key(decision) if estado else '') if part)


def _raw_text(decision, unit):
    """El texto LITERAL propuesto para la unidad: distingue `exacto` de acuerdo con otra redacción."""
    kind, uid = unit.get('kind'), unit['unit_id']
    if kind in ('summary', 'claim'):
        return str((decision.get('summary') or {}).get('resumen') or '')
    if kind == 'attribute':
        return str((decision.get('role') or {}).get('role') or '')
    if kind == 'editorial':
        entry = next((e for e in decision.get('editorial') or [] if e.get('unit_id') == uid), None) or {}
        return str(entry.get('text') or '')
    return ''


def _why(unit, live, sides, rows):
    """(motivo, exacto) de UNA unidad: veredicto, clase de etiqueta, evidencia y acción."""
    if any(row is None for row in rows.values()):
        return 'alguna lectura no decide la unidad', False
    verdicts = {side: row.get('verdict') for side, row in rows.items()}
    if len(set(verdicts.values())) > 1:
        return 'veredicto distinto: ' + ', '.join(f'{s}={v}' for s, v in sorted(verdicts.items())), False
    classes = {side: LABEL_CLASS.get(row.get('label')) for side, row in rows.items()}
    if len(set(classes.values())) > 1:
        return ('clase de etiqueta distinta: '
                + ', '.join(f'{s}={c}' for s, c in sorted(classes.items()))), False
    evidences = {side: str(row.get('evidence') or '') for side, row in rows.items()}
    no_source = sorted(s for s, e in evidences.items() if NO_SOURCE.search(e))
    cited = sorted(s for s, e in evidences.items() if CITED.search(e))
    if no_source and cited:
        return f'contradicción de evidencia: {no_source} no encuentra fuente, {cited} la cita', False
    estado = any(row.get('label') == 'estado' for row in rows.values())
    actions = {side: _action_key(live[side], unit, estado) for side in sides}
    if len(set(actions.values())) > 1:
        return 'acción distinta: ' + ', '.join(f'{s}={a}' for s, a in sorted(actions.items())), False
    exact = (len({row.get('confidence') for row in rows.values()}) == 1
             and len({_raw_text(live[side], unit) for side in sides}) == 1)
    return '', exact


def _unit_agreement(unit, live, sides):
    """La fila de `per_unit`: veredictos, clases, si cierra y por qué modo (o por qué no)."""
    rows = {side: _unit_of(live[side], unit['unit_id']) for side in sides}
    reason, exact = _why(unit, live, sides, rows)
    return {'verdicts': {s: (r or {}).get('verdict') for s, r in rows.items()},
            'classes': {s: LABEL_CLASS.get((r or {}).get('label')) for s, r in rows.items()},
            'agree': not reason, 'reason': reason,
            'mode': None if reason else ('exacto' if exact else 'redaccion')}


def _close_in_blocks(pack, opus, per_unit):
    """Una acción de entidad cierra ENTERA o no cierra: si una unidad que la ancla va al árbitro, sus
    hermanas van con ella (si no, la decisión recortada dejaría la acción sin unidad corregida)."""
    for group, active in _entity_actions(opus).items():
        anchors = [uid for uid in _anchors(pack, opus, group) if uid in per_unit]
        if not active or all(per_unit[uid]['agree'] for uid in anchors):
            continue
        for uid in anchors:
            if per_unit[uid]['agree']:
                per_unit[uid].update({'agree': False, 'mode': None,
                                      'reason': f'la acción «{group}» se decide en bloque y otra '
                                                'unidad que la ancla va al árbitro'})


def restrict_decision(decision, pack, unit_ids, partial=True):
    """(recortada, aplazadas) — la decisión acotada a `unit_ids`.

    Conserva sus unidades, sus aristas y su editorial; las acciones de entidad sólo si TODAS las
    unidades que las anclan están dentro (una acción sin ancla sólo sobrevive si no queda nada
    pendiente). Lo aplazado viaja en el segundo valor con su texto propuesto, para el árbitro.
    """
    keep = set(unit_ids or [])
    out = {key: copy.deepcopy(value) for key, value in decision.items() if key not in ('_meta', '_file')}
    out['units'] = [u for u in out.get('units') or [] if u.get('unit_id') in keep]
    out['edges'] = [e for e in out.get('edges') or [] if e.get('unit_id') in keep]
    out['editorial'] = [e for e in out.get('editorial') or [] if e.get('unit_id') in keep]
    deferred = {}
    for group, active in _entity_actions(decision).items():
        if not active:
            continue
        anchors = _anchors(pack, decision, group)
        if all(uid in keep for uid in anchors) and (anchors or not partial):
            continue
        deferred[group] = copy.deepcopy(decision.get(group))
        out[group] = _blank_block(group)
    return out, deferred


def dual_agreement(pack, decisions, full=None):
    """Qué unidades cierra el acuerdo Opus + Astra y cuáles van al árbitro (AC7, addendum 063-B).

    El acuerdo es POR UNIDAD: cierra la que las dos lecturas resuelven con el mismo veredicto, la
    misma clase de etiqueta, sin contradicción de evidencia y con la MISMA acción sobre ella
    (arista, prosa por hechos afirmados, alias, editorial, y el estado propuesto cuando la etiqueta
    es `estado`). Una unidad que coincide en todo eso pero se redacta distinto o con otra confianza
    cierra igual, con el texto de Opus (`redaccion`; `exacto` si coinciden también en eso).

    Sólo cuentan Opus y Astra: una lectura `fable-esc` (histórico del piloto) se ignora aunque llegue.
    `mode` = `dual` si cierran todas, `parcial` si algunas, `arbitraje` si ninguna. `final` es la
    decisión de Opus recortada a `closed_units` y validada contra el pack recortado: si no valida,
    no cierra nada (el motivo queda en `reasons`). Lo que no cierra sale en `tanda_units` (el nombre
    del campo es histórico: hoy lo recibe el árbitro).

    `full` es el pack entero cuando `pack` es solo el trozo que lee el dual (lo demás espera al
    dueño): las acciones de entidad ancladas fuera del trozo no se cierran aquí.
    """
    live = {side: d for side, d in (decisions or {}).items()
            if side in READER_SIDES and isinstance(d, dict)}
    opus_side = next((s for s in OPUS_SIDES if s in live), None)
    if opus_side is not None:
        live = {side: d for side, d in live.items() if side in (opus_side, ASTRA_SIDE)}
    sides = sorted(live)
    reasons = []
    if ASTRA_SIDE not in live:
        reasons.append(f'lado sin lectura válida: {ASTRA_SIDE}')
    if opus_side is None:
        reasons.append('lado sin lectura válida: opus')
    complete = not reasons
    per_unit = {}
    for unit in pack['units']:
        row = _unit_agreement(unit, live, sides)
        if not complete:
            row.update({'agree': False, 'mode': None,
                        'reason': 'faltan lecturas válidas para cerrar por acuerdo'})
        per_unit[unit['unit_id']] = row
    if complete:
        _close_in_blocks(pack, live[opus_side], per_unit)
    closed = [uid for uid, row in per_unit.items() if row['agree']]
    tanda = [uid for uid, row in per_unit.items() if not row['agree']]
    final, deferred = None, {}
    if closed:
        partial = bool(tanda) or len(pack['units']) < len((full or pack)['units'])
        final, deferred = restrict_decision(live[opus_side], full or pack, closed, partial=partial)
        errors = validate_decision(final, restrict_pack(pack, closed))
        if errors:
            reasons.append('la decisión recortada no valida: ' + '; '.join(errors[:4]))
            for uid in closed:
                per_unit[uid].update({'agree': False, 'mode': None,
                                      'reason': f'la decisión recortada no valida: {errors[0]}'})
            closed, tanda, final, deferred = [], [u['unit_id'] for u in pack['units']], None, {}
    reasons += [f'{uid[:8]}: {per_unit[uid]["reason"]}' for uid in tanda if per_unit[uid]['reason']]
    mode = 'dual' if closed and not tanda else ('parcial' if closed else 'arbitraje')
    return {'mode': mode, 'final': final, 'per_unit': per_unit, 'closed_units': closed,
            'tanda_units': tanda, 'reasons': reasons, 'deferred': deferred}


# ── tanda del dueño ───────────────────────────────────────────────────────────

ESC_DIR = 'esc'
TANDA_BLOCKS = (('A', 'discrepancias de veredicto entre lectores'),
                ('B', 'discrepancias de acción y dudas de los lectores'),
                ('C', 'sin discrepancia (riesgo alto primero): para aprobar en bloque'))
# Las opciones que el dueño ve en AskUserQuestion; `decision_parcial` dice qué hace cada una.
CHOICES = {'mantener-opus': ('Mantener Opus', {'source': 'opus'}),
           'adoptar-astra': ('Adoptar Astra', {'source': ASTRA_SIDE}),
           'retirar': ('Retirar', {'verdict': 'corregido', 'label': 'retirada'}),
           'verificar': ('Verificar sin cambio', {'verdict': 'verificado', 'label': 'ok'}),
           'otra': ('Otra (texto)', {'text': True})}
PROSE_UNITS = {'summary': 'summary', 'attribute': 'role', 'claim': 'summary'}


def _pack_row(conn, pack_id):
    return conn.execute('SELECT * FROM esc_packs WHERE pack_id=?', (pack_id,)).fetchone()


def manifest_of(root, pack_id):
    """El manifest congelado del pack (`esc/packs/<pack_id>.json`)."""
    path = Path(root) / ESC_DIR / 'packs' / f'{pack_id}.json'
    if not path.exists():
        raise ValueError(f'pack {pack_id} sin manifest en {path}')
    return json.loads(path.read_text())


def readings_of(conn, pack_id):
    """`{side: {'result': …|None, 'receipt': …}}` para este pack."""
    out = {}
    for row in conn.execute('SELECT side, result, receipt FROM esc_readings WHERE pack_id=?', (pack_id,)):
        out[row['side']] = {'result': json.loads(row['result']) if row['result'] else None,
                            'receipt': json.loads(row['receipt']) if row['receipt'] else {}}
    return out


# Resoluciones con las que la auditoría cierra una unidad sin el dueño: no vuelven a la tanda.
# `triple-cerrada` es la del piloto (Opus+Fable+Astra) y sigue contando como cerrada.
CLOSED_RESOLUTIONS = ('opus-cerrada', 'triple-cerrada', 'dual-cerrada', 'arbitraje-')
ARB_SIDE = 'opus-arb'


def closed_units(conn, unit_ids):
    """Las unidades del pack que ya cerró la auditoría (Opus sola, acuerdo dual o triple, árbitro)."""
    done = set()
    for uid in unit_ids:
        row = conn.execute('SELECT state, resolution FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row and row['state'] == 'verified' and str(row['resolution'] or '').startswith(CLOSED_RESOLUTIONS):
            done.add(uid)
    return done


def owner_units(conn, unit_ids):
    """Las unidades del pack que esperan al dueño (`needs-human`): en un pack que la máquina aún
    trabaja son las únicas que la tanda le pregunta y las únicas que su respuesta cierra."""
    waiting = set()
    for uid in unit_ids:
        row = conn.execute('SELECT state FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row and row['state'] == 'needs-human':
            waiting.add(uid)
    return waiting


def _decisions(readings):
    """Las lecturas válidas de Opus y Astra; `fable-esc` (histórico) y los demás lados, fuera."""
    return {side: data['result'] for side, data in readings.items()
            if side in READER_SIDES and data['result']}


def _unit_text(unit):
    value = unit.get('value')
    edge = unit.get('edge')
    if edge:
        return (f"({edge.get('start_name')}) -[{edge.get('type')}]-> ({edge.get('end_name')})"
                f" · «{edge.get('source_quote') or '—'}»"
                f" · {edge.get('source_doc') or '—'} p.{edge.get('source_page') or '—'}")
    return value if isinstance(value, str) else canonical(value)


def _unit_action(decision, unit):
    """Lo que ESTA lectura hace con ESTA unidad, como texto comparable entre lectores."""
    kind, uid = unit.get('kind'), unit['unit_id']
    if kind in PROSE_UNITS:
        block = decision.get(PROSE_UNITS[kind]) or {}
        return f'{PROSE_UNITS[kind]}:{block.get("action", "none")}'
    if kind == 'identity':
        return 'alias_deny:' + canonical(sorted(str(a) for a in decision.get('alias_deny') or []))
    if kind == 'edge':
        entry = next((e for e in decision.get('edges') or [] if e.get('unit_id') == uid), None)
        return 'arista:' + str((entry or {}).get('action') or 'keep')
    if kind == 'editorial':
        entry = next((e for e in decision.get('editorial') or [] if e.get('unit_id') == uid), None)
        return 'editorial:' + str((entry or {}).get('action') or 'keep')
    estado = decision.get('estado') or {}
    return f'estado:{estado.get("action", "none")}'


def _block_of(unit, decisions):
    """A discrepancia de veredicto · B discrepancia de acción o duda · C unánime."""
    rows = {side: _unit_of(d, unit['unit_id']) for side, d in decisions.items()}
    present = {side: row for side, row in rows.items() if row}
    if len({row.get('verdict') for row in present.values()}) > 1 or len(present) < len(rows):
        return 'A'
    if len({LABEL_CLASS.get(row.get('label')) for row in present.values()}) > 1:
        return 'B'
    evidences = {side: str(row.get('evidence') or '') for side, row in present.items()}
    if ([s for s, e in evidences.items() if NO_SOURCE.search(e)]
            and [s for s, e in evidences.items() if CITED.search(e)]):
        return 'B'
    if len({_unit_action(d, unit) for d in decisions.values()}) > 1:
        return 'B'
    if any(row.get('verdict') == 'incierto' or [s for s in row.get('requested_sources') or [] if str(s).strip()]
           for row in present.values()):
        return 'B'
    if any((d.get('notes') or '').strip() for d in decisions.values()):
        return 'B'
    return 'C'


def _proposals(decisions):
    """Las propuestas de cada lector (resumen, rol, alias, estado, aristas) en bloque."""
    lines = []
    for side, decision in sorted(decisions.items()):
        summary, role = decision.get('summary') or {}, decision.get('role') or {}
        estado = decision.get('estado') or {}
        bits = []
        if summary.get('action', 'none') != 'none':
            bits.append(f"resumen {summary['action']}: «{(summary.get('resumen') or '—')}»"
                        f" — {summary.get('reason') or ''}")
        if role.get('action', 'none') != 'none':
            bits.append(f"rol {role['action']}: «{(role.get('role') or '—')}» — {role.get('reason') or ''}")
        if decision.get('alias_deny'):
            bits.append('alias_deny: ' + ', '.join(str(a) for a in decision['alias_deny']))
        if estado.get('action', 'none') == 'set':
            bits.append(f"estado: {estado.get('status')} {estado.get('fecha')}"
                        f" ({estado.get('tipo')}, {estado.get('doc') or estado.get('url')})")
        for edge in decision.get('edges') or []:
            if edge.get('action', 'keep') != 'keep':
                bits.append(f"arista {str(edge.get('unit_id'))[:8]} → {edge['action']}"
                            f" {edge.get('new_type') or edge.get('prop') or edge.get('status') or ''}"
                            f" — {edge.get('reason') or ''}")
        for item in decision.get('editorial') or []:
            if item.get('action', 'keep') != 'keep':
                bits.append(f"editorial {str(item.get('unit_id'))[:8]} → {item['action']}")
        if (decision.get('notes') or '').strip():
            bits.append('notes: ' + decision['notes'].strip())
        lines.append(f'- **{side}**: ' + ('; '.join(bits) if bits else 'sin acciones'))
    return lines


def _options(decisions):
    ids = ['mantener-opus']
    if decisions.get(ASTRA_SIDE):
        ids.append('adoptar-astra')
    ids += ['retirar', 'verificar', 'otra']
    return [{'id': cid, 'label': CHOICES[cid][0], 'decision_parcial': CHOICES[cid][1]} for cid in ids]


def _mark_pending(conn, unit_ids, resolution, pack_id, at):
    """`needs-human` para lo que espera al dueño; lo `verified` no se toca (AC10)."""
    seq = conn.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] or 0
    marked = 0
    for uid in unit_ids:
        row = conn.execute('SELECT state, resolution FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row is None or row['state'] not in ('pending', 'needs-human'):
            continue
        conn.execute("UPDATE units SET state='needs-human', resolution=? WHERE unit_id=?",
                     (resolution, uid))
        marked += 1
        # Re-exportar la misma tanda (el informe se rehace cada latido) no vuelve a anotar el evento.
        if _has_event(conn, uid, 'esc-tanda', resolution):
            continue
        seq += 1
        conn.execute('INSERT INTO events(seq,at,unit_id,event,payload) VALUES (?,?,?,?,?)',
                     (seq, at, uid, 'esc-tanda', canonical({'resolution': resolution, 'pack_id': pack_id})))
    return marked


def _arbiter_rows(readings):
    """`{unit_id: fila}` de la lectura válida del árbitro, si la hay (el dueño la ve en su tanda)."""
    result = (readings.get(ARB_SIDE) or {}).get('result') or {}
    return {row.get('unit_id'): row for row in result.get('units') or [] if isinstance(row, dict)}


def export_tanda(root, conn, packs, date, units=None, name=None):
    """El informe de tanda (A/B/C) y su JSON para `AskUserQuestion`; marca lo suyo `needs-human`.

    Devuelve `(md_path, json_path)`. No cambia el estado de los packs (lo hace el orquestador) ni
    toca ninguna unidad ya `verified`. `units` acota la tanda a esas unidades (el árbitro manda
    sólo las suyas) y `name` cambia el nombre de los ficheros (`tanda-<fecha>` por defecto).
    """
    root = Path(root)
    resolution = f'tanda-{date}-pendiente'
    at = now()
    only = set(units) if units is not None else None
    rows, items, marked = [], [], 0
    for pack_id in packs:
        row = _pack_row(conn, pack_id)
        if row is None:
            raise ValueError(f'pack {pack_id} sin fila en esc_packs')
        manifest = manifest_of(root, pack_id)
        readings = readings_of(conn, pack_id)
        decisions = _decisions(readings)
        arbiter = _arbiter_rows(readings)
        jev = {side: readings[side]['result'] for side in JEV_SIDES if side in readings}
        # Cierre parcial: lo que la auditoría ya cerró no se le vuelve a preguntar al dueño. Un pack que
        # la máquina aún trabaja (dual, árbitro) solo aporta sus unidades `needs-human`.
        done = closed_units(conn, [u['unit_id'] for u in manifest['units']])
        waiting = owner_units(conn, [u['unit_id'] for u in manifest['units']]) \
            if row['state'] in MACHINE_STATES else None
        pending = [u for u in manifest['units'] if u['unit_id'] not in done
                   and (only is None or u['unit_id'] in only) and (waiting is None or u['unit_id'] in waiting)]
        for unit in pending:
            # Un lector que leyó solo un trozo del pack (el dual, sin lo del dueño) no opina de la unidad.
            mine = {side: d for side, d in decisions.items() if _unit_of(d, unit['unit_id'])}
            block = _block_of(unit, mine) if mine else 'B'
            rows.append({'pack_id': pack_id, 'row': row, 'manifest': manifest, 'unit': unit,
                         'decisions': decisions, 'mine': mine, 'jev': jev, 'block': block,
                         'arbiter': arbiter.get(unit['unit_id'])})
        marked += _mark_pending(conn, [u['unit_id'] for u in pending], resolution, pack_id, at)
    conn.commit()

    def order(item):
        unit = item['unit']
        return (_RISK.get(unit.get('riesgo'), 9), item['manifest']['entity']['id'], unit['unit_id'])

    lines = [f'# Tanda {date} — {len(rows)} unidades de {len(packs)} packs para el dueño', '',
             f'Marcadas `needs-human` con resolución `{resolution}`: {marked}.'
             ' Responde por unidad en `tanda-<fecha>.json` (`choice_id`; `text` si eliges «otra»; `aliases`,'
             ' la lista de los alias que se retiran, si retiras una identidad)'
             f' y aplica con `merge_tanda_answers`.', '']
    for block, title in TANDA_BLOCKS:
        block_rows = sorted([r for r in rows if r['block'] == block], key=order)
        lines += [f'## {block} — {title} ({len(block_rows)})', '']
        for item in block_rows:
            unit, decisions, manifest = item['unit'], item['decisions'], item['manifest']
            entity = manifest['entity']
            uid = unit['unit_id']
            clase = (entity.get('classification') or {}).get('clase') or '—'
            lines += [f"### {entity['id']} · pack `{item['pack_id']}` · `{uid[:8]}`",
                      f"- tier {unit.get('tier')} · tipo {unit.get('kind')} · riesgo {unit.get('riesgo')}"
                      f" · clase {clase} · campo {unit.get('field') or '(objeto completo)'}",
                      f"- texto: «{_unit_text(unit)}»", '',
                      '| lector | veredicto | etiqueta | confianza | evidencia |', '|---|---|---|---|---|']
            for side in READER_SIDES:
                row_unit = _unit_of(decisions[side], uid) if side in decisions else None
                if row_unit is None:
                    continue
                evidence = str(row_unit.get('evidence') or '').replace('|', '\\|')[:400]
                lines.append(f"| {side} | **{row_unit.get('verdict')}** | {row_unit.get('label')}"
                             f" | {row_unit.get('confidence')} | {evidence} |")
            if item['jev']:
                lines += ['', '- Jev: ' + '; '.join(f'{s}={canonical(v)[:160]}'
                                                    for s, v in sorted(item['jev'].items()))]
            if item['arbiter']:
                arb = item['arbiter']
                lines += ['', f"- árbitro ({ARB_SIDE}): **{arb.get('choice_id')}** · sostenido"
                              f" {arb.get('sostenido')} · al dueño {arb.get('al_dueno')}"
                              f" · {arb.get('motivo') or ''}",
                          f"  - texto: «{arb.get('text') or ''}» · evidencia: {str(arb.get('evidence') or '')[:400]}"]
            lines += ['', 'Propuestas:'] + _proposals(decisions) + ['']
            options = _options(item['mine'])
            items.append({'pack_id': item['pack_id'], 'entity': entity['id'], 'unit_id': uid,
                          'bloque': block,
                          'pregunta': f"{entity['name']} · {uid[:8]} [{unit.get('riesgo')}/{unit.get('kind')}]"
                                      f" — «{_unit_text(unit)[:200]}»",
                          'opciones': options,
                          'contexto': {'tier': unit.get('tier'), 'riesgo': unit.get('riesgo'),
                                       'kind': unit.get('kind'), 'clase': clase,
                                       'texto': _unit_text(unit),
                                       'lectores': {side: _unit_of(decisions[side], uid)
                                                    for side in decisions
                                                    if _unit_of(decisions[side], uid)},
                                       'arbitro': item['arbiter'],
                                       'propuestas': _proposals(decisions)}})
    report = root / ESC_DIR / 'report'
    report.mkdir(parents=True, exist_ok=True)
    stem = name or f'tanda-{date}'
    md_path, json_path = report / f'{stem}.md', report / f'{stem}.json'
    md_path.write_text('\n'.join(lines) + '\n')
    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + '\n')
    return md_path, json_path


def _none_summary():
    return {'action': 'none', 'resumen': '', 'sources': [], 'reason': ''}


def _none_role():
    return {'action': 'none', 'role': '', 'reason': ''}


def _none_estado():
    return {'action': 'none', 'status': '', 'fecha': '', 'doc': '', 'url': '', 'tipo': '', 'nota': ''}


def _ensure(decision, entity_id):
    decision.setdefault('entity_id', entity_id)
    decision.setdefault('units', [])
    decision['summary'] = decision.get('summary') or _none_summary()
    decision['role'] = decision.get('role') or _none_role()
    decision['estado'] = decision.get('estado') or _none_estado()
    decision.setdefault('alias_deny', [])
    decision.setdefault('edges', [])
    decision.setdefault('editorial', [])
    decision.setdefault('notes', '')
    decision.pop('_meta', None)
    decision.pop('_file', None)
    return decision


def _set_edge(decision, unit_id, entry):
    decision['edges'] = [e for e in decision['edges'] if e.get('unit_id') != unit_id]
    if entry:
        decision['edges'].append(entry)


def _set_editorial(decision, unit_id, entry):
    decision['editorial'] = [e for e in decision['editorial'] if e.get('unit_id') != unit_id]
    if entry:
        decision['editorial'].append(entry)


def _adopt(decision, other, unit, row):
    """Adopta también las acciones de entidad o de arista del lector elegido para ESA unidad."""
    kind, uid = unit.get('kind'), unit['unit_id']
    if kind in ('summary', 'claim'):
        decision['summary'] = copy.deepcopy(other.get('summary') or _none_summary())
    elif kind == 'attribute':
        decision['role'] = copy.deepcopy(other.get('role') or _none_role())
    elif kind == 'identity':
        decision['alias_deny'] = list(other.get('alias_deny') or [])
    elif kind == 'edge':
        _set_edge(decision, uid, copy.deepcopy(next((e for e in other.get('edges') or []
                                                     if e.get('unit_id') == uid), None)))
    elif kind == 'editorial':
        _set_editorial(decision, uid, copy.deepcopy(next((e for e in other.get('editorial') or []
                                                          if e.get('unit_id') == uid), None)))
    if row.get('label') == 'estado':
        decision['estado'] = copy.deepcopy(other.get('estado') or _none_estado())


def _apply_answer(decision, decisions, unit, answer, date, notes, who='dueño'):
    """Aplica una respuesta (del dueño o del árbitro) sobre la decisión de Opus; devuelve los problemas.

    `answer.evidence`, si viene (el árbitro la trae siempre), sustituye la evidencia de la unidad en
    `retirar`/`verificar`/`otra`: la fuente que queda en el registro es la que sostiene la decisión.
    `retirar` sobre una identidad exige `answer.aliases` con alias de esa identidad; si falta o no
    cuadra, devuelve el problema sin tocar `decision`. Si cuadra, `alias_deny` pasa a ser EXACTAMENTE esos
    alias (en el orden de la respuesta, sin repetidos): lo que propusiera la lectura previa se descarta.
    """
    kind, uid = unit.get('kind'), unit['unit_id']
    choice = answer.get('choice_id')
    evidence = str(answer.get('evidence') or '').strip()
    aliases = []
    if choice == 'retirar' and kind == 'identity':
        aliases, problem = aliases_to_retire(unit, answer.get('aliases'))
        if problem:
            return [f'{uid[:8]}: {problem}']
    target = _unit_of(decision, uid)
    if target is None and choice in ('mantener-opus',):
        return [f'{uid[:8]}: no hay lectura de Opus que mantener']
    if target is None:
        target = {'unit_id': uid, 'verdict': 'verificado', 'label': 'ok', 'evidence': '',
                  'confidence': 'alta', 'requested_sources': []}
        decision['units'].append(target)
    reason = f'tanda {date}: decisión del {who}'
    if choice == 'mantener-opus':
        return []
    if choice == 'adoptar-astra':
        other = decisions.get(ASTRA_SIDE)
        if not other:
            return [f'{uid[:8]}: no hay lectura válida de {ASTRA_SIDE} que adoptar']
        row = _unit_of(other, uid)
        if row is None:
            return [f'{uid[:8]}: {ASTRA_SIDE} no decidió esa unidad']
        target.clear()
        target.update(copy.deepcopy(row))
        _adopt(decision, other, unit, row)
        return []
    if choice == 'retirar':
        target.update({'verdict': 'corregido', 'label': 'retirada', 'confidence': 'alta',
                       'requested_sources': []})
        if evidence:
            target['evidence'] = evidence
        elif not str(target.get('evidence') or '').strip():
            target['evidence'] = f'tanda {date}: el {who} ordena retirar el dato'
        if kind in ('summary', 'claim'):
            decision['summary'] = {'action': 'remove', 'resumen': '', 'sources': [], 'reason': reason}
        elif kind == 'attribute':
            decision['role'] = {'action': 'remove', 'role': '', 'reason': reason}
        elif kind == 'edge':
            # El validador ata cada acción de arista a la etiqueta `arista`, no a `retirada`.
            target['label'] = 'arista'
            _set_edge(decision, uid, {'unit_id': uid, 'action': 'deny', 'new_type': '', 'prop': '',
                                      'value': '', 'status': '', 'evidence': target['evidence'],
                                      'reason': reason})
        elif kind == 'identity':
            # El validador exige la pareja [alias] ⇔ alias_deny: retirar alias es denegarlos. La respuesta SUSTITUYE la
            # propuesta de la lectura (AC281): se retira todo `alias_deny`, así que lo no elegido por el dueño no puede quedar.
            target['label'] = 'alias'
            decision['alias_deny'] = list(dict.fromkeys(aliases))
        elif kind == 'editorial':
            _set_editorial(decision, uid, {'unit_id': uid, 'action': 'remove', 'text': '', 'reason': reason})
        return []
    if choice == 'verificar':
        target.update({'verdict': 'verificado', 'label': 'ok', 'requested_sources': []})
        if evidence:
            target['evidence'] = evidence
        elif not str(target.get('evidence') or '').strip():
            target['evidence'] = f'tanda {date}: el {who} verifica sin cambio'
        if kind == 'edge':
            _set_edge(decision, uid, None)
        elif kind == 'editorial':
            _set_editorial(decision, uid, None)
        return []
    if choice == 'otra':
        text = str(answer.get('text') or '').strip()
        if not text:
            return [f'{uid[:8]}: la opción «otra» exige texto']
        notes.append(f'tanda {date} · {uid[:8]}: texto del {who}.')
        if kind in ('summary', 'claim'):
            decision['summary'] = {'action': 'replace', 'resumen': text,
                                   'sources': list(decision['summary'].get('sources') or []), 'reason': reason}
        elif kind == 'attribute':
            decision['role'] = {'action': 'replace', 'role': text, 'reason': reason}
        elif kind == 'editorial':
            _set_editorial(decision, uid, {'unit_id': uid, 'action': 'replace', 'text': text, 'reason': reason})
        else:
            # Sin sitio en el esquema para texto libre en aristas ni alias: va a `notes` y la unidad no cambia.
            notes.append(f'«{text}»')
            return []
        target.update({'verdict': 'corregido', 'label': 'reescrita', 'confidence': 'alta',
                       'requested_sources': []})
        if evidence:
            target['evidence'] = evidence
        elif not str(target.get('evidence') or '').strip():
            target['evidence'] = f'tanda {date}: texto del {who}'
        return []
    return [f'{uid[:8]}: opción desconocida {choice!r}']


def _normalize(decision, manifest):
    """Deja la decisión coherente con lo que el validador exige tras mezclar respuestas sueltas."""
    by_id = {u['unit_id']: u for u in manifest['units']}
    corrected = [u for u in decision['units'] if u.get('verdict') == 'corregido' and u['unit_id'] in by_id]

    def corrected_kind(kind):
        return any(by_id[u['unit_id']].get('kind') == kind for u in corrected)

    if not corrected_kind('summary'):
        decision['summary'] = _none_summary()
    if not corrected_kind('attribute'):
        decision['role'] = _none_role()
    if not any(u.get('label') == 'alias' for u in corrected):
        decision['alias_deny'] = []
    if not any(u.get('label') == 'estado' for u in corrected):
        decision['estado'] = _none_estado()
    arista = {u['unit_id'] for u in corrected if u.get('label') == 'arista'}
    decision['edges'] = [e for e in decision['edges']
                         if e.get('unit_id') in arista and e.get('action', 'keep') != 'keep']
    editorial = {u['unit_id'] for u in corrected}
    decision['editorial'] = [e for e in decision['editorial']
                             if e.get('unit_id') in editorial and e.get('action', 'keep') != 'keep']
    for block, key in ((decision['summary'], 'reason'), (decision['role'], 'reason')):
        if block.get('action', 'none') != 'none' and not str(block.get(key) or '').strip():
            block[key] = 'decisión del dueño en tanda'
    if int(manifest.get('index') or 1) != 1:
        decision['summary'], decision['role'] = _none_summary(), _none_role()
        decision['alias_deny'], decision['estado'] = [], _none_estado()
    return decision


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    os.replace(temporary, path)


def _has_event(conn, unit_id, event, resolution):
    for row in conn.execute('SELECT payload FROM events WHERE unit_id=? AND event=?', (unit_id, event)):
        try:
            if json.loads(row['payload']).get('resolution') == resolution:
                return True
        except (json.JSONDecodeError, TypeError):
            continue
    return False


def _close_units(conn, decision, resolution, who='humano'):
    """`verified/<resolución>[-corregido]` con su evento, sin duplicar si ya se cerró (idempotente)."""
    at = now()
    seq = conn.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] or 0
    for unit in decision['units']:
        corrected = unit.get('verdict') == 'corregido'
        full = resolution + ('-corregido' if corrected else '')
        event = f"{'corregido' if corrected else 'verificado'}-{who}"
        conn.execute("UPDATE units SET state='verified', resolution=? WHERE unit_id=?",
                     (full, unit['unit_id']))
        if _has_event(conn, unit['unit_id'], event, full):
            continue
        seq += 1
        conn.execute('INSERT INTO events(seq,at,unit_id,event,payload) VALUES (?,?,?,?,?)',
                     (seq, at, unit['unit_id'], event,
                      canonical({'evidence': unit.get('evidence') or '', 'resolution': full})))


def _all_verified(conn, unit_ids):
    for uid in unit_ids:
        row = conn.execute('SELECT state FROM units WHERE unit_id=?', (uid,)).fetchone()
        if row is not None and row['state'] != 'verified':
            return False
    return True


def merge_tanda_answers(root, conn, answers_path, date, resolution=None):
    """Convierte las respuestas del dueño (o del árbitro) en la decisión final de cada pack; devuelve
    los packs en los que cerró algo.

    `answers.json` = `[{pack_id, unit_id, choice_id, text?, evidence?, resolution?, aliases?}]`. Parte de la
    lectura de Opus (el árbitro puede decidir sin ella: parte entonces de una decisión vacía), aplica
    la elección unidad a unidad, valida con `validate_decision` y sólo entonces escribe la decisión
    en `esc/decisions/` y cierra las unidades. Lo que no valida NO se cierra: el motivo queda en
    `esc/report/tanda-<fecha>-errores.md`.

    `resolution` = `humano-<fecha>` por defecto (el dueño): la decisión cubre todas las unidades del
    pack que la auditoría no cerró y se escribe como `<pack_id>.json` (o `<pack_id>.humano-<fecha>.json`
    si hubo cierre parcial). Con otra resolución (`arbitraje-<fecha>[-prudente]`) sólo entran las
    respuestas que la llevan (o no llevan ninguna), la decisión cubre EXACTAMENTE las unidades
    respondidas —nada sin respuesta se cierra con la opinión de Opus— y se escribe como
    `<pack_id>.<resolución>.json`. El pack pasa a `closed` cuando todas sus unidades están verificadas.
    «retirar» usa la etiqueta que el validador exige según el `kind` (`retirada` en prosa y editorial,
    `arista` en aristas —la acción `deny` la exige—, `alias` en identidad, que es lo que ata `alias_deny`).
    En una identidad, `aliases` (lista) nombra los alias que se retiran: sin ella la respuesta no cierra.
    """
    root = Path(root)
    human = resolution is None
    resolution = resolution or f'humano-{date}'
    who, route = ('humano', 'dueno') if human else (resolution.split('-')[0], resolution.split('-')[0])
    answers = json.loads(Path(answers_path).read_text())
    if not human:
        answers = [a for a in answers if a.get('resolution', resolution) == resolution]
    by_pack = {}
    for answer in answers:
        by_pack.setdefault(answer.get('pack_id'), []).append(answer)
    closed, failed = [], []
    for pack_id, pack_answers in sorted(by_pack.items(), key=lambda kv: str(kv[0])):
        row = _pack_row(conn, pack_id)
        if row is None:
            failed.append((pack_id, [f'pack {pack_id!r} sin fila en esc_packs']))
            continue
        manifest = manifest_of(root, pack_id)
        readings = readings_of(conn, pack_id)
        decisions = _decisions(readings)
        base = next((side for side in OPUS_SIDES if side in decisions), None)
        if base is None and human:
            failed.append((pack_id, ['sin lectura de Opus de la que partir']))
            continue
        decision = _ensure(copy.deepcopy(decisions[base]) if base else {}, manifest['entity']['id'])
        # Si la auditoría cerró parte del pack, esta decisión solo cubre el resto (el dueño) o lo
        # respondido (el árbitro): se acota y se escribe aparte para no pisar la ya cerrada.
        done = closed_units(conn, [u['unit_id'] for u in manifest['units']])
        answered = {a.get('unit_id') for a in pack_answers}
        # Con el pack aún en la máquina (dual, árbitro), el dueño solo cierra lo que le espera a él.
        waiting = owner_units(conn, [u['unit_id'] for u in manifest['units']]) \
            if human and row['state'] in MACHINE_STATES else None
        scope_ids = [u['unit_id'] for u in manifest['units']
                     if u['unit_id'] not in done and (human or u['unit_id'] in answered)
                     and (waiting is None or u['unit_id'] in waiting)]
        if not scope_ids:
            failed.append((pack_id, ['ninguna unidad del pack espera respuesta']))
            continue
        scope = manifest
        if len(scope_ids) != len(manifest['units']):
            scope = restrict_pack(manifest, scope_ids)
            decision = _ensure(restrict_decision(decision, manifest, scope_ids)[0], manifest['entity']['id'])
        by_id = {u['unit_id']: u for u in scope['units']}
        notes, problems = [decision.get('notes') or ''], []
        for answer in pack_answers:
            unit = by_id.get(answer.get('unit_id'))
            if unit is None:
                problems.append(f'unidad {str(answer.get("unit_id"))[:8]} no pertenece al pack {pack_id}')
                continue
            problems += _apply_answer(decision, decisions, unit, answer, date, notes,
                                      'dueño' if human else 'árbitro')
        decision['notes'] = ' '.join(n.strip() for n in notes if str(n).strip()).strip()
        _normalize(decision, scope)
        problems += validate_decision(decision, scope)
        if problems:
            failed.append((pack_id, problems))
            continue
        decision['_meta'] = {'pack_id': pack_id, 'entity': manifest['entity']['id'],
                             'kind': manifest.get('kind'), 'index': manifest.get('index'),
                             'total': manifest.get('total'), 'resolution': resolution,
                             'route': route, 'decided_at': now(),
                             'sides': sorted(decisions), 'unit_ids': [u['unit_id'] for u in scope['units']],
                             'overlay_ref': row['overlay_ref'] or manifest.get('overlay_ref')}
        # `<pack_id>.json` solo si cubre el pack entero: si no, lo escribe (o escribirá) el cierre de la máquina.
        name = pack_id if human and len(scope_ids) == len(manifest['units']) else f'{pack_id}.{resolution}'
        _write_json(root / ESC_DIR / 'decisions' / f'{name}.json', decision)
        _close_units(conn, decision, resolution, who)
        if _all_verified(conn, [u['unit_id'] for u in manifest['units']]):
            conn.execute("UPDATE esc_packs SET state='closed', route=? WHERE pack_id=?", (route, pack_id))
        closed.append(pack_id)
    conn.commit()
    report = root / ESC_DIR / 'report'
    if failed:
        report.mkdir(parents=True, exist_ok=True)
        lines = [f'# Tanda {date} ({resolution}) — packs NO cerrados ({len(failed)})', '']
        for pack_id, problems in failed:
            lines += [f'## {pack_id}', ''] + [f'- {p}' for p in problems] + ['']
        stem = f'tanda-{date}' if human else resolution
        (report / f'{stem}-errores.md').write_text('\n'.join(lines) + '\n')
    return closed
