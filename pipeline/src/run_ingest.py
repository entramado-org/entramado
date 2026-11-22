"""CLI entrypoint for the ingestion pipeline.

Usage:
  uv run python -m src.run_ingest --subset      # 3-doc validation subset
  uv run python -m src.run_ingest               # full corpus (resumable)
  uv run python -m src.run_ingest --resume RUNID # resume a previous run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

import structlog
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from . import config, llm_client
from .graph import build_graph


def _setup_logging():
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


async def _run(subset: bool, run_id: str):
    log = structlog.get_logger()

    if not await llm_client.health():
        log.error("mlx_unreachable", url=config.MLX_BASE_URL,
                  hint="Start it with: cd pipeline && ./scripts/start_mlx_server.sh")
        sys.exit(1)

    ckpt_path = str(config.CHECKPOINTS_DIR / "ingest.sqlite")
    async with AsyncSqliteSaver.from_conn_string(ckpt_path) as saver:
        app = build_graph().compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": 50}
        await app.ainvoke({"subset": subset, "run_id": run_id, "stats": {}}, cfg)
    await llm_client.aclose()


def main():
    parser = argparse.ArgumentParser(description="El Entramado — KG ingestion")
    parser.add_argument("--subset", action="store_true", help="Process only the validation subset")
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume an existing run by id")
    args = parser.parse_args()

    _setup_logging()
    run_id = args.resume or ("subset" if args.subset else time.strftime("full-%Y%m%d-%H%M%S"))
    asyncio.run(_run(args.subset or (args.resume == "subset"), run_id))
    # torch/sentence-transformers leave non-daemon worker threads that can block
    # interpreter shutdown; all work is durably written by now, so exit hard.
    os._exit(0)


if __name__ == "__main__":
    main()
