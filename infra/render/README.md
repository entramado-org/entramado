# infra/render

`set_build_filters.sh` aplica a los 5 servicios de Render el `buildFilter` que declara `render.yaml` (el Blueprint
está pausado y nunca los aplicó: sin ellos cada push a `main` redespliega los 5, `sm-neo4j` incluido).
Primero `bash infra/render/set_build_filters.sh --check-only`: compara con lo vivo sin tocar nada (sale 2 si difiere).
Luego `bash infra/render/set_build_filters.sh`: PATCH solo de los que difieren y relectura exacta (sale 1 si no cuadra); no despliega.
Lo ejecuta el dueño: lee la API key de Render del Keychain (`-a entramado -s render-api-key`) y escribe en producción.
