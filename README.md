# El Entramado

**[entramado.org](https://entramado.org)** — un grafo de conocimiento sobre los
casos de corrupción que rodean al Gobierno en España, construido desde
documentos judiciales y oficiales públicos. Cada relación del grafo es trazable
a su fuente: documento, página y cita textual.

> **Presunción de inocencia.** Esta herramienta organiza información de
> documentos públicos y procedimientos en curso. Las relaciones reflejan lo que
> dicen las fuentes citadas — indicios, imputaciones o acusaciones — y no son
> una declaración de culpabilidad. Toda afirmación enlaza a su fuente, y el
> estado procesal de cada persona (investigado, acusado, condenado, absuelto…)
> se distingue explícitamente.

Contacto y rectificaciones: `contacto@entramado.org`.

## Qué hay dentro

- **Corpus judicial**: 85 documentos oficiales censados (sentencias, autos,
  informes UCO/UDEF, escritos de acusación y calificación, diarios de sesiones
  del Senado y del Congreso) sobre el caso Koldo/mascarillas, la rama de obra
  pública (Cerdán/Servinabar/Acciona), Ábalos, Aldama, Begoña Gómez, David
  Sánchez/Diputación de Badajoz, Zapatero/Plus Ultra, Leire Díez, financiación
  del PSOE, el caso Montoro o la causa del fiscal general del Estado. Las resoluciones judiciales y publicaciones
  oficiales están en `data/` tal cual se publicaron; los informes policiales de
  sumario se censan pero **no se redistribuyen crudos** en el repo (el sitio
  sirve su texto con cita en `/documentos`). Censo completo de procedencia:
  [`data/SOURCES.md`](./data/SOURCES.md).
- **Grafo con procedencia por capas** (`source_kind` en cada arista):
  - `judicial` — extraído de los documentos del corpus, con cita textual;
  - `contexto` — hechos públicos curados (cargos, partido, parentesco) que
    identifican a las personas pero no son indicios;
  - `ultima_hora` — desarrollos de prensa, aislados y siempre atribuidos al
    medio de origen;
  - registro mercantil (BORME), subvenciones (BDNS) y contratación pública
    (PLACSP) — datos oficiales que contextualizan, no imputan.
- **Capa editorial**: feed de última hora, briefings, patrones llamativos y la
  crítica institucional — todo citado, en `pipeline/data/` y la web.

## La web ([entramado.org](https://entramado.org))

Explorador del grafo (`/explore`), documentos con lectura en fuente
(`/documentos`), flujos de dinero (`/flujos`), fichas de casos (`/caso`) e
investigados (`/investigados`), última hora (`/ultima-hora`), patrones
(`/patrones`), glosario (`/glosario`) y análisis institucional
(`/institucional`).

## Conector MCP

El grafo se puede consultar desde tu propio Claude (claude.ai, Desktop o Code)
vía el conector MCP público de solo lectura en **`https://entramado.org/mcp`**:
búsqueda de entidades y documentos, relaciones con evidencia textual, caminos
entre entidades, flujos de dinero, cronologías, métricas de red, subvenciones y
contratación. Instrucciones en [entramado.org/conectar](https://entramado.org/conectar).

## Arquitectura

La app corre en **Docker**. La **ingesta** es un proceso editorial aparte
(Python/`uv` en el host) que construye el grafo y lo escribe en Neo4j; la app en
marcha no la necesita.

```
PIPELINE DE INGESTA (uv, py3.12)                  DOCKER COMPOSE — la app
  proceso editorial, se ejecuta aparte              api (FastAPI :8000)
    LangGraph · PyMuPDF · embeddings                  consultas al grafo + briefings
    extracción estructurada + capas    ──bolt──▶  mcp (conector MCP :8008)
    curadas + auditoría de calidad                neo4j :7474/:7687
    → escribe el grafo en Neo4j                       grafo + vector + fulltext
                                                    web (Next.js :3000)
```

- **Extracción** (en ingesta): extracción estructurada por chunk hacia un
  esquema cerrado de entidades/relaciones, con **cita textual obligatoria** en
  cada hecho, validación/reparación y verificación posterior contra el
  documento fuente; resolución híbrida de entidades (alias semilla → fuzzy →
  merge por embedding); procedencia (`source_kind`) en cada arista.
- **Almacenamiento**: Neo4j 5.26 con índice vectorial nativo (1024 dim,
  coseno) e índices full-text para búsqueda difusa.
- **API**: FastAPI — búsqueda, ego-grafo n-hop con tope de grado, overview,
  cronología, detalle con citas, briefings, feed de última hora, patrones.
- **Web**: Next.js 14 — grafo de fuerza WebGL, filtros por tipo, detalle con
  citas, y las páginas editoriales.

## Ejecutarlo en local

Requisitos: Docker, Node 20 y Python 3.12 vía
[`uv`](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                 # ajusta NEO4J_PASSWORD si quieres

# 1. Neo4j
make neo4j                           # espera a healthy (localhost:7474)

# 2. La app — funciona sobre los datos que haya en Neo4j
make up                              # api :8000 + web :3000
open http://localhost:3000
```

La app necesita un Neo4j **poblado**: el grafo se construye con el pipeline de
ingesta (sección siguiente), que es un proceso editorial con revisión humana —
no un comando que se lanza y ya. Para proponer que una trama nueva entre al
grafo, ver [Contribuir](#contribuir).

`make help` lista todos los targets. El proyecto compose se llama `entramado`
(red `entramado_default`, imágenes `entramado-*`).

## Cómo funciona la ingesta (`pipeline/`)

DAG de LangGraph: `discover → extract_text → extract_facts → resolve →
load_graph → embed → report`. Los intermedios pesados viven en disco
(`pipeline/data/{chunks,facts,entities}`), así que una ejecución interrumpida se
reanuda sin repetir la extracción. Sobre el grafo base se aplican después
las capas curadas (`make context`, `make relations`, overlays mercantil/BDNS/
PLACSP) y una auditoría de calidad con ledger propio (local, fuera del árbol
público — como el resto de deliberación interna).

> **Nota de reproducibilidad**: el árbol público incluye 71 de los 85
> documentos del corpus (no se redistribuyen los informes de sumario, una
> edición de periódico ni siete resoluciones difundidas por la prensa que
> contienen identificadores de personas físicas — ver
> [`data/SOURCES.md`](./data/SOURCES.md)), así que una
> re-ingesta local sobre un clon reproduce un grafo *parcial*. El grafo completo
> publicado en entramado.org se construye desde el corpus íntegro y viaja como
> seed versionado.

Módulos clave: `pdf_extract.py` (PyMuPDF + limpieza de marcas de agua),
`chunking.py` (por página, acotado en tokens), `schema.py` (tipos cerrados),
`extract_facts.py` (extracción JSON con validación/reparación),
`entity_resolution.py`, `neo4j_loader.py` (MERGE idempotente), `graph.py`.

## Notas

- Algunos autos del Tribunal Supremo (p. ej. `ATS_8919`) se publican con
  seudónimos anonimizados; es el documento fuente, no un error de extracción.
  Esos seudónimos solo conservan las relaciones con sociedades u organismos y
  los alias que figuran en `pipeline/data/relations/seudonimos_permitidos.yaml`;
  todo lo demás se retira al construir el grafo, y las retiradas constan en el
  registro público de rectificaciones con los extremos neutralizados.
- Umbrales de resolución de entidades en `pipeline/src/config.py`
  (`FUZZY_AUTOMERGE`, `EMBED_AUTOMERGE`).

## Contribuir

Este proyecto quiere ser una **herramienta de fiscalización del poder**. El
alcance declarado son los casos que rodean al Gobierno en ejercicio; otras
tramas documentadas (de cualquier administración, partido o época) se reciben y
se valoran, sin garantía de que entren.

**Lo que escribes en GitHub es público al instante y un pull request no se
puede borrar.** Por eso todo lo que afecta a personas va por correo a
**contacto@entramado.org**, no por issues ni PRs:

- **Rectificaciones** de algo publicado sobre ti o un tercero (ver el
  [aviso legal](https://entramado.org/aviso-legal)); tienen prioridad sobre
  cualquier otra contribución.
- **Correcciones de contenido con fuente**: errores de hecho, estados
  procesales desactualizados, atribuciones de medio, homónimos mal fusionados.
- **Tramas nuevas**: el censo de documentos oficiales con su procedencia es la
  contribución que más acelera. Lo ya detectado como pendiente está en
  [`BACKLOG.md`](./BACKLOG.md).

Por GitHub: **código** (pipeline, API, web, MCP), documentación y errores
técnicos, sin datos personales ni acusaciones. La ingesta y las correcciones
del grafo las ejecutan los mantenedores con el pipeline curado y sus gates.

Las tres reglas que resumen todas las demás:

1. **Presunción de inocencia**: el estado procesal se etiqueta con exactitud y
   nunca se agrava; lo exculpatorio se incorpora con la misma diligencia.
2. **Sin fuente no entra**: toda afirmación lleva documento+página, medio+URL
   o identificador de registro oficial.
3. **Solo los datos personales pertinentes**: nada de DNI, teléfonos,
   domicilios ni cuentas de personas físicas, ni siquiera citando documentos que
   los contengan.

La participación en el proyecto se rige por el
[código de conducta](./CODE_OF_CONDUCT.md).

El detalle completo está en [`CONTRIBUTING.md`](./CONTRIBUTING.md); las
vulnerabilidades, en privado según [`SECURITY.md`](./SECURITY.md).

## Licencias

- **Código** — [GNU AGPL-3.0](./LICENSE), que obliga a ofrecer el código de las
  versiones modificadas que se ofrezcan como servicio en red.
- **Datos editoriales** — las capas YAML curadas
  (`pipeline/data/{ultima_hora,patrones,context,accusations}/`,
  `api/app/data/cases.yaml`) y los textos editoriales del sitio son
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): reutilización con
  atribución a [entramado.org](https://entramado.org). Detalle de ámbito y
  exclusiones en [`LICENSE-DATA`](./LICENSE-DATA).
- **`data/*.pdf`** — documentos oficiales públicos (judiciales, parlamentarios,
  de auditoría) reproducidos tal cual desde sus fuentes; son obras de terceros
  y **no** quedan cubiertos por las licencias anteriores. Naturaleza y
  procedencia documento a documento en [`data/SOURCES.md`](./data/SOURCES.md).

El historial público de este repositorio comienza en su release de 2026.
