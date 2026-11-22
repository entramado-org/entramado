#!/usr/bin/env python3
"""El registro público de rectificaciones (`pipeline/data/rectificaciones/registro.yaml`) dice la verdad.

    python3 pipeline/scripts/registro_check.py --tree                       # CI: la forma de cada fila
    python3 pipeline/scripts/registro_check.py --snapshot DIR --root AUDIT --overlay REPO  # puerta del Path A

`--tree`: cada fila lleva los diez campos, con texto (`unidad` puede ser ""), un id `R-YYYY-MM-DD-NNNN`
que no se repite, `origen` y `decision` del vocabulario de la cabecera, fechas `YYYY-MM-DD` y
`seed_version` = `pendiente-path-a` o `AAAAMMDD-…`. Y el anonimato del registro frente al overlay público (spec 071
AC113, AC115 y AC123): el marcador de anonimato (`[tercero]`, `[sociedad]`…) solo va en la fila de una unidad cuya fila
vigente la retira, o sin unidad; la huella de una fila con marcador, con corchetes o sin ellos, solo en una regla deny
opaca (`{unidad, kinds?, reason}`, solo en una sección deny del overlay de relaciones), que respalda una fila con
marcador de su clave. Una fila marcada, entera (sus valores, sus claves y sus comentarios, salvo el valor de su
`unidad`: AC164), no nombra otro extremo que los de las posiciones de su `dato` y su `entidad` (AC143 y AC161: un dato
sin la forma de la fila de una arista no nombra ninguno), ninguna fila de arista nombra el mismo extremo dos veces
(AC153), una entrada deny es una opaca exacta o una explícita (AC159), y lo estructural (de una fila, su `unidad`; de
una opaca, su `unidad`, sus `declaraciones` y la etiqueta de su clave en su motivo validado) se deja fuera por su
posición, nunca sustituyendo la cadena: cualquier otra aparición de la huella, en mayúsculas o en minúsculas, en un
comentario (cada uno se barre además aparte) o escrita con un escape (se barre también lo leído: AC163), es un problema
(AC144 y AC146). Cada entrada de un YAML lleva las claves de la sección en que está (AC162). Lo mismo con `--snapshot`
y en `materializacion` (AC149). Una «suprimido» sin unidad ni marcador tiene la forma de la fila de una arista o una
fórmula cerrada de seudónimo judicial: con otra, nada la comprobaría (AC183).

`--snapshot`: además, la fila vigente de cada unidad (la de fecha más reciente; a igual fecha, la de id mayor) dice la
verdad frente a un export del grafo. Ninguna arista que el registro da por «suprimida» sigue viva en él
(`nodes.jsonl` + `relationships.jsonl`; vacío, con una línea ilegible o con aristas hacia nodos que no
están, no vale: exit 2, en vez de leerlo como un grafo sin las retiradas). `--root` es la raíz PRIVADA de la
auditoría (`census.sqlite` + `esc/decisions/*.json`, gitignored): de la unidad de cada fila `suprimido`
salen el par, el tipo, la capa (`source_kind`), el documento y la cita de la arista retirada, y en el export
se cuentan las vivas del mismo par y tipo en cualquier dirección (la regla `deny` borra A->B y B->A: una
copia inversa viva es una regla que no actuó) con la misma capa, el mismo documento (sin documento cuenta
como igual) y la misma cita (normalizada y recortada a 300; una cita que solo contiene la retirada es otra
copia); o con el tipo, el sentido o la firma que le dejan las reglas `fix` que la alcanzan (las de las operaciones
vigentes de otras aristas y, con `--overlay`, las del overlay; mismo par, el documento de la regla o ninguno y una
cita contenida en la otra), compuestas en cualquier orden, como en los Path A sucesivos (un retype seguido del `set`
de otra regla sobre el tipo nuevo): el loader aplica los `fix` antes que los `deny`, y uno que la cambia la salva.
Una acusación de la capa `acusacion`, una arista de prensa, de última hora o de contexto (con documento o sin él) y
una postura sobre una afirmación cuentan por la identidad de su cargador, con cualquier cita: la recrea con un MERGE por
par, tipo y capa que actualiza la cita (y, en una acusación, el documento) (AC59, AC101 y AC110). La afirmación de una
postura es su claim_id, que `units` no guarda y se recupera del objeto de su relación en el censo; si no se recupera, la
postura es «indeterminada» y nunca la acredita ni la deja viva otra afirmación de su titular (AC104). Las citas se
comparan normalizadas (NFKC, sin mayúsculas y con los espacios colapsados: AC107). Si un extremo de
la arista del censo ya no está en el export (re-identificado después del censo), las fases se componen también sobre
los pares actuales a que apuntan las reglas del overlay y sus copias se buscan en cualquier par por su firma: un tipo
al que la llevan las fases, su documento y su cita («par re-identificado»); una acusación o una postura, además por
su identidad en esos pares actuales, con cualquier documento y cita (AC68). Puede quedar UNA viva solo si la
decisión nombra (por un prefijo hex de 8 a 64 caracteres en su motivo o evidencia, que señale UNA sola arista de los
packs de la misma entidad contando la retirada: si señala varias, «referencia ambigua a la conservada»; si solo la
retirada, ninguna) la unidad CONSERVADA, y esa conservada es su gemela: el mismo hecho (tipo y extremos,
con dirección salvo en los tipos simétricos) con capa, documento y cita tal como la dejan las decisiones (un
`set_prop` sobre ellas cambia su firma) iguales a los de una de las copias vivas que pueden ser la retirada,
con lo que en el export no se sabe cuál de las dos sobrevive. La gemela que otra decisión retiró o
transformó (deny, retype, invert) no puede ser la superviviente y no da tolerancia. En cualquier otro caso,
ninguna; el texto libre del motivo no da tolerancia. Y la conservada tiene que seguir publicada: el mismo
hecho, con su dirección (una OWNS invertida es otra propiedad), vivo desde su documento («gemela conservada
ausente» si no); si un extremo no tiene entity_id (un Claim), la cita ocupa su lugar. No cuenta si alguna
decisión la retiró, la retipó o la invirtió, o si uno de sus extremos con entity_id ya no está en el export;
un `set_prop` o un `set_status` solo cambian props y la dejan donde estaba. Una fila «rectificado» de una arista
cuya operación vigente la retipa o la invierte no deja viva ninguna copia en un estado que no sea el decidido (otro
tipo, también el antiguo con la capa que deja la carga, AC84; o el tipo decidido fuera de su sentido: un swap que el
retype no decidió, AC71, o la copia sin invertir) ni ninguna en el estado decidido sin la carga de su `set` tal como
la escribe el loader, con sus props derivadas (`date_norm` y `date_year` de una fecha, el estado en su forma
canónica), aunque otra la lleve (AC73, AC76 y AC77; «arista «rectificada» viva sin corregir»). Las copias cuyo
sentido y carga se juzgan se identifican siempre igual, en el par del censo y en los actuales (AC85): una acusación o
una postura, por la identidad de su cargador, con cualquier documento y cita (AC81); el resto, por documento y cita,
con cualquier capa (la que conserva la del censo no lleva la carga, AC80); y se clasifican una sola vez, igual que en
`materializacion` (`clasificar`, AC96): la tolerancia solo exime las incompatibles, nunca una en el estado decidido sin
su carga. La de una corrección de props (un set_prop vigente) no deja ninguna copia de la arista sin su carga, sin
tolerancia, como `materializacion` (AC83; el set_status no entra: la capa de acusaciones recrea y escala el estado
después de la decisión). La de una corrección de props, un retype o un invert se busca siempre, además, por su
documento y su cita en cualquier otro par («par distinto del de la regla», AC86, AC89 y AC94), con criterio de
identidad: cuenta la que comparte exactamente un extremo con la arista del censo, en el sentido que da su posición, y
no la que coincide en par y tipo con otra arista del censo de la misma firma, censal o resultante; si la arista ya
tiene copia en sus pares, solo la de un par que el overlay declara suyo (una escisión o un merge que liga sus extremos,
o una regla con su etiqueta): las demás son otros hechos de su pasaje, un aviso que no cuenta como problema. Una
«suprimido» se busca además en esos pares declarados aunque sus nodos sigan: el loader escinde antes del deny (AC109).
La forma de otra arista del censo que excluye una copia lleva su dirección (AC102), y la etiqueta de una regla es de su
unidad: no le da su par a otra de la misma firma (AC103). La que también puede ser otra arista del censo de la misma
firma con un extremo fuera del export (comparte con ella el que le queda y alguno de sus estados alcanzables tiene su
tipo y su sentido) no se atribuye a ninguna: «copia ambigua entre unidades», que se resuelve a mano (AC91 y AC98). La
«rectificado» de un retype, un invert o un set_prop afirma además la arista corregida: sin ninguna copia en el estado
que decide, «rectificación decidida sin arista corregida», salvo que la fila ya tenga otro problema de copias (AC92 y
AC100). Y la «rectificado» que completa una «suprimido» anterior de la misma unidad necesita una operación vigente que
no la retire y una copia publicada en el estado que esta deja: en su sentido salvo en las simétricas (AC70), con la capa
de antes o la de después de su carga (AC88) o, si la retipa o la invierte, en el estado decidido y con los props de
su `set` (AC69 y AC74), desde el documento que cita si cita uno, también en otro par por la búsqueda libre (AC97).
Una «suprimido» vigente cuya operación vigente
conserva la arista (un retype, un invert, un set_prop o un keep) contradice la decisión (AC93). Una fila cuya unidad
no es una arista no entra, salvo la de una clave privada (AC121), que se comprueba con sus predicados. Una «suprimido»
sin unidad, sí (AC183 y AC184, como en `materializacion`): con marcador, con su declaración privada de `filas:` (el
fichero privado da la de cada una y solo las suyas, o la puerta no corre); sin él, la de una arista, con las reglas deny
explícitas del overlay que la retiran («retirada sin regla pública» sin ninguna), y una fórmula cerrada de seudónimo,
con su política (spec 070). `--snapshot` exige `--overlay`: sin sus reglas, lo que certificaría no sería lo que aplica
el loader (AC179).
Una «suprimido» de un deny con una copia viva del mismo par orientado, tipo, documento y cita en otra capa dice algo
falso salvo que lo declare con la fórmula de la copia duplicada (AC119; la que otra arista del censo de su par, que es
otro hecho, explica por la identidad de su capa es de esa otra). Cada fórmula de una asociación que el grafo sigue
publicando tras el overlay es literalmente verdad o un problema (D1): la copia duplicada, si hay una arista viva de su
tipo, en su sentido y desde su documento (AC119); la de AC125, si una regla `fix` retipa su par desde su documento al
tipo que nombra (AC136) y el grafo publica esa copia; la de AC142, si hay una arista viva entre sus dos nodos de su tipo
(sin tipos) o de cada tipo que nombra; también las de las filas retiradas por un predicado privado (AC150). Lo que el
grafo publica tras el overlay se simula con las fases del loader tal como las ejecuta su Cypher: cada una contra el
estado en que la deja la anterior y una sola transformación por arista (AC158). Una fila marcada cuya clave no da ningún
predicado suyo (AC157) o que no oculta ninguno de sus extremos (AC161) es un problema: nunca se salta. Las reglas
deny opacas se resuelven contra la raíz privada (`--root`), como el loader (AC114, AC121 y AC127), y cada predicado
privado se prepara como el loader prepara una deny explícita, tal como se declaró (AC151 y AC152). Con `--overlay`,
además, ningún fichero de texto público (los que publica git: AC133; de un JSON, cada elemento de una lista con su
clave, AC145), ningún texto del export (todos sus nodos, también los que no tienen entity_id: AC147) ni la propia fila
fuera de su `unidad` (AC143) permite reconstruir la arista retirada de una fila anonimizada (AC122-bis): por id o por
nombre (con el resolver del loader: AC137), una entrada con el oculto, el visible y el tipo de la retirada, o con el
oculto y una cita suya; y, si el grafo no publica el par tras el overlay, ninguna con el oculto y el visible (lo mismo
corre `materializacion`, que también tiene el censo: AC169). Una fila marcada cuyo par sí publica el grafo no oculta
nada: nombra sus dos extremos (D1). Son avisos un ítem del feed, un texto
curado de contexto o de acusaciones o el de un nodo del export (que publican el par), una escisión, una arista del grafo
con el oculto como extremo y el visible en su texto (D2), con la huella de la fila, y una regla `fix` cuya revelación
publica el grafo en la arista que corrige (AC132). Ninguna fila marcada lleva un trozo de su cita retirada (D2; los
caracteres de formato invisibles no lo esconden: AC148), ni ninguna entrada pública fuera de su regla opaca la huella
de su unidad, de 8 a 64 hex (AC131). Una unidad cuya cita reescribe una migración del Path A se identifica también
por la cita de después que declara la raíz privada (`esc/migraciones_citas.yaml`, solo huellas; sin el fichero, nada
cambia), y solo ella; una declaración que no vale no cuenta y es un problema (AC284–AC288).

El informe nombra la fila por su id si tiene la forma del registro (si no, por su posición, «fila N») y
nunca reproduce un valor leído del registro, ni los extremos ni la cita: de un YAML que no carga (también con una clave
repetida en cualquier nivel, que PyYAML resolvería en silencio a la última: AC108) da el fichero y la posición, y de
cualquier otro error, solo su clase. El log de Actions del repo es público.
Solo stdlib + PyYAML: no abre Neo4j y del pipeline solo carga `alcance` y la ontología (`pipeline/src/`, igual de
ligeros: la carga de un `set` se prepara como en el loader sin importarlo; una fecha que no es ISO ni un año la
interpreta `dateparser`, que el entorno de la API no tiene, y entonces la decisión no vale). Exit 0 sin problemas,
1 con problemas, 2 si un YAML no carga, si falta el censo o el export, si el export no vale, si una decisión
no vale (sin un decided_at con zona, con una precedencia irresoluble o con una carga que el loader descarta: AC55 y
AC78; el motivo nombra el prop solo si es del vocabulario cerrado, y si no, su posición: AC82, y la unidad solo si su
`unit_id` tiene la forma de una unidad, y si no, su posición: AC95) o ante un error interno.
"""
from __future__ import annotations

import argparse
import base64
import bisect
import codecs
from collections import Counter, defaultdict, namedtuple
import functools
import hashlib
import json
from pathlib import Path
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'pipeline'))

from src.alcance import (CAMPOS_OPACA, ESTRUCTURALES_OPACA, MARCADOR, POSTURAS, RANGO_CAPA, Opacas,  # noqa: E402
                         ReglaOpacaInvalida, YamlInvalido, alcanza_loader, cargar_documentos, carga, casa,
                         cita_normalizada, declaraciones_validas, es_opaca, forma_deny, huella, leer_yaml,
                         motivo_opaco_valido, nombre_de_unidad, regla_de, resolver, vigentes)
from src.ontologia import ontologia  # noqa: E402

REGISTRO = REPO / 'pipeline/data/rectificaciones/registro.yaml'
CAMPOS = ('id', 'fecha', 'origen', 'entidad', 'unidad', 'dato', 'fuente', 'decision', 'fecha_resolucion',
          'seed_version')
ORIGENES = ('auditoria-063', 'auditoria-067', 'solicitud')
DECISIONES = ('rectificado', 'suprimido', 'desestimado')
SEED_PENDIENTE = 'pendiente-path-a'
_ID = re.compile(r'R-\d{4}-\d{2}-\d{2}-\d{4}')
_FECHA = re.compile(r'\d{4}-\d{2}-\d{2}')
_SEED = re.compile(r'\d{8}-\S+')
_UNIDAD = re.compile(r'[0-9a-f]{64}')
# Prefijo de unit_id con el que una decisión nombra otra unidad («se conserva la arista f86b4e9c»).
_HEX = re.compile(r'[0-9a-f]{8,64}')
QUOTE_CHARS = 300
# Nodos sin entity_id: en el export real (qa067b) solo Document, Chunk y Claim. El exportador
# (`_qa063b_export_snap.py`) saca todos los nodos tal cual, sin etiquetas auxiliares propias.
SIN_ENTITY_ID = frozenset({'Claim', 'Chunk', 'Document'})
DOCUMENTAL = frozenset({'Chunk', 'Document'})


def _fragmento(labels) -> bool:
    """Un fragmento de documento del export: lleva Chunk y SOLO etiquetas documentales. Un nodo que además lleva una de
    entidad (Person+Chunk) es una ficha y no la fuente: su texto ni prueba una cita ni se rebaja a aviso."""
    labels = set(labels or ())
    return 'Chunk' in labels and labels <= DOCUMENTAL


SIMETRICOS = frozenset(ontologia().simetricas)
# Lo que una decisión hace con una arista y la quita de donde estaba: la borra, le cambia el tipo o la invierte.
QUITAN = frozenset({'deny', 'retype', 'invert'})
# Los props que forman la firma de una copia (`_rasgos`): una regla `fix` que cambia alguno la saca de su deny.
FIRMA = ('source_kind', 'source_doc', 'source_quote')
# Lo que dice un diagnóstico de una copia hallada fuera de los pares que deducen las reglas (spec 071 AC86 y AC89).
OTRO_PAR = 'par distinto del de la regla; '
# Los motivos que los dos checks dan igual (spec 071 AC91, AC92 y AC100) y su aviso común de AC94.
AMBIGUA = 'copia ambigua entre unidades'
SIN_CORREGIDA = 'rectificación decidida sin arista corregida'
NO_ATRIBUIDA = 'copia del mismo pasaje en otro par, hecho no atribuido'
# Una postura cuya afirmación no se recupera del censo (spec 071 AC104), con el mismo texto en los dos checks.
INDETERMINADA = 'postura indeterminada: su afirmación no se recupera del censo'
# La fórmula cerrada con que una fila «suprimido» declara que retiró una copia duplicada de una asociación que el grafo
# sigue publicando: tras el overlay, una arista viva de su mismo tipo, en su sentido (salvo en un tipo simétrico) y
# desde su documento (spec 071 AC119 y D1); y los dos problemas que la rodean, iguales en los dos checks.
SIGUE = 'la asociación sigue publicada'
DUPLICADA = f'copia duplicada retirada; {SIGUE}'
OTRA_CAPA = 'arista «suprimida» viva en otra capa'
SIN_DUPLICADA = 'declarada duplicada sin copia publicada'
# La fórmula cerrada con que una fila «suprimido» que ya no oculta nada declara que retiró una copia de una asociación
# que otra arista del mismo documento sigue publicando (spec 071 AC125), con el tipo al que una regla `fix` la retipó si
# no es el suyo, y su problema, iguales en los dos checks.
PUBLICADA = 'copia retirada; la asociación sigue publicada por otra arista del mismo documento'
_PUBLICADA = re.compile(rf': {re.escape(PUBLICADA)}(?:, rectificada como ([A-Z_]+))?')
SIN_PUBLICADA = 'fórmula sin asociación publicada'
RETIPO_NO_ALCANZABLE = 'fórmula con un retipo que ninguna regla alcanza'
# La fórmula cerrada de una fila «suprimido» cuyo par el grafo sigue publicando tras el overlay de otra manera (spec 071
# AC142, D1 y D3): con su mismo tipo desde otro documento o en el otro sentido, sin más; bajo otros tipos, que ninguna
# regla `fix` retipa desde el suyo, nombrándolos (del vocabulario de la ontología); y su problema, igual en los dos
# checks.
_SIGUE = re.compile(rf'; {SIGUE}(?: bajo ([A-Z_]+(?:, [A-Z_]+)*))?$')
SIN_SIGUE = 'fórmula de asociación publicada sin una arista viva del tipo que dice'
# Lo que el registro dice de una fila anonimizada cuyo par publica el grafo (D1: el marcador no protege nada; la fila
# nombra sus dos extremos con su fórmula) y de una que lleva un trozo de su cita retirada (D2).
MARCADO_PUBLICADO = 'fila anonimizada con el par publicado: nombra sus dos extremos (D1)'
TROZO_CITA = 'fila anonimizada con un trozo de su cita retirada'
# Un trozo de cita: tantas palabras seguidas (D2).
PALABRAS_TROZO = 6
# Una fila con marcador sin unidad cuyo anonimato no se puede comprobar: sin declaración privada (spec 071 AC134).
NO_VERIFICABLE = 'anonimato no verificable (AC113): fila con marcador sin unidad ni declaración privada'
# Una referencia a la unidad de una fila con marcador fuera de los campos estructurales de su regla opaca y de su fila
# (spec 071 AC115 y AC131): de 8 a 64 caracteres hexadecimales que son prefijo de la unidad.
HUELLA = 'huella de una fila anonimizada fuera de su regla opaca'
_HEX = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{8,64}(?![0-9a-fA-F])')
# Una fila anonimizada que nombra un extremo fuera de su `dato` (spec 071 AC143: sin el censo no se sabe si es el
# oculto) y una fila de arista que nombra el mismo extremo dos veces (AC153).
EXTREMO_FUERA = 'fila anonimizada que nombra un extremo fuera de su dato'
MISMO_EXTREMO = 'fila con el mismo extremo dos veces'
# Una fila marcada cuya clave no da ningún predicado suyo (spec 071 AC157) o que no oculta ninguno de sus extremos
# (AC161): nada que comprobar no es «nada que objetar».
SIN_PREDICADO = 'retirada sin predicado verificable'
# AC196: la fila anonimizada cuya entrada no está en su propio fichero (la de su posición no lleva su id).
SIN_ENTRADA_PROPIA = 'fila anonimizada sin su entrada en su fichero: no se puede barrer (AC196)'
MARCADOR_INCOHERENTE = 'marcador incoherente: no oculta nada'
# Un fichero público que no se puede leer como lo que es (un YAML que no se compone, un JSON que no se lee): se barre
# por líneas, pero lo que no se puede inspeccionar no se da por limpio (spec 071 AC174).
FICHERO_NO_VERIFICABLE = 'fichero público no verificable (AC174)'
FORMA_OPACA = 'regla opaca con forma inválida: solo {unidad, declaraciones?, kinds?, reason}'
# Lo que los dos apartados de AC122-bis dicen de una fila anonimizada y de un ítem del feed (spec 071).
RECONSTRUIBLE = 'fila anonimizada reconstruible'
# AC197: la que lo es en un texto nuestro por un nombre de una palabra junto al otro extremo de la fila.
RECONSTRUIBLE_CORTO = 'fila anonimizada reconstruible por un nombre de una palabra junto al otro extremo'
PAR_NO_PUBLICADO = 'fila anonimizada de-anonimizable, par no publicado'
FEED_PAR = 'ítem del feed con el par no publicado de una fila anonimizada (decisión editorial del dueño)'
EDITORIAL = 'texto público curado con el par de una fila anonimizada (decisión editorial del dueño)'
CITA_PUBLICADA = 'asociación reconstruible desde una cita publicada por el grafo'
# El aviso de una arista que el grafo publica con el extremo oculto de una fila anonimizada y su extremo visible en su
# texto, con el tipo o una cita de la retirada (D2 b): texto judicial citado, que no se reescribe.
PAR_DESDE_GRAFO = 'par reconstruible desde el grafo'
# Los ficheros públicos cuya prosa curada también publica el par que nombra (spec 071 AC122-bis b): la capa de contexto
# y las notas de acusaciones. Una entrada suya con el par de una fila anonimizada es un aviso editorial, no un problema,
# y el par cuenta como publicado; también es aviso una escisión del overlay.
CURADOS = frozenset({'context', 'accusations'})
# Una «rectificado» vigente que conserva el marcador: `privatizar` la deja así cuando el grafo no publica el extremo que
# oculta, y su arista no se puede nombrar ni dar por retirada sin una decisión (spec 071 AC113).
RECTIFICADO_MARCADO = ('rectificado con marcador: extremo no publicado y sin decisión de retirada (decisión del dueño '
                       'pendiente)')
# Lo más corto que cuenta como cita en un texto público (normalizado, `_llano`), y como nombre en prosa de una persona
# (o de cualquier otra entidad que no sea una sociedad o un organismo): dos palabras, porque un nombre de pila o un
# apellido sueltos no identifican a nadie. Por su id o por la estructura de su cargador, siempre.
CITA_MIN, NOMBRE_MIN, PALABRAS_MIN = 15, 4, 2
# Una sociedad o un organismo se nombran en prosa con su nombre o su alias completos, de cualquier longitud, salvo que
# solo tengan denominaciones genéricas (`_GENERICAS`), que solas no nombran a ninguno.
SOCIEDADES = frozenset({'Company', 'Organization'})
_GENERICAS = frozenset({
    'sl', 'sa', 'slu', 'sau', 'sc', 'scoop', 'sll', 'cb', 'ute', 'sociedad', 'limitada', 'anonima', 'unipersonal',
    'cooperativa', 'fundacion', 'ayuntamiento', 'asociacion', 'grupo', 'empresa', 'compania', 'gobierno', 'ministerio',
    'consejeria', 'diputacion', 'junta', 'partido', 'comision', 'agencia', 'instituto', 'servicio', 'direccion',
    'secretaria', 'oficina', 'departamento', 'universidad', 'hospital', 'banco', 'caja', 'consorcio', 'federacion',
    'confederacion', 'sindicato', 'colegio', 'camara', 'consejo', 'tribunal', 'juzgado', 'audiencia', 'fiscalia',
    'general', 'nacional', 'de', 'del', 'la', 'las', 'el', 'los', 'y', 'e'})
# Las operaciones vigentes que conservan la arista: una fila «suprimido» vigente sobre ella contradice la decisión (spec
# 071 AC93). El set_status no: la capa de acusaciones o la de última hora recrean o retiran la arista al margen de él.
CONSERVAN = ('retype', 'invert', 'set_prop', 'keep')
# Las capas cuyo cargador recrea la arista con la clave par + tipo + capa (`apoc.merge.relationship` con
# `{source_kind: …}` y nada más) y sobrescribe la cita en cada carga (spec 071 AC59, AC101 y AC110):
# `accusations_loader` (también el documento) y `press_loader`, `ultima_hora_loader` y `context_loader`. Su identidad es
# esa clave, sin cita ni documento (`_de_su_cargador`), tenga documento la arista o no; las posturas de `claims_loader`
# usan la misma.
CAPAS_POR_IDENTIDAD = frozenset({'acusacion', 'prensa', 'ultima_hora', 'contexto'})  # ontologia-ok: claves de cargador
# Lo que la búsqueda libre sabe de las otras aristas del censo y del overlay (`formas_ajenas`).
Ajenas = namedtuple('Ajenas', 'claves abiertas declarados', defaults=(frozenset(), (), frozenset()))
SIN_AJENAS = Ajenas()
# La clasificación de las copias de una arista bajo su operación vigente (`clasificar`).
Copias = namedtuple('Copias', 'incompatibles sin_carga decididas ambiguas no_atribuidas')
# La etiqueta `[hash8]` con que una regla del overlay nombra su unidad.
_MARCA = re.compile(r'\[([0-9a-f]{8})\]')


def _leer_yaml(path):
    """El contenido de un YAML (`alcance.leer_yaml`, que rechaza una clave repetida en cualquier nivel: spec 071
    AC108). Si no carga, para con exit 2 y el fichero y la posición por stderr, nunca el texto de la excepción: puede
    llevar el valor roto (`!!int "…"` lanza un ValueError con él) o la línea entera."""
    try:
        return leer_yaml(path)
    except YamlInvalido as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None


def cargar(path):
    """La lista `rectificaciones:` del registro, o None si el fichero no la tiene."""
    data = _leer_yaml(path)
    rows = data.get('rectificaciones') if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else None


def _forma(patron, value):
    return isinstance(value, str) and patron.fullmatch(value) is not None


def _etiqueta(row, n):
    """Cómo nombra el informe una fila: su id si tiene la forma del registro, si no «fila N». Un valor mal
    puesto puede llevar dentro justo lo que se retiró, y el log de Actions del repo es público."""
    return row['id'] if _forma(_ID, row.get('id')) else f'fila {n}'


def estructura(rows):
    """[(etiqueta, motivo)] por cada defecto de forma de las filas. Los motivos nombran el campo y, como
    mucho, el vocabulario permitido: nunca el valor leído."""
    problemas = []
    vistos = Counter()
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            problemas.append((f'fila {n}', 'no es un bloque de campos'))
            continue
        rid = _etiqueta(row, n)
        if isinstance(row.get('id'), str):
            vistos[row['id']] += 1
            if vistos[row['id']] == 2:
                problemas.append((rid, 'id repetido'))
        if 'id' in row and not _forma(_ID, row['id']):
            problemas.append((rid, 'id con formato inválido (R-YYYY-MM-DD-NNNN)'))
        faltan = [campo for campo in CAMPOS if campo not in row]
        if faltan:
            problemas.append((rid, 'faltan campos: ' + ', '.join(faltan)))
        vacios = [campo for campo in CAMPOS if campo in row and campo != 'unidad'
                  and not (isinstance(row[campo], str) and row[campo].strip())]
        if vacios:
            problemas.append((rid, 'campos sin texto: ' + ', '.join(vacios)))
        unidad = row.get('unidad', '')
        if not (unidad == '' or _forma(_UNIDAD, unidad)):
            problemas.append((rid, '`unidad` no es "" ni un unit_id del censo'))
        if 'origen' in row and row['origen'] not in ORIGENES:
            problemas.append((rid, f'`origen` fuera del vocabulario ({" | ".join(ORIGENES)})'))
        if 'decision' in row and row['decision'] not in DECISIONES:
            problemas.append((rid, f'`decision` fuera del vocabulario ({" | ".join(DECISIONES)})'))
        for campo in ('fecha', 'fecha_resolucion'):
            if isinstance(row.get(campo), str) and not _forma(_FECHA, row[campo]):
                problemas.append((rid, f'`{campo}` fuera de la forma YYYY-MM-DD'))
        seed = row.get('seed_version')
        if isinstance(seed, str) and seed != SEED_PENDIENTE and not _forma(_SEED, seed):
            problemas.append((rid, f'`seed_version` no es {SEED_PENDIENTE} ni AAAAMMDD-…'))
        # AC183: una «suprimido» sin unidad ni marcador solo se comprueba por la forma de su dato
        # (`retiradas_sin_unidad`): con otra forma, nada la comprueba, y es un problema ya en el árbol.
        dato = row.get('dato')
        if row.get('decision') == 'suprimido' and not unidad and isinstance(dato, str) and dato.strip() \
                and not MARCADOR.search(dato) and not _DATO_ARISTA.fullmatch(dato) \
                and not _SEUDONIMO_RETIRADA.fullmatch(dato):
            problemas.append((rid, DATO_SIN_FORMA))
    return problemas


def _cita(value):
    """La cita normalizada (`alcance.cita_normalizada`: NFKC, sin mayúsculas y con los espacios colapsados, spec 071
    AC107) y recortada a QUOTE_CHARS: la de una copia con la tilde descompuesta es la misma."""
    return cita_normalizada(value)[:QUOTE_CHARS]


def _par(rtype, start, end):
    """La arista sin dirección, como la borra una regla `deny` (`relations_loader.apply_deny`)."""
    return rtype, frozenset((start or None, end or None))


def _clave(rtype, start, end):
    """La arista como hecho: con dirección salvo en los tipos simétricos."""
    return _par(rtype, start, end) if rtype in SIMETRICOS else (rtype, (start or None, end or None))


