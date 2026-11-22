"""Central configuration for the ingestion pipeline.

Values come from the repo-root .env (loaded here) with sensible defaults so the
pipeline runs on the host while Neo4j runs in Docker (published on localhost).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# pipeline/ -> repo root
PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PIPELINE_DIR.parent

load_dotenv(REPO_ROOT / ".env")

# --- Corpus paths ---
DATA_DIR = REPO_ROOT / "data"                 # source PDFs
WORK_DIR = PIPELINE_DIR / "data"              # durable intermediates
CHUNKS_DIR = WORK_DIR / "chunks"
FACTS_DIR = WORK_DIR / "facts"
ENTITIES_DIR = WORK_DIR / "entities"
CHECKPOINTS_DIR = WORK_DIR / "checkpoints"
REPORTS_DIR = WORK_DIR / "reports"
REGISTRY_PATH = ENTITIES_DIR / "registry.json"

for _d in (CHUNKS_DIR, FACTS_DIR, ENTITIES_DIR, CHECKPOINTS_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Local LLM (MLX server on host) ---
MLX_HOST = os.getenv("MLX_HOST", "127.0.0.1")
MLX_PORT = os.getenv("MLX_PORT", "8080")
MLX_BASE_URL = os.getenv("MLX_BASE_URL", f"http://{MLX_HOST}:{MLX_PORT}/v1")
MLX_MODEL = os.getenv("MLX_MODEL", "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit")
MLX_API_KEY = os.getenv("MLX_API_KEY", "not-needed")

# --- Embeddings ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# --- Neo4j ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

# --- Chunking ---
CHUNK_TARGET_TOKENS = 900
CHUNK_OVERLAP_TOKENS = 140
MIN_PAGE_CHARS = 50            # below this a page's text layer is treated as empty

# --- OCR (Apple Vision via ocrmac; host/macOS only) ---
ENABLE_OCR = os.getenv("ENABLE_OCR", "1") == "1"
OCR_DPI = 200
OCR_LANGS = ["es-ES"]

# --- Extraction ---
# Client-side concurrency. Spec 069: extraction runs with 1 (LLM_CONCURRENCY=1, `make ingest`) so the
# server never batches two requests and temperature 0 is plain greedy decoding — the same chunk gives
# the same JSON. Briefings and other free-text jobs may use more.
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "5"))
LLM_MAX_TOKENS = 3000          # headroom for dense chunks (server cap is higher)
LLM_TIMEOUT_S = 180
LLM_TEMPERATURE = 0.0

# --- Entity resolution thresholds ---
FUZZY_AUTOMERGE = 92           # rapidfuzz token_set_ratio >= this -> auto-merge
EMBED_AUTOMERGE = 0.90         # cosine >= this -> auto-merge
EMBED_REVIEW_LOW = 0.80        # gray band [low, automerge) -> LLM adjudication

# Real-world homonyms that BOTH fuzzy and embedding passes would conflate.
# Pairs of _norm_key()-space names that must never land in the same cluster.
NEVER_MERGE: list[tuple[str, str]] = [
    # DS_C_15_744 p.35 los distingue: el presidente de Plus Ultra vs el conocido de Zapatero.
    ("julio martinez sola", "julio martinez martinez"),
    # Ingesta 20557 (2026-08-10): el merge por embeddings fundía org-names semánticamente
    # próximos pero DISTINTOS — vetos permanentes (claves _norm_key).
    ("el mundo", "el pais"),
    ("grupo parlamentario popular", "grupo parlamentario socialista"),
    ("grupo parlamentario popular", "grupo parlamentario socialista en la asamblea de madrid"),
    ("grupo parlamentario socialista", "grupo parlamentario socialista en la asamblea de madrid"),
    ("senado", "comision de investigacion del senado sobre el caso koldo"),
    ("sepi", "comision de investigacion de las deficiencias y presuntas irregularidades en la gestion de la sepi"),
    ("sociedad estatal de participaciones industriales", "comision de investigacion de las deficiencias y presuntas irregularidades en la gestion de la sepi"),
    ("comision de investigacion del senado sobre el caso koldo", "comision de investigacion de las deficiencias y presuntas irregularidades en la gestion de la sepi"),
    # Arbitrados en la verificación pre-carga (pasada 2): entes societarios/órganos distintos.
    ("adif", "adif alta velocidad"),
    ("comercial cueto sl", "comercial cueto internacional sl"),
    ("renfe", "grupo renfe"),
    ("psoe", "comision ejecutiva federal del psoe"),
    ("partido socialista obrero espanol", "comision ejecutiva federal del psoe"),
    ("psoe", "psoe de madrid"),
    ("partido socialista obrero espanol", "psoe de madrid"),
    ("presidencia del gobierno", "gabinete de la presidencia del gobierno"),
    ("fiscalia general del estado", "secretaria tecnica de la fiscalia general del estado"),
]

# --- Validation subset (one report, one TS auto, one transcript) ---
SUBSET_DOCS = [
    "informe-uco-cerdan-1-100.pdf",
    "ATS_8919_2025.pdf",
    "DS_C_15_103.pdf",
]

# Known central figures: seed aliases so the most important entities resolve
# deterministically (surface form -> canonical name), keyed by entity type.
SEED_ALIASES: dict[str, str] = {
    "koldo": "Koldo García",
    "koldo garcia": "Koldo García",
    "sr. garcia": "Koldo García",
    "abalos": "José Luis Ábalos",
    "jose luis abalos": "José Luis Ábalos",
    "ministro abalos": "José Luis Ábalos",
    "cerdan": "Santos Cerdán",
    "santos cerdan": "Santos Cerdán",
    "aldama": "Víctor de Aldama",
    "victor de aldama": "Víctor de Aldama",
    "begona gomez": "Begoña Gómez",
    "pedro sanchez": "Pedro Sánchez",
    "presidente del gobierno": "Pedro Sánchez",
    "zapatero": "José Luis Rodríguez Zapatero",
    "jose luis rodriguez zapatero": "José Luis Rodríguez Zapatero",
    "delcy": "Delcy Rodríguez",
    "delcy rodriguez": "Delcy Rodríguez",
    # Organizations: collapse acronym/short forms onto one canonical so future
    # ingests don't recreate the duplicates that context/merges.yaml folds.
    "partido socialista": "PSOE",
    "uco": "Unidad Central Operativa",
    "ucm": "Universidad Complutense de Madrid",
    "pde": "Puertos del Estado",
    "mitma": "Ministerio de Transportes, Movilidad y Agenda Urbana",
}
