# Press-enrichment layer (`source_kind="prensa"`)

Curated **neutral** biographical / structural facts about graph entities, reported by
named internet sources, each carrying its provenance. Loaded by
`pipeline/src/press_loader.py` (`make prensa`). Idempotent.

## Ground rules (keep the presumption-of-innocence model intact)
- **NEUTRAL facts only**: offices/cargos held, appointment dates, company foundations /
  ownership, family or professional ties reported as fact. **NEVER** alleged wrongdoing —
  that belongs to the judicial documents, with their own provenance.
- Every relation MUST carry `outlet` + `url` (a relation without attribution is skipped).
- `name` must match the canonical name already in the graph so it MERGEs onto the existing
  node (check with `/search`); otherwise a new press-tagged entity is created.
- Press relations never overwrite a judicial/contexto edge of the same (source, target, type);
  they are keyed by `source_kind='prensa'`.

## File format
```yaml
entities:
  - {name: "Grupo Vivir", type: Company, role: "Sociedad de Víctor de Aldama"}
relations:
  - source: "Víctor de Aldama"
    target: "Grupo Vivir"
    type: CONTROLS              # closed-set RelationType (see pipeline/src/schema.py)
    outlet: "Wikipedia"         # the source medium (shown in the UI badge)
    url: "https://es.wikipedia.org/wiki/Víctor_de_Aldama"
    date: "2018"               # optional; a leading YYYY also sets date_year
    quote: "verbatim snippet ≤300 chars"
    note: "short neutral gloss"
```

Re-run `make prensa` after editing (idempotent).