def _rasgos(props, rtype=None):
    """(capa, documento, cita normalizada): lo que el export deja ver de una copia. Dos copias son la misma
    si coinciden los tres; una cita que solo contiene la otra es otra copia (R-2026-10-03-0054: la cita
    retirada, de cuatro caracteres, está dentro de la de la arista que se conserva). Donde el cargador recrea la
    arista con un MERGE por par, tipo y capa que actualiza la cita, la cita no distingue copias (spec 071 AC59, AC101 y
    AC110): una arista de una capa de `CAPAS_POR_IDENTIDAD` es la misma con cualquier cita, página o documento (una
    acusación: persona, delito y tipo, y `accusations_loader` sobrescribe el documento, AC65; una de prensa, de última
    hora o de contexto: sus extremos y su tipo), y una postura sobre una afirmación, con cualquier cita o documento
    (titular, afirmación, postura y capa: `claims_loader`; la afirmación va en el par, AC104)."""
    kind = props.get('source_kind') or None
    if _de_su_cargador(props, rtype):
        return kind, None, None
    return kind, props.get('source_doc') or None, _cita(props.get('source_quote'))


def _de_su_cargador(props, rtype):
    """Si la copia con `props` bajo `rtype` es una de las que su cargador recrea por la clave par + tipo + capa (spec
    071 AC59, AC101 y AC110): una postura sobre una afirmación y una arista de la capa `acusacion`, de prensa, de última
    hora o de contexto, con documento o sin él. El cargador fusiona por esa clave sobre la que ya está, la escribiera él
    o el pipeline de documentos con esa capa: puede recrearla tras un deny o sobrescribir su cita."""
    return rtype in POSTURAS or props.get('source_kind') in CAPAS_POR_IDENTIDAD


def _firma_cruda(props):
    """(documento, cita normalizada): lo que identifica una copia cuando un extremo de la arista se re-identificó
    después del censo y no hay par en que buscarla. Sin la reducción por identidad de `_rasgos`: con un extremo
    desconocido, solo la cita distingue una acusación o una postura de las demás de su tipo."""
    return props.get('source_doc') or None, _cita(props.get('source_quote'))


def _por_identidad(value):
    """Si el cargador de la arista `value` del censo la recrea por su identidad, con cualquier cita (`_de_su_cargador`:
    la clave de fusión de su cargador, AC59 y AC101)."""
    return _de_su_cargador(value.get('props') or {}, value.get('type'))


# --- citas que migra el Path A (spec 071 AC284–AC288) -----------------------------------------------------------------

# La declaración privada, en la raíz de la auditoría: por unidad del censo, la huella de la cita de su firma antes y
# después de una migración que reescribe el `source_quote` de su arista (el overlay no puede: `relations_loader`).
MIGRACIONES_CITAS = 'esc/migraciones_citas.yaml'
CAMPOS_MIGRACION = frozenset({'documento', 'antes', 'despues'})
MIGRACION_NO_VALIDA = 'migración de cita declarada no válida'
# Dónde lleva la arista del censo de una unidad declarada su firma de después, (documento, huella): fuera de `props`,
# que leen las reglas, el predicado del loader y la carga.
_MIGRADA = 'firma_migrada'


def _huella(cita):
    return hashlib.sha256(cita.encode('utf-8')).hexdigest()


def huella_de_cita(quote):
    """La huella (sha256, 64 hex) de la cita de una firma (`_cita`: normalizada y recortada a QUOTE_CHARS), la de
    `antes` y `despues` en `MIGRACIONES_CITAS` (spec 071 AC284): dos citas que solo difieren tras el carácter
    QUOTE_CHARS tienen la misma firma y la misma huella. La única definición: la usa también el generador."""
    return _huella(_cita(quote))


def con_migracion(uid, value, migradas):
    """La arista `value` de la unidad `uid` del censo con la firma de después de su migración declarada (`migradas`, de
    `migraciones_citas`), si la tiene (AC285): solo su unidad la lleva, nunca otra con su misma firma."""
    if isinstance(value, dict) and uid in (migradas or {}):
        return {**value, _MIGRADA: migradas[uid]}
    return value


def de_su_fuente(value, props):
    """Si una copia viva con `props` lleva la fuente de la arista `value` del censo: su documento y su cita
    (`_firma_cruda`) o, si su unidad declara la migración de su cita (`con_migracion`), su documento y la huella de la
    de después (spec 071 AC285). La única identificación por la fuente de los dos checks (AC287)."""
    firma = _firma_cruda(props)
    if firma == _firma_cruda(value.get('props') or {}):
        return True
    migrada = value.get(_MIGRADA)
    return migrada is not None and migrada == (firma[0], _huella(firma[1]))


def firmas_censales(value):
    """{(documento, huella)}: las fuentes de la arista `value` del censo, como claves: la de su firma y, si su unidad
    declara una migración, la de después (AC287). Dos aristas del censo comparten fuente si comparten una."""
    props = value.get('props') or {}
    migrada = value.get(_MIGRADA)
    return frozenset({(props.get('source_doc') or None, huella_de_cita(props.get('source_quote'))),
                      *((migrada,) if migrada is not None else ())})


def migraciones_citas(root, conn, todas=None):
    """({unit_id: (documento, huella de después)}, [motivo]): las migraciones de cita que declara la raíz `root` de la
    auditoría (`MIGRACIONES_CITAS`, spec 071 AC284), contrastadas con su censo `conn` y con las decisiones (AC286): las
    de `todas` (las efectivas de un apply, con su lote: AC292) o, sin ellas, las de `esc/decisions`. Solo el fichero
    ausente no declara nada sin motivo; presente y vacío, nulo o sin ninguna entrada, es un motivo (AC284). Con cualquier
    motivo, ninguna (fallo cerrado: el comportamiento sin fichero, más el problema). Un motivo nombra la posición de la
    entrada o el hash8 de su unidad, nunca un valor (AC288)."""
    path = Path(root) / MIGRACIONES_CITAS
    if not path.is_file():
        return {}, []
    try:
        doc = leer_yaml(path)
    except (YamlInvalido, OSError, UnicodeError):
        return {}, ['no se lee como YAML (o con una clave repetida)']
    if not isinstance(doc, dict):
        return {}, ['no es un mapa de unidades']
    if not doc:
        return {}, ['no declara ninguna unidad']
    motivos, entradas = [], []
    for n, (uid, entrada) in enumerate(doc.items(), 1):
        nombre = nombre_de_unidad(uid, f'entrada {n}')
        if not (isinstance(uid, str) and _UNIDAD.fullmatch(uid) and isinstance(entrada, dict)
                and set(entrada) == CAMPOS_MIGRACION and isinstance(entrada['documento'], str)
                and entrada['documento'].strip()
                and all(isinstance(entrada[k], str) and _UNIDAD.fullmatch(entrada[k]) for k in ('antes', 'despues'))):
            motivos.append(f'{nombre}: sin la forma {{documento, antes, despues}} con huellas de 64 hex')
            continue
        fila = conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
        if fila is None or fila[0] != 'edge':
            motivos.append(f'{nombre}: no es una arista del censo')
            continue
        entradas.append((uid, nombre, entrada, json.loads(fila[1])))
    if not entradas:
        return {}, motivos
    censales = {(props.get('source_doc') or None, huella_de_cita(props.get('source_quote')))
                for (value,) in conn.execute("SELECT value FROM units WHERE kind='edge'")
                for props in [json.loads(value).get('props') or {}]}
    acciones = decisiones(root, set())[1] if todas is None else \
        {uid: {op.get('action') or 'keep'} for uid, (_, op) in vigentes(list(todas)).items()}
    out, duenas = {}, set()
    for uid, nombre, entrada, value in entradas:
        props, despues = value.get('props') or {}, (entrada['documento'], entrada['despues'])
        if _por_identidad(value):
            motivos.append(f'{nombre}: su cargador la identifica por su identidad, no por su cita')
        elif (props.get('source_doc') or None) != entrada['documento']:
            motivos.append(f'{nombre}: documento distinto del de su arista en el censo')
        elif entrada['antes'] != huella_de_cita(props.get('source_quote')):
            motivos.append(f'{nombre}: la huella de antes no es la de la cita de su arista en el censo')
        elif entrada['despues'] == huella_de_cita(''):
            motivos.append(f'{nombre}: la huella de después es la de una cita vacía')
        elif despues in censales:
            motivos.append(f'{nombre}: la huella de después es la firma de una arista del censo')
        elif despues in duenas:
            motivos.append(f'{nombre}: la huella de después ya la declara otra unidad')
        elif 'deny' in acciones.get(uid, ()):
            motivos.append(f'{nombre}: su operación vigente la retira (deny)')
        else:
            out[uid] = despues
        duenas.add(despues)
    return ({} if motivos else out), motivos


def _es_suya(value, props, tipo, carga=None):
    """Si una copia viva, con `props` bajo `tipo`, es la arista `value` del censo: la única identificación de las copias
    cuyo sentido y carga se juzgan (spec 071 AC85). Una de una capa que su cargador recrea por su identidad o una
    postura sobre una afirmación, por esa identidad (`_rasgos`, con la capa de antes o la de después de la `carga`), con
    cualquier documento y cita (AC81 y AC101); el resto, por su fuente (`de_su_fuente`: su documento y su cita, o la de
    después de la migración declarada de su unidad, AC285), que ninguna carga toca, con cualquier capa: un `set` pudo
    cambiarla, y la que conserva la del censo también es ella (AC80)."""
    antes = value.get('props') or {}
    if _por_identidad(value):
        return _rasgos(props, tipo) in {_rasgos(antes, tipo), _rasgos({**antes, **(carga or {})}, tipo)}
    return de_su_fuente(value, props)


class ExportInvalido(Exception):
    """El export no se puede leer como el grafo: su mensaje dice el fichero y la línea, nunca un valor."""


def _lineas(path):
    """(número, fila) de cada línea de un JSONL, una a una (los Chunk llevan el embedding). Vacío o con una línea
    que no es un objeto JSON: ExportInvalido."""
    vacio = True
    with Path(path).open(encoding='utf-8') as handle:
        for n, line in enumerate(handle, 1):
            try:
                fila = json.loads(line)
            except ValueError:
                raise ExportInvalido(f'{Path(path).name}: línea {n} ilegible') from None
            if not isinstance(fila, dict):
                raise ExportInvalido(f'{Path(path).name}: línea {n} ilegible')
            vacio = False
            yield n, fila
    if vacio:
        raise ExportInvalido(f'{Path(path).name} vacío')


def afirmacion(claim_id):
    """El extremo con que una arista llega a un Claim (spec 071 AC104): su claim_id, la clave con que lo fusiona
    `claims_loader`, con un prefijo que ningún entity_id lleva. Sin él, todas las posturas de un titular serían la misma
    arista."""
    return f'Claim:{claim_id}'


def _json(texto):
    try:
        valor = json.loads(texto or '{}')
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


def con_afirmacion(conn, uid, value):
    """La arista `value` de la unidad `uid` del censo con su extremo Claim recuperado (spec 071 AC104): `units` no
    guarda el claim_id (el extremo es None), pero el objeto de su relación en el censo apunta al nodo de ese extremo,
    que sí lo lleva. Si no se recupera (un censo sin `objects`, o un extremo que no es un Claim con claim_id), queda
    None: una postura así no se puede identificar en el export (`indeterminada`)."""
    if not isinstance(value, dict) or None not in (value.get('start'), value.get('end')):
        return value
    try:
        fila = conn.execute('SELECT o.payload FROM units u JOIN objects o ON o.object_id = u.object_id '
                            'WHERE u.unit_id=?', (uid,)).fetchone()
        relacion = _json(fila[0]) if fila else {}
        out = dict(value)
        for lado in ('start', 'end'):
            if out.get(lado) is None and isinstance(relacion.get(lado), str):
                nodo = conn.execute("SELECT payload FROM objects WHERE object_kind='node' AND ref=?",
                                    (relacion[lado],)).fetchone()
                payload = _json(nodo[0]) if nodo else {}
                claim_id = (payload.get('props') or {}).get('claim_id')
                if 'Claim' in (payload.get('labels') or ()) and isinstance(claim_id, str) and claim_id:
                    out[lado] = afirmacion(claim_id)
        return out
    except sqlite3.Error:
        return value


def aristas_censales(conn, migradas=None):
    """{unit_id: value} de todas las aristas del censo, con su extremo Claim recuperado (`con_afirmacion`) y, la de una
    unidad con una migración de cita declarada (`migradas`), su firma de después (`con_migracion`, AC285)."""
    return {uid: con_migracion(uid, con_afirmacion(conn, uid, json.loads(value)), migradas)
            for uid, value in conn.execute("SELECT unit_id, value FROM units WHERE kind='edge'")}


def indeterminada(value):
    """Si la arista `value` del censo es una postura cuya afirmación no se recuperó (`con_afirmacion`): no se puede
    decir qué copia del export es ella, y nunca vale cualquier Claim de su titular (spec 071 AC104)."""
    return value.get('type') in POSTURAS and None in (value.get('start'), value.get('end'))


_ESPERADO = ('una cadena', 'una lista de cadenas')


def _tipos_de_nombre(props):
    """[(clave, tipo)] de los nombres, denominaciones y alias de un nodo o una arista que no tienen el tipo que la web pinta como un
    nombre (`seudonimos.tipos_de_nombre`): fail-closed, como el resto de la lectura del export. Nunca el valor."""
    from src.seudonimos import tipos_de_nombre
    return tipos_de_nombre(props)


def export_vivo(snapshot):
    """({(tipo, {extremo, extremo}): [(inicio, fin, props)]}, {entity_id}) del export: cada copia con su
    dirección, bajo el par sin ella. El extremo Claim es su `afirmacion` (spec 071 AC104), y el de cualquier otro nodo
    sin entity_id, None. Un export sin la forma del grafo es ExportInvalido: vacío, con una línea ilegible, un nodo sin
    `ref` (o con uno repetido), sin etiquetas o de entidad sin entity_id, o una arista sin tipo o hacia un nodo que no
    está; y (AC338) un nombre, nombre canónico, nombre original o denominación que no es una cadena, o unos alias que
    no son una lista de cadenas. Leído tal cual, sin aristas ni extremos, certificaría todas las retiradas."""
    refs, ids = set(), {}
    for n, node in _lineas(Path(snapshot) / 'nodes.jsonl'):
        ref, labels, props = node.get('ref'), node.get('labels'), node.get('props') or {}
        if ref is None or ref in refs:
            raise ExportInvalido(f'nodes.jsonl: línea {n} sin ref o con un ref repetido')
        if not isinstance(labels, list) or not labels or not isinstance(props, dict):
            raise ExportInvalido(f'nodes.jsonl: línea {n} sin etiquetas o sin forma de nodo')
        refs.add(ref)
        if not set(labels) <= DOCUMENTAL:   # un nombre que no es una cadena se pinta como una (spec 070 AC338): ni el homónimo ni los caracteres lo leen entero
            for clave, tipo in _tipos_de_nombre(props):
                raise ExportInvalido(f'nodes.jsonl: línea {n}: `{clave}` no es {_ESPERADO[clave == "aliases"]}: {tipo}')
        if props.get('entity_id'):
            ids[ref] = props['entity_id']
        elif 'Claim' in labels and isinstance(props.get('claim_id'), str) and props['claim_id']:
            ids[ref] = afirmacion(props['claim_id'])
        elif not set(labels) <= SIN_ENTITY_ID:   # un Person+Chunk sin entity_id tampoco vale
            raise ExportInvalido(f'nodes.jsonl: línea {n}: un nodo de entidad sin entity_id')
    index = defaultdict(list)
    for n, rel in _lineas(Path(snapshot) / 'relationships.jsonl'):
        if not isinstance(rel.get('type'), str) or not rel['type'] or not isinstance(rel.get('props') or {}, dict):
            raise ExportInvalido(f'relationships.jsonl: línea {n} sin tipo o sin forma de arista')
        for clave, tipo in _tipos_de_nombre(rel.get('props') or {}):
            raise ExportInvalido(f'relationships.jsonl: línea {n}: `{clave}` no es {_ESPERADO[clave == "aliases"]}: {tipo}')
        if rel.get('start') is None or rel.get('end') is None or not {rel['start'], rel['end']} <= refs:
            raise ExportInvalido(f'relationships.jsonl: línea {n} apunta a un nodo que no está en nodes.jsonl')
        start, end = ids.get(rel['start']), ids.get(rel['end'])
        index[_par(rel['type'], start, end)].append((start, end, rel.get('props') or {}))
    return index, set(ids.values())


def decisiones(root, unidades):
    """({unit_id: (texto, entidad)}, {unit_id: {acción}}, {unit_id: {prop: valor}}, {unit_id: tipo nuevo}) con la
    operación vigente de cada arista (spec 071 AC54: la de más precedencia, `alcance.vigentes`; lo que hizo una
    decisión sustituida no cuenta): motivo y evidencia de la decisión vigente de cada unidad pedida, con la entidad de
    su pack; su acción; los props que le cambia (los de un `set_prop`, uno o un mapa, o el mapa `set` de un retype o un
    invert, AC71) tal como los escribe el loader en la arista (`alcance.carga`, con sus props derivadas: AC77), y el
    tipo al que la lleva, si es un `retype`. Una decisión con una carga que el loader descarta para con exit 2
    (`vigentes`, AC78)."""
    todas = []
    for path in sorted((Path(root) / 'esc/decisions').glob('*.json')):
        decision = json.loads(path.read_text(encoding='utf-8'))
        decision['_file'] = path.name
        todas.append(decision)
    out, acciones, sets, retipos = {}, {}, {}, {}
    for uid, (decision, op) in vigentes(todas).items():
        accion = op.get('action') or 'keep'
        acciones[uid] = {accion}
        cambia = carga(op) if accion in ('set_prop', 'retype', 'invert') else None
        if cambia:
            sets[uid] = cambia
        if accion == 'retype' and op.get('new_type'):
            retipos[uid] = op['new_type']
        if uid in unidades:
            entidad = (decision.get('_meta') or {}).get('entity') or decision.get('entity_id')
            evidencia = [str(u.get('evidence') or '') for u in decision.get('units') or [] if u.get('unit_id') == uid]
            out[uid] = (' '.join([*evidencia, str(op.get('reason') or ''), str(op.get('evidence') or '')]), entidad)
    return out, acciones, sets, retipos


def conservadas(conn, paquetes, uid, texto, entidad):
    """([(unit_id, value)], ambigua): las aristas que la decisión nombra por un prefijo de su unit_id, entre
    las de los packs de su entidad. Es lo que el apply escribe: «se conserva la arista f86b4e9c». El prefijo se
    resuelve contra todas, la retirada incluida: si solo la señala a ella, no nombra ninguna conservada; si
    señala varias unidades, no identifica ninguna: no se toma y `ambigua` es True."""
    candidatas = sorted(set(paquetes.get(entidad, ())) | {uid})
    elegidas, ambigua = set(), False
    for prefijo in set(_HEX.findall(texto)):
        coinciden = [otra for otra in candidatas if otra.startswith(prefijo)]
        ambigua |= len(coinciden) > 1
        elegidas.update(otra for otra in coinciden if len(coinciden) == 1 and otra != uid)
    out = []
    for otra in sorted(elegidas):
        unit = conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (otra,)).fetchone()
        if unit is not None and unit[0] == 'edge':
            out.append((otra, con_afirmacion(conn, otra, json.loads(unit[1]))))
    return out, ambigua


def _arista(value):
    return _clave(value.get('type'), value.get('start'), value.get('end'))


def _publicada(uid, value, sets):
    """Los props de una arista tal como quedan tras el apply: los del censo con los `set_prop` de las decisiones."""
    return {**(value.get('props') or {}), **sets.get(uid, {})}


def transformadoras(conn, acciones, sets, retipos):
    """{par sin dirección: [(unit_id, value)]}: las aristas del censo a las que una operación vigente cambia props, tipo
    o sentido, por su par: la regla de cada una puede alcanzar otra del par, con su tipo o con el que le deja otra."""
    invertidas = {uid for uid, hechas in acciones.items() if 'invert' in hechas}
    out = defaultdict(list)
    for uid in sorted(set(sets) | set(retipos) | invertidas):
        unit = conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (uid,)).fetchone()
        if unit is not None and unit[0] == 'edge':
            value = con_afirmacion(conn, uid, json.loads(unit[1]))
            out[frozenset((value.get('start'), value.get('end')))].append((uid, value))
    return out


def _extremos(rtype, start, end):
    """Los extremos de una copia en la clave de tolerancia: con dirección salvo en los tipos simétricos (AC52)."""
    return frozenset((start or None, end or None)) if rtype in SIMETRICOS else (start or None, end or None)


def _sentido(value, rtype, start, end):
    """El de una copia frente a la arista `value` del censo: «=» o «inv»; ninguno en las simétricas."""
    if rtype in SIMETRICOS:
        return None
    return '=' if (start, end) == (value.get('start'), value.get('end')) else 'inv'


def _estado(value):
    """(tipo, inicio, fin, firma) de una arista del censo: lo que transforman las fases de `_pasos`."""
    props = value.get('props') or {}
    return value.get('type'), value.get('start'), value.get('end'), tuple(props.get(k) for k in FIRMA)


def _regla_decidida(otra, origen, acciones, sets, retipos):
    """La regla que el apply escribe (`alcance.regla_de`) para la operación vigente de la arista `otra` del censo: su
    retype, su invert o los props de su `set_prop`."""
    return {**regla_de(origen), 'corrected_type': retipos.get(otra), 'swap': 'invert' in acciones.get(otra, ()),
            'set': sets.get(otra) or None}


def _pasos(regla, estado):
    """Los estados en que deja a la arista `estado` (tipo, inicio, fin, firma) cada fase de la regla `fix` en
    `relations_loader.apply_fix`: el retype casa su tipo; el set y el swap, el corregido si la fila también retipa
    (`coalesce(corrected_type, type)`). Las capas filtran como en el loader (sin `kinds`, solo judicial o nula) y la
    dirección cuenta salvo en las simétricas (AC52), menos en el swap, que casa en su sentido como `_SWAP_Q` y también
    invierte una simétrica (spec 071 AC170: el Cypher no las distingue, y un retipo posterior desde el otro sentido la
    lleva a un tipo dirigido); la cita se compara normalizada (`alcance.casa`): un superconjunto de lo que casa el
    loader."""
    tipo, inicio, fin, firma = estado
    arista = {'type': tipo, 'start': inicio, 'end': fin, 'props': dict(zip(FIRMA, firma))}
    dirigida = tipo not in SIMETRICOS
    sobre = {**regla, 'type': regla.get('corrected_type') or regla.get('type')}
    out = []
    if regla.get('corrected_type') and casa(regla, arista, exacta=True, dirigida=dirigida):
        out.append((regla['corrected_type'], inicio, fin, firma))
    if regla.get('set') and casa(sobre, arista, exacta=True, dirigida=dirigida):
        props = {**arista['props'], **{k: v for k, v in regla['set'].items() if k in FIRMA}}
        out.append((tipo, inicio, fin, tuple(props.get(k) for k in FIRMA)))
    if regla.get('swap') and casa(sobre, arista, exacta=True, dirigida=True):
        out.append((tipo, fin, inicio, firma))
    return out


ESTADOS_MAX = 4096


def componer(value, reglas):
    """{(tipo, inicio, fin, firma)}: los estados a los que las reglas `fix` pueden llevar la arista `value` (la firma es
    capa, documento y cita), con las fases del loader compuestas en cualquier orden y número de pasadas, como las
    aplican los Path A sucesivos sobre el linaje, hasta el punto fijo (spec 071 AC57: un retype seguido del `set` de
    otra regla sobre el tipo nuevo también la saca de su deny; AC170: un swap invierte también una simétrica, y un
    retipo posterior desde el otro sentido la lleva a un tipo dirigido). Solo las reglas de su par; ESTADOS_MAX acota
    la búsqueda."""
    par = frozenset((value.get('start'), value.get('end')))
    suyas = [r for r in reglas if frozenset((r.get('source_id'), r.get('target_id'))) == par]
    inicial = _estado(value)
    vistos, pendientes = {inicial}, [inicial]
    while pendientes and len(vistos) < ESTADOS_MAX:
        estado = pendientes.pop()
        for regla in suyas:
            for nuevo in _pasos(regla, estado):
                if nuevo not in vistos:
                    vistos.add(nuevo)
                    pendientes.append(nuevo)
    return vistos


def pares_actuales(uid, value, nodos, reglas, origen=False):
    """[value]: la arista del censo y, si un extremo suyo ya no está en el export (`nodos`: re-identificado después
    del censo), la misma arista en cada par actual al que apunta una regla del overlay (`reglas`, deny y fix ya
    preparadas) que lleva la etiqueta de su unidad o que, sin etiqueta, alcanza su firma, con el extremo que queda
    (spec 071 AC63): las transformaciones posteriores se escribieron sobre el par nuevo. La etiqueta `[hash8]` es de su
    unidad: una regla etiquetada para otra no le da un par por su firma (AC103). Con `origen`, [(value, origen)]:
    «censo», «etiqueta» o «firma», el par que solo deduce la firma de una regla sin etiqueta, que otra unidad de la
    misma firma puede compartir (`pares_y_origen`)."""
    falta = {x for x in (value.get('start'), value.get('end')) if x and x not in nodos}
    out = {}
    out[_par_de(value)] = (value, 'censo')
    if falta:
        queda = {value.get('start'), value.get('end')} - falta - {None}
        for regla in reglas:
            par = {regla.get('source_id'), regla.get('target_id')}
            marcas = _MARCA.findall(regla.get('reason') or '')
            propia = str(uid)[:8] in marcas
            if not par & queda or (marcas and not propia) or not (propia or casa(
                    {**regla, 'source_id': value.get('start'), 'target_id': value.get('end')}, value, exacta=True)):
                continue
            for nuevo in par - queda:
                actual = {**value, 'start': nuevo if value.get('start') in falta else value.get('start'),
                          'end': nuevo if value.get('end') in falta else value.get('end')}
                if _par_de(actual) not in out or (propia and out[_par_de(actual)][1] == 'firma'):
                    out[_par_de(actual)] = (actual, 'etiqueta' if propia else 'firma')
    lista = list(out.values())
    return lista if origen else [actual for actual, _ in lista]


def _par_de(value):
    return value.get('start'), value.get('end')


def pares_y_origen(uid, value, nodos, reglas):
    """(pares actuales de `pares_actuales`, {par sin dirección}) de una arista re-identificada: los pares en que se
    busca y los que solo deduce la firma de una regla sin etiqueta, donde una copia puede ser también de otra unidad de
    la misma firma (spec 071 AC103: pasa por la ambigüedad de AC98)."""
    con = pares_actuales(uid, value, nodos, reglas, origen=True)
    return ([actual for actual, _ in con],
            frozenset(frozenset(_par_de(actual)) for actual, origen in con if origen == 'firma'))


def firmas_posibles(uid, value, trans, acciones, sets, retipos, reglas=(), nodos=None, overlay=None):
    """{(tipo, sentido, firma)}: bajo qué tipo, en qué sentido y con qué capa, documento y cita puede seguir viva la
    arista `value` en el export: la del censo y cada estado al que la llevan, compuestas como el loader (`componer`),
    las reglas de las operaciones vigentes de las otras aristas del censo (como las escribe el apply) y las del overlay
    (`reglas`, las `fix` ya preparadas por el loader): un `set` le cambia la capa, un retype el tipo y un swap el
    sentido, y cualquiera la saca de su deny (5ª, 6ª, 8ª y 9ª pasadas de Codex). Desde la firma de antes y desde la de
    después de la carga de su propia operación vigente (`sets`): una copia a medio transformar puede llevar ya la capa
    nueva con el tipo o el sentido de antes (spec 071 AC84, el simétrico de AC80). Con `nodos` (los entity_id del
    export), si un extremo del censo se re-identificó, se compone también sobre sus pares actuales (AC63), que dicen
    las reglas `overlay` (deny y fix; sin ellas, las `reglas`); el sentido, el de cada uno."""
    return {(tipo, _sentido(actual, tipo, inicio, fin), _rasgos(dict(zip(FIRMA, firma)), tipo))
            for actual, tipo, inicio, fin, firma in _estados_posibles(uid, value, trans, acciones, sets, retipos,
                                                                      reglas, nodos, overlay)}


def _estados_posibles(uid, value, trans, acciones, sets, retipos, reglas=(), nodos=None, overlay=None):
    """[(par actual, tipo, inicio, fin, firma)]: los estados de `firmas_posibles` con sus extremos orientados y el par
    actual de que salen, lo que `formas_ajenas` necesita para no perder la dirección (spec 071 AC102)."""
    todas = [_regla_decidida(otra, origen, acciones, sets, retipos)
             for otra, origen in trans.get(frozenset((value.get('start'), value.get('end'))), ()) if otra != uid]
    actuales = pares_actuales(uid, value, nodos, reglas if overlay is None else overlay) if nodos is not None \
        else [value]
    cambios = ({}, sets[uid]) if sets.get(uid) else ({},)
    return [(actual, tipo, inicio, fin, firma) for actual in actuales for cambio in cambios
            for tipo, inicio, fin, firma in componer({**actual, 'props': {**(actual.get('props') or {}), **cambio}},
                                                     todas + list(reglas))]


def copias_posibles(index, value, posibles, con_sentido=False, carga=None):
    """{id de la copia: (tipo, extremos, firma)}: las copias vivas que pueden ser la arista `value`: del mismo par, bajo
    alguno de sus tipos posibles y con la firma que le corresponde; sin `con_sentido`, en cualquier dirección (la regla
    `deny` borra A->B y B->A); con él, solo en el tipo y el sentido de un estado posible (lo que un retype o un invert
    tiene que cambiar), y entonces también la que la identificación compartida reconoce como suya (`_es_suya`: por su
    documento y su cita, con cualquier capa, o por la identidad de su cargador): se identifica primero y se juzgan
    después la capa, el tipo y el sentido (spec 071 AC105). Los extremos, con su dirección salvo en los tipos simétricos
    (`_extremos`), para la tolerancia."""
    sin_sentido = {(tipo, firma) for tipo, _, firma in posibles}
    alcanzables = {(tipo, sentido) for tipo, sentido, _ in posibles}
    out = {}
    for tipo in sorted({tipo for tipo, _, _ in posibles if tipo}):
        for copia in index.get(_par(tipo, value.get('start'), value.get('end')), ()):
            inicio, fin, props = copia
            firma, sentido = _rasgos(props, tipo), _sentido(value, tipo, inicio, fin)
            if (((tipo, sentido, firma) in posibles
                 or ((tipo, sentido) in alcanzables and _es_suya(value, props, tipo, carga)))
                    if con_sentido else (tipo, firma) in sin_sentido):
                out[id(copia)] = (tipo, _extremos(tipo, inicio, fin), firma)
    return out


def copias_por_firma(index, value, posibles, actuales=None, solo=None):
    """[(tipo, None, firma)]: las copias vivas de la arista `value` del censo con un extremo que ya no está en el export
    (re-identificado después del censo, spec 071 AC61): en cualquier par y dirección (con `solo`, en esos pares: fuera
    de ellos un retype o un invert buscan con criterio de identidad, `copias_libres`), bajo un tipo al que la llevan las
    fases (`posibles`, AC57), con su fuente (`de_su_fuente`: su documento y su cita, o la de después de la migración
    declarada de su unidad, AC285), bajo la clave de su firma del censo; y, si su cargador la recrea por su identidad
    (`_de_su_cargador`: una acusación, una sin documento de prensa, de última hora o de contexto, o una postura, AC68 y
    AC101), también las que tienen esa identidad en uno de sus pares actuales
    (`actuales[1:]`, de `pares_actuales`), con cualquier documento y cita: el MERGE los sobrescribe sobre la misma
    arista. La capa no cuenta en la firma, que un `set` pudo cambiarla, y los extremos tampoco: la clave de tolerancia
    va sin ellos. Cada copia cuenta una vez."""
    return list(_vistas_por_firma(index, value, posibles, actuales, solo).values())


