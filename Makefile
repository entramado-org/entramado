.PHONY: help mlx neo4j up down logs ingest ingest-subset cleanup qa-migrate context relations accusations contracts claims documents prensa ultima-hora mercantil subvenciones contratacion contratacion-ampliada boe vault briefings obsidian shell-api clean

help:
	@echo "El Entramado — make targets:"
	@echo "  make mlx           Start the host MLX server (Qwen3-30B-A3B) — needed only for ingestion, not for the app."
	@echo "  make neo4j         Start only Neo4j (docker)."
	@echo "  make up            Start api + web + neo4j (docker)."
	@echo "  make down          Stop all docker services."
	@echo "  make logs          Tail docker logs."
	@echo "  make ingest-subset Run ingestion on the 3-doc validation subset (host)."
	@echo "  make ingest        Run full ingestion over all PDFs (host, resumable)."

mlx:
	cd pipeline && ./scripts/start_mlx_server.sh

neo4j:
	docker compose up -d neo4j

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

ingest-subset:
	cd pipeline && uv run python -m src.run_ingest --subset

ingest:
	cd pipeline && uv run python -m src.run_ingest

cleanup:
	cd pipeline && uv run python -m src.cleanup

context:
	cd pipeline && uv run python -m src.context_loader

relations:                 ## Load the curated relations-correction overlay (deny misleading edges + split merged homonyms)
	cd pipeline && uv run python -m src.relations_loader

accusations:               ## Load the curated ACCUSED_OF overlay (Person->Crime, document-grounded)
	cd pipeline && uv run python -m src.accusations_loader

contracts:                 ## Load the curated CONTRACTS overlay (reified procurement awards)
	cd pipeline && uv run python -m src.contracts_loader

claims:                    ## Load the curated CLAIMS-IN-DISPUTE overlay (reified Claim + ASSERTS/DISPUTES)
	cd pipeline && uv run python -m src.claims_loader

documents:                 ## Annotate Document nodes with curated doc_type (evidence weight) + firmeza de sentencias (spec 059)
	cd pipeline && uv run python -m src.documents_loader

pii-check:                 ## Gate PII (spec 059): IBAN/DNI/teléfono/e-mail/JID/matrícula en capas YAML/TS (+ GRAPH=1 nombres/alias del grafo vía NEO4J_URI)
	cd pipeline && uv run python -m scripts.pii_check $(if $(filter 1,$(GRAPH)),--graph,)

.PHONY: seudonimos-check
seudonimos-check:          ## Spec 070 (lo mismo que el CI): ninguna regla pública ni fila del registro empareja un seudónimo con sociedad/organismo o alias. SNAPSHOT=dir verifica además un export del grafo frente a seudonimos_permitidos.yaml
	cd pipeline && uv run python scripts/seudonimos_check.py $(if $(SNAPSHOT),--snapshot "$(abspath $(SNAPSHOT))",)

# Semantic PII sweep (spec 067): local gate before every Path A, never in CI (TypeSafe key and cost). Everything
# tracked in the data layer goes out as one unit per entry; the exporter refuses symlinks, untracked files and
# unclassified extensions. OUT, one run directory, defaults to a new private folder outside any repo. REVISADOS,
# the units already reviewed and kept, is the maintainer's private list outside the public tree; without it every
# flagged unit is listed. Exit 1 = units to review.
DECIDE_DIR ?= $(HOME)/Documents/projects/decide_local
REVISADOS ?= specs/067-pii-semantica-jev/revisados.yaml
PII_SEMANTICO_RUN := $(HOME)/Documents/projects/sm-private/pii-semantic/$(shell date +%Y%m%d-%H%M)
PII_SEMANTICO_OUT = $(abspath $(or $(OUT),$(PII_SEMANTICO_RUN)))
PII_SEMANTICO_SELECT = cd pipeline && uv run python -m scripts.pii_semantic_select --out "$(PII_SEMANTICO_OUT)" \
	$(if $(REVISADOS),--revisados "$(abspath $(REVISADOS))",)
