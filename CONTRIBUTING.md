# Contribuir a El Entramado

Gracias por querer mejorar el proyecto. Este repositorio alimenta
[entramado.org](https://entramado.org): un grafo de conocimiento sobre los casos
de corrupción que rodean al Gobierno en España, construido desde documentos
judiciales y oficiales públicos. Aquí las reglas no son burocracia: son la
diferencia entre documentar y difamar.

La participación en el proyecto se rige por el
[código de conducta](./CODE_OF_CONDUCT.md).

## Antes de nada: lo que escribes en GitHub es público y permanente

Los issues, los pull requests, sus diffs, sus comentarios y lo que adjuntes
(URLs, capturas, logs) se publican al instante, antes de que nadie los revise, y
pueden copiarse. **Cerrar un pull request no elimina su contenido**: borrarlo
requiere la intervención de GitHub y no retira las copias. Por eso las
contribuciones externas sobre personas —propuestas de casos, correcciones de
hechos, estados procesales, identidades— **se reciben exclusivamente por
correo**: **contacto@entramado.org**. El mantenedor revisa las fuentes en
privado y publica el cambio una vez aprobado, incluido todo el contenido de su
diff.

En issues y pull requests externos, nunca escribas acusaciones, nombres de
particulares ni datos personales, ni siquiera para señalar un error: describe el
problema sin repetirlo («la ficha X cita una página equivocada»).

## Qué se puede contribuir, y por dónde

**Por correo (contacto@entramado.org):**

- **Rectificaciones** de algo publicado sobre ti o sobre un tercero. Tienen
  prioridad sobre cualquier otra contribución (ver el
  [aviso legal](https://entramado.org/aviso-legal)).
- **Correcciones de contenido con fuente**: errores de hecho, estados
  procesales desactualizados, atribuciones de medio, homónimos mal fusionados.
- **Propuestas de tramas nuevas** (ver abajo).

**Plazos de respuesta del correo:**

- **Acuse de recibo**: en un día hábil como máximo.
- **Rectificaciones y supresiones sobre personas**: pasan por delante de todo lo
  demás; el objetivo es resolverlas, o explicar por qué no proceden, en tres
  días hábiles como máximo.
- **Otras peticiones**: respuesta en un mes como máximo.

**Por GitHub (issues técnicos y pull requests):**

- **Código** (pipeline, API, web, MCP) y documentación.
- **Errores técnicos**: enlaces rotos, fallos de la web o de la API, problemas
  de formato en las capas YAML.

**No:**

- Escritura directa al grafo Neo4j. El grafo se regenera desde un seed
  versionado; toda corrección de contenido pasa por las capas YAML curadas
  (overlays) que los loaders re-aplican. Un cambio hecho «a mano» en el grafo no
  existe.
- Aportaciones sin fuente verificable, análisis especulativos o atribuciones de
  intención. Este proyecto documenta lo que dicen las fuentes, no lo que
  «se sabe».
- Datos de deliberación interna o candidatos de cruce descartados: solo se
  versiona material necesario para el proyecto y aprobado para publicación,
  nunca deliberaciones, candidatos descartados ni datos personales innecesarios.

## Qué contribuciones interesan más

1. **Correcciones con fuente** sobre lo ya publicado.
2. **Tramas nuevas con documentos oficiales que aún no están en el grafo.**
   Así entró la causa del fiscal general del Estado (sentencia 1000/2025 del
   Tribunal Supremo), ingerida en agosto de 2026.
3. **Código y herramientas.**

El alcance declarado son los casos que rodean al Gobierno. Las propuestas de
otras tramas documentadas de fiscalización del poder —de cualquier
administración, partido o época— se reciben y se valoran, pero no se garantiza
su incorporación: dependen del alcance editorial, de que haya documentos
oficiales y de la capacidad de mantenerlas al día.

### Proponer una trama nueva

La ingesta la ejecutan los mantenedores (es un proceso editorial con extracción
verificada, gates de calidad y revisión humana, no un script que se lanza sobre
un PDF), pero el **trabajo de campo es la contribución que más acelera**.
Revisa antes [`BACKLOG.md`](./BACKLOG.md): puede que la trama ya esté en cola.
Si no está, envía por correo:

1. **Censo de documentos**: qué resoluciones o informes oficiales existen
   (sentencias, autos, diarios de sesiones, informes de fiscalización), con URL
   de origen y quién los publica, en el formato de `data/SOURCES.md`. Documentos
   oficiales publicados, no recortes de prensa.
2. **Por qué encaja**: qué poder fiscaliza (qué administración, qué nivel) y
   qué aporta al grafo que no esté ya.

No adjuntes datos personales innecesarios ni documentos sin revisar. El reparto
de personas y su estado procesal lo establece el mantenedor desde los propios
documentos.

## Reglas editoriales innegociables

1. **Presunción de inocencia.** El estado procesal se etiqueta con exactitud
   (investigado / imputado / procesado / acusado / condenado —firme o no— /
   absuelto / sobreseído) y nunca se agrava: «imputado» no es «condenado»,
   una petición de la acusación no es una pena. Los desarrollos exculpatorios
   (archivo, absolución, la Fiscalía retirando acusación) se incorporan con la
   misma diligencia que los incriminatorios.
2. **Procedencia obligatoria.** Toda relación lleva su `source_kind` y su
   evidencia: cita textual con documento y página para lo judicial; `outlet` +
   URL para prensa; identificador de registro (BORME/BOE/BDNS/PLACSP) para lo
   registral. Sin fuente, no entra.
3. **Atribución a fuente primaria.** El contenido de agencia se atribuye a la
   agencia («EFE (vía Infobae)»); las exclusivas, a su medio de origen con la
   URL original. No se atribuye una revelación a un medio que la fuente enlazada
   no nombra. Las citas entrecomilladas son literales del cuerpo de la fuente
   (un titular no es una cita).
4. **Gate anti-homónimo.** Nunca se casa una persona o empresa con un registro
   por similitud de nombre: se exigen ≥2 señales independientes (NIF si existe,
   provincia, fechas compatibles, vínculo documentado con el caso). En caso de
   duda, fuera. Usar un NIF para descartar homónimos no autoriza a publicarlo.
5. **Datos personales: solo los pertinentes.** Se publican únicamente los datos
   personales necesarios para la finalidad editorial (quién es quién en una
   causa y su estado procesal). No se introducen DNI, teléfonos, correos
   personales, domicilios particulares, cuentas bancarias ni matrículas de
   personas físicas, tampoco citando documentos que los contengan: se enmascara
   el dato y se conserva la referencia (documento y página). Los documentos de
   terceros del corpus se incorporan tras revisar su publicación y los datos que
   contienen; cuando corresponde, se censa solo su referencia sin redistribuir
   el PDF. Los datos de
   registros públicos (cargos BOE, administradores BORME, domicilios sociales)
   se valoran por su pertinencia: que sean públicos no implica que se publiquen
   (un domicilio social puede ser una vivienda). Dos redes lo comprueban: el
   escáner de todo el árbol (`pipeline/scripts/pii_tree_scan.py`), bloqueante
   en el CI, caza los identificadores con forma (DNI, teléfono, IBAN, correo
   personal aunque esté ofuscado…); y antes
   de cada regeneración del grafo el mantenedor pasa un barrido semántico local
   (`make pii-semantico`), que pregunta a un modelo de TypeSafe, entrada a
   entrada de todos los ficheros versionados de datos, por lo que no tiene forma
   (una vivienda sin número, familiares, salud, un seudónimo reidentificable) y
   deja para revisar a mano lo que no esté ya revisado. La lista de entradas ya
   revisadas y los resultados de cada pasada se guardan fuera del repo.
6. **Registros ≠ indicios.** Un dato administrativo (subvención, contrato,
   nombramiento, cargo societario) es estructura, no imputación: la presencia en
   un registro no imputa y la ausencia no exculpa.
7. **Documentos anonimizados no generan personas.** Las resoluciones que el
   CENDOJ publica con seudónimos («Alejo», «Benito», «Samuel»…) sustituyen
   nombres reales que la fuente no da y que cambian de una resolución a otra. De
   ellas se extraen únicamente entidades públicas (tribunales, Fiscalía,
   organismos, empresas, causas, delitos, hitos procesales) y sus relaciones; las
   personas quedan en el texto buscable del documento. Un nodo marcado
   `is_pseudonym` nunca se fusiona ni recibe nombre civil, aunque la prensa lo
   identifique (el cargador `make context` lo verifica y falla si ocurre). Un
   seudónimo solo conserva las relaciones con sociedades u organismos y los alias
   que figuran en `pipeline/data/relations/seudonimos_permitidos.yaml`; todo lo
   demás se retira al construir el grafo, sin listar lo retirado en ningún fichero
   público, y las retiradas constan en el registro público de rectificaciones con
   los extremos neutralizados. No se aceptan reglas `deny`/`alias_deny` sobre un
   seudónimo (`pipeline/scripts/seudonimos_check.py`, bloqueante en el CI).

## Mecánica de un pull request

1. Abre el PR con la plantilla y respeta su aviso: nada de acusaciones, nombres
   de particulares ni datos personales en el título, la descripción o el diff.
2. Indica qué comprobaciones has podido ejecutar. Las que no necesitan base de
   datos:
   - feed: `cd api && uv run python -c "from app.ultima_hora_feed import load_feed; load_feed()"`
   - patrones: `cd api && uv run python -m app.patrones`
   - tests: `PYTHONPATH=.:pipeline uv run --project pipeline --with pytest pytest -q pipeline/tests`
     y `cd api && PYTHONPATH=. uv run --with pytest pytest -q tests`
   - registro de rectificaciones (forma del registro público):
     `uv run --project api python pipeline/scripts/registro_check.py --tree`

   Las validaciones que requieren Neo4j las ejecuta el mantenedor en un entorno
   aislado, nunca contra producción.
3. Un mantenedor revisa contra las fuentes antes de fusionar. `main` está
   protegida: solo entra por pull request con el check `data-and-tests` en verde
   (validación del feed y de los patrones, tests de la API, escáner de datos
   personales del árbol y comprobación de seudónimos; los tests del pipeline se
   ejecutan en local porque dependen de macOS). No hay segundo
   revisor: con un único mantenedor, sus propios pull requests llevan su
   revisión editorial previa y el CI. Hay dos rutas de publicación:
   - **feed de última hora y patrones**: el merge a `main` activa el despliegue
     y el cambio está en producción en unos 16 minutos;
   - **grafo** (relaciones, contexto, acusaciones, seudónimos): además del
     merge, el mantenedor regenera el seed en un entorno aislado, pasa los gates
     (entre ellos `make pii-semantico`) y lo carga. El merge por sí solo no
     cambia el grafo.

**Pruebas de navegador.** La web tiene una suite de humo con Playwright
(`web/e2e/`; escritorio y móvil de 390 px, con el Chrome del sistema) que se
ejecuta contra el build de producción, no contra `next dev`. Con la API y el
grafo de desarrollo en marcha (`./dev-machine.sh up`), en `web/`:
`npm run build:e2e && npm run start:e2e` sirve la web en `http://localhost:3100`
y, en otra terminal, `npm run test:e2e` lanza la suite. Tras un despliegue se
ejecuta contra producción con `E2E_BASE_URL=https://entramado.org npm run test:e2e`.
El CI construye la web en cada pull request (job `web-build`: tipos y build),
pero no ejecuta esta suite porque necesita la API y el grafo.

## Seguridad

Las vulnerabilidades o los secretos expuestos se comunican en privado, nunca en
un issue: ver [`SECURITY.md`](./SECURITY.md).

## Licencias de las contribuciones

Al contribuir declaras que tienes derecho a aportar lo que envías y aceptas que
tu aportación se publique bajo las licencias del proyecto:
[AGPL-3.0](./LICENSE) para código y [CC BY 4.0](./LICENSE-DATA) para las capas
de datos editoriales. Estas licencias no cubren el material de terceros (por
ejemplo, los PDF oficiales del corpus) ni autorizan cualquier tratamiento de los
datos personales que aparezcan en él.