def _vistas_por_firma(index, value, posibles, actuales=None, solo=None):
    """{id de la copia: (tipo, None, firma)}: las de `copias_por_firma`, por la identidad de cada copia en el índice."""
    tipos, suya = {tipo for tipo, _, _ in posibles}, _firma_cruda(value.get('props') or {})
    dentro = None if solo is None else {_par(tipo, par.get('start'), par.get('end')) for tipo in tipos for par in solo}
    vistas = {}
    for clave, copias in index.items():
        if clave[0] in tipos and (dentro is None or clave in dentro):
            vistas.update({id(copia): (clave[0], None, suya) for copia in copias if de_su_fuente(value, copia[2])})
    if _por_identidad(value):
        firmas = {(tipo, firma) for tipo, _, firma in posibles}
        for actual in (actuales or [])[1:]:
            for tipo in tipos:
                vistas.update({id(copia): (tipo, None, _firma_cruda(copia[2]))
                               for copia in index.get(_par(tipo, actual.get('start'), actual.get('end')), ())
                               if (tipo, _rasgos(copia[2], tipo)) in firmas})
    return vistas


def _decidido(value, accion, nuevo):
    """(tipo, sentido) del estado en que deja a la arista `value` del censo su operación vigente (`accion`; `nuevo`, el
    tipo de un retype): un retype, el tipo nuevo en el sentido del censo; un invert, su tipo al revés; cualquier otra,
    su tipo en el sentido del censo. Un tipo simétrico no tiene sentido (None): el colapso puede llevarla a la canónica
    (AC60) y el swap nunca la toca."""
    tipo = nuevo if accion == 'retype' else value.get('type')
    if tipo in SIMETRICOS:
        return tipo, None
    return tipo, 'inv' if accion == 'invert' else '='


def _clave_copia(tipo, inicio, fin, props, por_firma):
    """La clave con que una copia viva entra en la tolerancia (`legitimas`): con sus extremos (con dirección salvo en
    los tipos simétricos) y sus rasgos; la de una arista re-identificada, sin extremos y por su documento y su cita
    (AC61)."""
    if por_firma:
        return tipo, None, _firma_cruda(props)
    return tipo, _extremos(tipo, inicio, fin), _rasgos(props, tipo)


def copias_identificadas(index, value, tipo, pares, carga=None):
    """[(sentido, inicio, fin, props)]: las copias vivas bajo `tipo` en los `pares` (la arista del censo y,
    re-identificada, sus pares actuales: `pares_actuales`) que son la arista `value` del censo por la única
    identificación (`_es_suya`, spec 071 AC85), cada una una vez y con su sentido frente al par en que está («=» o
    «inv»; ninguno en un tipo simétrico). Ninguna se descarta aquí por su capa ni por su sentido: quien las pide juzga
    el sentido y la carga de todas."""
    out, vistas = [], set()
    for par in pares:
        for copia in index.get(_par(tipo, par.get('start'), par.get('end')), ()):
            inicio, fin, props = copia
            if id(copia) not in vistas and _es_suya(value, props, tipo, carga):
                vistas.add(id(copia))
                out.append((_sentido(par, tipo, inicio, fin), inicio, fin, props))
    return out


def _sentido_libre(value, tipo, inicio, fin):
    """El sentido frente a la arista `value` del censo de una copia hallada fuera de sus pares que comparte con ella
    exactamente un extremo (AC89 i): el que da la posición de ese extremo, «=» si está en su sitio e «inv» si no;
    ninguno en un tipo simétrico."""
    if tipo in SIMETRICOS:
        return None
    if value.get('start') is not None and value.get('start') in (inicio, fin):
        return '=' if inicio == value.get('start') else 'inv'
    return '=' if fin == value.get('end') else 'inv'


def _puede_ser(abierta, tipo, inicio, fin):
    """Si la otra arista del censo `abierta` (unit_id, extremos que le quedan en el export, estados alcanzables y sus
    extremos del censo, de `formas_ajenas`) puede ser la copia (tipo, inicio, fin) (spec 071 AC98): alguno de sus
    estados alcanzables con ese tipo y, si es dirigido, con el extremo que le queda en el mismo sitio que en la copia;
    sin extremos en el export, el tipo basta. Sin ningún estado legible, sí: fallo cerrado."""
    _, quedan, estados, (a, b) = abierta
    if not estados:
        return True
    for t, s in estados:
        if t != tipo:
            continue
        if s is None or not quedan:
            return True
        orientado = (a, b) if s == '=' else (b, a)
        if all((x == orientado[0]) == (x == inicio) for x in quedan & {inicio, fin}):
            return True
    return False


def otras_que_pueden_ser(ajenas, tipo, inicio, fin):
    """Las otras aristas del censo de la misma firma con un extremo fuera del export que también pueden ser la copia
    (tipo, inicio, fin): las `ajenas.abiertas` que comparten con ella el extremo que les queda (o no conservan ninguno)
    y tienen un estado alcanzable con su tipo y su sentido (`_puede_ser`, spec 071 AC98)."""
    return tuple(abierta[0] for abierta in ajenas.abiertas
                 if (not abierta[1] or abierta[1] & {inicio, fin}) and _puede_ser(abierta, tipo, inicio, fin))


def _forma_dirigida(tipo, inicio, fin):
    """La clave de una arista como hecho dirigido (spec 071 AC102): sus extremos en orden salvo en los tipos simétricos,
    donde da igual. Con ella se excluye de la búsqueda libre la copia que es otra arista del censo, nunca la que solo
    comparte su par al revés."""
    return (tipo, frozenset((inicio, fin))) if tipo in SIMETRICOS else (tipo, (inicio, fin))


def copias_libres(index, value, tipos, pares, ajenas=SIN_AJENAS):
    """[(tipo, sentido, inicio, fin, props, otras, declarada)]: las copias de la arista `value` del censo bajo alguno de
    `tipos` que su documento y su cita hallan en cualquier otro par del export que los `pares` buscados: la ausencia
    nunca se acredita solo por los pares que deducen las reglas (spec 071 AC86 y AC89). La firma documental no es
    identidad (dos hechos del mismo pasaje la comparten), así que cuenta solo la que comparte exactamente un extremo con
    la arista del censo, con el sentido que da su posición (`_sentido_libre`; sin ninguno es otro hecho del pasaje: se
    descarta, nunca es un rojo), y no la que coincide en tipo, par y dirección con otra arista del censo de su misma
    firma, en su forma censal o en una resultante (`ajenas.claves`, de `formas_ajenas`, dirigidas salvo en los tipos
    simétricos: AC102): esa es la otra, y se evalúa bajo su propia unidad (AC89 ii). `otras`: las aristas del censo de
    su firma con un extremo fuera del export que también pueden ser esa copia (`otras_que_pueden_ser`, AC98); con
    alguna, la copia es ambigua y no se atribuye a ninguna (AC91). `declarada`: si su par es uno que el overlay declara
    de la arista (`ajenas.declarados`: una escisión o un merge que liga su extremo no compartido con el de la copia, o
    una regla con su etiqueta), lo único que se la atribuye cuando la arista ya tiene copia en sus pares (AC94). Una de
    las que su cargador recrea por su identidad, también por su cita: fuera de sus pares no hay identidad que la
    distinga de las demás. Sin documento ni cita no hay firma que buscar (una arista de contexto): ninguna. Su documento
    y su cita, o los de después de la migración declarada de su unidad (`de_su_fuente`, AC285)."""
    suya, extremos = _firma_cruda(value.get('props') or {}), {value.get('start'), value.get('end')} - {None}
    if suya == (None, ''):
        return []
    buscados = {_par(tipo, par.get('start'), par.get('end')) for tipo in tipos for par in pares}
    return [(clave[0], _sentido_libre(value, clave[0], inicio, fin), inicio, fin, props,
             otras_que_pueden_ser(ajenas, clave[0], inicio, fin), frozenset((inicio, fin)) in ajenas.declarados)
            for clave, copias in index.items()
            if clave[0] in tipos and clave not in buscados
            for inicio, fin, props in copias
            if len(({inicio, fin} - {None}) & extremos) == 1 and de_su_fuente(value, props)
            and _forma_dirigida(clave[0], inicio, fin) not in ajenas.claves]


def formas_ajenas(valores, trans, acciones, sets, retipos, reglas=(), nodos=None, overlay=None, enlaces=frozenset()):
    """{unit_id: Ajenas(claves, abiertas, declarados)}: lo que la búsqueda libre sabe de cada arista del censo
    (`valores`) además de su firma.
    `claves`: si comparte su documento y su cita con otras, las formas en que esas otras pueden estar vivas (spec 071
    AC89 ii), con su tipo y sus extremos orientados (`_forma_dirigida`: la dirección solo se ignora en los tipos
    simétricos, AC102): su forma censal y las que les dejan su operación vigente (el tipo nuevo de un retype, el sentido
    de un invert) y las reglas que las alcanzan (`_estados_posibles`, con las mismas reglas que el check), en su par del
    censo y, re-identificadas, en sus pares actuales.
    `abiertas`: (unit_id, extremos que le quedan, estados alcanzables, extremos del censo) de esas otras que tienen
    algún extremo fuera del export (`nodos`): re-identificadas, pueden estar en un par que sus reglas no dicen, así que
    una copia que comparte con una de ellas el extremo que le queda y que alguno de sus estados (tipo y sentido: la
    forma censal, el resultado de su operación vigente y los de `firmas_posibles`) puede producir no se sabe de cuál de
    las dos es (AC91 y AC98).
    `declarados`: los pares en que una copia libre se le atribuye aunque ya tenga copia en los suyos (AC94): los que una
    re-identificación que declara el overlay (`enlaces`: {entity_id, entity_id} de una escisión o de un merge) liga con
    uno de sus extremos, conservando el otro, y los de las reglas del overlay con su etiqueta (`overlay`, o `reglas`).
    Una arista sin nada de eso no está. Dos comparten firma si comparten una fuente (`firmas_censales`: la del censo o
    la de después de una migración declarada, AC287)."""
    grupos = defaultdict(list)
    for uid, value in valores.items():
        for firma in firmas_censales(value):
            grupos[firma].append(uid)
    gemelas = defaultdict(dict)
    for uids in grupos.values():
        for uid in uids if len(uids) > 1 else ():
            gemelas[uid].update(dict.fromkeys(o for o in uids if o != uid))
    formas, abiertas = {}, {}
    for uid in valores:
        if uid in gemelas:
            value = valores[uid]
            posibles = _estados_posibles(uid, value, trans, acciones, sets, retipos, reglas, nodos=nodos,
                                         overlay=overlay)
            estados = {(tipo, _sentido(actual, tipo, inicio, fin)) for actual, tipo, inicio, fin, _ in posibles}
            decidido = _decidido(value, next(iter(acciones.get(uid) or ()), None), retipos.get(uid))
            estados.add(decidido)
            pares = list({_par_de(actual): actual for actual, *_ in posibles}.values()) or [value]
            formas[uid] = {_forma_dirigida(tipo, inicio, fin) for _, tipo, inicio, fin, _ in posibles if tipo} | {
                _forma_dirigida(decidido[0], *((par.get('end'), par.get('start')) if decidido[1] == 'inv'
                                      else (par.get('start'), par.get('end'))))
                for par in pares if decidido[0]}
            extremos = {value.get('start'), value.get('end')} - {None}
            if nodos is not None and extremos - nodos:
                # Sin tipo en el censo no hay forma censal legible ni estados que comparar: compite (AC98).
                abiertas[uid] = (uid, frozenset(extremos & nodos), frozenset(estados) if value.get('type') else
                                 frozenset(), (value.get('start'), value.get('end')))
    vecinos = defaultdict(set)
    for enlace in enlaces:
        for x in enlace:
            vecinos[x] |= enlace - {x}
    etiquetadas = defaultdict(set)
    for regla in (reglas if overlay is None else overlay):
        for marca in _MARCA.findall(regla.get('reason') or ''):
            etiquetadas[marca].add(frozenset((regla.get('source_id'), regla.get('target_id'))))
    declarados = {}
    for uid, value in valores.items():
        a, b = value.get('start'), value.get('end')
        pares = {frozenset((a, z)) for z in vecinos.get(b, ())} | {frozenset((b, z)) for z in vecinos.get(a, ())} \
            | etiquetadas.get(uid[:8], set())
        if pares:
            declarados[uid] = frozenset(pares)
    otras = {uid: list(suyas) for uid, suyas in gemelas.items()}
    return {uid: Ajenas(frozenset().union(*(formas[o] for o in otras.get(uid, ()))),
                        tuple(abiertas[o] for o in otras.get(uid, ()) if o in abiertas),
                        declarados.get(uid, frozenset()))
            for uid in set(otras) | set(declarados)}


def _lleva(props, carga):
    """Si una copia lleva la carga de la fase `set` de su operación vigente (`carga`: la que escribe el loader, con sus
    props derivadas, spec 071 AC77): cada clave con su valor. Una ausente no la lleva (una fecha sin su `date_norm`);
    la que la carga borra (None), sí."""
    return all(props.get(prop) == valor for prop, valor in (carga or {}).items())


def clasificar(index, value, accion, nuevo, carga, posibles, actuales=None, ajenas=SIN_AJENAS, inferidos=frozenset()):
    """Copias(incompatibles, sin_carga, decididas, ambiguas, no_atribuidas): las copias vivas de la arista `value` del
    censo bajo su operación vigente (`accion`; `nuevo`, el tipo de un retype; `carga`, la de su fase `set` tal como la
    escribe el loader, con sus props derivadas: AC77), clasificadas una sola vez para los dos checks (spec 071 AC96).
    Identificadas como todas (`copias_identificadas`, AC85: una de una capa que su cargador recrea por su identidad o
    una postura, por esa identidad, con cualquier documento y cita; el resto por su documento y su cita, con cualquier
    capa) en su par del censo y, re-identificada (`actuales`, de `pares_actuales`), en sus pares actuales; y las que
    halla fuera de ellos la búsqueda libre (`copias_libres`, AC86 y AC89, marcadas `fuera`). Primero se identifica y
    después se juzgan la capa, el tipo y el sentido (AC105).
    `incompatibles`, [(clave, fuera)]: las que no pueden ser su resultado. Con un deny, toda copia posible, en cualquier
    dirección (`copias_posibles`, o `copias_por_firma` re-identificada: AC61 y AC68), también en los pares que el
    overlay declara suyos con una escisión o un merge aunque los nodos del censo sigan (`_de_la_retirada`, AC109; la
    búsqueda libre no cambia las retiradas); con un retype o un invert, la del tipo decidido fuera del sentido decidido
    (el tipo nuevo al revés, un swap que el retype no decidió: AC71, nunca en un tipo simétrico; su tipo sin invertir) y
    la de otro estado posible (`firmas_posibles`, también con la firma que deja la carga, AC84, o identificada como
    suya con cualquier capa, AC105). Son las únicas que la tolerancia puede eximir.
    `sin_carga`, [(clave, fuera)]: las del estado decidido que no llevan exactamente su carga, haya otra que la lleve o
    no (AC76 y AC80): un problema con tolerancia 0 en los dos checks (AC96).
    `decididas`, [(clave, props, fuera)]: las del estado decidido con su carga (AC74), que prueban la arista corregida
    (AC69, AC92 y AC100).
    `ambiguas`, [unit_id]: las otras aristas del censo que también pueden ser una copia libre (AC91 y AC98) o una de un
    par actual que solo deduce la firma de una regla sin etiqueta (`inferidos`, de `pares_y_origen`, AC103): esa copia
    no se le atribuye a ninguna.
    `no_atribuidas`: cuántas copias libres de su pasaje no se le atribuyen porque la arista ya tiene copia en sus pares
    (identificada o en un estado posible) y el overlay no declara la re-identificación que las haría suyas (AC94): otros
    hechos del pasaje, un aviso y nunca un problema; sin copia en sus pares, la búsqueda libre se las atribuye (AC89).
    Un set_prop (o un set_status) juzga las de su tipo en su sentido (en cualquiera, una simétrica): la que está al
    revés es otro hecho."""
    reidentificada, pares = actuales is not None, actuales or [value]
    if accion == 'deny':
        return Copias(_de_la_retirada(index, value, posibles, actuales, ajenas), [], [], [], 0)
    props_de_su_tipo = accion in ('set_prop', 'set_status')
    tipo, sentido = _decidido(value, accion, nuevo)
    otros = set() if props_de_su_tipo else {p for p in posibles if p[0] != tipo}
    incompatibles, sin_carga, decididas, ambiguas, no_atribuidas = [], [], [], set(), 0
    identificadas = []
    for s, inicio, fin, props in copias_identificadas(index, value, tipo, pares, carga):
        if props_de_su_tipo and s != sentido:
            continue
        otras = otras_que_pueden_ser(ajenas, tipo, inicio, fin) if frozenset((inicio, fin)) in inferidos else ()
        if otras:
            ambiguas.update(otras)  # AC103: el par solo lo deduce la firma, que comparte con otra unidad
            continue
        identificadas.append((s, inicio, fin, props))
    for s, inicio, fin, props in identificadas:
        clave = _clave_copia(tipo, inicio, fin, props, reidentificada)
        if s != sentido:
            incompatibles.append((clave, False))
        elif _lleva(props, carga):
            decididas.append((clave, props, False))
        else:
            sin_carga.append((clave, False))
    if otros:
        incompatibles += [(clave, False) for clave in (copias_por_firma(index, value, otros, actuales, solo=pares)
                                                         if reidentificada
                                                         else copias_posibles(index, value, otros, con_sentido=True,
                                                                              carga=carga).values())]
    locales = bool(identificadas or incompatibles)
    alcanzables = {(t, s) for t, s, _ in otros}
    for t, s, inicio, fin, props, otras, declarada in copias_libres(
            index, value, {tipo} | {t for t, _ in alcanzables}, pares, ajenas):
        if (t, s) not in alcanzables and (t != tipo or (props_de_su_tipo and s != sentido)):
            continue
        clave = _clave_copia(t, inicio, fin, props, True)
        if locales and not declarada:
            no_atribuidas += 1
        elif otras:
            ambiguas.update(otras)
        elif (t, s) != (tipo, sentido):
            incompatibles.append((clave, True))
        elif _lleva(props, carga):
            decididas.append((clave, props, True))
        else:
            sin_carga.append((clave, True))
    return Copias(incompatibles, sin_carga, decididas, sorted(ambiguas), no_atribuidas)


def _de_la_retirada(index, value, posibles, actuales, ajenas):
    """[(clave, fuera)]: las copias vivas que pueden ser la arista retirada `value`: en su par, bajo un tipo posible y
    con su firma posible (`copias_posibles`), o re-identificada (`actuales`), por su firma en cualquier par
    (`copias_por_firma`, AC61 y AC68); y, en los dos casos, las que con su firma posible están en un par que el overlay
    declara de la arista con una escisión o un merge (`ajenas.declarados`), aunque los nodos del censo sigan: el loader
    escinde antes del deny y la copia trasladada al extremo nuevo escapa a su regla (spec 071 AC109; marcadas `fuera`).
    Cada copia cuenta una vez."""
    reidentificada = actuales is not None
    vistas = {clave: (copia, False) for clave, copia in (
        _vistas_por_firma(index, value, posibles, actuales) if reidentificada
        else copias_posibles(index, value, posibles)).items()}
    sin_sentido = {(tipo, firma) for tipo, _, firma in posibles}
    for par in sorted(ajenas.declarados, key=lambda par: sorted(map(str, par))):
        for tipo in sorted({tipo for tipo, _ in sin_sentido if tipo}):
            for copia in index.get((tipo, par), ()):
                inicio, fin, props = copia
                if id(copia) not in vistas and (tipo, _rasgos(props, tipo)) in sin_sentido:
                    vistas[id(copia)] = (_clave_copia(tipo, inicio, fin, props, reidentificada), True)
    return list(vistas.values())


def sin_arista_corregida(fila, anteriores, accion, copias):
    """Si la fila vigente «rectificado» (`fila`) de un retype, un invert o un set_prop vigente (`accion`) afirma una
    arista corregida que el export no tiene (spec 071 AC92 y AC100): ninguna copia en el estado que decide su operación,
    con su carga o sin ella, ni en sus pares ni fuera (`copias`, de `clasificar`). La misma condición en los dos checks;
    la que completa una «suprimido» (en `anteriores`, las decisiones de sus filas anteriores) tiene la suya, desde el
    documento que cita (AC69 y AC90)."""
    return (fila or {}).get('decision') == 'rectificado' and 'suprimido' not in anteriores \
        and accion in ('retype', 'invert', 'set_prop') and not copias.decididas and not copias.sin_carga


def en_otra_capa(index, value, posibles=(), ajenos=frozenset()):
    """[(inicio, fin, props)]: las copias vivas de la arista `value` del censo con su mismo par orientado (en una
    simétrica, en cualquier sentido), su tipo, su documento y su cita normalizada (AC107), en una capa que no es la suya
    (spec 071 AC119): el mismo hecho, con la misma fuente, que otra capa sigue publicando. No la que es la propia arista
    en un estado al que la llevan las reglas (`posibles`, de `firmas_posibles`: un `set` de otra que le baja la capa),
    que ya cuenta como viva con su tolerancia, ni la que otra arista del censo de su par explica por la identidad de su
    capa (`ajenos`, de `estados_de_otras`: el mecanismo de AC96 y AC110), que es de esa otra."""
    props = value.get('props') or {}
    capa, doc = props.get('source_kind') or None, props.get('source_doc') or None
    cita, alcanzables, tipo = _cita(props.get('source_quote')), {(t, f) for t, _, f in posibles}, value.get('type')
    return [(inicio, fin, p) for inicio, fin, p in index.get(_par(tipo, value.get('start'), value.get('end')), ())
            if (tipo in SIMETRICOS or (inicio, fin) == (value.get('start'), value.get('end')))
            and (p.get('source_kind') or None) != capa and (p.get('source_doc') or None) == doc
            and _cita(p.get('source_quote')) == cita and (tipo, _rasgos(p, tipo)) not in alcanzables
            and (tipo, _extremos(tipo, inicio, fin), _rasgos(p, tipo)) not in ajenos]


def estados_de_otras(uid, value, por_par, trans, acciones, sets, retipos, reglas=(), nodos=None, overlay=None):
    """{(tipo, extremos, rasgos)}: las formas en que pueden estar vivas las otras aristas del censo del par de `value`
    (`por_par`: {par sin dirección: [(unit_id, value)]}), con los rasgos de la identidad de su capa (`_rasgos`): su
    forma censal y las que les dejan su operación vigente y las reglas (`_estados_posibles` y `_decidido`). Una copia de
    otra capa que una de ellas explica es de esa otra, no de la retirada (spec 071 AC119, el mecanismo de AC96 y AC110).
    No la explica la que es la misma asociación desde la misma fuente (su documento y su cita): esa es justo la copia
    que AC119 mide (la imputación de la capa `acusacion` de la que la judicial retirada era duplicado); comparten fuente
    si comparten una (`firmas_censales`, AC287)."""
    out = set()
    fuente = firmas_censales(value)
    for otra, suya in por_par.get(frozenset((value.get('start'), value.get('end'))), ()):
        if otra == uid or firmas_censales(suya) & fuente:
            continue
        for _, tipo, inicio, fin, firma in _estados_posibles(otra, suya, trans, acciones, sets, retipos, reglas,
                                                             nodos=nodos, overlay=overlay):
            out.add((tipo, _extremos(tipo, inicio, fin), _rasgos(dict(zip(FIRMA, firma)), tipo)))
        tipo, sentido = _decidido(suya, next(iter(acciones.get(otra) or ()), None), retipos.get(otra))
        if tipo:
            inicio, fin = (suya.get('end'), suya.get('start')) if sentido == 'inv' \
                else (suya.get('start'), suya.get('end'))
            for cambio in ({}, sets.get(otra) or {}):
                out.add((tipo, _extremos(tipo, inicio, fin), _rasgos({**(suya.get('props') or {}), **cambio}, tipo)))
    return out


def copias_en_otra_capa(uid, value, index, posibles, por_par, trans, acciones, sets, retipos, reglas=(), nodos=None,
                        overlay=None):
    """Las copias de `en_otra_capa` que no explica otra arista del censo de su par (`estados_de_otras`): las mismas en
    los dos checks (spec 071 AC119)."""
    copias = en_otra_capa(index, value, posibles)
    if copias:
        copias = en_otra_capa(index, value, posibles, estados_de_otras(uid, value, por_par, trans, acciones, sets,
                                                                       retipos, reglas, nodos, overlay))
    return copias


def duplicada_declarada(fila):
    """Si la fila declara con la fórmula cerrada que retiró una copia duplicada de otra capa (AC119)."""
    return str((fila or {}).get('dato') or '').endswith(f': {DUPLICADA}')


def motivo_otra_capa(fila, copias):
    """El problema de la fila «suprimido» de un deny ante las copias de su arista en otra capa (`en_otra_capa`, AC119):
    sin la fórmula, que la asociación sigue publicada. None si dice la verdad o la declara (lo que dice la fórmula, en
    `motivo_publicada`)."""
    if duplicada_declarada(fila):
        return None  # su verdad, en `motivo_publicada` (D1)
    if copias:
        return f'{OTRA_CAPA} ({len(copias)} {"copia" if len(copias) == 1 else "copias"}; tolerancia 0)'
    return None


def _alcanzan(uid, value, trans, acciones, sets, retipos):
    """[(otra, tipo, extremos, props)]: las otras aristas del censo cuya regla (la de su operación vigente, como la
    escribe el apply) alcanza la retirada en alguna fase (con dirección salvo en las simétricas, AC52) y cómo queda
    cada una tras ella: su tipo nuevo, su sentido y sus props (el loader aplica los `fix` antes que los `deny`)."""
    estado = _estado(value)
    out = []
    for otra, origen in trans.get(frozenset((value.get('start'), value.get('end'))), ()):
        if otra == uid or not _pasos(_regla_decidida(otra, origen, acciones, sets, retipos), estado):
            continue
        tipo, inicio, fin = retipos.get(otra, origen.get('type')), origen.get('start'), origen.get('end')
        if 'invert' in acciones.get(otra, ()):
            inicio, fin = fin, inicio
        out.append((otra, tipo, _extremos(tipo, inicio, fin), _publicada(otra, origen, sets)))
    return out


def legitimas(uid, value, nombradas, acciones, trans, sets, retipos, por_firma=False):
    """Counter{(tipo, extremos, firma)}: las aristas que pueden seguir vivas con derecho y confundirse con la
    retirada. Cada conservada nombrada que es el mismo hecho (con dirección salvo en las simétricas) y cuya operación
    vigente no la retira ni la transforma, con la firma que le dejan sus `set_prop`; y cada otra arista del censo cuya
    regla alcanza la retirada (con su dirección, AC52) y cuya operación vigente no es un deny, como la deja esa regla
    (con su tipo nuevo, si es un `retype`, y al revés, si es un invert). Una regla del overlay sin unidad no deja
    ninguna arista con derecho. `por_firma`: las claves de `copias_por_firma`, sin extremos (AC61)."""
    def clave(tipo, extremos, props):
        return (tipo, None, _firma_cruda(props)) if por_firma else (tipo, extremos, _rasgos(props, tipo))

    out = {}
    for otra, c in nombradas:
        if not acciones.get(otra, set()) & QUITAN and _arista(c) == _arista(value):
            out[otra] = clave(c.get('type'), _extremos(c.get('type'), c.get('start'), c.get('end')),
                              _publicada(otra, c, sets))
    for otra, tipo, extremos, props in _alcanzan(uid, value, trans, acciones, sets, retipos):
        if 'deny' not in acciones.get(otra, set()):
            out[otra] = clave(tipo, extremos, props)
    return Counter(out.values())


def _copias(index, value):
    """[(clave, props)]: las copias vivas del par y tipo de una unidad, en cualquier dirección."""
    rtype = value.get('type')
    return [(_clave(rtype, start, end), props)
            for start, end, props in index.get(_par(rtype, value.get('start'), value.get('end')), ())]


def tolerancia(vivas, legitimas):
    """Cuántas de las copias vivas que pueden ser la retirada pueden ser, en cambio, una arista que sigue con derecho
    (`legitimas`): cada una excusa una copia con su mismo tipo y firma, porque en el export no se sabe cuál de las dos
    sobrevive. Una firma que ninguna viva lleva no excusa a ninguna."""
    presentes = Counter(vivas)
    return sum(min(presentes[k], n) for k, n in legitimas.items())


def conservada_ausente(nombradas, acciones, sets, index, nodos):
    """Si alguna arista que la decisión dijo conservar ya no está publicada: el mismo hecho (tipo y extremos,
    con dirección salvo en las simétricas) vivo desde su documento, con cualquier cita (una copia extraída de
    nuevo cuenta). Un extremo Claim es su afirmación, recuperada del censo (`con_afirmacion`, spec 071 AC104); si no
    se recuperó, no se puede identificar, así que la copia viva tiene que llevar además su cita. Documento y cita son
    los que deja el apply (con los `set_prop`). No cuenta la que alguna decisión retiró, retipó o invirtió, ni la que
    perdió un extremo con entity_id (nodo podado por otra vía); `set_prop` y `set_status` solo cambian props y la
    arista tiene que seguir."""
    for uid, value in nombradas:
        extremos = {value.get('start') or None, value.get('end') or None}
        if acciones.get(uid, set()) & QUITAN or not extremos - {None} <= nodos:
            continue
        props = _publicada(uid, value, sets)
        clave, doc = _arista(value), props.get('source_doc') or None
        cita = _cita(props.get('source_quote')) if None in extremos else None
        if not any(viva == clave and (live.get('source_doc') or None) == doc
                   and (cita is None or _cita(live.get('source_quote')) == cita)
                   for viva, live in _copias(index, value)):
            return True
    return False


_PDF = re.compile(r'[\w.\-]+\.pdf')


def vigentes_por_unidad(rows):
    """{unidad: (etiqueta, fila, [decisiones anteriores])}: la fila vigente de cada unidad (spec 071 AC67), la de
    fecha más reciente y, a igual fecha, la de id mayor. El registro no borra historia: una «suprimido» que una fila
    posterior completa sigue en él, pero ya no es lo que afirma."""
    por_unidad = defaultdict(list)
    for n, row in enumerate(rows, 1):
        if row.get('unidad'):
            por_unidad[row['unidad']].append((str(row.get('fecha') or ''), str(row.get('id') or ''), n, row))
    out = {}
    for unidad, filas in por_unidad.items():
        filas.sort(key=lambda x: (x[0], x[1]))
        *anteriores, (_, _, n, row) = filas
        out[unidad] = (_etiqueta(row, n), row, [a[3].get('decision') for a in anteriores])
    return out


def documentos(fila):
    """Los documentos que cita la `fuente` de una fila del registro (ninguno si la cita es neutra)."""
    return set(_PDF.findall(str((fila or {}).get('fuente') or '')))