.PHONY: pii-semantico pii-semantico-select
pii-semantico:             ## Gate PII semántico (spec 067), previo a cada Path A: una unidad por entrada de todo lo versionado en pipeline/data y api/app/data → Jev → política → OUT/seleccion.md. OUT=dir (por defecto ~/Documents/projects/sm-private/pii-semantic/<fecha>, fuera de todo repo) REVISADOS=lista privada. Saca el texto del Mac vía TypeSafe (solo datos ya públicos); no va al CI
	@echo "pii-semantico: OUT=$(PII_SEMANTICO_OUT)"
	git ls-files pipeline/data api/app/data \
		| uv run --project pipeline python pipeline/scripts/pii_semantic_export.py --out "$(PII_SEMANTICO_OUT)" --files-from -
	cd "$(DECIDE_DIR)" && set -a && . ./.env && set +a && uv run --no-sync decide run --backend jev --model jev-1.13.0 \
		--questions "$(CURDIR)/pipeline/scripts/pii_semantic_questions.json" \
		--items "$(PII_SEMANTICO_OUT)/items.jsonl" --out "$(PII_SEMANTICO_OUT)/pred.jsonl" --resume
	$(PII_SEMANTICO_SELECT)

pii-semantico-select:      ## Re-aplica la política del gate semántico a OUT/pred.jsonl sin volver a llamar a Jev. OUT=dir de la pasada REVISADOS=lista privada
	@test -n "$(OUT)" || (echo 'OUT=dir is required (the run to re-select)'; exit 2)
	$(PII_SEMANTICO_SELECT)

centralidad:               ## Recompute network metrics (betweenness/closeness/Louvain communities) into node props — rerun after each ingest
	cd pipeline && uv run python -m src.network_metrics

# ── Reviewable migrations (Path A, gen_*_migration); the 016/052 audit loop was retired in spec 069 F5 ──
qa-migrate:                ## GATED human apply: run a migration file in ONE transaction. FILE=… (dry-run unless CONFIRM=1). Review the .cypher first!
	cd pipeline && uv run python -m src.qa_audit migrate --file $(abspath $(FILE)) $(if $(filter 1,$(CONFIRM)),--confirm,)

