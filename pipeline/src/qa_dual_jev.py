"""Jev (TypeSafe System One) client, shared by the staged audit (spec 063, `qa_esc_jev`).

`jev-latest` is what the caller asks for; the 063 calibration belongs to `JEV_MEASURED_MODEL`,
so a resolved model other than that one is a configuration halt, never a reading. The 061
measured reader prompt and its 0-9 defect codes were retired by spec 069 F5 — see git history.

Remote API: the state text leaves the machine. Explicit owner decision of 2026-09-17, recorded in
`decide_local/specs/002-jev-benchmark` and in the memory vault (decisions.md 2026-09-17, 061-B).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
# `qa_dual*` never imports `config` (it drags Neo4j settings in); the key lives in the repo-root
# `.env` (gitignored) exactly like the rest of the secrets, so load just that.
load_dotenv(REPO / '.env', override=False)

API_KEY_ENV = 'TYPESAFE_API_KEY'
# What the caller asks for and the model the prompts were measured on. `jev-latest` resolving to
# anything else is a configuration halt, not a reading: the calibration belongs to 1.13.0.
JEV_MODEL = 'jev-latest'
JEV_MEASURED_MODEL = 'jev-1.13.0'
# The SDK defaults to 10 s; long states need more (061 benchmark: p50 287 ms, max ~1 s).
TIMEOUT_S = 60.0
MAX_RETRIES = 3


class ConfigError(RuntimeError):
    """Missing/invalid credentials or a model that is not the measured one: halt, never retry."""


class JevClient:
    """Thin wrapper over `typesafe-sdk`, calqued on decide_local's backend (jev.py:31-45).

    Subclasses add `system_one` with their question conversion (`qa_esc_jev.EscJevClient`).

    The key is checked before the SDK is even imported, so a missing key never touches the
    network; retries (429/5xx/connection, 3 attempts) and the 60 s timeout are the SDK's.
    """

    def __init__(self, model=JEV_MODEL):
        if not os.environ.get(API_KEY_ENV, '').strip():
            raise ConfigError(f'{API_KEY_ENV} no definida (repo .env)')
        try:
            from typesafe_sdk import RetryPolicy, TypeSafeClient
        except ImportError as exc:  # pragma: no cover - dependency of the pipeline venv
            raise ConfigError('typesafe-sdk no instalado: uv sync en pipeline/') from exc
        self.model = model
        self._client = TypeSafeClient(model=model, timeout=TIMEOUT_S,
                                      retry=RetryPolicy(max_retries=MAX_RETRIES))

    def close(self):
        self._client.close()


def classify_exception(exc):
    """`config` halts the loop; everything else is a reader failure classified by its text."""
    try:
        from typesafe_sdk import TypeSafeAuthenticationError, TypeSafePermissionDeniedError
        fatal = (ConfigError, TypeSafeAuthenticationError, TypeSafePermissionDeniedError)
    except ImportError:  # pragma: no cover
        fatal = (ConfigError,)
    if isinstance(exc, fatal):
        return f'config: {exc}'
    status = getattr(exc, 'status', None)
    if status is not None:
        return f'api-error {status}: {exc}'
    return f'{type(exc).__name__}: {exc}'


def raw_answers(response):
    """Everything the API said, for `calls.jsonl` (the finding keeps only what the gate uses)."""
    out = {}
    for name, answer in response.choices.items():
        out[name] = {'type': 'choice', 'choice': answer.choice, 'confidence': answer.confidence,
                     'probabilities': dict(answer.probabilities)}
    for name, answer in response.nouls.items():
        out[name] = {'type': 'noul', 'noul': answer.noul}
    return out