def _sigue_publicada(index, value, fila, actuales, accion=None, nuevo=None, carga=None, ajenas=SIN_AJENAS,
                     inferidos=frozenset()):
    """Si el export tiene la arista `value` como hecho, o re-identificada, en uno de sus pares actuales, en el estado
    que deja su operación vigente (spec 071 AC88: `accion`, con `nuevo`, el tipo de un retype, y `carga`, los props de
    su `set`): el tipo tras un retype y el sentido tras un invert, con su dirección salvo en los tipos simétricos (AC70:
    una OWNS B→A no conserva la A→B); la capa de antes o la de después de la carga (la que aún conserva la del censo
    está publicada, sin corregir, y eso lo cuenta la comprobación de la carga) y, si la fila cita un documento, desde
    él: lo que afirma una «rectificado» que completa una «suprimido» (AC67) y lo que conserva una decisión humana
    posterior a una supresión (AC70). También la copia, con esa dirección, esa capa y ese documento, que halla fuera de
    sus pares la búsqueda libre sin ambigüedad (`copias_libres`, AC89, AC91 y AC98) y que se le atribuye: sin copia del
    hecho en sus pares, o en un par que el overlay declara suyo (AC94 y AC97). Es copia del hecho la que cuenta
    `clasificar` (AC96): la identificada en su sentido; la de un retype o un invert, en cualquiera. La de un par
    actual que solo deduce la firma (`inferidos`) y que también puede ser otra unidad no se le atribuye (AC103)."""
    tipo, sentido = _decidido(value, accion, nuevo)
    antes, docs, pares = value.get('props') or {}, documentos(fila), actuales or [value]
    capas = {antes.get('source_kind') or None, {**antes, **(carga or {})}.get('source_kind') or None}

    def vale(s, props):
        return s == sentido and (props.get('source_kind') or None) in capas \
            and (not docs or props.get('source_doc') in docs)

    def suya(inicio, fin):
        return frozenset((inicio, fin)) not in inferidos or not otras_que_pueden_ser(ajenas, tipo, inicio, fin)

    if any(vale(_sentido(actual, tipo, inicio, fin), props) and suya(inicio, fin) for actual in pares
           for inicio, fin, props in index.get(_par(tipo, actual.get('start'), actual.get('end')), ())):
        return True
    locales = any(s == sentido or accion in ('retype', 'invert')
                  for s, *_ in copias_identificadas(index, value, tipo, pares, carga))
    return any(vale(s, props) for _, s, _, _, props, otras, declarada in copias_libres(index, value, {tipo}, pares,
                                                                                       ajenas)
               if not otras and (declarada or not locales))


def _viva(corrige, reidentificada, n, tolera, fuera=False):
    """El motivo de una fila cuya arista sigue viva: la «suprimida», o la «rectificada» sin corregir; `fuera`, alguna
    hallada en un par distinto del de la regla (AC86 y AC89)."""
    que = '«rectificada» viva sin corregir' if corrige else '«suprimida» viva'
    return (f'arista {que} ({"par re-identificado; " if reidentificada else ""}{OTRO_PAR if fuera else ""}'
            f'{n} {"copia" if n == 1 else "copias"}; tolerancia {tolera})')


def preparadas(declaraciones):
    """{clave: [predicado]} con cada predicado privado preparado exactamente como una deny explícita del loader
    (`relations_loader._preparar_deny`: extremos y tipo sin blancos; documento, trozo de cita y capas tal como se
    declararon, sin convertir un vacío en comodín: spec 071 AC151 y AC152), conservando si es `ajena`. Idempotente. Uno
    que el loader no prepara: ReglaOpacaInvalida."""
    if not declaraciones:
        return {}
    from src import relations_loader
    out = {}
    for clave, predicados in declaraciones.items():
        out[clave] = []
        for p in predicados:
            fila = relations_loader._preparar_deny(p, registrar=False)
            if fila is None:
                raise ReglaOpacaInvalida('una declaración privada sin la forma de un deny')
            out[clave].append({**fila, **({'ajena': True} if p.get('ajena') else {})})
    return out


def _de_predicado(p):
    """La arista que nombra el predicado `p` (tipo, inicio y fin, y su documento): con la que se comprueba la fórmula de
    una fila sin unidad del censo (spec 071 AC150)."""
    return {'type': p['type'], 'start': p['source_id'], 'end': p['target_id'], 'props': {'source_doc': p.get('doc')}}


# Las fórmulas cerradas con que una fila «suprimido» sin unidad ni marcador dice que se retiraron relaciones de un
# seudónimo judicial (las del vocabulario de la spec 070): su verdad la comprueba la política de seudónimos, no el
# registro (spec 071 AC183).
_SEUDONIMO_RETIRADA = re.compile(
    r'\(Person:[a-z0-9]+(?:-[a-z0-9]+)*\): (?:relación institucional retirada|relaciones institucionales retiradas|'
    r'relación institucional duplicada retirada|cita con cargo identificativo retirada) \(seudónimo judicial\)')
SIN_REGLA_PUBLICA = 'retirada sin regla pública'
OVERLAY_INESTABLE = 'las reglas fix del overlay no se estabilizan (cargas sucesivas en ciclo)'
DATO_SIN_FORMA = 'retirada sin unidad con un dato que no se puede comprobar'


def deny_del_par(fila, explicitas):
    """[regla]: las deny explícitas `explicitas` (preparadas: `source_id`, `target_id`, `type`, `doc`) que casan la
    arista del `dato` de la fila del registro `fila`: su tipo, su par en cualquier orden y, si su `fuente` cita
    documentos, uno de ellos o ninguno (spec 071 AC183). None si el dato no tiene la forma de la fila de una arista."""
    m = _DATO_ARISTA.fullmatch(str(fila.get('dato') or ''))
    if not m:
        return None
    par, docs = frozenset((m['a'], m['b'])), documentos(fila)
    return [r for r in explicitas if r.get('type') == m['tipo']
            and frozenset((r.get('source_id'), r.get('target_id'))) == par
            and (not docs or not r.get('doc') or r.get('doc') in docs)]


def nombrados_por_su_regla(fila, deny, fix, lista=()):
    """{id}: los extremos de la arista del `dato` de la fila del registro `fila` (los de sus dos posiciones) si ya los
    nombra una regla pública asociada a ella (principio P de la fusión 070 ⟷ 071, 9-nov), o ninguno: `deny` y
    `fix`, las reglas explícitas del overlay (nunca una opaca, que no nombra su par), preparadas con su par y su
    `reason`. Asociada es la que lleva en su motivo la huella `[hash8]` de la unidad de la fila (la etiqueta con que la
    escribe el apply, `qa_esc_apply._motivo`), si su par es el del dato; si ninguna regla lleva esa huella y la fila es
    «suprimido», la deny sin etiqueta de ninguna unidad que casa su arista (`deny_del_par`, el criterio de AC183; la
    etiquetada es la de otra fila); una «rectificado» no se casa con una regla por su par. Y `lista`, las entradas
    `aristas` de la lista pública de permitidas (spec 070): la que tiene por seudónimo y por otro extremo los dos del
    dato, con cualquier tipo y cualquier decisión, publica el par como D1 de la 071 (9-nov). Por cualquiera de las vías,
    solo si la `entidad` de la fila es uno de los dos (spec 071 AC295: si no, la fila juntaría a un tercero con el par).
    La huella de una unidad, la de `_MARCA` (AC296). Nombrarlos no publica nada que esa regla o esa entrada no
    publiquen. Ninguno más: ni otro extremo, ni el de la regla de otra fila, ni el de una regla sin asociación."""
    m = _DATO_ARISTA.fullmatch(str(fila.get('dato') or ''))
    if not m:
        return frozenset()
    par, unidad = frozenset((m['a'], m['b'])), str(fila.get('unidad') or '')
    if fila.get('entidad') not in par:
        return frozenset()
    etiquetadas = [r for r in (*deny, *fix) if unidad and f'[{unidad[:8]}]' in str(r.get('reason') or '')]
    if etiquetadas:
        suyas = [r for r in etiquetadas if frozenset((r.get('source_id'), r.get('target_id'))) == par]
    elif fila.get('decision') == 'suprimido':
        suyas = deny_del_par(fila, [r for r in deny if not _MARCA.search(str(r.get('reason') or ''))])
    else:
        suyas = []
    listada = any(frozenset((e.get('seudonimo'), e.get('otro'))) == par for e in lista if isinstance(e, dict))
    return par if suyas or listada else frozenset()


def retiradas_sin_unidad(rows, filas, deny, index, tras_overlay, fix=None):
    """(cuántas, [(etiqueta, motivo)]): las «suprimido» sin unidad (tandas del dueño posteriores al censo), comprobadas
    como su arista (spec 071 AC183 y AC184). Con marcador, con su declaración privada (`filas`, preparadas; AC134):
    sin ella no hay qué comprobar, y lo dicen el anonimato («no verificable») y la cobertura del fichero privado.
    Sin marcador, por la forma de su `dato`: una arista «(A) -[T]-> (B): …», con las reglas deny explícitas del overlay
    (`deny`, preparadas como las prepara el loader) de su tipo y su par en cualquier orden, y de los documentos que
    cita su `fuente` si cita alguno —sin ninguna, `SIN_REGLA_PUBLICA`—; una fórmula cerrada de seudónimo
    (`_SEUDONIMO_RETIRADA`), la de su política (spec 070), sin más; otra, `DATO_SIN_FORMA` (fallo cerrado). Ninguna
    copia viva en el export (`index`) que sus predicados alcancen como el loader, ni, con el overlay, ninguna a la que
    el overlay lleve su arista bajo otro tipo o en otro sentido en alguna de sus cargas sucesivas (`_transformadas`,
    AC194 y AC220; si no se estabilizan, `OVERLAY_INESTABLE`); y la fórmula de una arista,
    literalmente verdad con su par tras el overlay (`tras_overlay`; `fix`, las reglas `fix` del overlay, o None sin
    él). La comparten `--snapshot` (`supervivientes`) y la puerta del Path A (`materializacion`)."""
    explicitas = [r for r in deny or () if not r.get('unidad')]
    problemas, cuantas = [], 0
    por_par = _copias_por_par(index)
    for n, fila in enumerate(rows, 1):
        if not isinstance(fila, dict) or fila.get('unidad') or fila.get('decision') != 'suprimido':
            continue
        rid, dato = _etiqueta(fila, n), str(fila.get('dato') or '')
        if MARCADOR.search(dato):
            if rid not in filas:
                continue
            predicados = filas[rid]
        elif _SEUDONIMO_RETIRADA.fullmatch(dato):
            continue
        else:
            predicados = deny_del_par(fila, explicitas)
            if predicados is None:
                problemas.append((rid, DATO_SIN_FORMA))
                continue
            if not predicados:
                problemas.append((rid, SIN_REGLA_PUBLICA))
                continue
        cuantas += 1
        vivas = {id(copia): copia for r in predicados
                 for copia in index.get(_par(r['type'], r['source_id'], r['target_id']), ())
                 if alcanza_loader(r, {'type': r['type'], 'start': copia[0], 'end': copia[1], 'props': copia[2]},
                                   'deny')}
        inestable = False
        if fix is not None:
            for r in predicados:
                copias, estable = _transformadas(r, por_par, deny, fix)
                vivas.update({id(copia): copia for copia in copias})
                inestable = inestable or not estable
        if vivas:
            problemas.append((rid, _viva(False, False, len(vivas), 0)))
        if inestable:
            problemas.append((rid, OVERLAY_INESTABLE))
        publicada = motivo_publicada(fila, _de_predicado(predicados[0]), tras_overlay, fix)
        if publicada:
            problemas.append((rid, publicada))
    return cuantas, problemas


def _copias_por_par(index):
    """{par: [(tipo, copia)]}: las copias del export (`index`: {(tipo, par): [copia]}) agrupadas por su par."""
    por_par = defaultdict(list)
    for (tipo, par), copias in index.items():
        por_par[par] += [(tipo, copia) for copia in copias]
    return por_par


def _capas_iniciales(predicado, props):
    """[capa]: las capas (`source_kind`) que pudo tener, antes del overlay, la arista cuya copia exportada lleva `props`
    y que el predicado de retirada alcanza (spec 071 AC213): una regla `fix` con `set.source_kind` la cambia, así que la
    capa de la copia final no es la de partida. La de la copia, si no cambió; y las que la deny alcanza: la judicial
    o la nula (cualquier deny) y las suyas (`kinds`)."""
    capas = [props.get('source_kind')]
    for capa in (None, 'judicial', *(predicado.get('kinds') or ())):
        if capa not in capas:
            capas.append(capa)
    return capas


def _firma(estado):
    """Lo que distingue un estado del grafo tras el overlay (`tras_el_overlay`) de otro: sus aristas con sus props,
    sin el orden de las copias."""
    return sorted((tipo, sorted(map(str, par)), str(inicio), str(fin), json.dumps(props, sort_keys=True, default=str))
                  for (tipo, par), copias in estado.items() for inicio, fin, props in copias)


def _cargas_sucesivas(index, deny, fix):
    """([estado], estable): el grafo tras cada carga sucesiva del overlay (`deny` y `fix`, preparadas) a partir de
    `index` (spec 071 AC220). El linaje atraviesa varias cargas (cada Path A carga el overlay sobre el grafo anterior) y
    `tras_el_overlay` es UNA, con una sola transformación por arista (AC158): dos retipos encadenados (A→B y B→C) dejan
    la arista en B tras la primera carga y en C tras la segunda. Se repite, con las `deny` en cada carga, hasta que una
    carga no cambie lo que dejó la anterior (`estable`); la cota es el número de reglas más una, y si ninguna carga
    repite a la anterior (un ciclo de retipos o inversiones) no es estable."""
    estados, actual = [], index
    for _ in range(len(deny or ()) + len(fix or ()) + 1):
        siguiente = tras_el_overlay(actual, deny, fix)
        estados.append(siguiente)
        if _firma(siguiente) == _firma(actual):
            return estados, True
        actual = siguiente
    return estados, False


def _transformadas(predicado, por_par, deny, fix):
    """([copia], estable): las copias vivas del export (`por_par`: {par: [(tipo, copia)]}) que son la arista de un
    predicado de retirada después del overlay (spec 071 AC194): con el tipo del predicado (el estado inicial) y su
    documento y su cita, la deny la alcanza, y el overlay entero (`deny` y `fix`, como los aplica el loader:
    `tras_el_overlay`, AC158) la deja viva con el tipo, el sentido, el documento, la cita y la capa de la copia, tras
    alguna de las cargas sucesivas que el linaje atraviesa (`_cargas_sucesivas`, AC220). El estado inicial no es la
    copia final (AC213): se parte de cada capa compatible con el predicado (`_capas_iniciales`) y es el overlay el que
    debe dejar la capa que lleva la copia. También la que conserva el tipo del predicado si un `set` la movió de capa
    y la deny ya no la alcanza; la que la deny alcanza tal cual la cuenta ya la búsqueda directa. `estable` es False
    si alguna simulación no se estabiliza (AC220)."""
    estable = True

    def sigue_viva(copia, tipo, a, b):
        nonlocal estable
        inicio, fin, props = copia
        for capa in _capas_iniciales(predicado, props):
            antes = {k: v for k, v in props.items() if k != 'source_kind'}
            if capa is not None:
                antes['source_kind'] = capa
            if not alcanza_loader(predicado, {'type': predicado.get('type'), 'start': a, 'end': b, 'props': antes},
                                  'deny'):
                continue
            estados, estaba = _cargas_sucesivas({_par(predicado.get('type'), a, b): [(a, b, antes)]}, deny, fix)
            estable = estable and estaba
            if any((tipo in SIMETRICOS or (x, y) == (inicio, fin))
                   and (p.get('source_doc') or None) == (props.get('source_doc') or None)
                   and _cita(p.get('source_quote')) == _cita(props.get('source_quote'))
                   and (p.get('source_kind') or None) == (props.get('source_kind') or None)
                   for final in estados for x, y, p in final.get(_par(tipo, inicio, fin), ())):
                return True
        return False
    out = []
    for tipo, copia in por_par.get(frozenset((predicado.get('source_id'), predicado.get('target_id'))), ()):
        inicio, fin, props = copia
        if tipo == predicado.get('type') and alcanza_loader(
                predicado, {'type': tipo, 'start': inicio, 'end': fin, 'props': props}, 'deny'):
            continue
        if any(sigue_viva(copia, tipo, a, b) for a, b in dict.fromkeys(((inicio, fin), (fin, inicio)))):
            out.append(copia)
    return out, estable


def supervivientes(rows, conn, root, index, nodos, reglas=(), overlay=None, enlaces=frozenset(), privadas=None,
                   repo=None, tras_overlay=None, nombres=None, snapshot=None, filas=None, deny=(), registro=None,
                   referencias_privadas=None):
    """(filas comprobadas, [(etiqueta, motivo)], [(etiqueta, aviso)]): lo que la fila vigente de cada unidad afirma y el
    export desmiente (AC67), con las copias de su arista clasificadas como en `materializacion` (`clasificar`, AC96). De
    una «suprimido», que su operación vigente no conserve la arista (AC93), que su arista no siga viva y que la
    conservada que nombra siga publicada; de una «rectificado» de un retype o un invert vigente, que ninguna copia siga
    en un estado que no es el decidido (AC64 y AC71), con la tolerancia de las otras aristas de su par, ni en el
    decidido sin la carga de su `set` (AC76 y AC80), sin tolerancia (AC96), tampoco en un par distinto del de la regla
    (AC89); de una «rectificado» de un set_prop vigente, que cada copia de su arista lleve la carga, sin tolerancia,
    como la cuenta `materializacion` (AC83, con las copias de AC81 y AC86); de las dos, que la búsqueda libre no halle
    una copia ambigua entre unidades (AC91 y AC98) y, si no tienen otro problema de copias, que la arista corregida esté
    publicada (`sin_arista_corregida`, AC92 y AC100); de una «rectificado» que completa una «suprimido» anterior, que la
    respalde una operación vigente que no la retira y que su arista esté publicada en su sentido (AC70 y AC97) o, si la
    retipa o la invierte, en el estado decidido (AC69), con la capa que deja su carga o aún sin ella (AC88), desde el
    documento que cita si cita uno. Avisa, sin que cuente como problema, de las copias de su pasaje en otro par que no
    se le atribuyen (AC94). El set_status queda fuera, como en `materializacion`: la capa de acusaciones recrea y escala
    el estado después de la decisión. `reglas`: las `fix` del overlay ya preparadas (`--overlay`), que también dejan
    copias posibles (AC51); `overlay`: sus deny y fix, que dicen los pares actuales de una arista re-identificada
    (AC63); `enlaces`: las re-identificaciones que declara (escisiones y merges, AC94). Una «suprimido» de un deny con
    una copia de su arista en otra capa (mismo par orientado, tipo, documento y cita) dice algo falso salvo que lo
    declare con la fórmula cerrada `DUPLICADA`, y con ella, si no queda ninguna (AC119); no cuenta la que otra arista
    del censo de su par explica por la identidad de su capa (`copias_en_otra_capa`). Una «suprimido» con la fórmula de
    AC125 (`PUBLICADA`) dice la verdad si el grafo publica tras el overlay (`tras_overlay`, de `tras_el_overlay`; sin
    él, el export tal cual) una copia de su asociación desde su documento, con un retipo que alcanza alguna de `reglas`
    (`motivo_publicada`, AC136). `privadas`: las declaraciones privadas de las reglas opacas (AC121): la fila de una
    retirada sin unidad del censo lleva su clave privada y se comprueba con su predicado, como la cuenta el loader. Con
    `repo` (la puerta, `--overlay`), además, que ningún fichero público ni ningún texto del export `snapshot` permita
    reconstruir la arista retirada de una fila anonimizada (`reconstrucciones`, AC122-bis y AC133, con los `nombres` del
    export y las declaraciones privadas de las filas sin unidad, `filas`, AC134)."""
    privadas, filas = preparadas(privadas), preparadas(filas)
    tras_overlay = index if tras_overlay is None else tras_overlay
    vigentes_ = vigentes_por_unidad(rows)
    # AC284–AC286: la arista de una unidad con una migración de cita declarada lleva su firma de después; una
    # declaración que no vale no cuenta y es un problema.
    migradas, no_validas = migraciones_citas(root, conn)
    valores = aristas_censales(conn, migradas)
    por_par = defaultdict(list)
    for uid, value in valores.items():
        por_par[frozenset((value.get('start'), value.get('end')))].append((uid, value))
    unidades = {}
    problemas = []
    comprobadas, avisos = Counter(), []
    sin_unidad, problemas = retiradas_sin_unidad(rows, filas, deny, index, tras_overlay,
                                                 reglas if overlay is not None else None)
    problemas += [(MIGRACIONES_CITAS, f'{MIGRACION_NO_VALIDA}: {motivo}') for motivo in no_validas]
    comprobadas['suprimido'] += sin_unidad
    for unidad, (rid, fila, _) in vigentes_.items():
        if fila.get('decision') not in ('suprimido', 'rectificado'):
            continue
        unit = conn.execute('SELECT kind FROM units WHERE unit_id=?', (unidad,)).fetchone()
        if unit is None and unidad in privadas:
            # AC121: una retirada sin unidad del censo, por su declaración privada: ninguna arista viva que su predicado
            # alcance como la alcanza el loader.
            if fila.get('decision') != 'suprimido':
                problemas.append((rid, 'clave privada en una fila que no retira su arista'))
                continue
            comprobadas['suprimido'] += 1
            # Los predicados `ajena` son de otros hechos que se hicieron opacos bajo su clave (AC124): no son suyos. Sin
            # ninguno suyo, nada dice qué arista retira la fila (AC157).
            predicados = [p for p in privadas[unidad] if not p.get('ajena')]
            if not predicados:
                problemas.append((rid, SIN_PREDICADO))
                continue
            vivas = [(r['type'], inicio, fin) for r in predicados
                     for inicio, fin, props in index.get(_par(r['type'], r['source_id'], r['target_id']), ())
                     if alcanza_loader(r, {'type': r['type'], 'start': inicio, 'end': fin, 'props': props}, 'deny')]
            inestable = False
            if overlay is not None:
                # AC213: y las copias que el overlay entero deja vivas con otro tipo, otro sentido o otra capa.
                copias = _copias_por_par(index)
                for r in predicados:
                    transformadas, estable = _transformadas(r, copias, deny, reglas)
                    vivas += [(r['type'], c[0], c[1]) for c in transformadas]
                    inestable = inestable or not estable
            if vivas:
                problemas.append((rid, _viva(False, False, len(vivas), 0)))
            if inestable:
                problemas.append((rid, OVERLAY_INESTABLE))
            # AC150: su fórmula, como la de una unidad del censo, con el par de su predicado.
            publicada = motivo_publicada(fila, _de_predicado(predicados[0]), tras_overlay,
                                         reglas if overlay is not None else None)
            if publicada:
                problemas.append((rid, publicada))
        elif unit is None:
            if fila.get('decision') == 'suprimido':
                problemas.append((rid, 'la unidad no está en el censo'))
        elif unit[0] == 'edge':
            unidades[unidad] = valores[unidad]
    porque, acciones, sets, retipos = decisiones(root, set(unidades))
    trans = transformadoras(conn, acciones, sets, retipos)
    ajenas = formas_ajenas(valores, trans, acciones, sets, retipos, reglas, nodos, overlay, enlaces)
    paquetes = defaultdict(set)
    for entidad, unit_ids in conn.execute('SELECT entity, unit_ids FROM esc_packs'):
        paquetes[entidad].update(json.loads(unit_ids or '[]'))
    for unidad, (rid, fila, anteriores) in vigentes_.items():
        value = unidades.get(unidad)
        if value is None:
            continue
        accion = next(iter(acciones.get(unidad) or ()), None)
        completa = fila['decision'] == 'rectificado' and 'suprimido' in anteriores
        corrige = fila['decision'] == 'rectificado' and accion in ('retype', 'invert')
        corrige_props = fila['decision'] == 'rectificado' and accion == 'set_prop'
        if fila['decision'] == 'rectificado' and not (completa or corrige or corrige_props):
            continue
        comprobadas[fila['decision']] += 1
        if fila['decision'] == 'suprimido' and accion in CONSERVAN:
            # AC93: la fila da por retirada una arista que su decisión vigente conserva, la retire lo que la retire.
            problemas.append((rid, f'fila contradice la decisión (suprimido; decisión: {accion})'))
        if indeterminada(value):
            # AC104: sin su afirmación, cualquier postura de su titular pasaría por ella.
            problemas.append((rid, INDETERMINADA))
            continue
        # Un extremo del censo que ya no está en el export se re-identificó después del censo: su par no existe y el
        # hecho puede seguir vivo entre el nodo nuevo y el otro extremo (AC61). La `entidad` de la fila no cuenta.
        reidentificada = bool({value.get('start'), value.get('end')} - {None} - nodos)
        actuales, inferidos = pares_y_origen(unidad, value, nodos, overlay if overlay is not None else reglas) \
            if reidentificada else (None, frozenset())
        mias = ajenas.get(unidad, SIN_AJENAS)
        posibles = firmas_posibles(unidad, value, trans, acciones, sets, retipos, reglas, nodos=nodos,
                                   overlay=overlay) if corrige or fila['decision'] == 'suprimido' else set()
        otra_capa = motivo_otra_capa(fila, copias_en_otra_capa(
            unidad, value, index, posibles, por_par, trans, acciones, sets, retipos, reglas, nodos, overlay)) \
            if fila['decision'] == 'suprimido' and accion == 'deny' else None
        if otra_capa:
            # AC119: la misma asociación, con la misma fuente, sigue publicada por otra capa: la fila lo dice con su
            # fórmula, o no dice la verdad.
            problemas.append((rid, otra_capa))
        publicada = motivo_publicada(fila, value, tras_overlay, reglas if overlay is not None else None) \
            if fila['decision'] == 'suprimido' else None
        if publicada:
            # AC125: la fila dice que otra arista del mismo documento sigue publicando su asociación, y el grafo no.
            problemas.append((rid, publicada))
        copias = clasificar(index, value, accion if corrige or corrige_props else 'deny', retipos.get(unidad),
                            sets.get(unidad), posibles, actuales, mias, inferidos) \
            if corrige or corrige_props or fila['decision'] == 'suprimido' else Copias([], [], [], [], 0)
        if copias.ambiguas:
            # AC91: una copia que también puede ser otra arista del censo no se atribuye a ninguna; se resuelve a mano.
            problemas.append((rid, f'{AMBIGUA} ({", ".join(huella(u, "?") for u in (unidad, *copias.ambiguas))})'))
        if copias.no_atribuidas:
            # AC94: con su arista en sus pares, otro hecho de su pasaje en otro par no es suyo sin una re-identificación
            # declarada; se avisa, sin que cuente.
            avisos.append((rid, f'{NO_ATRIBUIDA} ({unidad[:8]})'))
        if completa and accion in (None, 'deny'):
            problemas.append((rid, 'fila «rectificado» sin una operación vigente que la respalde'))
        elif completa and not ([c for c, props, _ in copias.decididas
                                if not documentos(fila) or props.get('source_doc') in documentos(fila)] if corrige
                               else _sigue_publicada(index, value, fila, actuales, accion, retipos.get(unidad),
                                                     sets.get(unidad), mias, inferidos)):
            # AC69, AC88 y AC97: la «rectificado» que completa una «suprimido» afirma la arista publicada en el estado
            # que deja su operación vigente, desde el documento que cita.
            problemas.append((rid, 'arista «rectificada» ausente del export'))
        if corrige_props:
            # AC83: cada copia de la arista lleva la carga del loader, haya otra que la lleve o no; la cuentan igual los
            # dos checks (`materializacion`, AC79), así que ninguna gemela la excusa. También la de un par distinto del
            # de la regla (AC86). Sin ninguna, la fila afirma una corrección que no se publica (AC100).
            if copias.sin_carga:
                problemas.append((rid, _viva(True, reidentificada, len(copias.sin_carga), 0,
                                             any(fuera for _, fuera in copias.sin_carga))))
            elif not copias.ambiguas and sin_arista_corregida(fila, anteriores, accion, copias):
                problemas.append((rid, SIN_CORREGIDA))
            continue
        if fila['decision'] == 'rectificado' and not corrige:
            continue
        texto, entidad = porque.get(unidad, ('', None))
        nombradas, ambigua = conservadas(conn, paquetes, unidad, texto, entidad)
        if ambigua and not corrige:
            problemas.append((rid, 'referencia ambigua a la conservada'))
        # AC96: la tolerancia (otra arista del par que puede seguir viva con derecho) solo exime copias incompatibles;
        # una del estado decidido sin la carga de su `set` (AC76) es un problema, haya o no otra que la explique.
        vivas, sin_carga = copias.incompatibles, copias.sin_carga
        tolera = tolerancia([clave for clave, _ in vivas], legitimas(unidad, value, nombradas, acciones, trans, sets,
                                                                      retipos, por_firma=reidentificada))
        if len(vivas) > tolera or sin_carga:
            problemas.append((rid, _viva(corrige, reidentificada, len(vivas) + len(sin_carga), tolera,
                                         any(fuera for _, fuera in vivas + sin_carga))))
        elif not copias.ambiguas and sin_arista_corregida(fila, anteriores, accion, copias):
            # AC92: la «rectificado» afirma una arista corregida que el export no tiene; si la fila ya tiene otro
            # problema de copias (viva sin corregir, ambigua), ese manda.
            problemas.append((rid, SIN_CORREGIDA))
        if not corrige and conservada_ausente(nombradas, acciones, sets, index, nodos):
            problemas.append((rid, 'gemela conservada ausente'))
    if repo is not None:
        reconstruidas, del_feed = anonimato_con_censo(rows, valores, privadas, repo, snapshot, tras_overlay, nombres,
                                                      filas, registro, referencias_privadas)
        problemas += reconstruidas
        avisos += del_feed
    return comprobadas, problemas, avisos


# --- el anonimato del registro frente a los YAML públicos (spec 071 AC113–AC115, AC122 y AC123) --------------------

# Las secciones de los ficheros públicos que un diagnóstico puede nombrar (spec 071 AC135): las del esquema de cada
# fichero. Cualquier otra se nombra por su orden en el fichero («sección n»): una clave libre puede llevar un valor.
SECCIONES = frozenset({
    'accusations', 'adjudicatarias', 'alias_deny', 'allow', 'ampliada_adjudicatarias', 'ampliada_organos',
    'ampliada_sin_adjudicaciones', 'citas', 'claims', 'companies', 'contracts', 'convocantes', 'deny', 'documents',
    'entities', 'feed', 'fix', 'items', 'merges', 'network', 'organismos', 'organos', 'patrones', 'pseudonyms',
    'rectificaciones', 'relations', 'revisados', 'sin_menores', 'splits', 'subvenciones', 'summaries', 'trayectorias'})


def _seccion_nombrada(nombre, n):
    return nombre if nombre in SECCIONES else f'sección {n}'


