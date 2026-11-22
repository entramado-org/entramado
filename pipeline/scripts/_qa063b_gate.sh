#!/bin/bash
# Puerta de idempotencia del Path A 063 (vive en el repo: el scratchpad pierde ficheros).
# Uso: _qa063b_gate.sh <outdir> <base-version>   p.ej. _qa063b_gate.sh /tmp/x 20261004-qa063z
# Levanta el efímero sobre el seed base, corre dos ciclos de loaders + colapso simétrico y exige
# alledges, accstatus y el export canónico idénticos entre ciclos. Spec 070: antes del ciclo 1 inyecta un
# fixture por seudónimo (arista a una sociedad sintética + alias sintético) que la política de
# `make relations` tiene que borrar, y tras cada ciclo el grafo tiene que coincidir con
# seudonimos_permitidos.yaml. El canónico final frente al del seed base solo puede diferir en lo que liste
# DIFF_ESPERADO=<fichero> (la copia revisada de <outdir>/base-vs-final.diff de una pasada anterior). Migraciones
# opcionales antes de los ciclos: PRE_MIGRATION=<file.cypher>, MIGRACION_PRENSA=<file.cypher> (spec 070, AC82),
# MIGRACION_CITAS=<file.cypher> (spec 070, AC90) y REPOSICION=<file.cypher> (spec 071).
# Cualquier diferencia o fixture superviviente termina con exit 1. Deja el efímero ARRIBA para
# métricas/export/lints.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
OUT="${1:?outdir}"; BASE="${2:?base version}"
export NEO4J_URI=bolt://192.168.64.3:7689 NEO4J_USER=neo4j NEO4J_PASSWORD=neo4j
export BASE_TGZ="$PWD/neo4j/neo4j-data-$BASE.tgz" BASE_SRC="$PWD/neo4j/data.$BASE/data"
D="$PWD/pipeline/scripts/_qa063b_dev.sh"
vm(){ container machine run -n sm-dev --root -- env BASE_TGZ="$BASE_TGZ" BASE_SRC="$BASE_SRC" bash "$D" "$@"; }
collapse(){
  (cd pipeline && uv run -q python -m scripts.gen_symmetric_collapse_migration >/dev/null) || return 1
  make -s qa-migrate FILE=pipeline/data/audit/patches/manual_collapse_symmetric_20260619.cypher CONFIRM=1 >/dev/null
}
falla(){ echo "❌ $*" >&2; exit 1; }
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
# REGISTRO_ROOT = raíz privada de la auditoría (census.sqlite + esc/decisions/ + esc/reglas_opacas.yaml), por defecto la
# de la 063. Las reglas deny opacas del overlay se resuelven contra ella (spec 071 AC114, AC116 y AC121): el loader la
# recibe en QA_CENSO_ROOT y aborta sin ella, y la puerta no arranca si falta.
REGISTRO_ROOT="${REGISTRO_ROOT:-pipeline/data/audit/dual/20260909-20260909-qa061}"
case "$REGISTRO_ROOT" in /*) ;; *) REGISTRO_ROOT="$PWD/$REGISTRO_ROOT" ;; esac
[ -f "$REGISTRO_ROOT/census.sqlite" ] || { echo "CENSO_FALTA: $REGISTRO_ROOT"; exit 1; }
export QA_CENSO_ROOT="$REGISTRO_ROOT"
# Nada de lo que la puerta lee sale a la consola: diffs, logs y salidas de migración llevan ids, documentos y citas
# (una retirada entre ciclos reproduciría su contenido). Van a ficheros en $OUT; en consola, marcas, recuentos,
# huellas sha256 y rutas.
# Spec 070 (AC1): ninguna regla pública ni fila del registro empareja un seudónimo, antes de tocar nada; y antes de
# `edit-up`, porque el loader no comprueba documentos públicos (AC56). En consola, solo su recuento.
(cd pipeline && uv run -q python scripts/seudonimos_check.py) > "$OUT/seudonimos-tree.log" 2>&1 \
  || falla "seudonimos_check: el repo empareja seudónimos: $OUT/seudonimos-tree.log"
grep -E '^seudonimos-check: [0-9]+ hallazgos$' "$OUT/seudonimos-tree.log"
# Una arista por entrada de la lista, o una por documento si la entrada acota `docs`.
N=$(cd pipeline && uv run -q python -c 'from src import seudonimos; print(seudonimos.aristas_esperadas(seudonimos.cargar_permitidos()))' \
      2> "$OUT/aristas-esperadas.err") \
  && grep -Eq '^[0-9]+$' <<< "$N" || falla "no se pudo leer seudonimos_permitidos.yaml: $OUT/aristas-esperadas.err"
vm rmwork >/dev/null 2>&1
{ vm extract && vm mkwork && vm edit-up; } > "$OUT/arranque.log" 2>&1 || { echo "ARRANQUE_FALLO: $OUT/arranque.log"; exit 1; }
# Cada captura falla cerrada, por su exit y por lo que deja: `set -uo pipefail` no aborta solo, y dos capturas
# fallidas (ficheros vacíos o a medias) pasarían la comparación entre ciclos. Nada sale a la consola antes de
# validarse: stdout y stderr de cada `vm` van a ficheros en $OUT, y luego se imprime el recuento (counts = «nodos,
# aristas») o la marca y la ruta del log.
capturar(){  # capturar <n> → c<n>.counts, a<n>.tsv, s<n>.tsv (y <captura><n>.err)
  vm counts-edit > "$OUT/counts$1.out" 2> "$OUT/counts$1.err" && tail -1 "$OUT/counts$1.out" > "$OUT/c$1.counts" \
    && grep -Eq '^[0-9]+, [0-9]+$' "$OUT/c$1.counts" || { echo "CAPTURA_FALLO counts $1: $OUT/counts$1.err"; exit 1; }
  vm alledges-edit > "$OUT/a$1.tsv" 2> "$OUT/alledges$1.err" && [ -s "$OUT/a$1.tsv" ] \
    || { echo "CAPTURA_FALLO alledges $1: $OUT/alledges$1.err"; exit 1; }
  vm accstatus-edit > "$OUT/s$1.tsv" 2> "$OUT/accstatus$1.err" && [ -s "$OUT/s$1.tsv" ] \
    || { echo "CAPTURA_FALLO accstatus $1: $OUT/accstatus$1.err"; exit 1; }
  echo "captura $1: $(cat "$OUT/c$1.counts")"
}
resumen(){  # resumen <diff>: cuántas líneas salen y entran, su sha256 y su ruta; nunca el contenido
  echo "  $(grep -c '^<' "$1") salen, $(grep -c '^>' "$1") entran; sha256 $(shasum -a 256 "$1" | cut -d' ' -f1); $1"
}
# Una redirección fallida (destino sin permiso, disco lleno) también da 1, como unas diferencias: el diff ha de dejar su
# fichero, recién escrito y con contenido si hay diferencias. Uno ausente nunca vale por «sin diferencias».
comparar(){  # comparar <antes> <después> <diff>: 0 iguales, 1 distintos; un diff que falla (2 o más) aborta
  rm -f "$3" || { echo "DIFF_FALLO: $3"; exit 1; }
  diff "$1" "$2" > "$3" 2> "$3.err"; local rc=$?
  [ "$rc" -le 1 ] && [ -f "$3" ] && { [ "$rc" -eq 0 ] || [ -s "$3" ]; } || { echo "DIFF_FALLO $rc: $3.err"; exit 1; }
  return "$rc"
}
# Las comprobaciones finales escriben su stdout y su stderr en $OUT (spec 071 AC82: un diagnóstico puede nombrar lo que
# no ha de salir a la consola); en consola, su marca, cuántas líneas de problema dejan y la ruta.
comprobar(){  # comprobar <NOMBRE> <prefijo de sus problemas> <log> <orden…>: el exit de la orden
  local nombre="$1" prefijo="$2" log="$3"; shift 3
  "$@" > "$log" 2>&1; local rc=$?
  if [ "$rc" -eq 0 ]; then echo "${nombre}_OK: $log"
  else echo "${nombre}_FALLO (exit $rc; $(grep -c "^$prefijo" "$log") líneas de problema): $log"; fi
  return "$rc"
}
migrar(){  # migrar <NOMBRE> <cypher>: una vez y antes de los ciclos; su salida (la Cypher lleva citas) a fichero
  make -s qa-migrate FILE="$2" CONFIRM=1 > "$OUT/$1.log" 2>&1; local rc=$?
  echo "$1 exit $rc"
  [ "$rc" -eq 0 ] || { echo "${1}_FALLO: $OUT/$1.log"; exit 1; }
}
exportar(){  # exportar <n> → snap<n>/ (canonico.tsv no vacío): el export canónico del grafo, su salida a fichero
  bash "$D" snap-edit "$OUT/snap$1" > "$OUT/snap$1.log" 2>&1 && [ -s "$OUT/snap$1/canonico.tsv" ] \
    || falla "export canónico $1: $OUT/snap$1.log"
}
# El canónico del seed tal como está publicado: la base de la comparación final (antes de PRE_MIGRATION).
exportar 0
# Base (c0/a0/s0): el seed tal cual, antes de las migraciones de abajo. Lo que estas cambian aparece en el diff
# a0 → a2 y tiene que estar en el diff esperado.
capturar 0
# Migración opcional, una vez y antes de los ciclos (p. ej. renombrar un nodo): PRE_MIGRATION=<file.cypher>.
if [ -n "${PRE_MIGRATION:-}" ]; then migrar PRE_MIGRATION "$PRE_MIGRATION"; fi
# Spec 070 (AC82): la nota reescrita de una arista de prensa en sus copias vivas, que el ciclo no toca (la capa de prensa
# no se recarga sobre el linaje), una vez y antes de los ciclos: MIGRACION_PRENSA=<file.cypher>, la salida de
# _migracion_070_prensa.py (fuera del repo). Cambia props: sale en el canónico frente al seed base, no en alledges.
if [ -n "${MIGRACION_PRENSA:-}" ]; then migrar MIGRACION_PRENSA "$MIGRACION_PRENSA"; fi
# Spec 070 (AC90): la cita literal del auto en una arista judicial de seudónimo cuya cita guardada omite texto sin
# marcarlo, una vez y antes de los ciclos: MIGRACION_CITAS=<file.cypher>, la salida de _migracion_070_citas.py (fuera
# del repo). No mueve aristas: cambia el `source_quote` de una (canónico frente al seed base; alledges igual).
if [ -n "${MIGRACION_CITAS:-}" ]; then migrar MIGRACION_CITAS "$MIGRACION_CITAS"; fi
# Spec 071: aristas que se reponen, una vez y antes de los ciclos (salida de _reposicion_071.py, fuera del repo):
# REPOSICION=<file.cypher>. Entran en a0 → a2; los dos ciclos y registro_check prueban que ninguna regla las
# vuelve a borrar.
if [ -n "${REPOSICION:-}" ]; then migrar REPOSICION "$REPOSICION"; fi
vm fixture-up > "$OUT/fixture-up.log" 2>&1 || falla "fixture-up: $OUT/fixture-up.log"
FIXTURE_RECUENTOS='^fixture 070: aristas=[0-9]+ alias=[0-9]+ nodo=[0-9]+ sin_id=[0-9]+ '
FIXTURE_RECUENTOS+='· seudónimo↔sociedad/organismo=[0-9]+ \(lista: [0-9]+\)$'
for i in 1 2; do
  bash pipeline/scripts/_qa063b_cycle.sh > "$OUT/cycle$i.log" 2>&1 || { echo "CICLO_FALLO $i: $OUT/cycle$i.log"; exit 1; }
  collapse > "$OUT/colapso$i.log" 2>&1 || { echo "COLAPSO_FALLO $i: $OUT/colapso$i.log"; exit 1; }
  if [ "$i" = 1 ]; then   # su salida a fichero (AC57); en consola, solo la línea de recuentos, de formato fijo
    vm fixture-check "$N" > "$OUT/fixture-check.log" 2>&1 \
      || falla "FIXTURE_070: la política de seudónimos no se aplicó en el ciclo 1: $OUT/fixture-check.log"
    grep -E "$FIXTURE_RECUENTOS" "$OUT/fixture-check.log"
  fi
  capturar "$i"
  exportar "$i"
  (cd pipeline && uv run -q python scripts/seudonimos_check.py --snapshot "$OUT/snap$i") \
    > "$OUT/seudonimos-snap$i.log" 2>&1 \
    || falla "el grafo del ciclo $i no coincide con seudonimos_permitidos.yaml: $OUT/seudonimos-snap$i.log"
  grep -E '^seudonimos-check: .+; [0-9]+ problemas$' "$OUT/seudonimos-snap$i.log"
done
# Idempotencia: el segundo ciclo no cambia nada.
comparar "$OUT/a1.tsv" "$OUT/a2.tsv" "$OUT/a1-a2.diff" || { echo ALLEDGES_DIFIEREN; resumen "$OUT/a1-a2.diff"; exit 1; }
echo ALLEDGES_IDENTICOS
comparar "$OUT/s1.tsv" "$OUT/s2.tsv" "$OUT/s1-s2.diff" || { echo ACC_DIFIEREN; resumen "$OUT/s1-s2.diff"; exit 1; }
echo ACC_IDENTICOS
comparar "$OUT/snap1/canonico.tsv" "$OUT/snap2/canonico.tsv" "$OUT/canonico1-2.diff" \
  || { echo CANONICO_DIFIERE_ENTRE_CICLOS; resumen "$OUT/canonico1-2.diff"; exit 1; }
echo CANONICO_IDENTICO
# Frente al seed base: el canónico final solo difiere en lo que liste DIFF_ESPERADO.
echo "--- frente al seed base"
comparar "$OUT/snap0/canonico.tsv" "$OUT/snap2/canonico.tsv" "$OUT/base-vs-final.diff"; base=$?
resumen "$OUT/base-vs-final.diff"
if [ "$base" -eq 1 ]; then
  { [ -n "${DIFF_ESPERADO:-}" ] && cmp -s "$OUT/base-vs-final.diff" "$DIFF_ESPERADO"; } \
    || falla "BASE_DIFIERE fuera de lo listado: revisa el diff y, si todo viene de lo que aplica este Path A, repite con DIFF_ESPERADO=<copia revisada>"
  echo BASE_DIFF_LISTADO
fi
# a0 → a2: los cambios esperados del Path A, con los de las migraciones.
echo "--- cambios a0 → a2"
comparar "$OUT/a0.tsv" "$OUT/a2.tsv" "$OUT/a0-a2.diff"; resumen "$OUT/a0-a2.diff"
# Registro público: ninguna arista que registro.yaml da por «suprimida» sigue viva en el grafo final.
(cd pipeline && uv run -q python scripts/_qa063b_export_snap.py "$OUT/snap2") > "$OUT/export.log" 2>&1 \
  || { echo "EXPORT_FALLO: $OUT/export.log"; exit 1; }
# El exportador saca todos los nodos (MATCH (n)) y todas las aristas (MATCH ()-[r]->()), una línea cada uno: las mismas
# cuentas que c2, porque nada cambia el grafo entre medias. Uno corto pasaría por un grafo sin las retiradas.
exportado="$(wc -l < "$OUT/snap2/nodes.jsonl" | tr -d ' '), $(wc -l < "$OUT/snap2/relationships.jsonl" | tr -d ' ')"
[ "$exportado" = "$(cat "$OUT/c2.counts")" ] \
  || { echo "EXPORT_INCOMPLETO (exportado: $exportado; c2: $(cat "$OUT/c2.counts"))"; exit 1; }
# Y lo decidido está aplicado (spec 071 AC43): ninguna retirada de un pack `applied` sin regla, fila o edición de su
# capa sigue viva. Corren las dos y la puerta falla si falla cualquiera.
registro(){ (cd pipeline && uv run -q python scripts/registro_check.py --snapshot "$OUT/snap2" --root "$REGISTRO_ROOT" \
  --overlay ..); }
materializacion(){ (cd pipeline && uv run -q python -m src.qa_esc_apply --root "$REGISTRO_ROOT" --repo .. \
  materializacion --snapshot "$OUT/snap2"); }
rc=0
comprobar REGISTRO 'REGISTRO ' "$OUT/registro.log" registro || rc=1
comprobar MATERIALIZACION 'MATERIALIZACION: ' "$OUT/materializacion.log" materializacion || rc=1
[ "$rc" -eq 0 ] && echo PUERTA_OK
exit "$rc"