# ── Censo de la auditoría dual (spec 061) — el ledger sobre el que corre la 063 ──
# El latido 061 (lectores Jev+Astra, lotes, puerta, parches) se retiró en la spec 069 F5.
# AUDIT = la raíz congelada de la auditoría; por defecto, la más reciente bajo
# pipeline/data/audit/dual/ (gitignored). Ningún target abre Neo4j ni Render.
.PHONY: qa-lint qa-dual-census qa-dual-lint qa-dual-test
# Raíz por defecto = la más reciente con dual-state.json (la salida del lint también lleva
# manifest, así que el marcador es el estado).
DUAL_DEFAULT := $(shell ls -d pipeline/data/audit/dual/*/dual-state.json 2>/dev/null | sort | tail -1 | sed 's|/dual-state.json$$||')
DUAL = $(or $(AUDIT),$(DUAL_DEFAULT))

qa-lint:                   ## Comprobaciones deterministas L1-L26 sobre el export del seed (0 tokens). SNAPSHOT=dir OUT=dir
	cd pipeline && .venv/bin/python -m src.qa_lint --snapshot "$(abspath $(or $(SNAPSHOT),neo4j/integral-20260907))" --out "$(abspath $(or $(OUT),pipeline/data/audit/dual/lint-$(shell date +%Y%m%d)))" --as-of $(or $(ASOF),$(shell date +%Y-%m-%d)) $(if $(CHECKS),--checks $(CHECKS),) --fail-on L1,L2,L12,L13,L15,L26

qa-dual-census:            ## Censo acotado T0-T2 (+editorial, +muestra T4) desde el export del seed. SNAPSHOT=dir SEED=file [AUDIT=dir PRESENTATION=file T4=10]
	@test -n "$(SNAPSHOT)" -a -n "$(SEED)" || (echo 'SNAPSHOT=dir and SEED=file are required'; exit 2)
	cd pipeline && .venv/bin/python -m src.qa_dual $(if $(AUDIT),--root "$(abspath $(AUDIT))",) census --snapshot "$(abspath $(SNAPSHOT))" --seed "$(abspath $(SEED))" --editorial $(if $(PRESENTATION),--presentation "$(abspath $(PRESENTATION))",) $(if $(T4),--t4-sample $(T4),)

qa-dual-lint:              ## Ejecuta el lint y marca lint_checks en el censo. AUDIT=dir [SNAPSHOT=dir]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	cd pipeline && .venv/bin/python -m src.qa_dual --root "$(abspath $(DUAL))" lint $(if $(SNAPSHOT),--snapshot "$(abspath $(SNAPSHOT))",)

qa-dual-test:              ## Todos los tests deterministas del pipeline (cierre del RUNBOOK-063); sin modelos, sin red, sin grafo
	pipeline/.venv/bin/python -m unittest discover -s pipeline/tests -v

prensa:
	cd pipeline && uv run python -m src.press_loader

ultima-hora:              ## Load the curated ÚLTIMA HORA overlay (press developments, source_kind='ultima_hora')
	cd pipeline && uv run python -m src.ultima_hora_loader

mercantil:                ## Load the curated MERCANTIL overlay (BORME registry props, props-only, is_mercantil)
	cd pipeline && uv run python -m src.mercantil_loader

subvenciones:             ## Load the curated SUBVENCIONES overlay (BDNS grants/avales, source_kind='subvencion')
	cd pipeline && uv run python -m src.subvenciones_loader

contratacion:             ## Load the curated CONTRATACION overlay (PLACSP minor contracts aggregates, source_kind='contratacion')
	cd pipeline && uv run python -m src.contratacion_loader

contratacion-ampliada:    ## Load the curated CONTRATACION AMPLIADA overlay (PLACSP tenders + aggregated platforms + CCAA minors, CONTRACTOR_OF, source_kind='contratacion')
	cd pipeline && uv run python -m src.contratacion_ampliada_loader

boe:                      ## Load the curated BOE overlay (sección II.A appointments/ceses aggregated per office, source_kind='boe')
	cd pipeline && uv run python -m src.boe_loader

prensa-audit:              ## Auditoría reproducible de cabeceras de TODA la capa de prensa (feed última hora + overlay + source_kind='prensa') — offline, sin Neo4j
	cd pipeline && uv run python scripts/press_outlet_audit.py

vault:
	cd pipeline && uv run python -m src.obsidian_export

briefings:
	cd pipeline && uv run python -m src.briefings

obsidian: vault
	@open -a Obsidian "$(PWD)/sm-vault" 2>/dev/null || echo "Open Obsidian and pick this folder as vault: $(PWD)/sm-vault"

healthcheck:               ## Smoke-test every MCP tool (OK/fallo + latencia) against NEO4J_URI — diagnoses a session in one step
	docker run --rm --network entramado_default \
		-v $(PWD)/sm-vault:/vault:ro \
		-v $(PWD)/pipeline/data/patrones:/data/patrones:ro \
		-e NEO4J_URI=bolt://neo4j:7687 -e NEO4J_USERNAME=neo4j \
		-e NEO4J_PASSWORD=$(or $(NEO4J_PASSWORD),changeme) -e VAULT_DIR=/vault \
		entramado-api python -m app.healthcheck

shell-api:
	docker compose exec api bash

clean:
	docker compose down -v
	rm -rf pipeline/data/{chunks,facts,entities,checkpoints,reports}/*

# ── Auditoría escalonada por entidad (spec 063) ───────────────────────────────
# Sustituye al latido 061 para lo pendiente. Misma raíz $(DUAL) (ledger census.sqlite);
# todo lo nuevo vive en $(DUAL)/esc/ y $(DUAL)/esc-state.json. CLI: pipeline/src/qa_esc.py.
.PHONY: qa-esc-census qa-esc-classify qa-esc-calibrate qa-esc-pack qa-esc-opus qa-esc-route qa-esc-escalate qa-esc-arbitrate \
        qa-esc-step qa-esc-status qa-esc-report qa-esc-tanda qa-esc-tanda-apply qa-esc-apply qa-esc-record \
        qa-esc-halt qa-esc-resume qa-esc-reroute-owner qa-esc-test qa-esc-smoke
ESC_PY = cd pipeline && .venv/bin/python -m src.qa_esc --root "$(abspath $(DUAL))" --repo "$(abspath .)"

qa-esc-census:             ## 063: cola de entidades desde el ledger (riesgo alto→medio→bajo, nº unidades). AUDIT=dir [SCOPE=pending|verified-T0]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) census --scope $(or $(SCOPE),pending)

qa-esc-classify:           ## 063: clasificador Jev (clase/riesgo/aserción) sobre la cola; escribe lecturas jev-class. AUDIT=dir [N=20]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) classify --n $(or $(N),20)

qa-esc-calibrate:          ## 063: calibración de Jev contra el gold humano (decide bench) → esc/jev/calibration.md. AUDIT=dir [RUN_DECIDE=1]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) calibrate $(if $(RUN_DECIDE),--run-decide,)

qa-esc-pack:               ## 063: construye N packs por entidad (manifest + markdown). AUDIT=dir [N=6 OVERLAY_REF=sha SOURCES=normal|ampliado]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) pack --n $(or $(N),6) $(if $(OVERLAY_REF),--overlay-ref $(OVERLAY_REF),) $(if $(SOURCES),--sources $(SOURCES),)

qa-esc-opus:               ## 063: lee N packs con Opus 5 (búsqueda restringida a qa_esc_ev.py). AUDIT=dir [N=3 EFFORT=xhigh|high]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) opus --n $(or $(N),3) $(if $(EFFORT),--effort $(EFFORT),)

qa-esc-route:              ## 063: enruta los packs leídos (reglas fijas + Jev solo endurece) → cerrar/dual/tanda. AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) route

qa-esc-escalate:           ## 063-B: Astra ciega sobre los packs escalados; acuerdo dual Opus+Astra o árbitro. AUDIT=dir [N=2 ASTRA_TOOLS=auto|never|always]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) escalate --n $(or $(N),2) --astra-tools $(or $(ASTRA_TOOLS),auto)

qa-esc-arbitrate:          ## 063-B: árbitro Opus 5.5 (xhigh, buscador) + validación de Astra sobre los packs arb-pending. AUDIT=dir OBLIGATORIO [N=2 DRY=1 FROM_TANDA=1]
	@test -n "$(AUDIT)" || (echo 'AUDIT=<raíz de la auditoría> is required (sin default: nunca el ledger real por accidente)'; exit 2)
	$(ESC_PY) arbitrate --n $(or $(N),2) $(if $(DRY),--dry-run,) $(if $(FROM_TANDA),--from-tanda,)

qa-esc-reroute-owner:      ## 063: re-enruta por unidad los packs que route() mandó enteros al dueño. AUDIT=dir [DRY=1]
	@test -n "$(AUDIT)" || (echo 'AUDIT=<raíz de la auditoría> is required (sin default: nunca el ledger real por accidente)'; exit 2)
	$(ESC_PY) reroute-owner $(if $(DRY),--dry-run,)

qa-esc-step:               ## 063: UN latido (una acción, una línea JSON; <10 s si no lanza nada). AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) step

qa-esc-status:             ## 063: foto del bucle: ventana, breaker, packs por estado, consulta G7 (cierres indebidos). AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) status

qa-esc-report:             ## 063: informe + esc/report/tanda-<fecha>.md (A/B/C) con los textos de los lectores. AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) report

qa-esc-tanda:              ## 063: exporta la tanda pendiente del dueño (JSON para AskUserQuestion). AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) tanda

qa-esc-tanda-apply:        ## 063: fusiona las respuestas del dueño en las decisiones finales. AUDIT=dir DEC=<fichero de respuestas>
	@test -n "$(DUAL)" -a -n "$(DEC)" || (echo 'AUDIT y DEC son obligatorios'; exit 2)
	$(ESC_PY) tanda-apply --dec "$(abspath $(DEC))"

qa-esc-apply:              ## 063: overlays desde esc/decisions (summaries/roles/relations + registro de rectificaciones). AUDIT=dir [CHECK=1|DRYRUN=1 RESOLUTIONS=a,b TAG=… UPD=YYYY-MM-DD]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	cd pipeline && .venv/bin/python -m src.qa_esc_apply --root "$(abspath $(DUAL))" --repo "$(abspath .)" $(if $(RESOLUTIONS),--resolutions $(RESOLUTIONS),) $(if $(TAG),--tag "$(TAG)",) $(if $(UPD),--upd $(UPD),) $(if $(CHECK),check,$(if $(DRYRUN),dry-run,apply))

qa-esc-record:             ## 063: registra en el censo las decisiones aplicadas (backup + eventos + esc_packs applied). AUDIT=dir [RESOLUTIONS=a,b DOC=ruta]
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	cd pipeline && .venv/bin/python -m src.qa_esc_apply --root "$(abspath $(DUAL))" --repo "$(abspath .)" $(if $(RESOLUTIONS),--resolutions $(RESOLUTIONS),) record $(if $(DOC),--doc "$(DOC)",)

.PHONY: registro-check
registro-check:            ## Registro público de rectificaciones: forma de cada fila (lo mismo que el CI). SNAPSHOT=dir comprueba además que ninguna arista «suprimida» siga viva en ese export del grafo (censo de AUDIT=dir)
	cd pipeline && uv run python scripts/registro_check.py \
	  $(if $(SNAPSHOT),--snapshot "$(abspath $(SNAPSHOT))" --root "$(abspath $(DUAL))" --overlay "$(abspath .)",--tree)

qa-esc-halt:               ## 063: para el latido (las lecturas en vuelo terminan). AUDIT=dir REASON="…"
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) halt --reason "$(or $(REASON),manual)"

qa-esc-resume:             ## 063: reanuda tras HALT: reinicia ventana y breaker; lotes huérfanos → resume. AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	$(ESC_PY) resume

qa-esc-smoke:              ## 063: paso 0 del piloto: 1 entidad con Sonnet/Opus, comprueba sandbox y formato. AUDIT=dir
	@test -n "$(DUAL)" || (echo 'AUDIT=<raíz de la auditoría dual> is required'; exit 2)
	cd pipeline && .venv/bin/python ../specs/063-auditoria-escalonada-por-entidad/tools-063/smoke_opus.py --root "$(abspath $(DUAL))"

qa-esc-test:               ## 063: tests deterministas de la auditoría escalonada; sin modelos, sin red, sin grafo
	pipeline/.venv/bin/python -m unittest discover -s pipeline/tests -p 'test_qa_esc*.py' -v

# ── Ontología controlada (spec 069) ──────────────────────────────────────────
.PHONY: ontologia ontologia-check
ontologia:                 ## 069: regenera los artefactos derivados de pipeline/data/ontologia/ontologia.yaml (web, preguntas Jev)
	pipeline/.venv/bin/python pipeline/scripts/ontologia_gen.py

ontologia-check:           ## 069: falla si un artefacto derivado está desfasado, si hay una copia literal del vocabulario o un bool() sobre una salida de LLM
	pipeline/.venv/bin/python pipeline/scripts/ontologia_check.py

.PHONY: revision-status
revision-status:           ## 069: cola de revisión (lo que ningún modelo decide solo) por punto de decisión y motivo
	cd pipeline && .venv/bin/python -m src.revision status