def entradas_publicas(repo):
    """[(posición, texto, fichero, sección, valor, barrido)]: cada entrada de cada fichero de texto público de `repo`
    (`pipeline/data/**`, sin la auditoría ni los PDF: spec 071 AC133). De un YAML (`_entradas_yaml`), cada elemento de
    una sección de lista, con las líneas que ocupa y los comentarios que lo siguen hasta el siguiente (`valor`, el
    elemento leído), y cada sección que no es una lista, entera; las dos con la clave de su sección (AC162) y, además
    del texto literal, sus claves y escalares leídos (AC163, con el contenido de un `!!binary`: AC173), en todos sus
    documentos (AC174); cada línea que no está en ninguna (cabeceras, comentarios sueltos y la clave de una sección de
    lista, con su clave leída) y cada comentario de las demás, aparte (AC144); de un JSON, leído (con una clave
    repetida, cada objeto con todos sus pares, y además por líneas: AC175), cada elemento de una lista de primer nivel
    o cada clave de un mapa, con su clave (también la de cada elemento de una clave cuyo valor es una lista, y la de una
    lista vacía: AC145); de cualquier otro (Markdown, texto, CSV), cada línea. Un YAML que no se compone o un JSON que
    no se lee, por líneas y con una entrada de sección `FICHERO_NO_VERIFICABLE` (AC174). `barrido` es su texto sin los
    campos estructurales, quitados por su posición (`_estructurales`): el de las demás entradas, su texto. La posición
    es «fichero:sección[n]», «fichero:sección», «fichero:[n]», «fichero:clave n[i]», «fichero:línea n», «fichero:
    documento n…» o «fichero», con las secciones de vocabulario cerrado (`SECCIONES`; otra, «sección n»: AC135): nunca
    un valor."""
    base = Path(repo) / 'pipeline/data'
    out = []
    for path in _ficheros_publicos(repo):
        rel = path.relative_to(base)
        if 'audit' in rel.parts or path.suffix.lower() == '.pdf':
            continue
        datos = path.read_bytes()
        try:
            texto = datos.decode('utf-8')
        except UnicodeDecodeError:
            texto = datos.decode('latin-1')
        if path.suffix.lower() in ('.yaml', '.yml'):
            out += _entradas_yaml(path, rel, texto)
        elif path.suffix.lower() == '.json':
            out += _entradas_json(rel, texto)
        else:
            out += [(f'{rel}:línea {n + 1}', linea, str(rel), None, None, linea, None)
                    for n, linea in enumerate(_lineas_de(texto)) if linea.strip()]
    return out


# AC199: la fila «rectificado» de la ficha de un nodo cuya identificación con un tercero se retira (la fórmula cerrada
# de R-2026-10-08-0009): la identidad anterior del nodo no puede seguir publicada en ningún sitio.
_IDENTIDAD_RETIRADA = re.compile(r'\((?P<eid>[A-Z][A-Za-z]*:[a-z0-9]+(?:-[a-z0-9]+)*)\): ficha corregida: '
                                 r'identificación con un tercero retirada; la fuente la daba solo como posible')
IDENTIDAD_RETIRADA = 'identidad retirada todavía publicada'
# Mientras el export lleva el nodo del id anterior, lo que el export dice de él espera a la migración del Path A.
IDENTIDAD_PENDIENTE = 'identidad retirada: pendiente del Path A (migración + make centralidad)'
IDENTIDAD_SIN_CENSO = 'identidad retirada sin su unidad de identidad en el censo'
# Un fichero de datos que no se decodifica (JSON o YAML mal formado, un binario que no se lee) no se da por limpio: se
# barre literal y se avisa (AC214).
IDENTIDAD_NO_DECODIFICABLE = 'identidad retirada: fichero no decodificable, solo se barrió literal'
# Los ficheros versionados en que se busca: los de datos y los de la API, de texto estructurado o Markdown.
_DIRS_IDENTIDAD = ('pipeline/data', 'api/app/data')
_EXTENSIONES_IDENTIDAD = frozenset({'.yaml', '.yml', '.json', '.md'})


def _cadenas(valor):
    """Las cadenas de un valor leído (JSON o export): cada cadena, clave y elemento de lista, a cualquier profundidad
    (un objeto JSON leído por pares, una lista de pares, también)."""
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, dict):
        for clave, hijo in valor.items():
            yield from _cadenas(clave)
            yield from _cadenas(hijo)
    elif isinstance(valor, (list, tuple)):
        for hijo in valor:
            yield from _cadenas(hijo)


def _valores_del_fichero(sufijo, texto):
    """([cadena], decodificado): los valores de un fichero de datos tal como los lee su cargador, con sus escapes
    resueltos (`Jos\\u00e9` es `José`; spec 071 AC214): cada cadena, clave y elemento de un JSON (todos los pares de un
    objeto, también con una clave repetida) y los escalares de todos los documentos de un YAML (`_decodificados`: sus
    escapes, un `!!binary`, un entero con guiones bajos). Un Markdown no tiene más valores que su texto. Sin decodificar
    (el fichero no se lee así, o un binario o un escalar no se construye), `decodificado` es False."""
    if sufijo == '.json':
        try:
            return list(_cadenas(json.loads(texto, object_pairs_hook=list))), True
        except (ValueError, RecursionError):
            return [], False
    if sufijo in ('.yaml', '.yml'):
        fallos = []
        try:
            valores = [x for doc in yaml.compose_all(texto, Loader=yaml.SafeLoader) if doc is not None
                       for x in _decodificados(doc, fallos=fallos)]
        except (yaml.YAMLError, RecursionError):
            return [], False
        return valores, not fallos
    return [], True


def identidades_retiradas(rows, conn, repo, snapshot=None):
    """([(etiqueta, motivo)], [(etiqueta, aviso)]): de cada fila «rectificado» cuyo `dato` es la fórmula cerrada de una
    identificación retirada (`_IDENTIDAD_RETIRADA`), la identidad ANTERIOR del nodo, la de su unidad de identidad del
    censo (`conn`: su entity_id y sus nombres de dos palabras o más; nunca lo que dice la fila pública), no puede
    aparecer en ningún fichero versionado de datos de `repo` (YAML, JSON o Markdown de `pipeline/data` y
    `api/app/data`: un problema por fichero, con la primera línea) ni en un nodo o una arista del export `snapshot`
    (problema); en un fragmento del documento o en la cita de una arista, el texto de la fuente, es un aviso (spec 071
    AC199). Mientras el export lleve el nodo del id anterior (la migración del Path A pendiente), todo lo que el export
    dice de él es un aviso, `IDENTIDAD_PENDIENTE` (enmienda 24 bis); los ficheros versionados, siempre un problema. Sin
    su unidad de identidad en el censo, la fila no se puede comprobar: problema. AC214: además del texto literal de cada
    fichero, sus valores decodificados (`_valores_del_fichero`) y los valores del export a cualquier profundidad
    (`_cadenas`): un JSON con `ensure_ascii` o un YAML con escapes no esquiva el nombre. Un fichero que no se
    decodifica, solo literal y con un aviso."""
    problemas, avisos, retiradas = [], [], []
    for n, row in enumerate(rows, 1):
        if not (isinstance(row, dict) and row.get('decision') == 'rectificado'
                and _IDENTIDAD_RETIRADA.fullmatch(str(row.get('dato') or ''))):
            continue
        rid, unidad = _etiqueta(row, n), row.get('unidad')
        fila = conn.execute('SELECT kind, value FROM units WHERE unit_id=?', (unidad,)).fetchone() \
            if isinstance(unidad, str) and unidad else None
        identidad = json.loads(fila[1]).get('identity') if fila is not None and fila[0] == 'identity' else None
        viejo = identidad.get('entity_id') if isinstance(identidad, dict) else None
        if not isinstance(viejo, str) or not viejo:
            problemas.append((rid, IDENTIDAD_SIN_CENSO))
            continue
        tipo = viejo.split(':', 1)[0]
        nombres = {_llano(x) for x in (identidad.get('name'), *(identidad.get('aliases') or ())) if _es_nombre(x, tipo)}
        retiradas.append((rid, viejo, nombres))
    if not retiradas:
        return problemas, avisos

    def nombra(texto, viejo, nombres):
        llano = _llano(texto)
        return _nombra(viejo, texto) or any(_palabra(n, llano) for n in nombres)
    for path, rel in _ficheros_de_datos(repo):
        texto = path.read_bytes().decode('utf-8', errors='replace')
        valores, decodificado = _valores_del_fichero(path.suffix.lower(), texto)
        for rid, viejo, nombres in retiradas:
            if nombra(texto, viejo, nombres):
                linea = next((i for i, linea in enumerate(_lineas_de(texto), 1) if nombra(linea, viejo, nombres)),
                             None)
                problemas.append((rid, f'{IDENTIDAD_RETIRADA} ({rel}' + (f':línea {linea})' if linea else ')')))
            elif any(nombra(valor, viejo, nombres) for valor in valores):
                problemas.append((rid, f'{IDENTIDAD_RETIRADA} ({rel})'))
            if not decodificado:
                avisos.append((rid, f'{IDENTIDAD_NO_DECODIFICABLE} ({rel})'))
    if snapshot is None:
        return problemas, avisos
    nodos = list(_lineas(Path(snapshot) / 'nodes.jsonl'))
    pendientes = {(n.get('props') or {}).get('entity_id') for _, n in nodos} & {viejo for _, viejo, _ in retiradas}

    def apunta(rid, viejo, problema, posicion):
        if viejo in pendientes:
            avisos.append((rid, f'{IDENTIDAD_PENDIENTE} ({posicion})'))
        else:
            (problemas if problema else avisos).append((rid, f'{IDENTIDAD_RETIRADA} ({posicion})'))
    for n, nodo in nodos:
        props = nodo.get('props') or {}
        texto = '\n'.join(_cadenas(props))
        for rid, viejo, nombres in retiradas:
            if nombra(texto, viejo, nombres):
                apunta(rid, viejo, not _fragmento(nodo.get('labels')), f'export:nodo[{n}]')
    for n, arista in _lineas(Path(snapshot) / 'relationships.jsonl'):
        props = arista.get('props') or {}
        # AC221: el mapa entero, claves incluidas; de `source_quote` se separa el valor, no su clave.
        suyo = '\n'.join(cadena for k, v in props.items() for cadena in (*_cadenas(k), *(
            () if k == 'source_quote' else _cadenas(v))))
        for rid, viejo, nombres in retiradas:
            if nombra(suyo, viejo, nombres):
                apunta(rid, viejo, True, f'export:arista[{n}]')
            elif nombra(str(props.get('source_quote') or ''), viejo, nombres):
                apunta(rid, viejo, False, f'export:arista[{n}]')
    return problemas, avisos


def _ficheros_de_datos(repo):
    """[(ruta, posición)]: los ficheros versionados (o sin ignorar) de datos de `repo` en que se busca una identidad
    retirada (`_DIRS_IDENTIDAD`, con `_EXTENSIONES_IDENTIDAD`), sin la auditoría privada."""
    try:
        salida = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                                 '--', *_DIRS_IDENTIDAD], capture_output=True, check=True).stdout
        rutas = {Path(repo) / os.fsdecode(f) for f in salida.split(b'\0') if f}
    except (OSError, subprocess.CalledProcessError):
        rutas = {p for d in _DIRS_IDENTIDAD for p in (Path(repo) / d).rglob('*')}
    return [(p, p.relative_to(repo).as_posix()) for p in sorted(rutas)
            if p.is_file() and p.suffix.lower() in _EXTENSIONES_IDENTIDAD and 'audit' not in p.relative_to(repo).parts]


def _ficheros_publicos(repo):
    """Los ficheros de `pipeline/data` que publica `repo`: los que git versiona o no ignora (un checkout tiene cachés
    ignoradas, que no se publican), o todos si `repo` no es un checkout de git (un `git archive`)."""
    base = Path(repo) / 'pipeline/data'
    try:
        salida = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                                 '--', 'pipeline/data'], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return sorted(p for p in base.rglob('*') if p.is_file())
    return sorted({p for p in (Path(repo) / os.fsdecode(f) for f in salida.split(b'\0') if f) if p.is_file()})


# Los saltos de línea que reconoce PyYAML (`Scanner.scan_line_break`, y los que cuenta `Reader.forward`): CRLF es uno
# solo, y CR, LF, NEL, LS y PS. Los demás que parte `str.splitlines` (VT, FF, FS, GS, RS) no son imprimibles para PyYAML
# y no entran en un YAML que se compone. Una sola noción de línea en todo el módulo (AC232): la de las marcas de PyYAML.
_SALTO_YAML = re.compile(r'\r\n|[\r\n\x85\u2028\u2029]')
_SALTOS = '\r\n\x85\u2028\u2029'


def _lineas_de(texto, conservar=False):
    """Las líneas de `texto` como las numera PyYAML (`_SALTO_YAML`), con su salto si `conservar`: lo que
    `str.splitlines` daría para un texto que PyYAML lee, y no partido por VT, FF, FS, GS ni RS, que ahí son parte de la
    línea."""
    out, inicio = [], 0
    for m in _SALTO_YAML.finditer(texto):
        out.append(texto[inicio:m.end() if conservar else m.start()])
        inicio = m.end()
    return out + [texto[inicio:]] if inicio < len(texto) else out


class _Posiciones:
    """Dónde empieza y dónde acaba, en líneas, cada construcción de un YAML (spec 071 AC237), según los indicadores de
    cada una y no las marcas de los nodos de PyYAML: un nodo empieza en su primer valor y no en su `-`, su `?` o su
    clave, acaba en el primer token que le sigue (que puede ser la clave de la sección vecina), y el nodo de un ancla es
    el mismo para ella y para cada alias que la usa, con las marcas del ancla.

    Una aparición es (id del padre, posición en él, si es una clave), o ('raiz', n.º de documento): cada una con la
    línea de su indicador (`ini`), el nodo en que acabó de componerse (`nodo`) y su sitio entre sus hermanos (`hijos`,
    `orden`). Su rango va de su línea a la del indicador del siguiente hermano (`fin`); el de la última aparición, al
    fin de lo que contiene a su padre, hasta el cierre del documento (`cierres`, que pone quien conoce sus `---`). El
    rango lógico de un ancla va de la línea de su `&` (`ancla`) al fin de la aparición en que se define (`definicion`);
    una aparición que es un alias (`alias`) suma el de su ancla y el de los alias que contiene (`expansion`)."""

    def __init__(self):
        self.ini, self.nodo, self.orden, self.definicion, self.ancla = {}, {}, {}, {}, {}
        self.hijos, self.alias, self.raices, self.cierres = {}, set(), [], []

    def fin(self, aparicion):
        """La línea del indicador que sigue a `aparicion`: el de su hermano siguiente, o el que sigue a su padre."""
        while aparicion[0] != 'raiz':
            hermanos = self.hijos[aparicion[0]]
            if self.orden[aparicion] + 1 < len(hermanos):
                return self.ini[hermanos[self.orden[aparicion] + 1]]
            aparicion = self.definicion[aparicion[0]]
        return self.cierres[aparicion[1]]

    def rango(self, aparicion, desde=None):
        """(primera, última + 1) de `aparicion`, con al menos su línea; `desde` cambia la primera."""
        ini = self.ini[aparicion] if desde is None else desde
        return ini, max(self.fin(aparicion), ini + 1)

    def expansion(self, aparicion):
        """[(primera, última + 1)]: el rango lógico de la ancla de cada alias en `aparicion` o dentro de ella, y de los
        que haya dentro de esas anclas (AC237: alias anidados, claves de fusión, listas de flujo)."""
        out, vistos, pendientes = [], set(), [aparicion]
        while pendientes:
            actual = pendientes.pop()
            nodo = self.nodo[actual]
            if actual in self.alias:
                out.append(self.rango(self.definicion[id(nodo)], self.ancla[id(nodo)]))
            if id(nodo) not in vistos:
                vistos.add(id(nodo))
                pendientes.extend(self.hijos.get(id(nodo), ()))
        return out


class _CargadorConTokens(yaml.SafeLoader):
    """El cargador seguro de PyYAML, con los tokens que consume guardados (`consumidos`): el escaneo que compone los
    nodos da también los tramos de sus valores (`_tokens_de_texto`, AC227), sin escanear el fichero dos veces. Y dónde
    empieza cada construcción (`posiciones`, AC237): la línea del último indicador consumido cuando el analizador pide
    su nodo (el `-` de un elemento de una secuencia de bloque, el `?` o la clave de un par; el primer token del nodo en
    los demás casos), que no es la del nodo ni la de sus marcas."""

    def __init__(self, stream):
        super().__init__(stream)
        self.consumidos = []
        self.posiciones = _Posiciones()
        self.guion = None
        self.llave = None

    def get_token(self):
        token = super().get_token()
        self.consumidos.append(token)
        if isinstance(token, yaml.BlockEntryToken):
            self.guion = token.start_mark.line
        elif isinstance(token, yaml.KeyToken):
            self.llave = token.start_mark.line
        return token

    def compose_node(self, parent, index):
        # El indicador de este nodo es el último que se consumió: el analizador lo consume antes de pedir el nodo, y el
        # del siguiente, después de que el nodo (con todo lo que contiene) esté compuesto.
        pos, evento = self.posiciones, self.peek_event()
        if parent is None:
            aparicion = ('raiz', len(pos.raices))
            pos.raices.append(aparicion)
        else:
            aparicion = (id(parent), len(parent.value), index is None)
            pos.orden[aparicion] = len(pos.hijos.setdefault(id(parent), []))
            pos.hijos[id(parent)].append(aparicion)
        if isinstance(parent, yaml.SequenceNode) and not parent.flow_style:
            linea = self.guion
        elif isinstance(parent, yaml.MappingNode) and index is None and self.llave is not None:
            linea = self.llave
        else:
            linea = evento.start_mark.line
        self.llave = None
        nodo = super().compose_node(parent, index)
        pos.ini[aparicion], pos.nodo[aparicion] = linea, nodo
        if isinstance(evento, yaml.AliasEvent):
            pos.alias.add(aparicion)
        else:
            pos.definicion[id(nodo)] = aparicion
            if evento.anchor is not None:
                pos.ancla[id(nodo)] = evento.start_mark.line
        return nodo


def _componer(texto):
    """([nodo], [token], _Posiciones): los nodos de todos los documentos de un YAML (como `yaml.compose_all`), los
    tokens que se leyeron para componerlos y dónde empieza cada construcción (`_CargadorConTokens`). Falla con
    `yaml.YAMLError` si no se compone."""
    cargador = _CargadorConTokens(texto)
    try:
        documentos = []
        while cargador.check_node():
            documentos.append(cargador.get_node())
        return documentos, cargador.consumidos, cargador.posiciones
    finally:
        cargador.dispose()


def _inicios_de_documento(tokens, n_lineas, n_documentos):
    """[primera línea] de cada documento tras el primero (AC235): la de sus directivas, si las tiene, y si no la de su
    `---`; la última línea del anterior es la que le precede. Si no salen tantas como documentos hay, ninguno cierra
    antes del final del fichero (los rangos se solapan, no se pierden)."""
    inicios, directiva, visto = [], None, False
    for token in tokens:
        if isinstance(token, yaml.DirectiveToken):
            directiva = token.start_mark.line if directiva is None else directiva
        elif isinstance(token, yaml.DocumentStartToken):
            if visto:
                inicios.append(token.start_mark.line if directiva is None else directiva)
            visto, directiva = True, None
        elif not isinstance(token, (yaml.StreamStartToken, yaml.StreamEndToken, yaml.DocumentEndToken)):
            visto = True
    return inicios if len(inicios) == n_documentos - 1 else [n_lineas] * (n_documentos - 1)


def _apertura_del_primer_documento(tokens):
    """La línea de las directivas o del `---` que abre el primer documento (AC239), o None si lo abre su contenido: la
    de los demás es `_inicios_de_documento`. Lo que hay en esa línea, y entre ella y su primera entrada, es la apertura
    de su raíz."""
    directiva = None
    for token in tokens:
        if isinstance(token, yaml.DirectiveToken):
            directiva = token.start_mark.line if directiva is None else directiva
        elif isinstance(token, yaml.DocumentStartToken):
            return token.start_mark.line if directiva is None else directiva
        elif not isinstance(token, yaml.StreamStartToken):
            return None
    return None


def _tokens_de_texto(texto, tokens=None):
    """([(inicio, fin)], [(línea, marca)]): lo que el escáner de PyYAML ve en un YAML (spec 071 AC227), de los `tokens`
    ya leídos (`_componer`) o, sin ellos, escaneando `texto`. Los cuerpos: el tramo de cada escalar que es su valor, sin
    lo que PyYAML cuenta dentro del TRAMO del nodo y no es valor: el ancla y la etiqueta que lo preceden y los
    comentarios que haya entre ellas y el valor; y, en un escalar de bloque, su cabecera (el indicador `|` o `>`, el de
    sangría y conservación y el comentario de cola), que es sintaxis y texto nuestro: el cuerpo empieza en la línea
    siguiente (AC232: tras el primer salto que admite PyYAML, `_SALTO_YAML`). Las marcas: el ancla, el alias, la
    etiqueta y la directiva (AC242: su nombre y sus parámetros, también el identificador y el prefijo de `%TAG`) tal
    como se escriben, con su línea. Si no se lee, nada (sin cuerpos, todo `#` es un comentario: fallo
    cerrado)."""
    cuerpos, marcas = [], []
    try:
        for token in yaml.scan(texto, Loader=yaml.SafeLoader) if tokens is None else tokens:
            inicio, fin = token.start_mark.index, token.end_mark.index
            if isinstance(token, yaml.ScalarToken):
                if token.style in ('|', '>'):
                    salto = _SALTO_YAML.search(texto, inicio, fin)
                    inicio = salto.end() if salto else fin
                cuerpos.append((inicio, fin))
            elif isinstance(token, (yaml.AnchorToken, yaml.AliasToken, yaml.TagToken, yaml.DirectiveToken)):
                marcas.append((token.start_mark.line, texto[inicio:fin]))
    except yaml.YAMLError:
        return [], []
    return cuerpos, marcas


def _cuerpo(nodo, cuerpos):
    """(inicio, fin): el valor del escalar `nodo` dentro de su tramo (`cuerpos`, de `_tokens_de_texto`): sin su ancla,
    su etiqueta, sus comentarios ni la cabecera de un escalar de bloque (AC227). Vacío, en su fin, si no lo tiene (un
    valor nulo sin escribir)."""
    i = bisect.bisect_left(cuerpos, (nodo.start_mark.index,))
    if i < len(cuerpos) and cuerpos[i][1] <= nodo.end_mark.index:
        return cuerpos[i]
    return nodo.end_mark.index, nodo.end_mark.index


def _comentarios(texto, cuerpos):
    """{línea: comentario}: los comentarios de un YAML, de línea entera o de cola: un `#` fuera del valor de todo
    escalar (`cuerpos`, de `_tokens_de_texto`; spec 071 AC144 y AC227: también el de la cabecera de un escalar de bloque
    o el que sigue a un ancla o a una etiqueta, que PyYAML cuenta dentro del tramo del nodo), al principio de la línea
    o tras un blanco. Las líneas se numeran como las cuenta PyYAML (también el salto `\\x85` y los separadores de
    línea y de párrafo de Unicode), que son las de sus marcas: un comentario no cambia de elemento por ello."""
    out, inicio, comienzos = {}, 0, [a for a, _ in cuerpos]

    def en_un_valor(posicion):
        k = bisect.bisect_right(comienzos, posicion) - 1
        return k >= 0 and posicion < cuerpos[k][1]
    for n, linea in enumerate(_lineas_de(texto, conservar=True)):
        for i, c in enumerate(linea):
            if c == '#' and (i == 0 or linea[i - 1] in ' \t') and not en_un_valor(inicio + i):
                out[n] = linea[i:].rstrip(_SALTOS)
                break
        inicio += len(linea)
    return out


def _estructurales(rel, seccion, item, valor, texto, cuerpos):
    """(tramos, nodos, motivo): lo de un elemento YAML que no dice nada del hecho y no se barre (spec 071 AC144 y
    AC163), por su posición: el valor de la `unidad` de una fila del registro; y los de la `unidad` y las
    `declaraciones` de una regla opaca, y la etiqueta de su propia clave dentro de su motivo si este es su fórmula
    cerrada. `tramos`, en el texto literal; `nodos`, los de esos valores (por identidad); `motivo`, el nodo del motivo y
    el tramo de la etiqueta en su valor leído. Cualquier otra aparición de la unidad, de un prefijo suyo o de un id
    oculto, comentarios incluidos, se barre."""
    if not isinstance(item, yaml.MappingNode) or not isinstance(valor, dict):
        return [], set(), None
    raiz = Path(rel).parts[0]
    opaca = raiz == 'relations' and seccion == 'deny' and es_opaca(valor)
    if not (opaca or (raiz == 'rectificaciones' and seccion == 'rectificaciones')):
        return [], set(), None
    campos = {k.value: v for k, v in item.value if isinstance(k, yaml.ScalarNode) and isinstance(v, yaml.ScalarNode)}
    suyos = [campos[k] for k in (('unidad', 'declaraciones') if opaca else ('unidad',)) if k in campos]
    tramos, motivo = [_cuerpo(n, cuerpos) for n in suyos], None
    nodo = campos.get('reason')
    if opaca and nodo is not None and motivo_opaco_valido(valor):
        # La fórmula cerrada lleva una sola etiqueta (`_MOTIVO_OPACA`): la primera es la suya.
        etiqueta = f'[{valor["unidad"][:8]}]'
        i = texto.find(etiqueta, *_cuerpo(nodo, cuerpos))
        if i >= 0:
            tramos.append((i, i + len(etiqueta)))
        j = nodo.value.find(etiqueta)
        if j >= 0:
            motivo = (nodo, (j, j + len(etiqueta)))
    return tramos, {id(n) for n in suyos}, motivo


# Lo que un `!!binary` puede llevar dentro y seguir siendo verificable (spec 071 AC188): más, el fichero no se da por
# limpio. Sus BOM, del más largo al más corto (el de UTF-32 LE empieza por el de UTF-16 LE).
BINARIO_MAX = 1 << 20
_BOMS = ((codecs.BOM_UTF32_LE, 'utf-32'), (codecs.BOM_UTF32_BE, 'utf-32'), (codecs.BOM_UTF8, 'utf-8-sig'),
         (codecs.BOM_UTF16_LE, 'utf-16'), (codecs.BOM_UTF16_BE, 'utf-16'))
# Las etiquetas de escalar cuyo valor construido se escribe de otra manera que su texto (spec 071 AC189): un entero con
# `_`, en hexadecimal o en octal, un real, un booleano; con los constructores seguros del cargador.
_CONSTRUCTOR = yaml.SafeLoader('')
_CONSTRUIDOS = {'tag:yaml.org,2002:int': yaml.SafeLoader.construct_yaml_int,
                'tag:yaml.org,2002:float': yaml.SafeLoader.construct_yaml_float,
                'tag:yaml.org,2002:bool': yaml.SafeLoader.construct_yaml_bool}


def _texto_binario(valor):
    """El texto de un `!!binary` (su base64, `valor`) como lo decodifica su BOM (UTF-8, UTF-16 o UTF-32, en cualquier
    orden de bytes) o, sin él, como UTF-8 estricto; None si no es base64, pasa de BINARIO_MAX o no se decodifica sin
    pérdidas (spec 071 AC173 y AC188): un texto decodificado con pérdidas puede esconder lo que se busca. Sin BOM, un
    texto UTF-16 o UTF-32 de solo ASCII es UTF-8 válido con NUL intercalados: va también sin ellos."""
    if len(valor) > 2 * BINARIO_MAX:
        return None
    try:
        datos = base64.decodebytes(valor.encode('ascii'))
    except (ValueError, UnicodeEncodeError):
        return None
    if len(datos) > BINARIO_MAX:
        return None
    codificacion = next((nombre for bom, nombre in _BOMS if datos.startswith(bom)), 'utf-8')
    try:
        texto = datos.decode(codificacion)
    except UnicodeDecodeError:
        return None
    return f'{texto}\n{texto.replace(chr(0), "")}' if '\x00' in texto else texto


def _decodificados(nodo, fuera=frozenset(), motivo=None, fallos=None):
    """[texto]: las claves y los escalares del nodo YAML `nodo` tal como los lee el cargador, con sus escapes resueltos
    (spec 071 AC163: `"\\u0061bcdef01"` es `abcdef01`) y, además de su texto, el contenido de un `!!binary` (AC173 y
    AC188: `_texto_binario`) y el valor construido de un entero, un real o un booleano (AC189: `12_345_678`,
    `0xbc614e`),
    sin los nodos estructurales `fuera` y, en el motivo de una opaca (`motivo`, de `_estructurales`), sin su etiqueta.
    Un nodo al que vuelve un alias, una vez. Un binario o un escalar que no se puede leer así se apunta en `fallos`."""
    out, vistos = [], set()

    def recorrer(n):
        if id(n) in vistos or id(n) in fuera:
            return
        vistos.add(id(n))
        if isinstance(n, yaml.ScalarNode):
            a, b = motivo[1] if motivo is not None and n is motivo[0] else (0, 0)
            out.append(n.value[:a] + ' ' * (b - a) + n.value[b:])
            if n.tag == 'tag:yaml.org,2002:binary':
                texto = _texto_binario(n.value)
                if texto is None:
                    if fallos is not None:
                        fallos.append(n.start_mark.line)
                else:
                    out.append(texto)
            elif n.tag in _CONSTRUIDOS:
                try:
                    out.append(str(_CONSTRUIDOS[n.tag](_CONSTRUCTOR, n)))
                except Exception:  # noqa: BLE001 (lo que el cargador no construye no se da por leído)
                    if fallos is not None:
                        fallos.append(n.start_mark.line)
        elif isinstance(n, yaml.SequenceNode):
            for hijo in n.value:
                recorrer(hijo)
        elif isinstance(n, yaml.MappingNode):
            for clave, valor in n.value:
                recorrer(clave)
                recorrer(valor)

    recorrer(nodo)
    return out


