"""API configuration from environment (set by docker-compose)."""
from __future__ import annotations

import os

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]

# Relationship types that are provenance plumbing, excluded from the visual graph.
PROVENANCE_RELS = ["MENTIONED_IN", "PART_OF"]
