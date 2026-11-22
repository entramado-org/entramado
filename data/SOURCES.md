# Procedencia del corpus documental (`data/*.pdf`)

Censo documento a documento del corpus: 85 documentos en 90 ficheros PDF (el
informe UCO 96/2025 viaja además troceado en 5 ficheros locales) — qué es cada
uno, de dónde procede y dónde está publicado. Censo realizado el **2026-08-03**
y ampliado el **2026-08-10** con la causa 20557/2024, la calificación de la
20775/2020 y los diarios del Senado, y el **2026-09-05** con 31 autos del
CENDOJ de la causa 20775/2020 (sección 2);
método: identificación por lectura de cabecera/portada de cada PDF local +
localización de la copia pública, verificando la coincidencia por hash MD5
(byte a byte) o por cotejo de texto cuando el alojador re-firma la descarga.

Última revisión del recuento: **2026-10-05**.

Notas generales:

- Las resoluciones judiciales y los demás actos oficiales **no son objeto de
  propiedad intelectual** (art. 13 de la Ley de Propiedad Intelectual). Se
  reproducen aquí como documentación de interés público; el tratamiento de los
  datos personales que contienen se rige por lo descrito en el
  [aviso legal](https://entramado.org/aviso-legal) del sitio
  (rectificación/supresión: contacto@entramado.org).
- Muchas resoluciones «en bruto» llevan **Código Seguro de Verificación (CSV)**:
  su autenticidad puede comprobarse en la sede judicial electrónica
  (`administraciondejusticia.gob.es`), con independencia de quién aloje la copia.
- «Sin copia pública localizada» significa que a fecha del censo no se encontró
  el PDF íntegro alojado en ninguna fuente oficial ni de prensa — no que el
  documento sea secreto: en todos los casos su contenido fue objeto de cobertura
  informativa amplia.
- El censo cubre el corpus completo (85 documentos), pero **no todo el corpus se
  redistribuye en este repositorio**: el árbol público incluye 71 de los 78
  documentos de las secciones 1-3, un PDF cada uno; los informes de sumario
  (sección 4), una edición de periódico (sección 5) y siete resoluciones de la
  sección 3 con identificadores de personas físicas — 14 documentos en 19
  ficheros — se censan sin incluirse en él; el motivo se explica en cada sección.

## 1 · Publicaciones oficiales (verificadas byte a byte contra su fuente)

| Fichero | Documento | Fecha | Publicado en |
|---|---|---|---|
| `DS_C_15_103.pdf` | DS Senado, Com. investigación «caso Koldo» (Op. Delorme) — comparecencia de José Luis Ábalos | 06-05-2024 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_103.PDF) |
| `DS_C_15_233.pdf` | DS Senado, Com. Koldo — Sánchez Fuentefría y Gómez García (Mº Transportes) | 14-11-2024 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_233.PDF) |
| `DS_C_15_659.pdf` | DS Senado, Com. investigación SEPI — Bartolomé Lora (vicepresidente SEPI) | 15-04-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_659.PDF) |
| `DS_C_15_717.pdf` | DS Senado, Com. SEPI — Pedro Martín Molina | 28-05-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_717.PDF) |
| `DS_C_15_727.pdf` | DS Senado, Com. Interior — directora general de la Guardia Civil (caso Leire) | 16-06-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_727.PDF) |
| `DS_C_15_744.pdf` | DS Senado, Com. SEPI — Alcázar Jiménez y Gómez Martín | 29-06-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_744.PDF) |
| `DS_C_15_745.pdf` | DS Senado, Com. Koldo — Alzórriz y Grande-Marlaska | 30-06-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_745.PDF) |
| `DS_C_15_754.pdf` | DS Senado, Com. SEPI (extraordinaria) — Sara Aagesen | 08-07-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_754.PDF) |
| `DS_C_15_755.pdf` | DS Senado, Com. SEPI — Soledad Fernández Doctor (ex directora general de la AEAT) | 13-07-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_755.PDF) |
| `DS_C_15_756.pdf` | DS Senado, Com. Koldo — Manuel Llamas (director adjunto operativo de la Guardia Civil) | 17-07-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_756.PDF) |
| `DS_C_15_758.pdf` | DS Senado, Com. Koldo — Cristina Narbona (presidenta del PSOE) | 22-07-2026 | [senado.es](https://www.senado.es/legis15/publicaciones/pdf/senado/ds/DS_C_15_758.PDF) |
| `DSCD-15-CI-8.pdf` | DS Congreso, Com. investigación material sanitario covid — IB-Salut / Illes Balears | 27-05-2024 | [congreso.es](https://www.congreso.es/public_oficiales/L15/CONG/DS/CI/DSCD-15-CI-8.PDF) |
| `20240819_informe_audit_ejec_om_adquis_mascarillas_an.pdf` | Informe de auditoría de la DG de Organización e Inspección (Mº Transportes) sobre las órdenes TMA/263/2020 y TMA/292/2020 (mascarillas) | 19-08-2024 | [transportes.gob.es](https://www.transportes.gob.es/recursos_mfom/paginabasica/recursos/20240819_informe_audit_ejec_om_adquis_mascarillas_an.pdf) |

## 2 · Resoluciones del Tribunal Supremo publicadas por el CENDOJ

Los 42 `ATS_*.pdf`/`AATS_*.pdf` son autos de la Sala Segunda del TS en la **causa especial
20775/2020** (Ábalos/Koldo/Cerdán), en su **versión anonimizada oficial del
CENDOJ** (los seudónimos tipo «Segismundo» o «Benito» son de la anonimización
del CENDOJ, no un error del corpus). Cada PDF lleva en cabecera su ROJ, ECLI e
Id Cendoj; se localizan en el [buscador de jurisprudencia del
CGPJ](https://www.poderjudicial.es/search/) (que no ofrece enlaces profundos
estables). Reutilización conforme a las condiciones del CENDOJ.

| Fichero | ECLI | Fecha | Contenido |
|---|---|---|---|
| `ATS_15039_2024.pdf` | ECLI:ES:TS:2024:15039A | 11-12-2024 | Incoación; personación de acusaciones populares |
| `ATS_7320_2025.pdf` | ECLI:ES:TS:2025:7320A | 29-07-2025 | Deniega secuestro cautelar de audios filtrados |
| `ATS_8611_2025.pdf` | ECLI:ES:TS:2025:8611A | 18-09-2025 | Deniega libertad de investigado en prisión provisional |
| `ATS_8919_2025.pdf` | ECLI:ES:TS:2025:8919A | 23-09-2025 | Relato de antecedentes de la instrucción |
| `ATS_9905_2025.pdf` | ECLI:ES:TS:2025:9905A | 15-10-2025 | Mantiene medidas cautelares (existe copia de prensa no anonimizada: [Vocento](https://s2.ppllstatics.com/rc/www/multimedia/2025/10/15/autoabalos.pdf)) |
| `ATS_9928_2025.pdf` | ECLI:ES:TS:2025:9928A | 31-10-2025 | Testificales de personal del PSOE sobre pagos en efectivo |
| `ATS_10280_2025.pdf` | ECLI:ES:TS:2025:10280A | 03-11-2025 | Continuación por trámites del procedimiento abreviado |
| `ATS_10788_2025.pdf` | ECLI:ES:TS:2025:10788A | 27-11-2025 | Prisión provisional de aforado (copia de prensa no anonimizada: [El Confidencial](https://static.ecestaticos.com/file/0b7/eaf/00f/0b7eaf00f883aa20d704780fe048e1a0.pdf)) |
| `ATS_10789_2025.pdf` | ECLI:ES:TS:2025:10789A | 27-11-2025 | Prisión provisional (segundo investigado, mismo día) |
| `ATS_11406_2025.pdf` | ECLI:ES:TS:2025:11406A | 10-12-2025 | Desestima apelación sobre personación (auto nº 22491/2025) |
| `ATS_242_2026.pdf` | ECLI:ES:TS:2026:242A | 15-01-2026 | Mandamientos bancarios a investigados (Acciona/Erkolan) |
| `ATS_3155_2021.pdf` | ECLI:ES:TS:2021:3155A | 03-03-2021 | Inadmite la denuncia inicial contra el ministro (mascarillas) y archiva; declara la competencia sobre el aforado |
| `ATS_6254_2021.pdf` | ECLI:ES:TS:2021:6254A | 10-05-2021 | Desestima súplica contra el archivo de 03-03-2021 |
| `ATS_13325_2024.pdf` | ECLI:ES:TS:2024:13325A | 05-11-2024 | Reapertura de la causa especial y competencia sobre el aforado (versión CENDOJ de `auto-causa-especial-aforado.pdf`) |
| `AATS_13839_2024.pdf` | ECLI:ES:TS:2024:13839A | 12-11-2024 | Auto aclaratorio del ATS 13325/2024 (errores materiales) |
| `ATS_2606_2025.pdf` | ECLI:ES:TS:2025:2606A | 06-03-2025 | Desestima apelación de la acusación popular sobre grabaciones del aeropuerto (DP 341/2020) |
| `ATS_2608_2025.pdf` | ECLI:ES:TS:2025:2608A | 06-03-2025 | Confirma la unificación de las acusaciones populares |
| `ATS_5540_2025.pdf` | ECLI:ES:TS:2025:5540A | 06-03-2025 | Desestima apelación y nulidad contra el suplicatorio al Congreso |
| `ATS_5934_2025.pdf` | ECLI:ES:TS:2025:5934A | 30-06-2025 | Auto del instructor de prisión provisional (versión CENDOJ de `auto-prision-cerdan-2025-06-30.pdf`) |
| `ATS_7081_2025.pdf` | ECLI:ES:TS:2025:7081A | 16-07-2025 | Desestima apelaciones contra las diligencias del instructor de 23-04-2025 (informe sobre adjudicación de obras) |
| `ATS_7107_2025.pdf` | ECLI:ES:TS:2025:7107A | 23-07-2025 | Desestima apelación contra la prisión provisional de 30-06-2025 |
| `ATS_9744_2025.pdf` | ECLI:ES:TS:2025:9744A | 13-10-2025 | Confirma medidas cautelares personales a un investigado; relato indiciario de obra pública (A-68, A-32) |
| `ATS_11327_2025.pdf` | ECLI:ES:TS:2025:11327A | 02-12-2025 | No ha lugar a la personación de un solicitante |
| `ATS_11788_2025.pdf` | ECLI:ES:TS:2025:11788A | 10-12-2025 | Desestima apelaciones contra el auto de procedimiento abreviado de 03-11-2025 (auto nº 22493/2025) |
| `ATS_207_2026.pdf` | ECLI:ES:TS:2026:207A | 08-01-2026 | Desestima apelación por denegación de diligencias y acceso a material procesal |
| `ATS_280_2026.pdf` | ECLI:ES:TS:2026:280A | 15-01-2026 | Confirma la denegación de devolución de dispositivos informáticos intervenidos |
| `ATS_246_2026.pdf` | ECLI:ES:TS:2026:246A | 16-01-2026 | Confirma la negativa a comunicar al Congreso todas las resoluciones de la causa |
| `ATS_248_2026.pdf` | ECLI:ES:TS:2026:248A | 16-01-2026 | Desestima apelaciones contra la prisión provisional (27-11-2025) y la apertura de juicio oral (11-12-2025) |
| `ATS_287_2026.pdf` | ECLI:ES:TS:2026:287A | 16-01-2026 | No procede expulsar a los partidos políticos personados como acusación popular |
| `ATS_288_2026.pdf` | ECLI:ES:TS:2026:288A | 16-01-2026 | Confirma la denegación de diligencias de investigación |
| `ATS_253_2026.pdf` | ECLI:ES:TS:2026:253A | 19-01-2026 | Desestima las apelaciones del acusado aforado contra su prisión provisional; transcribe los escritos de acusación |
| `ATS_1640_2026.pdf` | ECLI:ES:TS:2026:1640A | 06-02-2026 | Confirma el acceso de la UCO a las evidencias digitales para la pieza del JCI nº 2 (pagos en metálico del PSOE) |
| `ATS_2566_2026.pdf` | ECLI:ES:TS:2026:2566A | 03-03-2026 | Pleno de siete: desestima las cuestiones previas y resuelve la admisión de prueba para el juicio oral |
| `ATS_3218_2026.pdf` | ECLI:ES:TS:2026:3218A | 24-03-2026 | Deniega la libertad de dos acusados en vísperas del juicio oral |
| `ATS_3257_2026.pdf` | ECLI:ES:TS:2026:3257A | 26-03-2026 | Testimonio por informe escrito de la presidenta del Congreso y de un ministro (art. 703 LECrim) |
| `ATS_3258_2026.pdf` | ECLI:ES:TS:2026:3258A | 26-03-2026 | Estima súplica: sin efecto el ofrecimiento de acciones a INECO y TRAGSATEC |
| `ATS_4262_2026.pdf` | ECLI:ES:TS:2026:4262A | 21-04-2026 | Desestima súplica sobre espacios en el centro penitenciario para preparar la defensa |
| `ATS_4512_2026.pdf` | ECLI:ES:TS:2026:4512A | 07-05-2026 | Deniega la libertad solicitada al terminar el juicio oral |
| `ATS_5556_2026.pdf` | ECLI:ES:TS:2026:5556A | 27-05-2026 | Desestima súplica contra la denegación de libertad de 07-05-2026 |
| `AATS_6465_2026.pdf` | ECLI:ES:TS:2026:6465A | 24-06-2026 | Auto aclaratorio de la STS 418/2026: corrige un cruce de seudónimos («Borja»→«Augusto») |
| `AATS_7235_2026.pdf` | ECLI:ES:TS:2026:7235A | 07-07-2026 | Deniega aclaración/complemento de la STS 418/2026 |
| `AATS_7236_2026.pdf` | ECLI:ES:TS:2026:7236A | 17-07-2026 | Deniega aclaración/subsanación/complemento de la STS 418/2026 |

Las 31 resoluciones añadidas el 05-09-2026 completan las 43 que el buscador del CENDOJ devuelve para la
causa (la STS 2553/2026 es la versión anonimizada de la sentencia 418/2026, ya en corpus por su copia de prensa,
y no se duplica). Son documentos anonimizados: de ellos no se extraen personas al grafo, solo entidades públicas
(tribunales, Fiscalía, organismos, causas, delitos y hitos procesales); las personas quedan en el texto buscable.

## 3 · Resoluciones y escritos difundidos por la prensa (no publicados por el órgano emisor)

Resoluciones judiciales «en bruto» (notificaciones con CSV, sin anonimizar) y
escritos de parte cuyo texto íntegro circuló a través de medios de comunicación.
El Poder Judicial no publica estos documentos como tales (solo notas de prensa);
la copia del corpus coincide con la alojada por el medio indicado.

> **Siete ficheros de esta sección no se incluyen en el árbol público del
> repositorio** (escaneo del 2026-10-05 con las reglas de redacción de
> `api/app/redact.py`, con OCR para los PDF sin capa de texto):
> `aldama-movil.pdf`, `2024-10-23-Exposicion-razonada-Abalos.pdf`,
> `auto-peinado-juicio-oral-begona-2026-06-20.pdf`, `auto-zapatero.pdf`,
> `auto-zapatero-joyas-2026-06-12.pdf`,
> `sentencia-ts-abalos-koldo-aldama-2026-06-22.pdf` y
> `sentencia-ts-garcia-ortiz-fiscal-general.pdf`. Al no estar anonimizados por
> el órgano emisor, contienen DNI, teléfonos o domicilios de personas físicas;
> el sitio redacta esos datos al servir el texto troceado, pero un PDF íntegro
> no permite redactarlos. Mismo régimen que la sección 4: se censan, se enlaza
> su copia pública cuando existe y el grafo conserva cada hecho con documento y
> página. Los demás ficheros de la sección solo contienen datos de contacto de
> órganos judiciales, sedes oficiales o despachos profesionales.

| Fichero | Documento | Fecha | Copia pública |
|---|---|---|---|
| `2024-10-23-Exposicion-razonada-Abalos.pdf` | Exposición razonada del JCI nº 2 (AN) al TS sobre Ábalos (DP 65/2023) | 23-10-2024 | [El Independiente](https://www.elindependiente.com/wp-content/uploads/2024/10/2024-10-23-Exposicion-razonada-Abalos.pdf) — texto idéntico |
| `ts-cita-abalos.pdf` | Auto TS: incoación y citación voluntaria de Ábalos; cita a Koldo García y Aldama | 02-12-2024 | Sin copia pública localizada |
| `auto-causa-especial-aforado.pdf` | Auto TS: reapertura de la causa especial respecto de Ábalos | 05-11-2024 | Sin copia pública localizada |
| `levanta-secreto-santos-cerdan.pdf` | Auto TS: levanta el secreto de la pieza separada (registros del 9-10 jun 2025) | 12-06-2025 | Sin copia pública localizada |
| `auto-prision-cerdan-2025-06-30.pdf` | Auto TS: prisión provisional de Santos Cerdán | 30-06-2025 | Difundido por prensa (El Confidencial); URL exacta no relocalizada en el censo |
| `EN0E47_3.pdf` | Notificación LexNet del auto anterior (prisión de Cerdán) a procurador de parte personada | 30-06-2025 | [fuentesinformadas.com](https://fuentesinformadas.com/wp-content/uploads/2025/06/EN0E47_3.pdf) — MD5 idéntico |
| `sentencia-ts-abalos-koldo-aldama-2026-06-22.pdf` | Sentencia TS 418/2026 (Ábalos, Koldo, Aldama) | 22-06-2026 | [ARA](https://static1.ara.cat/ara/public/content/file/original/2026/0622/12/c-e-3-20775-20-stc.pdf) y [The Objective](https://theobjective.com/wp-content/uploads/2026/06/sentecia-ts-abalos-koldo-aldama.pdf) — misma resolución (difieren solo en el CSV de descarga) |
| `aldama-movil.pdf` | Providencia TS sobre el volcado del móvil de Aldama + escrito de personación | 05-12-2024 | Sin copia pública localizada |
| `calificacion-fiscal-abalos-aldama-garcia.pdf` | Escrito de calificación del Fiscal (conclusiones provisionales, causa especial 20775/2020: Ábalos, Koldo García y Aldama) | nov-2025 | [fuentesinformadas.com](https://fuentesinformadas.com/wp-content/uploads/2025/11/Calificacion-Abalos.Aldama-Garcia.pdf) |
| `auto-ts-garcia-ortiz-2025-01-13.pdf` | Auto del instructor Hurtado (causa especial 20557/2024): extiende la investigación a Diego Villafañe y cita a los investigados | 13-01-2025 | [El Español](https://s1.elespanol.com/2025/01/13/actualidad/auto_130125_-002.pdf) |
| `auto-ts-garcia-ortiz-2025-07-29.pdf` | Auto TS nº 21797/2025 (trámites posteriores nº 44, ponente De Porres; causa 20557/2024) con voto particular de Palomo del Arco | 29-07-2025 | [fuentesinformadas.com](https://fuentesinformadas.com/wp-content/uploads/2025/07/CAUSA_ESPECIAL_20557-24-044.pdf) |
| `sentencia-ts-garcia-ortiz-fiscal-general.pdf` | Sentencia TS 1000/2025 (causa especial 20557/2024, fiscal general García Ortiz), 238 pp. — **incluye los votos particulares** de Polo y Ferrer (pp. 185-238; el PDF suelto del voto que circuló es un duplicado íntegro de esas páginas y no se censa aparte) | 09-12-2025 | [aelpa.org](https://aelpa.org/web-2018/wp-content/uploads/2025/12/sentencia-fiscal-garcia-ortiz.pdf) |
| `auto-begona-incoacion-2024-04-16.pdf` | Auto de incoación DP 1146/2024 (J. Instr. 41 Madrid, caso Begoña Gómez); decretó secreto inicial | 16-04-2024 | Sin copia pública localizada |
| `20250110-auto-desestima-recursos-reforma-pdf.pdf` | Auto J. Instr. 41: desestima en bloque recursos de reforma (la fecha real es 08-01-2025) | 08-01-2025 | Sin copia pública localizada |
| `auto-begona-jurado-2025-09-23.pdf` | Auto: transformación a procedimiento del Tribunal del Jurado | 23-09-2025 | [COPE](https://www.cope.es/uploads/files/2025/09/24/20250924Auto-transformando-el-procedimiento-Tribunal-del-Jurado.pdf) |
| `AUTO-IMPUTACION-BEGONA-GOMEZ-11.04.2026.pdf` | Auto: mantiene imputación de Gómez, Barrabés y Álvarez pese a la petición fiscal de sobreseimiento | 11-04-2026 | [fuentesinformadas.com](https://fuentesinformadas.com/wp-content/uploads/2026/04/AUTO-IMPUTACION-BEGONA-GOMEZ-11.04.2026.pdf) |
| `auto-peinado-juicio-oral-begona-2026-06-20.pdf` | Auto de apertura de juicio oral (Tribunal del Jurado 1146/2024) | 20-06-2026 | [Demócrata](https://www.democrata.es/uploads/s1/27/67/79/0/auto-apertura-del-juicio-oral-juez-peinado.pdf) |
| `auto-peinado-juicio-oral-begona-2026-09-21.pdf` | Auto de apertura de juicio oral tras el auto AP 655/2026 (Tribunal del Jurado 1146/2024): Begoña Gómez (tráfico de influencias y malversación) y Cristina Álvarez (malversación) | 21-09-2026 | Copia verificable por CSV 1296335874013465898025 en [gestiona.comunidad.madrid/csv](https://gestiona.comunidad.madrid/csv) |
| `auto-zapatero.pdf` | Auto + cédula (Tribunal Central de Instancia, DP 77/2024-S): cita a Zapatero como investigado y autoriza registros | 18-05-2026 | [Artículo 14](https://sta.articulo14.es/main-files/uploads/2026/05/Auto-imputacion-juez-Calama-Zapatero.pdf-1_compressed.pdf) |
| `auto-zapatero-joyas-2026-06-12.pdf` | Auto de la pieza separada sobre joyas/efectos incautados | 12-06-2026 | Solo se localizó el anexo pericial de tasación ([The Objective](https://theobjective.com/wp-content/uploads/2026/06/joyas-zapatero-tasacion.pdf)); el auto en sí, sin copia pública localizada |
| `autoentradaferraz.pdf` | Auto (DP 150/2025, caso Leire): requerimiento documental al PSOE + autorización subsidiaria de registro en Ferraz | 26-05-2026 | [Demócrata](https://www.democrata.es/uploads/s1/26/22/37/0/autoentradaferraz.pdf) |
| `sentencia-ap-badajoz-david-sanchez-gallardo-2026-07-14.pdf` | Sentencia 152/2026 AP Badajoz (David Sánchez, Gallardo y otros) | 14-07-2026 | [cita.es](https://cita.es/sentencia/david-sanchez-condenado.pdf) — texto idéntico con portada añadida por el alojador |
| `ESCRITO-DE-ACUSACION_fdo.pdf` | Escrito de acusación de la acusación popular unificada (PA 43/25, caso Diputación de Badajoz) — escrito de parte, no resolución | 05-2025 | [iniciativa2028.es](https://iniciativa2028.es/wp-content/uploads/2025/05/ESCRITO-DE-ACUSACION_fdo.pdf) (portal afín a las acusaciones) |

## 4 · Informes policiales y administrativos incorporados a las causas

Informes de la Guardia Civil (UCO), de la Policía Nacional (UDEF) y de la AEAT
aportados a procedimientos en instrucción. No tienen publicación oficial; su
texto íntegro circuló a través de medios.

> **Estos ficheros no se incluyen en el árbol público del repositorio**
> (decisión editorial 2026-08-03): no son resoluciones judiciales sino piezas
> de sumario difundidas por filtración, con gran densidad de datos personales
> de terceros. El sitio sirve su contenido troceado y con cita en
> [/documentos](https://entramado.org/documentos) — que es donde este proyecto
> ejerce la cobertura informativa (art. 85 RGPD) —, y el grafo conserva cada
> hecho extraído con documento y página. Se censan aquí por transparencia; las
> copias públicas enlazadas son las de los medios que los difundieron.

| Fichero(s) | Documento | Fecha | Copia pública |
|---|---|---|---|
| `informe-uco.pdf` + troceos locales `informe-uco-cerdan-{1-100,101-200,201-300,301-400,401-490}.pdf` | Informe UCO nº 96/2025 «presuntas irregularidades en contratación pública» (490 pp., causa especial 20775/2020 — el «informe UCO sobre Cerdán») | 05-06-2025 | [Heraldo de León](https://www.heraldodeleon.es/media/heraldodeleon/files/2025/06/15/Informe+UCO_compressed-comprimido.pdf) — portada cotejada |
| `informe-udef-1907.pdf` | Informe UDEF-BBCA nº 1907/26 (DP 77/2024, Plus Ultra — avance de investigación) | 22-04-2026 | [fuentesinformadas.com](https://fuentesinformadas.com/wp-content/uploads/2026/05/21-informe-1907-UDEF-BBCA-de-22-04-2026-AVANCE-II-1.pdf) |
| `informe-udef-1908.pdf` | Informe UDEF-BBCA nº 1908/26 (DP 77/2024 — análisis de comunicaciones, 155 pp.) | 22-04-2026 | [fuentesinformadas.com](https://fuentesinformadas.com/wp-content/uploads/2026/05/UDEF-Zapatero-1.pdf) |
| `informe-udef-2910-focus-social.pdf` | Informe UDEF-BBCA nº 2910/26 (DP 77/2024 — Grupo Gloria/SOBOCE/Focus Social) | 22-06-2026 | [prnoticias.com](https://prnoticias.com/wp-content/uploads/2026/06/24-06-26-INFORME-UDEF-2910-26-FOCUS-SOCIAL.pdf.pdf) |
| `informe_guardia_civil.pdf` | Atestado UCO nº 95/2024 (DP 1146/2024, caso Begoña Gómez — «TOMO I») | 14-05-2024 | Sin copia pública localizada |
| `Informe-InstruccionN3.pdf` | Informe AEAT al J. Instr. nº 3 de Badajoz (residencia fiscal, caso David Sánchez) | 2024 (post 18-06) | Sin copia pública localizada |

## 5 · Otros

| Fichero | Documento | Fecha | Nota |
|---|---|---|---|
| `2025-04-29-badajoz-1-pdf-2.pdf` | Edición del diario **La Crónica de Badajoz** (nº 5.056) cuya página 13 recoge el procesamiento de David Sánchez y Gallardo | 29-04-2025 | Es una publicación de prensa (obra protegida de su editor), no un documento judicial. Ingresó al corpus por esa página; único documento del corpus en esta situación. **No se incluye en el árbol público del repositorio** (obra de su editor); su contenido troceado se sirve con cita en /documentos. |