def _unir(rangos):
    """[(primera, última + 1)]: los rangos de líneas `rangos` en orden y sin solaparse (una línea se barre una vez)."""
    out = []
    for a, b in sorted(rangos):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _entradas_yaml(path, rel, texto):
    """Las entradas de un YAML público (`entradas_publicas`). Cada una barre su texto literal (sus líneas, con los
    comentarios que lleven: AC235 y AC237, de la línea de su indicador, el `-` de un elemento, el `?` o la clave de
    una sección, a la anterior al de la siguiente, o al cierre de su sección, de su documento o del fichero, con los
    de cabecera y los de cola; y lo que expande cada alias que lleve: el rango lógico completo de su ancla, de su `&`
    al indicador siguiente, y el de los alias que esta contiene, `_Posiciones`) y, aparte, sus claves y escalares
    leídos (AC163), con las claves de la sección en que está (AC162: un mapa cuyas claves son ids no separa la clave
    de lo que dice de ella): de una sección de lista, cada elemento con la clave de la sección; de una sección que no
    es una lista (también una lista vacía), la clave con todo su valor. Todos los documentos del fichero, cada uno con
    sus secciones y sus valores construidos aparte (los que siguen al primero, con «documento n» en la posición;
    AC185), y una raíz escalar, una entrada con su valor leído (AC174). Lo que no está en ninguna (cabeceras, la línea
    de la clave de una sección de lista, con su clave leída) va por líneas, y cada comentario de una entrada, aparte
    (AC144). Un YAML que no se compone o no se construye, o con un binario o un escalar que no se lee (AC188 y AC189),
    no se da por limpio (`FICHERO_NO_VERIFICABLE`, AC174 y AC185)."""
    lineas, cubiertas = _lineas_de(texto), set()
    try:
        documentos, tokens, posiciones = _componer(texto)
    except yaml.YAMLError:
        documentos, tokens, posiciones = None, [], None
    # AC185: los valores de cada documento, construidos aparte, para la estructura (los nombres que resuelven sus
    # cargadores) y las exclusiones por posición; uno que no se construye (una etiqueta desconocida, un binario que no
    # es base64, una clave repetida) no se da por limpio.
    no_verificable, fallos = documentos is None, []
    construidos = [None] * len(documentos or ())
    if documentos is not None:
        try:
            construidos = cargar_documentos(texto, path.name)
        except YamlInvalido:
            no_verificable = True
    # AC227: el valor de cada escalar, sin lo que PyYAML cuenta en el tramo de su nodo y no es valor.
    cuerpos, marcas = _tokens_de_texto(texto, tokens) if documentos is not None else ([], [])
    blanco, blanco_fuentes, elementos, claves_sueltas = list(texto), list(texto), [], {}
    inicios = _inicios_de_documento(tokens, len(lineas), len(documentos or ()))
    if posiciones is not None:
        posiciones.cierres = [inicios[d] if d < len(inicios) else len(lineas) for d in range(len(documentos))]
    aperturas = [_apertura_del_primer_documento(tokens), *inicios]

    def cubrir(rangos):
        cubiertas.update(n for a, b in rangos for n in range(a, b))
    for d, (nodo, leido_doc) in enumerate(zip(documentos or (), construidos), 1):
        prefijo = f'{rel}:' if d == 1 else f'{rel}:documento {d}:'
        # AC235: de la primera línea del documento (su `---`) a la anterior a la del siguiente, o al final del fichero.
        abre, cierra = (0 if d == 1 else inicios[d - 2]), posiciones.cierres[d - 1]
        raiz = posiciones.raices[d - 1]
        if isinstance(nodo, yaml.ScalarNode):
            if nodo.tag == 'tag:yaml.org,2002:null' and not nodo.value:
                continue  # un documento vacío
            # AC239: nunca rebasa el cierre exclusivo del documento (el fin de un bloque está en el primer token que le
            # sigue, que puede ser el `---` del siguiente).
            ini = min(abre, nodo.start_mark.line)
            fin = max(cierra, ini + 1)
            cubiertas.update(range(ini, fin))
            leidos_valor = _decodificados(nodo, fallos=fallos)
            elementos.append((f'{rel}:documento {d}', [(ini, fin)], '', leido_doc, [], leidos_valor, leidos_valor,
                              None))
            continue
        if isinstance(nodo, (yaml.MappingNode, yaml.SequenceNode)) and not nodo.value:
            # AC241: una raíz vacía (`[]`, `{}`, con ancla, etiqueta, directivas o `---`) es una sola entrada, con su
            # apertura y su cierre, del principio del documento a su cierre. Lo cubierto sale de las entradas generadas.
            rangos = [(min(abre, posiciones.ini[raiz]), max(cierra, posiciones.ini[raiz] + 1))]
            cubrir(rangos)
            leidos_valor = _decodificados(nodo, fallos=fallos)
            elementos.append((f'{rel}:documento {d}', rangos, '', leido_doc, [], leidos_valor, leidos_valor, None))
            continue
        # AC237: cada sección es un par (su clave y su valor) del mapa raíz, o la propia secuencia raíz; el rango de
        # cada elemento y de cada sección va de la línea de su indicador a la del siguiente, y lo que expande un alias
        # (el rango lógico completo de su ancla y el de los alias que esta contiene) se suma al de quien lo usa.
        if isinstance(nodo, yaml.MappingNode):
            secciones = [(c, v, (id(nodo), i, True), (id(nodo), i, False)) for i, (c, v) in enumerate(nodo.value)]
        else:
            secciones = [(None, nodo, None, raiz)] if isinstance(nodo, yaml.SequenceNode) else []
        for orden, (clave, valor, de_clave, de_valor) in enumerate(secciones, 1):
            nombre = clave.value if isinstance(clave, yaml.ScalarNode) else ''
            etiqueta = _seccion_nombrada(nombre, orden) if clave is not None else ''
            ancestros = _decodificados(clave, fallos=fallos) if clave is not None else []
            leidos = leido_doc.get(nombre) if isinstance(leido_doc, dict) else leido_doc if clave is None else None
            clave_en = posiciones.ini[de_clave if clave is not None else de_valor]
            cierre = posiciones.fin(de_valor)
            suma = posiciones.expansion(de_valor)
            # AC239: lo que lleva cada entrada de la sección además de lo suyo: lo que expande el alias de su clave (la
            # clave la heredan todas) y, la primera de la raíz, su apertura (el `{`, el `[`, el ancla o la etiqueta, con
            # su comentario, que preceden a su primera clave o elemento).
            suma_clave = posiciones.expansion(de_clave) if clave is not None else []
            apertura = []
            if orden == 1:
                primera = posiciones.ini[(id(valor), 0, False)] if clave is None and valor.value else clave_en
                desde = min(posiciones.ini[raiz], aperturas[d - 1] if aperturas[d - 1] is not None else cierra)
                apertura = [(desde, primera)] if desde < primera else []
            if isinstance(valor, yaml.SequenceNode) and valor.value:
                leidos = leidos if isinstance(leidos, list) else []
                if clave is not None:
                    claves_sueltas[clave_en] = ancestros
                # Una lista de flujo o un alias de una lista es una sola construcción, sin guiones que separen a sus
                # elementos: todos llevan lo mismo (de la clave al cierre de la sección, con los comentarios de entre
                # sus elementos y de cola, y lo que expande cualquier alias de dentro).
                una_sola = valor.flow_style or de_valor in posiciones.alias
                if una_sola:
                    comun = [(clave_en, max(cierre, clave_en + 1)), *suma, *suma_clave, *apertura]
                    desde = clave_en + 1 if de_valor in posiciones.alias else posiciones.ini[(id(valor), 0, False)]
                    cubiertas.update(range(desde, comun[0][1]))
                    cubrir([*suma, *suma_clave, *apertura])
                for i, item in enumerate(valor.value):
                    de_item = (id(valor), i, False)
                    if una_sola:
                        rangos = comun
                    else:
                        # El elemento va de su guion al del siguiente (el último, al cierre de su sección); el primero,
                        # con la clave de la sección y lo que hay entre ella y su guion.
                        ini = posiciones.ini[de_item]
                        rangos = [posiciones.rango(de_item), *posiciones.expansion(de_item), *suma_clave]
                        cubiertas.update(range(*rangos[0]))
                        cubrir(rangos[1:])
                        if i == 0:
                            rangos.extend(apertura)
                            cubrir(apertura)
                        if i == 0 and clave is not None and clave_en < ini:
                            rangos.append((clave_en, ini))
                            cubiertas.update(range(clave_en + 1, ini))
                    leido = leidos[i] if i < len(leidos) else None
                    tramos, fuera, motivo = _estructurales(rel, nombre, item, leido, texto, cuerpos)
                    for a, b in tramos:
                        _en_blanco(blanco, a, b)
                    # AC215: lo que el elemento dice por su cuenta, sin los valores de sus campos de fuente, por su
                    # posición (en el texto literal y entre los escalares leídos).
                    fuentes = _nodos_de_fuente(item)
                    for nodo_fuente in fuentes:
                        _en_blanco(blanco_fuentes, *_cuerpo(nodo_fuente, cuerpos))
                    elementos.append((f'{prefijo}{etiqueta}[{i}]', _unir(rangos), etiqueta, leido, ancestros,
                                      _decodificados(item, fallos=fallos),
                                      _decodificados(item, fuera, motivo, fallos=fallos),
                                      _decodificados(item, {id(n) for n in fuentes})))
            elif clave is not None:
                # De la línea de su clave al cierre de la sección: un mapa, un escalar, un alias, una lista de flujo o
                # de bloque vacíos (AC237) generan su entrada con todos sus comentarios.
                rangos = [(clave_en, max(cierre, clave_en + 1)), *suma, *suma_clave, *apertura]
                cubrir(rangos)
                leidos_valor = _decodificados(valor, fallos=fallos)
                fuentes = _nodos_de_fuente(valor)
                for nodo_fuente in fuentes:
                    _en_blanco(blanco_fuentes, *_cuerpo(nodo_fuente, cuerpos))
                # Una lista vacía no tiene elementos que validar como filas de su sección: su entrada va sin ella.
                vacia = isinstance(valor, yaml.SequenceNode)
                elementos.append((f'{prefijo}{etiqueta}', _unir(rangos), '' if vacia else etiqueta, leidos, ancestros,
                                  leidos_valor, leidos_valor, _decodificados(valor, {id(n) for n in fuentes})))
    barridas, sin_fuentes = _lineas_de(''.join(blanco)), _lineas_de(''.join(blanco_fuentes))
    comentarios = _comentarios(texto, cuerpos)
    # AC227: lo del tramo literal que no es sintaxis ni valor leído, por línea: los comentarios (también los de cabecera
    # de un escalar de bloque o tras un ancla o una etiqueta) y el nombre de un ancla, de un alias o de una etiqueta.
    anotaciones = defaultdict(list)
    for n, marca in marcas:
        anotaciones[n].append(marca)
    for n, comentario in comentarios.items():
        anotaciones[n].append(comentario)
    def de(textos, rangos):
        return [texto for a, b in rangos for texto in textos[a:b]]
    out = [(posicion, '\n'.join([*de(lineas, rangos), *ancestros, *todos]), str(rel), etiqueta, leido,
            '\n'.join([*de(barridas, rangos), *ancestros, *barridos]),
            None if propios is None else Propio(
                '\n'.join([*de(sin_fuentes, rangos), *ancestros, *propios]),
                (*ancestros, *propios, *(x for n in sorted(anotaciones) if any(a <= n < b for a, b in rangos)
                                         for x in anotaciones[n]))))
           for posicion, rangos, etiqueta, leido, ancestros, todos, barridos, propios in elementos]
    sueltas = [(n, '\n'.join([linea, *claves_sueltas.get(n, ())])) for n, linea in enumerate(lineas)
               if n not in cubiertas and linea.strip()]
    marca = [(str(rel), '', str(rel), FICHERO_NO_VERIFICABLE, None, '', None)] if no_verificable or fallos else []
    return out + [(f'{rel}:línea {n + 1}', suelta, str(rel), None, None, suelta, None) for n, suelta in sueltas] \
        + [(f'{rel}:línea {n + 1}', comentario, str(rel), None, None, comentario, None)
           for n, comentario in sorted(comentarios.items()) if n in cubiertas] + marca


def _sin_repetidas(pares):
    """El mapa de un objeto JSON, o ValueError si repite una clave: `json` se queda con la última en silencio, y el
    texto público lleva las dos (`_Pares`)."""
    claves = [clave for clave, _ in pares]
    if len(set(claves)) != len(claves):
        raise ValueError('clave repetida')
    return dict(pares)


class _Pares(list):
    """Un objeto de un JSON que repite alguna clave, como la lista de TODOS sus pares (clave, valor), en su orden y
    juntos (spec 071 AC175): el cargador lee el último de cada clave, y el texto público lleva todos."""


# AC195: las claves que dan la identidad de lo que una entrada nombra: repetida, la entrada es ambigua.
_CLAVE_IDENTIDAD = re.compile(r'source|target|type|name|entity_id|person|\w+_id|\w+_type')
ENTRADA_AMBIGUA = 'entrada ambigua (clave de identidad repetida)'


def _ambigua(valor):
    """Si algún objeto de un valor JSON leído (`_Pares`, a cualquier profundidad) repite una clave de identidad
    (spec 071 AC195): el JSON público que la repite es él mismo un defecto, y no se sabe qué identidad lee el
    cargador."""
    if isinstance(valor, _Pares):
        claves = [k for k, _ in valor if isinstance(k, str) and _CLAVE_IDENTIDAD.fullmatch(k)]
        return len(set(claves)) != len(claves) or any(_ambigua(v) for _, v in valor)
    if isinstance(valor, dict):
        return any(_ambigua(v) for v in valor.values())
    if isinstance(valor, list):
        return any(_ambigua(v) for v in valor)
    return False


def _cadenas_de_linea(linea):
    """(cadena,): las cadenas JSON de una línea de un JSON público, leídas: con sus escapes resueltos y sin las
    comillas de la sintaxis (spec 071 AC222)."""
    def leida(m):
        try:
            return json.loads(m.group(0))
        except ValueError:
            return m.group(0)
    return tuple(leida(m) for m in re.finditer(r'"(?:[^"\\]|\\.)*"', linea))


def _entradas_json(rel, texto):
    """Las entradas de un JSON público (`entradas_publicas`), leído, con sus escapes resueltos (AC163): cada elemento de
    una lista de primer nivel o cada clave de un mapa, con su clave (de una clave cuyo valor es una lista, cada elemento
    con ella: AC145). Con una clave repetida, cada objeto con todos sus pares (`_Pares`, AC175) y, además, su texto
    literal por líneas. Uno que no se lee, por líneas y sin darlo por limpio (`FICHERO_NO_VERIFICABLE`, AC174)."""
    lineas = [(f'{rel}:línea {n + 1}', linea, str(rel), None, None, linea, Propio(linea, _cadenas_de_linea(linea)))
              for n, linea in enumerate(_lineas_de(texto)) if linea.strip()]
    try:
        doc, literal = json.loads(texto, object_pairs_hook=_sin_repetidas), []
    except ValueError:
        try:
            doc, literal = json.loads(texto, object_pairs_hook=_Pares), lineas
        except ValueError:
            return lineas + [(str(rel), '', str(rel), FICHERO_NO_VERIFICABLE, None, '', None)]

    def entrada(posicion, valor):
        # El valor leído, con sus escapes resueltos (AC163), y sus claves; la que repite una clave de identidad, además
        # con su marca (AC195).
        texto, sin = json.dumps(valor, ensure_ascii=False), _sin_fuentes(valor)
        return [(posicion, texto, str(rel), None, valor, texto,
                 Propio(json.dumps(sin, ensure_ascii=False), tuple(_cadenas(sin))))] \
            + ([(posicion, '', str(rel), ENTRADA_AMBIGUA, None, '', None)] if _ambigua(valor) else [])

    if isinstance(doc, list) and not isinstance(doc, _Pares):
        return literal + [e for n, v in enumerate(doc) for e in entrada(f'{rel}:[{n}]', v)]
    if isinstance(doc, (dict, _Pares)):
        # Como una sección de un YAML: de una clave cuyo valor es una lista, cada elemento con su clave (una lista de
        # registros no es una asociación entre ellos, y la clave puede serlo: AC145).
        out = []
        for n, (k, v) in enumerate(doc if isinstance(doc, _Pares) else doc.items(), 1):
            if isinstance(v, list) and not isinstance(v, _Pares) and v:
                out += [e for i, x in enumerate(v) for e in entrada(f'{rel}:clave {n}[{i}]', {k: x})]
            else:
                out += entrada(f'{rel}:clave {n}', {k: v})
        if isinstance(doc, _Pares):
            claves = [k for k, _ in doc if isinstance(k, str) and _CLAVE_IDENTIDAD.fullmatch(k)]
            if len(set(claves)) != len(claves):
                out.append((str(rel), '', str(rel), ENTRADA_AMBIGUA, None, '', None))
        return literal + out
    return literal + entrada(f'{rel}:línea 1', doc)


# Los invisibles por defecto que no son de la categoría Cf (propiedad Default_Ignorable_Code_Point): el unificador de
# grafemas, los rellenos de hangul y de jemer, los selectores de variación y los de mongol. Tampoco se ven.
_INVISIBLES_NO_CF = (0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x3164, 0xFFA0,
                     *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0))


@functools.lru_cache(maxsize=1)
def _invisibles():
    """(el conjunto de los caracteres invisibles, la clase de expresión regular que los quita) (`_sin_invisibles`):
    los de la categoría Cf de los planos 0, 1 y 14, donde está cada uno de los asignados, y `_INVISIBLES_NO_CF`. El
    conjunto dice si un texto tiene alguno (`isdisjoint`, 70 veces más rápido que buscar con la clase: medido); la
    clase, solo cuando lo tiene."""
    cf = [c for c in (*range(0x20000), *range(0xE0000, 0xE1000)) if unicodedata.category(chr(c)) == 'Cf']
    todos = [chr(c) for c in (*cf, *_INVISIBLES_NO_CF)]
    return frozenset(todos), re.compile('[' + ''.join(re.escape(c) for c in todos) + ']')


def _sin_invisibles(texto, sustituto=''):
    """`texto` sin lo que no se ve y en NFKC, con `sustituto` en lugar de cada carácter invisible (spec 071 AC226 y
    AC230): LA definición de invisible, la que usan `_formas` (los identificadores, las huellas) y `_llano` (los
    nombres y las citas) y todo filtro previo: los de formato (categoría Cf: anchura cero, guion blando, BOM…) y los
    demás que no se ven (`_INVISIBLES_NO_CF`), también los que salen de normalizar (U+FFA0 en NFKC es U+3164). La
    mayoría de los textos no tienen ninguno: sin ellos, ASCII tal cual y lo demás solo en NFKC."""
    if texto.isascii():
        return texto
    conjunto, clase = _invisibles()
    if conjunto.isdisjoint(texto):
        texto = unicodedata.normalize('NFKC', texto)
        if conjunto.isdisjoint(texto):
            return texto
    return clase.sub('', unicodedata.normalize('NFKC', clase.sub(sustituto, texto)))


@functools.cache
def _formas(texto):
    """(sin invisibles, con un blanco por cada invisible): un texto como se compara un identificador (un entity_id, un
    documento, una huella) con un texto público (spec 071 AC226): sin caracteres invisibles (`_sin_invisibles`) y NFKC
    (el ancho completo, las ligaduras), sin tocar mayúsculas ni tildes. Dos formas: quitar el carácter invisible une lo
    que separaba (`Person:jo` + ZWSP + `se`), y ponerle un blanco conserva el límite de token de lo que separaba entre
    dos palabras; un identificador se busca en las dos (una sola si son la misma). Sin tope en la caché: se llama por
    cada texto público y cada fila del registro, y un tope menor que los textos haría recalcular el mismo texto en cada
    fila (medido: 5 veces más lento); casi siempre devuelve el propio texto, así que no copia nada."""
    texto = str(texto or '')
    formas = [_sin_invisibles(texto, sustituto) for sustituto in ('', ' ')]
    return (formas[0],) if formas[0] == formas[1] else tuple(formas)


def _visible(texto):
    """El texto sin caracteres invisibles y en NFKC (`_formas`, AC226): lo que ve quien lo lee."""
    return _formas(texto)[0]


def referencias_hex(texto, unidades):
    """{unidad}: las de `unidades` (las de las filas con marcador) de las que `texto` lleva una referencia: un tramo de
    8 a 64 caracteres hexadecimales, en mayúsculas o en minúsculas, que es prefijo suyo (spec 071 AC131 y AC146),
    también escrito con caracteres invisibles o en ancho completo (AC226). Un prefijo que casa con varias, todas."""
    return {u for forma in _formas(texto) for m in _HEX.finditer(forma) for u in unidades
            if u.startswith(m.group(0).lower())}


@functools.cache
def _plegadas(texto):
    """Las formas de `texto` (`_formas`) en minúsculas plegadas, para compararlas con un identificador sin distinguir
    mayúsculas (AC226). En la caché, como `_formas`."""
    return tuple(forma.casefold() for forma in _formas(texto))


@functools.cache
def _clave_de_id(valor):
    """El identificador `valor` como se compara (AC226): sin invisibles, en NFKC y en minúsculas plegadas."""
    return _visible(valor).casefold()


@functools.lru_cache(maxsize=4096)
def _patron_de_id(clave):
    """El patrón con que se busca un identificador (ya plegado) como token propio (`_nombra`)."""
    return re.compile(rf'(?<![\w:.\-]){re.escape(clave)}(?![\w.\-])')


def _nombra(valor, texto):
    """Si `texto` nombra `valor` (un entity_id o un documento) como tal, no como parte de otro más largo. Los dos,
    normalizados igual (`_formas`: sin caracteres Cf y NFKC, y mayúsculas solo para comparar; spec 071 AC226): ni un
    carácter invisible, ni el ancho completo, ni las mayúsculas esconden un identificador; los límites de token son los
    de siempre. La subcadena primero (barata); el patrón, solo si está."""
    clave = _clave_de_id(valor)
    if not clave:
        return False
    for forma in _plegadas(texto):
        if clave in forma and _patron_de_id(clave).search(forma) is not None:
            return True
    return False


def tras_el_overlay(index, deny=(), fix=()):
    """{(tipo, par): [(inicio, fin, props)]}: las copias del export que siguen vivas cuando el loader aplica sobre ellas
    el overlay (`deny` y `fix`, ya preparadas), como un Path A sobre el linaje (`relations_loader.main`). Las fases de
    las reglas `fix`, tal como las ejecutan `_RETYPE_Q`, `_SET_Q` y `_SWAP_Q` (spec 071 AC158): cada una casa contra el
    estado en que la deja la anterior, nunca contra lo que ella misma acaba de cambiar, y hace una sola transformación
    por arista: el tipo corregido de la primera regla que la casa (en el orden de las reglas, `head(collect)`), sin
    encadenar retipos; la carga y el motivo (`qa_set_run`) de la primera que la casa sobre su tipo corregido, no la
    suma de todas; y una sola inversión si la casa alguna (`WITH DISTINCT r`), también en una simétrica. Después las
    `deny`, cada una con el predicado exacto del loader (`alcance.alcanza_loader`). Las escisiones no: el export ya las
    lleva. Lo que el grafo publica tras el overlay (spec 071 AC122-bis y AC125)."""
    por_par = defaultdict(lambda: ([], []))
    for regla in fix:
        por_par[frozenset((regla.get('source_id'), regla.get('target_id')))][0].append(regla)
    for regla in deny:
        por_par[frozenset((regla.get('source_id'), regla.get('target_id')))][1].append(regla)
    out = defaultdict(list)
    for (tipo, par), copias in index.items():
        arreglos, retiradas = por_par[par] if par in por_par else ((), ())
        for inicio, fin, props in copias:
            arista = {'type': tipo, 'start': inicio, 'end': fin, 'props': dict(props)}
            retipo = next((r for r in arreglos if r.get('corrected_type') and alcanza_loader(r, arista, 'retype')),
                          None)
            if retipo is not None:
                arista['type'] = retipo['corrected_type']
            carga = next((r for r in arreglos if r.get('set') and alcanza_loader(r, arista, 'set')), None)
            if carga is not None:
                arista['props'].update(carga['set'])
                arista['props']['qa_set_run'] = carga.get('reason')
            if any(r.get('swap') and alcanza_loader(r, arista, 'invert') for r in arreglos):
                arista['start'], arista['end'] = arista['end'], arista['start']
            if not any(alcanza_loader(regla, arista, 'deny') for regla in retiradas):
                out[_par(arista['type'], arista['start'], arista['end'])].append(
                    (arista['start'], arista['end'], arista['props']))
    return out


def tipos_publicados(value, vivas, tipos):
    """{tipo}: bajo cuáles de `tipos` publica el grafo tras el overlay (`vivas`, de `tras_el_overlay`) la asociación de
    la arista `value` del censo desde su documento: alguna copia viva de su par, en su sentido salvo en un tipo
    simétrico, con su mismo documento (spec 071 AC125)."""
    doc = (value.get('props') or {}).get('source_doc') or None
    return {tipo for tipo in tipos
            for inicio, fin, props in vivas.get(_par(tipo, value.get('start'), value.get('end')), ())
            if (tipo in SIMETRICOS or (inicio, fin) == (value.get('start'), value.get('end')))
            and (props.get('source_doc') or None) == doc}


def tipos_alcanzables(value, fix, vivas):
    """{tipo}: el de la arista `value` del censo y los retipos a los que una regla `fix` (preparada) lleva una copia
    publicada de su par y su documento: el de cada copia viva tras el overlay (`vivas`) que, con el tipo original, la
    regla alcanza con el predicado del loader en su fase de retipo (`alcance.alcanza_loader`; spec 071 AC136). Un retipo
    que solo dice la prosa de una fila no lo es."""
    tipo, doc = value.get('type'), (value.get('props') or {}).get('source_doc') or None
    out = {tipo}
    for regla in fix:
        nuevo = regla.get('corrected_type')
        if not nuevo or regla.get('type') != tipo or nuevo in out:
            continue
        if any((props.get('source_doc') or None) == doc
               and alcanza_loader(regla, {'type': tipo, 'start': inicio, 'end': fin, 'props': props}, 'retype')
               for inicio, fin, props in vivas.get(_par(nuevo, value.get('start'), value.get('end')), ())):
            out.add(nuevo)
    return out


def tipos_entre(inicio, fin, vivas):
    """{tipo}: los de las aristas que el grafo publica tras el overlay (`vivas`, de `tras_el_overlay`) entre los nodos
    `inicio` y `fin`, en cualquier dirección, documento o capa: si hay alguno, el par está publicado (spec 071 D1)."""
    par = frozenset((inicio, fin))
    return {tipo for (tipo, otro), copias in vivas.items() if otro == par and copias}


def motivo_publicada(fila, value, vivas, fix=None):
    """El problema de una fila «suprimido» con una de las fórmulas de la asociación publicada (spec 071 D1), cada una
    literalmente verdad o un problema: con la de la copia duplicada (`DUPLICADA`, AC119), que el grafo no publique tras
    el overlay ninguna arista de su tipo, en su sentido (salvo en un tipo simétrico) y desde su documento
    (`tipos_publicados`); con la de AC125 (`PUBLICADA`), que el retipo que nombra no lo alcance ninguna regla `fix`
    (`tipos_alcanzables`, AC136; sin las reglas del overlay, `fix` None, no se puede saber) o que no publique ninguna
    copia de su asociación desde su documento, bajo su tipo o ese retipo; con la de AC142 (`SIGUE`), que no haya
    ninguna arista viva entre sus dos nodos del tipo que dice (el suyo si no nombra ninguno; si los nombra, cada uno, y
    de la ontología). None si la fórmula dice la verdad o la fila no lleva ninguna."""
    dato = str((fila or {}).get('dato') or '')
    if dato.endswith(f': {DUPLICADA}'):
        return None if tipos_publicados(value, vivas, {value.get('type')}) else SIN_DUPLICADA
    sigue = _SIGUE.search(dato)
    if sigue:
        tipos = set(sigue.group(1).split(', ')) if sigue.group(1) else {value.get('type')}
        return None if tipos <= frozenset(ontologia().tipos_relacion) \
            and tipos <= tipos_entre(value.get('start'), value.get('end'), vivas) else SIN_SIGUE
    m = _PUBLICADA.search(dato)
    if not m or not dato.endswith(m.group(0)):
        return None
    tipos = {value.get('type')}
    if m.group(1):
        if fix is not None and m.group(1) not in tipos_alcanzables(value, fix, vivas):
            return RETIPO_NO_ALCANZABLE
        tipos.add(m.group(1))
    return None if tipos_publicados(value, vivas, tipos) else SIN_PUBLICADA


_DATO_ARISTA = re.compile(r'\((?P<a>[^()]*)\) -\[(?P<tipo>[A-Z_]+)\]-> \((?P<b>[^()]*)\)(?P<resto>: .*)', re.S)


def visibles_de_fila(row):
    """{id}: los extremos que una fila marcada publica, solo por su posición (spec 071 AC161): los huecos de su `dato`
    sin marcador, si tiene la forma de la fila de una arista, y su `entidad`. Nunca un id que el dato diga en otro
    sitio: una fila que revela al oculto en su texto no lo convierte por eso en visible."""
    dato = _DATO_ARISTA.fullmatch(str(row.get('dato') or ''))
    huecos = {h for h in (dato['a'], dato['b']) if not MARCADOR.search(h)} if dato else set()
    entidad = row.get('entidad')
    return huecos | ({entidad} if isinstance(entidad, str) and entidad else set())


def extremos_de_fila(dato, value, entidad):
    """(extremos, ocultos, resto): los dos extremos de la arista `value` del censo como los nombra el `dato` de su fila
    (el del censo, o la `entidad` de la fila si la arista se re-identificó a ella), con el del censo en lugar de cada
    marcador; los que oculta, y el texto que sigue a la arista. La orientación la dan los dos extremos (spec 071 AC153):
    si el que la fila nombra es el del otro lado de `value` (una regla puede escribir el par al revés que el dato), el
    marcador toma el otro. None si el dato no tiene la forma de la fila de esa arista (su tipo, y cada extremo nombrado
    así o con un marcador de su clase) o si nombra el mismo extremo dos veces (AC113 y D1)."""
    m = _DATO_ARISTA.fullmatch(str(dato or ''))
    if not m or m['tipo'] != value.get('type'):
        return None
    inicio, fin = str(value.get('start') or ''), str(value.get('end') or '')
    marcados = [bool(MARCADOR.fullmatch(hueco.partition(':')[2])) for hueco in (m['a'], m['b'])]
    if marcados.count(True) == 1:
        visible = (m['a'], m['b'])[marcados.index(False)]
        suyo, otro = ((inicio, fin) if marcados[1] else (fin, inicio))
        if visible == otro and visible != suyo:
            inicio, fin = fin, inicio
    extremos, ocultos = [], set()
    for hueco, extremo in ((m['a'], inicio), (m['b'], fin)):
        clase, _, marca = hueco.partition(':')
        if hueco in (extremo, entidad):
            extremos.append(hueco)
        elif MARCADOR.fullmatch(marca) and extremo.startswith(f'{clase}:'):
            extremos.append(extremo)
            ocultos.add(extremo)
        else:
            return None
    if extremos[0] == extremos[1]:
        return None
    return extremos, ocultos, m['resto']


def trozo_de_cita(row, citas, barrido=None):
    """Si la fila `row` lleva un trozo de `PALABRAS_TROZO` palabras seguidas de alguna de `citas` (las de su arista
    retirada, llanas), sin contar la puntuación: una fila anonimizada nunca lleva su cita, y por eso la cita retirada en
    una arista del grafo no la reconstruye (spec 071 D2). En todo lo que se barre de ella (`barrido`, el de su entrada
    del fichero: claves, comentarios, valores anidados y leídos, sin el valor estructural de su `unidad`; AC187), o, sin
    él, en sus campos de texto salvo la unidad."""
    def palabras(texto):
        return re.sub(r'\W+', ' ', texto).split()
    campos = barrido if barrido is not None else \
        ' '.join(v for k, v in row.items() if k != 'unidad' and isinstance(v, str))
    texto = f" {' '.join(palabras(_llano(campos)))} "
    return any(f" {' '.join(cita[i:i + PALABRAS_TROZO])} " in texto
               for cita in (palabras(c) for c in citas) for i in range(len(cita) - PALABRAS_TROZO + 1))


def _llano(texto):
    """Un texto como se compara un nombre o una cita en un fichero público: sin lo que no se ve (`_sin_invisibles`, la
    misma definición que `_formas`: spec 071 AC148 y AC230), NFKC, sin mayúsculas, con los espacios colapsados
    (`alcance.cita_normalizada`, AC107) y sin tildes. Es lo que más se llama del barrido: el ASCII no tiene nada que
    quitar (medido: lo más barato)."""
    texto = str(texto or '')
    if texto.isascii():
        return cita_normalizada(texto)
    visible = _sin_invisibles(texto)
    return ''.join(c for c in unicodedata.normalize('NFKD', cita_normalizada(visible)) if not unicodedata.combining(c))


def _palabra(nombre, texto):
    return nombre in texto and re.search(rf'(?<!\w){re.escape(nombre)}(?!\w)', texto) is not None


def nombres_del_export(snapshot):
    """{entity_id: {nombre}}: los nombres con que el export publica cada entidad (`name`, `canonical_name` y `aliases`),
    llanos (`_llano`), para buscarla por su nombre en un texto público (spec 071 AC122-bis)."""
    out = defaultdict(set)
    for _, node in _lineas(Path(snapshot) / 'nodes.jsonl'):
        props = node.get('props') or {}
        if not props.get('entity_id'):
            continue
        for nombre in (props.get('name'), props.get('canonical_name'), *(props.get('aliases') or ())):
            if _es_nombre(nombre, props['entity_id'].split(':', 1)[0]):
                out[props['entity_id']].add(_llano(nombre))
    return out


def _es_nombre(nombre, tipo=None):
    """Si `nombre` identifica en prosa a una entidad de clase `tipo`: el de una sociedad o un organismo, completo y con
    algo más que denominaciones genéricas; el de cualquier otra, al menos `PALABRAS_MIN` palabras y `NOMBRE_MIN`
    caracteres, llano."""
    llano = _llano(nombre) if isinstance(nombre, str) else ''
    if tipo in SOCIEDADES:
        return any(re.sub(r'[^\w]', '', palabra) not in _GENERICAS for palabra in llano.split())
    return len(llano) >= NOMBRE_MIN and len(llano.split()) >= PALABRAS_MIN


