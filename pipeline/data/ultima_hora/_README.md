# Última hora layer (`source_kind="ultima_hora"`)

Curated **breaking-news / case-development** facts that, for now, exist only in the press —
sentence requests, new UCO findings, fresh indictments — and for which there is **no
downloadable official document yet**, so they cannot enter the judicial (source-anchored)
graph. Loaded by `pipeline/src/ultima_hora_loader.py` (`make ultima-hora`). Idempotent.

## Why this is separate from the `press` layer
The `press` layer is strictly **neutral biographical facts** and **forbids alleged wrongdoing**.
This layer **deliberately allows allegation-type developments** (a per-edge `status`:
investigado / imputado / procesado / acusado …) **because it is kept fully separate**: its own
`source_kind`, its own UI toggle in the explorer, its own `/ultima-hora` page, and its own badge.
Presumption of innocence always applies.

## Ground rules (keep the rigor model intact)
- **Every relation MUST carry `outlet` + `url`** (an unattributed relation is skipped) — plus,
  ideally, `date` + a verbatim `quote`. No attribution → it doesn't belong here.
- Allegations are allowed but must be framed as such: use `status` + `note` and let the UI mark
  them "presunto". Never assert guilt.
- `name` must match the canonical name already in the graph so it MERGEs onto the existing node
  (check with `/search`); otherwise a new última-hora-tagged entity is created.
- Última-hora edges never overwrite a judicial/contexto/prensa edge of the same (source, target,
  type); they are keyed by `source_kind='ultima_hora'`. The `relations` deny/fix overlay only
  touches `judicial`/NULL edges, so it leaves these alone.

## File format
```yaml
entities:
  - {name: "Isabel Pardo de Vera", type: Person}
relations:
  - source: "Isabel Pardo de Vera"
    target: "Santos Cerdán"
    type: ASSOCIATED_WITH        # closed-set RelationType (see pipeline/src/schema.py)
    status: "investigado"        # optional procedural stage (rendered "presunto")
    outlet: "elDiario.es"
    url: "https://www.eldiario.es/..."
    date: "2026-06-12"
    quote: "verbatim snippet ≤300 chars"
    note: "short neutral gloss of the development"
```

The richer, public-facing editorial feed (headlines + deks) lives in `web/lib/ultimaHora.ts`;
this YAML only feeds the GRAPH edges (for the explorer toggle). Keep the two consistent.

Re-run `make ultima-hora` after editing (idempotent).