# AC197: lo que no distingue a nadie como nombre de una palabra: las denominaciones genéricas (las de aquí y las de la
# spec 070), las palabras funcionales, los tratamientos y los nombres comunes de una sociedad (los de `_NO_DISTINTIVAS`
# del 070), y los nombres genéricos del grafo por su huella (sha16; la lista cerrada `_GENERICAS_HUELLAS` del 070).
_NO_NOMBRE = _GENERICAS | frozenset({
    'scp', 'slne', 'coop', 'aie', 'srl', 'spa', 'ltd', 'llc', 'inc', 'gmbh', 'ag', 'bv', 'nv', 'plc', 'sas', 'holding',
    'comunidad', 'cabildo', 'estado', 'lo', 'un', 'una', 'unos', 'unas', 'en', 'a', 'al', 'o', 'u', 'con', 'por',
    'para', 'sin', 'sobre', 'su', 'sus', 'esta', 'este', 'esa', 'ese', 'dicha', 'dicho', 'otra', 'otro', 'misma',
    'mismo', 'cada', 'toda', 'todo', 'como', 'que', 'ni', 'entre', 'tras', 'ante', 'bajo', 'desde', 'hasta', 'hacia',
    'segun', 'mediante', 'sociedades', 'empresas', 'mercantil', 'mercantiles', 'companias', 'entidad', 'entidades',
    'tipo', 'forma', 'd', 'da', 'dna', 'don', 'dona', 'sr', 'sra', 'srta', 'sres', 'sras', 'senor', 'senora'})
_GENERICAS_HUELLAS = frozenset({'de4743c879734dc3', 'd764d42592fc9687', '9cb403795f3e2673', 'ef46d887b93b39f0',
                                '8676b73384a972c9', '0bec84246d4c54be'})


def _nombre_corto(nombre, tipo=None):
    """El nombre de una palabra de una entidad que no es una sociedad ni un organismo (spec 071 AC197), llano, o None:
    de `NOMBRE_MIN` caracteres o más y que no es una palabra que no distingue (`_NO_NOMBRE`) ni un nombre genérico del
    grafo (`_GENERICAS_HUELLAS`). Solo cuenta junto al otro extremo de la fila (`_comencion_corta`)."""
    llano = _llano(nombre) if isinstance(nombre, str) else ''
    if tipo in SOCIEDADES or len(llano.split()) != 1 or len(llano) < NOMBRE_MIN or llano in _NO_NOMBRE:
        return None
    if {hashlib.sha256(str(x).encode()).hexdigest()[:16] for x in (nombre, nombre.strip(), llano)} & _GENERICAS_HUELLAS:
        return None
    return llano


def nombres_cortos(snapshot):
    """{entity_id: {nombre}}: los nombres de una palabra con que el export publica cada entidad que no es una sociedad
    ni un organismo (`_nombre_corto`; spec 071 AC197)."""
    out = defaultdict(set)
    for _, node in _lineas(Path(snapshot) / 'nodes.jsonl'):
        props = node.get('props') or {}
        if not isinstance(props.get('entity_id'), str):
            continue
        for nombre in (props.get('name'), props.get('canonical_name'), *(props.get('aliases') or ())):
            corto = _nombre_corto(nombre, props['entity_id'].split(':', 1)[0])
            if corto:
                out[props['entity_id']].add(corto)
    return out


# `propio`: lo que la entrada dice por su cuenta, sin los valores de sus campos de fuente (`_CAMPOS_FUENTE`), quitados
# por su posición (AC215): su `texto` (el literal, con las comillas de la sintaxis) y sus `escalares` (cada clave y
# escalar leído, cada comentario, también el de cabecera de un escalar de bloque o tras un ancla o una etiqueta, y cada
# ancla, alias y etiqueta, por separado: AC222 y AC227). None si no se calculó (la entrada no es un elemento con
# campos).
Propio = namedtuple('Propio', 'texto escalares')
Entrada = namedtuple('Entrada', 'posicion texto fichero seccion valor ids tipos citas llano regla barrido propio',
                     defaults=(None, '', None))


def _ids_de(valor, entidades, resolver, entity_id, tipos_entidad, out, nombres, cortos=None):
    """Los entity_id que una entrada nombra por su estructura, como los resuelven sus cargadores: `<lado>_id`, o el
    nombre de `<lado>` con `<lado>_type`, con la resolución del loader de relaciones (`resolver`,
    `relations_loader._resolve`: el id sin blancos, o el nombre con su tipo sin blancos y Person si no lo dice; spec 071
    AC137) o con el tipo que le da la lista `entities` de su fichero (`_id_from_loose` de prensa, última hora y
    contexto); `{type, name}` (o su `entity_id`); `person` (acusaciones y afirmaciones); los `duplicates` de un merge,
    con el tipo de su canónico; y cualquier valor que es un entity_id (`entities` del feed y de los patrones, `id` de
    los seudónimos). Cada nombre resuelto se apunta en `nombres` para buscarlo en los textos; el de una palabra, en
    `cortos` (AC197: solo cuenta junto al otro extremo de una fila)."""
    def anota(tipo, nombre):
        nombre = str(nombre or '').strip()
        if tipo in tipos_entidad and nombre:
            eid = entity_id(tipo, nombre)
            out.add(eid)
            if _es_nombre(nombre, tipo):
                nombres[eid].add(_llano(nombre))
            elif cortos is not None and _nombre_corto(nombre, tipo):
                cortos[eid].add(_nombre_corto(nombre, tipo))

    def sigue(v):
        _ids_de(v, entidades, resolver, entity_id, tipos_entidad, out, nombres, cortos)
    if isinstance(valor, _Pares):
        # AC175 y AC186: lo que lee el cargador (el último valor de cada clave) y cada par con su clave y el contexto de
        # las demás (el mapa de los últimos con ese valor en su clave): un valor anterior de una clave repetida conserva
        # la identidad que le da su clave (un `target` con su `target_type`), también anidado. AC195: y cada estado
        # acumulado, el mapa tal como quedaba tras cada par en su orden, que conserva un `target` con el `target_type`
        # que lo acompañaba aunque después se repitan los dos.
        ultimos, acumulado = dict(valor), {}
        sigue(ultimos)
        for k, v in valor:
            sigue({**ultimos, k: v})
            acumulado[k] = v
            sigue(dict(acumulado))
    elif isinstance(valor, dict):
        for lado in ('source', 'target'):
            crudo, nombre, tipo = valor.get(f'{lado}_id'), valor.get(lado), valor.get(f'{lado}_type')
            if not all(x is None or isinstance(x, str) for x in (crudo, nombre, tipo)) or not (crudo or nombre):
                continue
            eid = resolver(valor, lado)
            if eid:
                out.add(eid)
                if not (crudo or '').strip() and _es_nombre(nombre, eid.split(':', 1)[0]):
                    nombres[eid].add(_llano(nombre))
                elif not (crudo or '').strip() and cortos is not None and _nombre_corto(nombre, eid.split(':', 1)[0]):
                    cortos[eid].add(_nombre_corto(nombre, eid.split(':', 1)[0]))
            if isinstance(nombre, str) and nombre.strip() in entidades:
                anota(entidades[nombre.strip()], nombre)
        if valor.get('type') in tipos_entidad and isinstance(valor.get('name'), str):
            if isinstance(valor.get('entity_id'), str) and valor['entity_id'].strip():
                out.add(valor['entity_id'].strip())
            anota(valor['type'], valor['name'])
        if isinstance(valor.get('person'), str):
            anota('Person', valor['person'])
        canonico = valor.get('canonical')
        if isinstance(canonico, dict) and canonico.get('type') in tipos_entidad:
            for dup in valor.get('duplicates') or ():
                if isinstance(dup, str):
                    anota(canonico['type'], dup)
        for v in valor.values():
            sigue(v)
    elif isinstance(valor, list):
        for v in valor:
            sigue(v)
    elif isinstance(valor, str) and _ES_ID.fullmatch(valor.strip()):
        out.add(valor.strip())


def _citas_de(valor, out):
    """Los textos de las claves de cita de una entrada (`quote`, `quote_contains`, `source_quote`, `cita`…), llanos."""
    if isinstance(valor, (dict, _Pares)):
        for k, v in (valor if isinstance(valor, _Pares) else valor.items()):
            if isinstance(v, str) and ('quote' in str(k) or 'cita' in str(k)) and len(_llano(v)) >= CITA_MIN:
                out.add(_llano(v))
            else:
                _citas_de(v, out)
    elif isinstance(valor, list):
        for v in valor:
            _citas_de(v, out)


_ES_ID = re.compile(r'[A-Z][A-Za-z]+:[^\s:]+')


@functools.lru_cache(maxsize=4)
def _patron_de_ids(tipos_entidad):
    """(el patrón que lee un entity_id en un texto ya plegado, {tipo plegado: tipo canónico}) de los `tipos_entidad` de
    la ontología (`ids_en_texto`)."""
    canonico = {t.casefold(): t for t in tipos_entidad}
    tipos = '|'.join(sorted(map(re.escape, canonico), key=len, reverse=True))
    return re.compile(rf'(?<![\w:])({tipos}):([a-z0-9]+(?:-[a-z0-9]+)*)'), canonico


def ids_en_texto(texto):
    """Los entity_id que nombra un texto libre: un tipo de entidad de la ontología, dos puntos y un slug como los de
    `entity_resolution.entity_id` (minúsculas, cifras y guiones), sin la puntuación que lo siga. Sobre las formas del
    texto ya plegadas, como las compara `_nombra` (`_plegadas`: sin caracteres invisibles, en ancho completo, en
    mayúsculas, `ß` por `ss`; AC226 y AC231), sin `IGNORECASE` ni `lower`, que no pliegan igual (`ı`, `İ`) y daban un
    tipo que el diccionario no tiene: cada id, en su forma canónica."""
    patron, canonico = _patron_de_ids(ontologia().tipos_entidad)
    return list(dict.fromkeys(f'{canonico[tipo]}:{slug}'
                              for forma in _plegadas(texto) for tipo, slug in patron.findall(forma)))


_TOKEN_TIPO = re.compile(r'(?<![A-Za-z_])[A-Z][A-Z_]+(?![A-Za-z_])')
# Las entradas de los ficheros de acusaciones son ACCUSED_OF aunque no lo escriban: las crea `accusations_loader`.
TIPO_IMPLICITO = {'accusations': 'ACCUSED_OF'}


def referencias(repo, nombres, aparte=(), cortos=None):
    """[Entrada]: las entradas públicas de `repo` (`entradas_publicas`) con lo que nombran: los entity_id que resuelve
    su estructura (`_ids_de`, con el resolver de su cargador), los tipos de relación que escriben (o el implícito de su
    fichero), sus citas y su texto llano, comentarios incluidos, y su texto sin los campos estructurales (el `barrido`
    de `entradas_publicas`, AC144). Una regla opaca también: todo salvo su clave, sus declaraciones y la etiqueta de su
    clave en su motivo (spec 071 AC130). Añade a `nombres` los nombres que resuelven (AC122-bis). `aparte`: las
    entradas crudas de otro fichero que también se barre (el registro que se comprueba, si no es el del repo: AC196).
    `cortos`: donde se apuntan los nombres de una palabra que resuelven (AC197)."""
    crudas = entradas_publicas(repo) + list(aparte)
    if not crudas:
        return []
    # El resolutor y el entity_id del loader viven en `alcance` y `entity_ids` (solo biblioteca estándar); el loader
    # mismo, solo para preparar una regla `fix` (el CI corre sin el entorno del pipeline).
    from src.entity_ids import entity_id
    onto = ontologia()
    tipos_entidad, tipos_relacion = frozenset(onto.tipos_entidad), frozenset(onto.tipos_relacion)
    entidades_por_fichero = defaultdict(dict)
    for _, _, fichero, seccion, valor, _, _ in crudas:
        if seccion == 'entities' and isinstance(valor, dict) and valor.get('type') in tipos_entidad \
                and isinstance(valor.get('name'), str):
            entidades_por_fichero[fichero][valor['name'].strip()] = valor['type']
    out = []
    for posicion, texto, fichero, seccion, valor, barrido, propio in crudas:
        leido = valor
        if Path(fichero).parts[0] == 'relations' and es_opaca(valor):
            texto, leido = barrido, {k: v for k, v in valor.items() if k not in ESTRUCTURALES_OPACA}
        ids, citas = set(), set()
        _ids_de(leido, entidades_por_fichero[fichero], resolver, entity_id, tipos_entidad, ids, nombres, cortos)
        _citas_de(leido, citas)
        tipos = {t for t in _TOKEN_TIPO.findall(texto) if t in tipos_relacion}
        if seccion in TIPO_IMPLICITO:
            tipos.add(TIPO_IMPLICITO[seccion])
        regla = None
        if Path(fichero).parts[0] == 'relations' and seccion == 'fix' and isinstance(valor, dict):
            from src import relations_loader
            from src.qa_esc_apply import _SinLog
            previo, relations_loader.log = relations_loader.log, _SinLog()
            try:
                regla = next(iter(relations_loader._prepare_fix([valor])), None)
            except (SystemExit, Exception):
                regla = None
            finally:
                relations_loader.log = previo
        out.append(Entrada(posicion, texto, fichero, seccion, valor, frozenset(ids), frozenset(tipos),
                           frozenset(citas), _llano(texto), regla, barrido, propio))
    return out


def entradas_del_export(snapshot, vivas):
    """[Entrada]: lo que publica el grafo como texto, que también cuenta como público (spec 071 AC133): cada nodo del
    export (todas sus propiedades de texto salvo su entity_id, que nombra si lo tiene; los que no, una afirmación, un
    documento o un fragmento, no nombran ninguno: AC147) y cada arista viva tras el overlay (`vivas`, de
    `tras_el_overlay`: sus propiedades de texto, sus extremos, su tipo y su cita). La posición es «export:nodo[n]» (su
    línea en el export) o «export:arista[n]» (la n-ésima viva), nunca el id."""
    def textos(props, fuera=()):
        # AC221: las claves de primer nivel son texto que el grafo publica, como los valores; solo se salta la de
        # `fuera` (el entity_id de un nodo, que `ids` ya nombra).
        partes = []
        for k, v in props.items():
            if k in fuera:
                continue
            partes.append(k)
            if isinstance(v, str):
                partes.append(v)
            elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                partes += v
        return '\n'.join(partes)
    out = []
    for n, node in _lineas(Path(snapshot) / 'nodes.jsonl'):
        props = node.get('props') or {}
        texto = textos(props, ('entity_id',))
        ids = frozenset({props['entity_id']}) if isinstance(props.get('entity_id'), str) else frozenset()
        out.append(Entrada(f'export:nodo[{n}]', texto, 'export', 'nodo', props, ids, frozenset(), frozenset(),
                           _llano(texto), None, texto))
    n = 0
    for (tipo, _), copias in vivas.items():
        for inicio, fin, props in copias:
            texto = textos(props)
            cita = _llano(props.get('source_quote') or '')
            out.append(Entrada(f'export:arista[{n}]', texto, 'export', 'arista', props,
                               frozenset({inicio, fin} - {None}), frozenset({tipo}),
                               frozenset({cita} if len(cita) >= CITA_MIN else ()), _llano(texto), None, texto))
            n += 1
    return out


# Los campos que llevan el texto de la fuente (citas): lo que dicen no es nuestro (AC197).
_CAMPOS_FUENTE = frozenset({'quote_contains', 'source_quote', 'quote', 'cita'})
# Los que dicen qué documento cita una entrada: el `doc` de una regla, el `source_doc` de una arista.
_CAMPOS_DOC = frozenset({'doc', 'source_doc'})
# Lo que en un texto es un identificador y no palabras (AC197, enmienda 24 bis): una URL, un entity_id, un nombre de
# fichero, un id del BOE o una huella. Por su tramo: sus palabras no nombran a nadie.
_IDENTIFICADOR = re.compile(r'https?://\S+|(?<![\w:])[A-Z][A-Za-z]*:[a-z0-9]+(?:[-_][a-z0-9]+)*'
                            r'|[\w.\-]+\.(?i:pdf|ya?ml|json|md|txt|html?|csv|docx?|xlsx?)(?![\w.])'
                            r'|BOE-[A-Z]-\d{4}-\d+|(?<![0-9a-f])[0-9a-f]{16,}(?![0-9a-f])')
# Las comillas de una cita: la que abre y la que la cierra (la recta, la misma) y todas las que cuentan (AC242).
_CIERRE_DE = {'«': '»', '“': '”', '"': '"'}
_COMILLA = re.compile('[«»“”"]')
# Las elipsis que parten una cita (AC197: la cita literal de su documento).
_ELIPSIS = re.compile(r'\[(?:…|\.\.\.)\]|\((?:…|\.\.\.)\)|…|\.\.\.')


def _pares_de_comillas(texto):
    """[(inicio, fin, interior)]: los pares de comillas de `texto` cuyo tramo puede eximirse (AC242, AC244, AC246 y
    AC250), TODO O NADA y en UN recorrido sobre la secuencia de sus comillas. Un par es válido si sus dos comillas son
    contiguas en esa secuencia (ninguna otra comilla entre medias, aunque haya texto, vacío o con saltos de línea) y la
    segunda cierra la primera (la recta, con una recta): una comilla de cierre nunca se reutiliza como apertura. Si
    TODAS las comillas del escalar forman pares válidos, esos son los pares; si hay una sola SUELTA (un cierre sin
    apertura, una apertura sin cierre, una apertura seguida de otra comilla que no la cierra, una anidación, una recta
    de más o de menos), no se exime nada del escalar, de ninguna clase: una comilla perdida o sobrante no puede quitar
    un bloqueo, ni delante ni detrás de ella. Reparar el entrecomillado (borrar la suelta de un escalar ambiguo) sí
    puede recuperar una exención: es corregirlo, no ampliarlo. Lineal: cada comilla se localiza una sola vez."""
    out, abierta = [], -1
    for m in _COMILLA.finditer(texto):
        i = m.start()
        if abierta < 0:
            if texto[i] not in _CIERRE_DE:
                return []
            abierta = i
        elif _CIERRE_DE[texto[abierta]] == texto[i]:
            out.append((abierta, i + 1, texto[abierta + 1:i]))
            abierta = -1
        else:
            return []
    return out if abierta < 0 else []


def _en_blanco(caracteres, inicio, fin):
    """La lista de caracteres `caracteres` con [inicio, fin) en blanco, salvo los saltos de línea (`_SALTOS`): poner un
    tramo en blanco no cambia las líneas."""
    for i in range(inicio, fin):
        if caracteres[i] not in _SALTOS:
            caracteres[i] = ' '


def _nodos_de_fuente(nodo):
    """[ScalarNode]: los nodos de los valores de texto de los campos de fuente (`_CAMPOS_FUENTE`) de un nodo YAML, a
    cualquier profundidad: los mismos que `_textos_de_fuente` lee del valor construido (spec 071 AC215). Sin el que
    también es el valor de otra cosa (un ancla que un campo de fuente y otro campo comparten): ese texto es también
    nuestro, y quitarlo por su posición lo borraría de los dos."""
    fuentes, ajenos = [], set()

    def recorrer(n, es_fuente):
        if isinstance(n, yaml.ScalarNode):
            if es_fuente and n.tag == 'tag:yaml.org,2002:str':
                fuentes.append(n)
            else:
                ajenos.add(id(n))
        elif isinstance(n, yaml.SequenceNode):
            for hijo in n.value:
                recorrer(hijo, False)
        elif isinstance(n, yaml.MappingNode):
            for clave, valor in n.value:
                recorrer(clave, False)
                recorrer(valor, isinstance(clave, yaml.ScalarNode) and clave.value in _CAMPOS_FUENTE)
    recorrer(nodo, False)
    return [n for n in fuentes if id(n) not in ajenos]


def _sin_fuentes(valor):
    """El valor leído sin el texto de sus campos de fuente (en blanco), como lo ve `_textos_de_fuente` (AC215): un campo
    de fuente que no es un texto no se toca ni se recorre."""
    if isinstance(valor, (dict, _Pares)):
        pares = [(k, ('' if isinstance(v, str) else v) if k in _CAMPOS_FUENTE else _sin_fuentes(v))
                 for k, v in (valor if isinstance(valor, _Pares) else valor.items())]
        return _Pares(pares) if isinstance(valor, _Pares) else dict(pares)
    if isinstance(valor, list):
        return [_sin_fuentes(v) for v in valor]
    return valor


def _textos_de_fuente(valor):
    """[texto]: los valores de los campos de cita (`_CAMPOS_FUENTE`) de un valor leído, a cualquier profundidad."""
    if isinstance(valor, _Pares):
        pares = list(valor)
    elif isinstance(valor, dict):
        pares = list(valor.items())
    elif isinstance(valor, list):
        return [x for v in valor for x in _textos_de_fuente(v)]
    else:
        return []
    return [v for k, v in pares if k in _CAMPOS_FUENTE and isinstance(v, str)] \
        + [x for k, v in pares if k not in _CAMPOS_FUENTE for x in _textos_de_fuente(v)]


def _documentos_de(valor):
    """{doc}: los documentos que cita un valor leído (sus campos `_CAMPOS_DOC`, a cualquier profundidad)."""
    if isinstance(valor, (dict, _Pares)):
        pares = list(valor) if isinstance(valor, _Pares) else list(valor.items())
        return {v.strip() for k, v in pares if k in _CAMPOS_DOC and isinstance(v, str) and v.strip()} \
            | {d for k, v in pares if k not in _CAMPOS_DOC for d in _documentos_de(v)}
    if isinstance(valor, list):
        return {d for v in valor for d in _documentos_de(v)}
    return set()


def textos_de_documentos(snapshot):
    """{doc_id: texto llano}: el texto de los fragmentos de cada documento del export, en su orden, donde se busca una
    cita literal (`tramos_citados`, AC197)."""
    trozos = defaultdict(list)
    for _, nodo in _lineas(Path(snapshot) / 'nodes.jsonl'):
        props = nodo.get('props') or {}
        if _fragmento(nodo.get('labels')) and isinstance(props.get('doc_id'), str) \
                and isinstance(props.get('text'), str):
            orden = props.get('chunk_index')
            trozos[props['doc_id']].append((orden if isinstance(orden, int) else 0, props['text']))
    return {doc: _llano(' '.join(texto for _, texto in sorted(ts, key=lambda x: x[0]))) for doc, ts in trozos.items()}


def tramos_citados(texto, documentos, por_documento):
    """[(inicio, fin)]: los tramos de `texto`, por su posición, que son una cita que su documento dice literalmente
    (spec 071 AC197 y AC215): un tramo entre comillas («», “”, "") de `CITA_MIN` caracteres o más cuyos trozos entre
    elipsis están todos en el texto de los fragmentos de uno de sus `documentos` (`por_documento`, de
    `textos_de_documentos`), con sus comillas. Es texto de la fuente; el tramo que no se encuentra sigue siendo nuestro
    (fallo cerrado), y lo que la cita contiene fuera de ese tramo también."""
    textos = [por_documento[d] for d in sorted(documentos) if d in por_documento]
    out = []
    for inicio, fin, tramo in _pares_de_comillas(texto if textos else ''):
        trozos = [x for x in (_llano(t).strip(' .,;:') for t in _ELIPSIS.split(tramo)) if x]
        if len(_llano(tramo)) >= CITA_MIN and trozos and any(all(x in f for x in trozos) for f in textos):
            out.append((inicio, fin))
    return out


def _contenedores(nombres, palabras):
    """{palabra: {nombre largo}}: los nombres de varias palabras (de cualquier entidad, `nombres`) que llevan dentro una
    de `palabras`, nombres de una palabra (AC197): dentro de uno de ellos, no es la mención de quien la lleva sola."""
    out = defaultdict(set)
    for ns in nombres.values():
        for n in ns:
            for w in set(n.split()) & palabras if ' ' in n else ():
                out[w].add(n)
    return out


def _del_oculto(ocultos, visibles, nombres, cortos, referencias=()):
    """{palabra}: las palabras sueltas que nombran a un extremo oculto (spec 071 AC197 y enmienda 24 bis): los nombres
    de una palabra (`cortos`) de los que son personas, y las `referencias` privadas de la clave de la fila; nunca una
    palabra que también es un nombre de un extremo visible o parte de uno."""
    de_visibles = {w for x in visibles for w in cortos.get(x, ())} \
        | {w for x in visibles for n in nombres.get(x, ()) for w in n.split()}
    return ({w for x in ocultos if x.startswith('Person:') for w in cortos.get(x, ())}
            | {_llano(w) for w in referencias}) - de_visibles


def _menciona_corto(e, ocultos, visibles, nombres, cortos, contenedores, referencias=()):
    """Si la entrada `e` nombra a un extremo oculto por un nombre de una palabra o una referencia privada de su clave
    (`_del_oculto`), sin necesitar al visible (spec 071 AC212): la de la propia fila y la de la regla opaca de su
    unidad, que ya publican el extremo visible y el tipo. Sin los identificadores (`_IDENTIFICADOR`), sin los
    marcadores de anonimato de la fórmula de la fila (`MARCADOR`: «tercero») y sin una palabra dentro del nombre largo
    de otra entidad (`contenedores`)."""
    palabras = _del_oculto(ocultos, visibles, nombres, cortos, referencias)
    if not palabras:
        return False
    llano = _llano(_IDENTIFICADOR.sub(' ', MARCADOR.sub(' ', _visible(e.texto))))
    return any(_palabra(w, _sin_nombres(llano, contenedores.get(w, ()))) for w in palabras)


def _comencion_corta(e, ocultos, visibles, nombres, cortos, contenedores, referencias=(), por_documento=None):
    """El apartado de una entrada que nombra a la vez a un extremo oculto de la fila y al visible, alguno por un nombre
    de una palabra (spec 071 AC197 y enmienda 24 bis), o None. Del oculto cuentan su id, su estructura, sus nombres de
    varias palabras y, solo si es una persona, sus nombres de una palabra (`cortos`), con las `referencias` privadas de
    la clave de la fila; nunca una palabra que también es un nombre de un extremo visible o parte de uno. Del visible,
    su id, su estructura o un nombre completo suyo: uno de una palabra, solo si es el entero y no parte de uno de
    varias. Las palabras de un identificador (`_IDENTIFICADOR`) no nombran a nadie, ni una palabra dentro de un nombre
    largo de otra entidad (`contenedores`). 'feed' en el feed; 'editorial' en prosa curada, en el grafo, en una escisión
    del overlay o si el oculto o el visible solo están en el texto de la fuente (sus campos de cita y los tramos de un
    texto nuestro que su documento dice literalmente, `tramos_citados` con `por_documento`, por su posición); 'corto'
    si un texto nuestro nombra a los dos."""
    del_oculto = _del_oculto(ocultos, visibles, nombres, cortos, referencias)
    del_visible = {x: {w for w in cortos.get(x, ()) if not any(w in n.split() for n in nombres.get(x, ()))}
                   for x in visibles}
    if not any(w in e.llano for w in del_oculto | {w for ws in del_visible.values() for w in ws}):
        return None

    def una(palabras, llano):
        return any(_palabra(w, _sin_nombres(llano, contenedores.get(w, ()))) for w in palabras)

    def nombrados(llano):
        oculto = una(del_oculto, llano) or any(
            x in e.ids or _nombra(x, e.texto) or any(_palabra(n, llano) for n in nombres.get(x, ())) for x in ocultos)
        return oculto and any(x in e.ids or _nombra(x, e.texto) or any(_palabra(n, llano) for n in nombres.get(x, ()))
                              or una(del_visible[x], llano) for x in visibles)
    limpio = _llano(_IDENTIFICADOR.sub(' ', _visible(e.texto)))
    if not nombrados(limpio):
        return None
    if e.fichero == 'ultima_hora/feed.yaml':
        return 'feed'
    if e.fichero == 'export' or Path(e.fichero).parts[0] in CURADOS \
            or (Path(e.fichero).parts[0] == 'relations' and e.seccion == 'splits'):
        return 'editorial'
    # AC215: lo que la entrada dice por su cuenta, sin los valores de sus campos de fuente (por su posición, `propio`)
    # ni los tramos que son una cita verificada de su documento (`tramos_citados`, por su posición): lo que esa cita
    # contiene, dicho fuera de ella, es texto nuestro. AC222: la cita se busca en cada escalar leído (o comentario) por
    # separado, nunca en el literal: ni las comillas de la sintaxis ni un tramo que cruza dos campos son una cita.
    documentos = _documentos_de(e.valor)

    def sin_citas(texto):
        for inicio, fin in reversed(tramos_citados(texto, documentos, por_documento or {})):
            texto = f'{texto[:inicio]} {texto[fin:]}'
        return texto
    nuestro = sin_citas(e.texto) if e.propio is None else '\n'.join(sin_citas(x) for x in e.propio.escalares)
    return 'corto' if nombrados(_llano(_IDENTIFICADOR.sub(' ', _visible(nuestro)))) else 'editorial'


def _sin_nombres(llano, largos):
    """El texto llano con cada aparición (como palabras) de los nombres `largos` en blanco."""
    for n in largos:
        llano = re.sub(rf'(?<!\w){re.escape(n)}(?!\w)', ' ', llano)
    return llano


def _menciona(eid, entrada, nombres):
    """Si la entrada nombra la entidad `eid`: por su id en el texto, por la estructura que resuelve su cargador o por
    uno de sus nombres como palabra en su texto llano."""
    return _nombra(eid, entrada.texto) or eid in entrada.ids \
        or any(_palabra(nombre, entrada.llano) for nombre in nombres.get(eid, ()))


def copias_corregidas(regla, vivas):
    """[(inicio, fin, props)]: las copias que el grafo publica tras el overlay (`vivas`, de `tras_el_overlay`) y que la
    regla `fix` preparada `regla` alcanza con el predicado del loader en su fase de `set` (`alcance.alcanza_loader`:
    par, tipo corregido, documento, trozo de cita, capas y dirección, la de antes de su swap; spec 071 AC132)."""
    tipo = regla.get('corrected_type') or regla.get('type')
    out = []
    for inicio, fin, props in vivas.get(_par(tipo, regla.get('source_id'), regla.get('target_id')), ()):
        antes = (fin, inicio) if regla.get('swap') and tipo not in SIMETRICOS else (inicio, fin)
        if alcanza_loader(regla, {'type': tipo, 'start': antes[0], 'end': antes[1], 'props': props}, 'set'):
            out.append((inicio, fin, props))
    return out


def cita_publicada(entrada, ocultos, visibles, fragmentos, vivas, nombres):
    """Si lo que una regla `fix` (`entrada.regla`) revela de una retirada anonimizada lo publica ya el grafo en la
    arista que corrige (spec 071 AC132): alguna copia que la regla alcanza tras el overlay como el loader
    (`copias_corregidas`) y cuyos textos publicados tras él (su cita y lo que le escribe el `set` de las reglas) llevan
    lo revelador: cada trozo de una cita de la retirada que lleva la entrada (`fragmentos`, llanos) y cada extremo
    oculto que la entrada nombra, como extremo suyo o en esos textos; sin trozos de cita, también cada extremo visible
    que nombra. Lo que solo dice el motivo de la regla, el grafo no lo publica: tampoco el que su `set` deja en la
    arista (`qa_set_run`, AC158), que la API no sirve."""
    if entrada.regla is None:
        return False
    nombrados = {x for x in ocultos | (set() if fragmentos else visibles) if _menciona(x, entrada, nombres)}
    for inicio, fin, props in copias_corregidas(entrada.regla, vivas):
        # AC221: con las claves de primer nivel, que la API sirve; `qa_set_run`, no (clave y valor).
        texto = '\n'.join(p for k, v in props.items() if k != 'qa_set_run'
                          for p in ((k, v) if isinstance(v, str) else (k,)))
        llano = _llano(texto)
        if all(f in llano for f in fragmentos) and all(
                x in (inicio, fin) or _nombra(x, texto) or any(_palabra(n, llano) for n in nombres.get(x, ()))
                for x in nombrados):
            return True
    return False


def hallazgos(rows, valores, privadas, entradas, vivas, nombres, filas=None, propias=None, cortos=None,
              referencias=None, por_documento=None, contexto=frozenset()):
    """[(etiqueta, clave, Entrada, apartado)]: las entradas públicas (`entradas`, de `referencias` y de
    `entradas_del_export`) que permiten reconstruir la arista retirada de cada fila anonimizada (spec 071 AC122-bis; el
    marcador protege la arista retirada, no el par). Con el censo, el id que la fila oculta es el extremo de su unidad
    (o de su declaración privada, AC121; la de una fila sin unidad, por su id en `filas`, AC134) que su `dato` no
    nombra; lo que el grafo publica es lo que queda tras el overlay (`vivas`, de `tras_el_overlay`). Una fila sin unidad
    ni declaración: 'no_verificable', sin entrada; una cuya clave no da ningún predicado suyo, 'sin_predicado' (AC157),
    y una que no oculta ninguno de sus extremos (los visibles, solo por posición: AC161), 'incoherente'. 'propia': la
    propia fila fuera de su `unidad`, o la regla opaca de su
    unidad (u otra fila de su unidad) fuera de su clave, sus declaraciones y su etiqueta, que nombra al oculto: la
    unidad lo une a la fila (AC143 y AC144).
    'a': la que lo nombra (por id o por nombre) junto al extremo visible y al tipo original de la arista, o junto a una
    cita suya (igual o contenida, llana); con el documento solo, nunca. 'cita' si la entrada es una regla `fix` cuya
    revelación publica ya el grafo en la arista que corrige (`cita_publicada`, AC132); es un aviso.
    'b': si el grafo no publica el par {oculto, visible}, la que nombra los dos. Un ítem de `ultima_hora/feed.yaml`
    ('feed') y un texto curado de contexto o de acusaciones o una escisión del overlay ('editorial') son avisos: los
    decide el dueño. Un texto curado que nombra los dos publica el par, y entonces las demás no son problema.
    Del grafo mismo (D2: texto judicial citado, que no se reescribe): una arista viva con el oculto como extremo y el
    visible en su texto, con el tipo o una cita de la retirada, es un aviso ('grafo'); con la cita sin el visible, nada
    (el lector no tiene la cita retirada: ninguna fila la lleva, 'trozo_cita' si la lleva); y el texto de un nodo que
    nombra los dos es un aviso editorial, como un texto curado. Y si el grafo publica el par, el marcador no protege
    nada: 'par_publicado' (D1), sin buscar más. La entrada de la propia fila es la de su fichero (`propias`, por su
    índice: `filas_propias`; por defecto, las del registro del repo entre `entradas`), y tiene que llevar su id: si no,
    la fila no se puede barrer ('sin_entrada_propia', AC196). AC197: con `cortos` ({entity_id: nombres de una palabra},
    `_nombre_corto`) y las `referencias` privadas de cada clave, una entrada que nombra a un extremo oculto y al
    visible, alguno por un nombre de una palabra (`_comencion_corta`, con los textos de los documentos del export,
    `por_documento`, para las citas literales): 'corto' en un texto nuestro; en prosa curada, en el texto del grafo o
    solo en el de la fuente, 'editorial'. Un nombre de una palabra sin el otro extremo no cuenta. D1 (enmienda 24 bis):
    el par publicado es también el de una relación que carga la capa de contexto del repo (`contexto`, de
    `pares_de_contexto`), aunque el export aún no la lleve."""
    privadas, filas, referencias = preparadas(privadas), preparadas(filas), referencias or {}
    if propias is None:
        propias = filas_propias(entradas, REGISTRO_REL)
    contenedores = _contenedores(nombres, {w for ws in (cortos or {}).values() for w in ws}
                                 | {_llano(w) for ws in referencias.values() for w in ws})
    publicados = {frozenset((inicio, fin)) for copias in vivas.values() for inicio, fin, _ in copias} | set(contexto)
    out = []
    for n, row in enumerate(rows, 1):
        dato = str(row.get('dato') or '') if isinstance(row, dict) else ''
        if not MARCADOR.search(dato):
            continue
        clave, rid = row.get('unidad') or '', _etiqueta(row, n)
        if not clave and rid not in filas:
            out.append((rid, clave, None, 'no_verificable'))
            continue
        # Los predicados privados `ajena` son de otros hechos que se hicieron opacos bajo su clave (AC124): no son
        # suyos. Sin ninguno, la fila no se puede comprobar (AC157).
        predicados = ([regla_de(valores[clave])] if clave in valores else []) \
            + [p for p in privadas.get(clave, ()) if not p.get('ajena')] + [dict(p) for p in filas.get(rid, ())]
        if not predicados:
            out.append((rid, clave, None, 'sin_predicado'))
            continue
        extremos = {x for p in predicados for x in (p.get('source_id'), p.get('target_id')) if x}
        # Lo que la fila publica no lo oculta: los extremos que nombran las posiciones de su `dato` y su `entidad`
        # (AC161), nunca un id que el dato diga en otro sitio. Una fila marcada que no oculta ninguno es incoherente.
        visibles = extremos & visibles_de_fila(row)
        ocultos = extremos - visibles
        if not ocultos:
            out.append((rid, clave, None, 'incoherente'))
            continue
        tipos = {p.get('type') for p in predicados} - {None}
        suya = [(valores[clave].get('props') or {}).get('source_quote')] if clave in valores else []
        citas = {_llano(c) for c in suya + [p.get('quote_contains') for p in predicados]
                 if c and len(_llano(c)) >= CITA_MIN}
        publicado = any(frozenset((o, v)) in publicados for o in ocultos for v in visibles)
        nombrados = extremos_de_fila(dato, valores[clave], row.get('entidad')) if clave in valores else None
        if publicado or (nombrados and nombrados[1] and frozenset(nombrados[0]) in publicados):
            out.append((rid, clave, None, 'par_publicado'))
            continue
        propia = propias.get(n - 1)
        if propia is None or not isinstance(propia.valor, dict) or propia.valor.get('id') != row.get('id'):
            out.append((rid, clave, None, 'sin_entrada_propia'))
            continue
        if trozo_de_cita(row, citas, propia.barrido):
            out.append((rid, clave, None, 'trozo_cita'))
        con_el_par = []
        for e in entradas:
            if e is propia or (clave and e.fichero != 'export' and es_opaca(e.valor)
                               and e.valor.get('unidad') == clave):
                # AC143 y AC144: la propia fila solo deja fuera su `unidad` (no es un id), y la regla opaca de su unidad
                # (u otra fila de su unidad) solo su clave, sus declaraciones y su etiqueta: cualquier otro campo o
                # comentario suyo que nombre al oculto la reconstruye, porque la unidad lo une a la fila, que publica
                # el extremo visible y el tipo.
                # AC212: y sus nombres de una palabra y las referencias privadas de su clave, sin necesitar al visible.
                if any(_menciona(x, e, nombres) for x in ocultos) or (
                        cortos is not None and _menciona_corto(e, ocultos, visibles, nombres, cortos, contenedores,
                                                               referencias.get(clave, ()) if clave else ())):
                    out.append((rid, clave, e, 'propia'))
                continue
            oculto = any(_menciona(x, e, nombres) for x in ocultos)
            visible = oculto and any(_menciona(x, e, nombres) for x in visibles)
            fragmentos = {c for c in citas if c in e.llano} | {x for x in e.citas if any(x in c for c in citas)} \
                if oculto else set()
            if cortos is not None and not visible and not fragmentos:
                corto = _comencion_corta(e, ocultos, visibles, nombres, cortos, contenedores,
                                         referencias.get(clave, ()) if clave else (), por_documento)
                if corto:
                    out.append((rid, clave, e, corto))
                    continue
            if not oculto:
                continue
            if e.fichero == 'export':
                if e.seccion == 'nodo' and visible:
                    con_el_par.append(e)
                elif e.seccion == 'arista' and ocultos & e.ids and visible and (fragmentos or tipos & e.tipos):
                    out.append((rid, clave, e, 'grafo'))
                continue
            if fragmentos or (visible and tipos & e.tipos):
                publica = cita_publicada(e, ocultos, visibles, fragmentos, vivas, nombres)
                out.append((rid, clave, e, 'cita' if publica else 'a'))
            elif visible and not publicado:
                con_el_par.append(e)
        curado = any(Path(e.fichero).parts[0] in CURADOS or e.fichero == 'export' for e in con_el_par)
        for e in con_el_par:
            if e.fichero == 'ultima_hora/feed.yaml':
                out.append((rid, clave, e, 'feed'))
            elif Path(e.fichero).parts[0] in CURADOS or e.fichero == 'export' \
                    or (Path(e.fichero).parts[0] == 'relations' and e.seccion == 'splits'):
                out.append((rid, clave, e, 'editorial'))
            elif not curado:
                out.append((rid, clave, e, 'b'))
    return out


def reconstrucciones(rows, valores, privadas, entradas, vivas, nombres, filas=None, propias=None, cortos=None,
                     referencias=None, por_documento=None, contexto=frozenset()):
    """([(etiqueta, motivo)], [(etiqueta, aviso)]): los `hallazgos` de AC122-bis: problemas los apartados 'a' y 'b', la
    fila con marcador que no se puede comprobar (AC134), la que marca un par que el grafo publica (D1) y la que lleva un
    trozo de su cita retirada (D2); avisos los ítems del feed, los textos curados y los del grafo (con la huella de la
    fila: los decide el dueño) y las reglas `fix` cuya revelación ya publica el grafo (AC132). Y, como en `--tree`, la
    huella de una fila con marcador en cualquier entrada fuera de sus campos estructurales (AC131). Los motivos dicen la
    fila y la posición, nunca el id."""
    problemas, avisos = [], []
    sin_entrada = {'no_verificable': NO_VERIFICABLE, 'par_publicado': MARCADO_PUBLICADO, 'trozo_cita': TROZO_CITA,
                   'sin_predicado': SIN_PREDICADO, 'incoherente': MARCADOR_INCOHERENTE,
                   'sin_entrada_propia': SIN_ENTRADA_PROPIA}
    for rid, clave, e, apartado in hallazgos(rows, valores, privadas, entradas, vivas, nombres, filas, propias, cortos,
                                             referencias, por_documento, contexto):
        if apartado in sin_entrada:
            problemas.append((rid, sin_entrada[apartado]))
        elif apartado in ('feed', 'editorial', 'grafo'):
            aviso = {'feed': FEED_PAR, 'editorial': EDITORIAL, 'grafo': PAR_DESDE_GRAFO}[apartado]
            avisos.append((rid, f'{aviso} ({huella(clave, "?")}, {e.posicion})'))
        elif apartado == 'corto':
            problemas.append((rid, f'{RECONSTRUIBLE_CORTO} ({e.posicion})'))
        elif apartado == 'cita':
            avisos.append((rid, f'{CITA_PUBLICADA} ({e.posicion})'))
        else:
            problemas.append((rid, f'{RECONSTRUIBLE if apartado in ("a", "propia") else PAR_NO_PUBLICADO} '
                                   f'({e.posicion})'))
    marcadas = _marcadas(rows)
    problemas += [(rid, f'{HUELLA} ({e.posicion})') for e in entradas
                  for unidad in sorted(referencias_hex(e.barrido, marcadas)) for rid in marcadas[unidad]]
    return problemas, avisos


def anonimato_con_censo(rows, valores, privadas, repo, snapshot, vivas, nombres, filas=None, registro=None,
                        referencias_privadas=None):
    """([(etiqueta, motivo)], [(etiqueta, aviso)]): `reconstrucciones` sobre las entradas públicas de `repo` y las del
    export `snapshot`, con lo que el grafo publica tras el overlay (`vivas`) y los nombres del export (`nombres`, de
    `nombres_del_export`; se le suman los que resuelven los ficheros públicos). Lo que corren, con el mismo resultado,
    las dos comprobaciones que tienen el censo: `--snapshot` con `--overlay` (en `supervivientes`) y la puerta del
    Path A, `materializacion` (spec 071 AC169, que amplía AC149). Sin el censo (`--tree`) no se sabe qué oculta una
    fila. AC196: las filas son las de `registro` (por defecto, el registro de `repo`), y el barrido de cada una sale de
    ese mismo fichero; si es otro que el del repo, se barren los dos. AC197: con las referencias privadas de cada clave
    (`referencias_privadas`, del fichero privado) y los textos de los documentos del export (las citas literales); D1,
    con los pares que publica la capa de contexto del repo (`pares_de_contexto`, enmienda 24 bis)."""
    nombres = defaultdict(set, {k: set(v) for k, v in (nombres or {}).items()})
    cortos = defaultdict(set, nombres_cortos(snapshot) if snapshot else {})
    aparte = _registro_aparte(registro, repo)
    entradas = referencias(repo, nombres, _crudas_del_registro(registro, REGISTRO_APARTE) if aparte else (), cortos) \
        + (entradas_del_export(snapshot, vivas) if snapshot else [])
    propias = filas_propias(entradas, REGISTRO_APARTE if aparte else REGISTRO_REL)
    return reconstrucciones(rows, valores, privadas, entradas, vivas, nombres, filas, propias, cortos,
                            referencias_privadas, textos_de_documentos(snapshot) if snapshot else {},
                            pares_de_contexto(repo))


class _Mudo:
    """Un registro de eventos que no escribe nada: el de un cargador al que se le pide solo su preparación."""

    def __getattr__(self, _):
        return lambda *a, **k: None


def pares_de_contexto(repo):
    """{frozenset({a, b})}: los pares de las relaciones que carga la capa de contexto de `repo` (`make context`:
    `context_loader.load_files` y `_prepare`, con su tipo, sus extremos resueltos y su `source_url`), aunque el seed
    aún no las lleve: lo que el repo publica es público (spec 071 D1, enmienda 24 bis). El import del cargador, solo si
    algún fichero de la capa declara relaciones; una capa que el cargador no prepara para sin ningún valor."""
    carpeta = Path(repo) / 'pipeline/data/context' if repo is not None else None
    if carpeta is None or not any(isinstance(doc, dict) and doc.get('relations')
                                  for path in sorted(carpeta.glob('*.y*ml')) for doc in [_leer_yaml(path)]):
        return frozenset()
    from src import context_loader
    previo, context_loader.log = context_loader.log, _Mudo()
    try:
        entidades, relaciones, *_ = context_loader.load_files(carpeta)
        _, aristas = context_loader._prepare(entidades, relaciones)
    except SystemExit:
        print('registro-check: la capa de contexto no se prepara (make context aborta)', file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        context_loader.log = previo
    return frozenset(frozenset((a['source_id'], a['target_id'])) for a in aristas)


def _marcadas(rows):
    """{unidad: [etiqueta]}: las filas con marcador por su unidad (las que la llevan con su forma)."""
    out = defaultdict(list)
    for n, row in enumerate(rows, 1):
        if isinstance(row, dict) and MARCADOR.search(str(row.get('dato') or '')) and _forma(_UNIDAD, row.get('unidad')):
            out[row['unidad']].append(_etiqueta(row, n))
    return out


# El registro del repo, y el que se comprueba cuando es otro fichero (`--registro`, AC196): los dos son un registro para
# lo que se deja fuera por su posición (`_estructurales`), y sus entradas no se confunden.
REGISTRO_REL = 'rectificaciones/registro.yaml'
REGISTRO_APARTE = 'rectificaciones/--registro'


def _registro_aparte(registro, repo):
    """Si el registro que se comprueba (`registro`) es otro fichero que el del repo de los YAML públicos (`repo`)."""
    if registro is None:
        return False
    if repo is None:
        return True
    try:
        return Path(registro).resolve() != (Path(repo) / 'pipeline/data' / REGISTRO_REL).resolve()
    except OSError:
        return True


def _crudas_del_registro(registro, rel):
    """Las entradas crudas (las de `entradas_publicas`) del fichero de registro `registro`, con la posición de `rel`;
    vacías si no se lee."""
    try:
        datos = Path(registro).read_bytes()
    except OSError:
        return []
    try:
        texto = datos.decode('utf-8')
    except UnicodeDecodeError:
        texto = datos.decode('latin-1')
    return _entradas_yaml(Path(registro), Path(rel), texto)


def filas_propias(entradas, rel):
    """{índice: entrada}: la entrada de cada fila del registro de posición `rel` (`rel:rectificaciones[n]`) entre
    `entradas`, por su índice (spec 071 AC196): quien la usa comprueba que lleva el id de su fila."""
    patron = re.compile(rf'{re.escape(rel)}:rectificaciones\[(\d+)\]')
    out = {}
    for e in entradas:
        m = patron.fullmatch(e[0])
        if m and e[2] == rel:
            out[int(m.group(1))] = e
    return out


def _filas_barridas(registro):
    """{índice: (id, texto)}: cada fila del registro `registro` (el fichero) entera, como la barre `entradas_publicas`:
    sus líneas con sus comentarios, sus claves y sus valores leídos, sin el valor estructural de su `unidad` (spec 071
    AC164), con el id que lleva (AC196: la fila se vincula a su entrada por su posición y por su id). Vacío si el
    fichero no se lee."""
    return {n: (valor.get('id') if isinstance(valor, dict) else None, e[5])
            for n, e in filas_propias(_crudas_del_registro(registro, REGISTRO_REL), REGISTRO_REL).items()
            for valor in (e[4],)}


def anonimato(rows, repo, registro=None):
    """[(etiqueta, motivo)]: el anonimato del registro sin el censo (spec 071 AC113, AC115 y AC123), lo que corre el CI.
    El marcador solo va en filas de una unidad cuya fila vigente la retira (AC113): la de una unidad cuya arista sigue
    publicada (su fila vigente no es «suprimido») nombra los dos extremos, y lo conserva una fila sin unidad (AC123).
    Una fila anterior de una unidad retirada después también lo conserva: nombrarla publicaría lo retirado. Una
    «rectificado» vigente marcada es la que `privatizar` deja porque el grafo no publica su extremo: espera una decisión
    del dueño. Con los ficheros públicos de `repo`: la huella de una fila con marcador (de 8 a 64 caracteres
    hexadecimales que son prefijo de su unidad: AC131) solo puede ir en los campos estructurales de su regla opaca y de
    sus filas; una regla opaca tiene la forma cerrada `{unidad, declaraciones?, kinds?, reason}` (AC127), solo en una
    sección deny del overlay de relaciones, la respalda una fila con marcador de su misma clave y su motivo es su
    fórmula cerrada (AC130: otro puede nombrar lo que la fila oculta). Una fila anonimizada, entera (`registro`, el
    fichero: sus valores, sus claves y sus comentarios, salvo el valor de su `unidad`; AC164), no nombra otro extremo
    que los de las posiciones de su `dato` y su `entidad` (sin el censo no se sabe si es el oculto: AC143 y AC161; un
    dato sin la forma de la fila de una arista no nombra ninguno) ni la huella de otra fila marcada, y ninguna fila de
    arista nombra el mismo extremo dos veces (AC153). Una entrada deny que no es ni una opaca ni una explícita (otra
    clave, como una variante de `unidad`) es un problema de forma (AC159). Corre igual con `--tree`, con `--snapshot`
    y en `materializacion` (AC149). Los motivos nombran la fila o la posición, nunca un valor."""
    problemas = []
    vigentes_ = vigentes_por_unidad([r for r in rows if isinstance(r, dict)])
    marcadas = _marcadas(rows)
    barridas = _filas_barridas(registro) if registro is not None else {}
    aparte = _registro_aparte(registro, repo) and repo is not None
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        rid = _etiqueta(row, n)
        dato = _DATO_ARISTA.fullmatch(str(row.get('dato') or ''))
        if dato and dato['a'] == dato['b'] and not MARCADOR.search(dato['a']):
            problemas.append((rid, MISMO_EXTREMO))
        if not MARCADOR.search(str(row.get('dato') or '')):
            continue
        posicion = f'{REGISTRO_APARTE if aparte else REGISTRO_REL}:rectificaciones[{n - 1}]'
        su_id, entera = barridas.get(n - 1, (None, None))
        if entera is None or su_id != row.get('id'):
            # AC196: la entrada de su posición no es la suya: lo que dice la fila.
            entera = '\n'.join(f'{k}\n{v}' for k, v in row.items() if k != 'unidad')
        if set(ids_en_texto(entera)) - visibles_de_fila(row):
            problemas.append((rid, f'{EXTREMO_FUERA} ({posicion})'))
        problemas += [(otra, f'{HUELLA} ({posicion})')
                      for unidad in sorted(referencias_hex(entera, marcadas)) for otra in marcadas[unidad]]
        vigente = vigentes_[row['unidad']][1].get('decision') if row.get('unidad') else 'suprimido'
        if vigente == 'rectificado':
            problemas.append((rid, RECTIFICADO_MARCADO))
        elif vigente != 'suprimido':
            problemas.append((rid, 'marcador de anonimato en una fila cuya arista no se retira: la nombra entera '
                                   '(AC113)'))
    if repo is None:
        return list(dict.fromkeys(problemas))
    otras = _crudas_del_registro(registro, REGISTRO_APARTE) if aparte else []
    for posicion, texto, fichero, seccion, valor, barrido, _ in entradas_publicas(repo) + otras:
        if seccion in (FICHERO_NO_VERIFICABLE, ENTRADA_AMBIGUA):
            problemas.append((posicion, seccion))
            continue
        # Solo el overlay de relaciones tiene reglas: la `unidad` de una fila del registro no hace de ella una opaca.
        regla_deny = Path(fichero).parts[0] == 'relations' and seccion == 'deny'
        opaca = Path(fichero).parts[0] == 'relations' and es_opaca(valor)
        # AC176: la forma de una explícita, con la misma función que el loader (claves, campos y valores).
        invalida = forma_deny(valor) if regla_deny and not opaca else None
        if invalida:
            problemas.append((posicion, f'regla deny con forma inválida: {invalida} (AC159)'))
        elif opaca and not regla_deny:
            problemas.append((posicion, 'regla opaca fuera de una sección deny del overlay de relaciones'))
        elif opaca and not (set(valor) <= CAMPOS_OPACA and _forma(_UNIDAD, valor.get('unidad'))
                            and isinstance(valor.get('reason'), str) and declaraciones_validas(valor)
                            and (valor.get('kinds') is None or (isinstance(valor['kinds'], list) and valor['kinds']
                                                                and all(k in RANGO_CAPA for k in valor['kinds'])))):
            problemas.append((posicion, FORMA_OPACA))
        elif opaca and valor['unidad'] not in marcadas:
            problemas.append((posicion, 'regla opaca sin fila anonimizada que la respalde'))
        elif opaca and not motivo_opaco_valido(valor):
            problemas += [(rid, f'{RECONSTRUIBLE} ({posicion}): motivo de regla opaca fuera de su fórmula')
                          for rid in marcadas[valor['unidad']]]
        problemas += [(rid, f'{HUELLA} ({posicion})')
                      for unidad in sorted(referencias_hex(barrido, marcadas)) for rid in marcadas[unidad]]
    # La huella en una fila del registro del repo sale también de su fila entera: una vez.
    return list(dict.fromkeys(problemas))


def _reglas_overlay(repo, raiz=None, registro=None):
    """(deny, fix) del overlay de `repo`, preparadas por el loader (el import pesado solo con --overlay); una deny
    opaca, contra la raíz privada de la auditoría `raiz` (spec 071 AC114 y AC121). Un overlay sin ningún fichero de
    relaciones no tiene reglas que preparar ni pide el import pesado (el de un test, en el entorno de la API): su
    fichero privado se contrasta igual, con ninguna regla opaca (AC171), y una declaración que nada resuelve para
    con exit 2. `registro`: el que se comprueba, con el que se contrasta `filas:` (AC183; por defecto, el del
    overlay)."""
    if repo is None:
        return (), ()
    registro = registro or Path(repo) / 'pipeline/data/rectificaciones/registro.yaml'
    relaciones = Path(repo) / 'pipeline/data/relations'
    if not any(relaciones.glob('*.yaml')) and not any(relaciones.glob('*.yml')):
        if raiz:
            try:
                Opacas(raiz).verificar((), registro)
            except ReglaOpacaInvalida as exc:
                print(f'reglas opacas: {exc}', file=sys.stderr)
                raise SystemExit(2) from None
        return (), ()
    from src.qa_esc_apply import _reglas_del_overlay
    deny, fix = _reglas_del_overlay(repo, raiz, registro)
    return [regla for _, regla in deny], [regla for _, regla in fix]


def _enlaces_overlay(repo):
    """Las re-identificaciones que declara el overlay de `repo` (escisiones y merges, spec 071 AC94), con los entity_id
    que calculan sus cargadores (`qa_esc_apply._reidentificaciones`). El import pesado, solo con --overlay y si el
    overlay declara alguna."""
    if repo is None:
        return frozenset()
    declaradas = (doc.get(clave) for carpeta, clave in (('pipeline/data/relations', 'splits'),
                                                        ('pipeline/data/context', 'merges'))
                  for path in sorted((Path(repo) / carpeta).glob('*.y*ml'))
                  for doc in [_leer_yaml(path)] if isinstance(doc, dict))
    if not any(declaradas):
        return frozenset()
    from src.qa_esc_apply import _reidentificaciones
    return _reidentificaciones(repo)


def _n(problemas):
    return f'{len(problemas)} problema' + ('' if len(problemas) == 1 else 's')


def main(argv=None):
    """El CLI. Ninguna excepción sale con su texto, que puede llevar un valor del registro o del censo: un
    SystemExit pasa con su código y cualquier otra da una línea con su clase y exit 2."""
    try:
        return _main(argv)
    except Exception as exc:
        print(f'registro-check: error interno ({type(exc).__name__})', file=sys.stderr)
        return 2


def _main(argv):
    ap = argparse.ArgumentParser(description='Registro público de rectificaciones: forma y veracidad')
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument('--tree', action='store_true', help='forma de cada fila (lo que corre el CI)')
    modo.add_argument('--snapshot', type=Path, help='export del grafo (nodes.jsonl + relationships.jsonl)')
    ap.add_argument('--root', type=Path, help='raíz privada de la auditoría (census.sqlite + esc/decisions)')
    ap.add_argument('--registro', type=Path, default=REGISTRO, help='registro.yaml (por defecto, el de este repo)')
    ap.add_argument('--overlay', type=Path, help='raíz del repo de los YAML públicos. Con --snapshot, obligatorio '
                    '(AC179): sus reglas deny y fix (relations*.yaml) dicen lo que sobrevive y dejan copias posibles '
                    'de la retirada (AC51; las prepara el loader, así que pide el entorno del pipeline), el fichero '
                    'privado se contrasta con ellas (AC171) y ninguno puede de-anonimizar una fila (AC122); con '
                    '--tree, el overlay del anonimato (AC115; por defecto, el de este repo si el registro es el suyo)')
    args = ap.parse_args(argv)
    if args.snapshot and not args.root:
        ap.error('--snapshot exige --root')
    if args.snapshot and not args.overlay:
        # AC179: sin el overlay no se cargan las reglas deny ni se contrasta el fichero privado: lo que la puerta
        # certificaría no sería lo que el loader aplica.
        ap.error('--snapshot exige --overlay')

    rows = cargar(args.registro)
    if rows is None:
        print(f'registro-check: {args.registro.name} no tiene la lista `rectificaciones:`')
        return 1
    problemas = estructura(rows)
    if args.tree:
        # AC115 y AC123: el anonimato del registro frente al overlay, sin el censo (lo que corre el CI).
        problemas += anonimato(rows, args.overlay or (REPO if args.registro == REGISTRO else None), args.registro)
    if args.snapshot and not problemas:
        census = args.root / 'census.sqlite'
        if not census.is_file() or not (args.root / 'esc/decisions').is_dir():
            print(f'registro-check: falta el censo de la auditoría en {args.root} (census.sqlite y esc/decisions/); '
                  'la puerta del registro lo necesita: REGISTRO_ROOT=<raíz de la auditoría>')
            return 2
        if not all((args.snapshot / name).is_file() for name in ('nodes.jsonl', 'relationships.jsonl')):
            print(f'registro-check: {args.snapshot} no es un export del grafo (nodes.jsonl + relationships.jsonl)')
            return 2
        try:
            vivo = export_vivo(args.snapshot)
        except ExportInvalido as exc:
            print(f'registro-check: el export no vale ({exc})')
            return 2
        try:
            opacas = Opacas(args.root)
            privadas, filas, referencias_privadas = preparadas(opacas.privadas), preparadas(opacas.filas), \
                opacas.referencias
        except ReglaOpacaInvalida as exc:
            print(f'registro-check: las reglas opacas no se resuelven ({exc})', file=sys.stderr)
            return 2
        deny, fix = _reglas_overlay(args.overlay, args.root, args.registro)
        enlaces = _enlaces_overlay(args.overlay)
        # Lo que el grafo publica tras el overlay (AC122-bis y AC125) y los nombres de sus entidades (AC122-bis).
        tras_overlay = tras_el_overlay(vivo[0], deny, fix)
        nombres = nombres_del_export(args.snapshot) if args.overlay else {}
        conn = sqlite3.connect(census.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            comprobadas, problemas, avisos = supervivientes(rows, conn, args.root, *vivo, reglas=fix, deny=deny,
                                                            overlay=[*deny, *fix] if args.overlay else None,
                                                            enlaces=enlaces, privadas=privadas, repo=args.overlay,
                                                            tras_overlay=tras_overlay, nombres=nombres,
                                                            snapshot=args.snapshot, filas=filas,
                                                            registro=args.registro,
                                                            referencias_privadas=referencias_privadas)
            # AC199: la identidad anterior de un nodo cuya identificación se retira, en ningún sitio.
            retiradas, avisos_identidad = identidades_retiradas(rows, conn, args.overlay, args.snapshot)
            problemas += retiradas
            avisos += avisos_identidad
        finally:
            conn.close()
        # AC149: la forma del anonimato (marcadores, reglas opacas y huellas) también con --snapshot; lo que ya dice el
        # contraste con el grafo, una vez.
        problemas += [p for p in anonimato(rows, args.overlay or (REPO if args.registro == REGISTRO else None),
                                           args.registro) if p not in problemas]
        for rid, motivo in problemas:
            print(f'REGISTRO {rid}: {motivo}')
        for rid, aviso in avisos:
            print(f'registro-check: aviso {rid}: {aviso}')
        print(f'registro-check: {comprobadas["suprimido"]} aristas «suprimidas» y {comprobadas["rectificado"]} '
              f'«rectificadas» contra {args.snapshot.name}; {_n(problemas)}'
              + (f'; {len(avisos)} aviso' + ('' if len(avisos) == 1 else 's') if avisos else ''))
        return 1 if problemas else 0
    for rid, motivo in problemas:
        print(f'REGISTRO {rid}: {motivo}')
    print(f'registro-check: {len(rows)} filas; {_n(problemas)} de forma'
          + ('; snapshot sin comprobar' if args.snapshot and problemas else ''))
    return 1 if problemas else 0


if __name__ == '__main__':
    sys.exit(main())
