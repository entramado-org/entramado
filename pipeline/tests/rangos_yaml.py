"""Oráculo de los rangos de las entradas de un YAML público (spec 071 AC237), independiente de `registro_check`.

Un documento de prueba se escribe con marcas que dicen de quién es cada trozo de texto; el oráculo no recorre tokens
para saberlo, lo sabe quien lo escribió:

- `⟦o⟧` donde empieza la apertura de la raíz (el `---` del primer documento, el `{`, el `[`, el ancla o la etiqueta
  que preceden a su primera clave o elemento): va con la primera unidad de la raíz; también lo que sigue a `⟦d⟧`.
- `⟦s⟧` donde empieza una sección (la clave o el `?`; en una raíz de flujo que es una lista, el `[`; en una que es un
  mapa, la clave de cada par, que están en líneas distintas); `⟦r⟧`, al principio de un documento cuya raíz es un
  escalar (es una sola unidad desde su primera línea, comentarios de cabecera incluidos); `⟦e⟧` donde empieza un
  elemento de una lista de bloque (su `-`); `⟦d⟧` donde empieza el documento siguiente (sus directivas o su `---`).
  Una sección va de su marca a la siguiente `⟦s⟧` o `⟦d⟧`; un elemento, de la suya a la siguiente `⟦e⟧`, `⟦s⟧` o
  `⟦d⟧`, y el primero lleva además la sección entera hasta su guion (clave y comentarios entre uno y otro). Una sección
  sin `⟦e⟧` (escalar, mapa, alias, lista de flujo o vacía) es una sola unidad.
- `⟦A:nombre⟧` justo antes del `&nombre` y, si el ancla no acaba donde acaba lo que la contiene, `⟦/nombre⟧` donde
  empieza lo que le sigue. Un alias `*nombre` suma a la unidad que lo contiene todo el rango lógico de su ancla (del
  `&` hasta lo que le sigue, con sus comentarios de cabecera y de cola), y el de los alias que haya dentro de ella.

Con el escaneo de PyYAML se enumeran los huecos entre cada par de tokens consecutivos y en cada uno se inserta un
comentario (a continuación del token, o en una línea propia antes del siguiente). La propiedad, para cada extremo
visible `Person:vNN-fictez` del documento: el comentario y el extremo comparten alguna entrada de `_entradas_yaml` si y
solo si algún elemento (o sección, si no es una lista de bloque) los lleva a la vez según las marcas. Los dos sentidos:
el primero cierra la pérdida de un comentario de cabecera o de cola; el segundo, la fabricación de un par con el
comentario de la sección vecina. Los huecos cuyo YAML con el comentario no compone o construye otros datos se descartan,
contados por tipo de par de tokens."""
import itertools
import random
import re

import yaml

COMENTARIO = '# Terciano'
SEPARADORES = {'LF': '\n', 'CR': '\r', 'CRLF': '\r\n', 'NEL': '\x85', 'LS': ' ', 'PS': ' '}
_SALTO = re.compile(r'\r\n|[\r\n\x85  ]')
_MARCA = re.compile('⟦([^⟧]*)⟧')
_ALIAS = re.compile(r'(?<![\w&*])\*([A-Za-z]\w*)')
_VISIBLE = re.compile(r'Person:v\d\d-fictez')
_ATAJO = re.compile('§(\\d\\d?)')


def con_visibles(dsl):
    """`§7` → `Person:v07-fictez`: un extremo visible distinto por cada unidad."""
    return _ATAJO.sub(lambda m: f'Person:v{int(m[1]):02d}-fictez', dsl)


def _todos_visibles(texto, marcas):
    """(texto, marcas): el texto con CADA escalar (clave o valor, en un ancla o en su uso, en la raíz o en un elemento)
    cambiado por un extremo visible distinto `Person:vNN-fictez`, y las marcas desplazadas. Con un comentario en cada
    hueco, comprobar a la vez todos los extremos es el producto visible × hueco (AC239): la entrada que lleva a uno
    no depende de lo que valgan los demás escalares. En un escalar de bloque se cambia el cuerpo; `<<` no es un
    escalar que se pueda cambiar."""
    cambios, n = [], 0
    for token in yaml.scan(texto, Loader=yaml.SafeLoader):
        if not isinstance(token, yaml.ScalarToken) or (token.value == '<<' and token.style is None):
            continue
        a, b = token.start_mark.index, token.end_mark.index
        n += 1
        extremo = f'Person:v{n:02d}-fictez'
        if token.style in ('|', '>'):
            salto = _SALTO.search(texto, a, b)
            if not salto:
                n -= 1
                continue
            cuerpo = texto[salto.end():b]
            sangria = re.match(r'[ ]*', next((x for x in _SALTO.split(cuerpo) if x.strip()), ''))[0]
            final = cuerpo[len(cuerpo.rstrip('\r\n\x85\u2028\u2029')):]
            cambios.append((salto.end(), b, sangria + extremo + final))
        else:
            cambios.append((a, b, extremo))
    out, previo = [], 0
    for a, b, nuevo in cambios:
        out.append(texto[previo:a] + nuevo)
        previo = b
    out.append(texto[previo:])
    return ''.join(out), [(o + sum(len(nuevo) - (b - a) for a, b, nuevo in cambios if b <= o), k) for o, k in marcas]


class Documento:
    """El texto de un documento de prueba (sin marcas, con el separador de línea `sep`) y de quién es cada trozo. Con
    `todos` (por defecto), cada escalar es un extremo visible distinto (`_todos_visibles`)."""

    def __init__(self, dsl, sep='\n', todos=True):
        crudo = con_visibles(dsl).replace('\n', sep)
        partes, marcas, n, previo = [], [], 0, 0
        for m in _MARCA.finditer(crudo):
            partes.append(crudo[previo:m.start()])
            n += m.start() - previo
            marcas.append((n, m[1]))
            previo = m.end()
        partes.append(crudo[previo:])
        texto = ''.join(partes)
        if todos:
            texto, marcas = _todos_visibles(texto, marcas)
        self.texto, self.sep = texto, sep
        final = len(self.texto)
        cortes = sorted({a for a, k in marcas if k in ('s', 'e', 'd', 'r')} | {final})
        secciones, actual = [], None
        for a, k in sorted(marcas):
            if k in ('s', 'r'):
                actual = {'ini': a, 'elementos': [], 'raiz': k == 'r'}
                secciones.append(actual)
            elif k == 'e':
                if actual is None:
                    actual = {'ini': None, 'elementos': [], 'raiz': False}
                    secciones.append(actual)
                actual['elementos'].append(a)
            elif k == 'd':
                actual = None
        fin_seccion = {}
        for s in secciones:
            desde = s['ini'] if s['ini'] is not None else s['elementos'][0]
            fin_seccion[id(s)] = min([x for x, k in marcas if k in ('s', 'r', 'd') and x > desde] + [final])
        # Cada unidad: sus tramos y, si es un elemento de una lista de bloque, `clave`: el tramo de la clave de su
        # sección, que heredan todos sus elementos (la clave, y lo que expande si es un alias).
        self.unidades = []
        for s in secciones:
            fin = fin_seccion[id(s)]
            if not s['elementos']:
                # La raíz escalar del primer documento incluye el desplazamiento 0, el de sus comentarios de cabecera:
                # `(-1, b)` admite `q == 0`. Un comentario en una línea propia justo antes del `---` del siguiente es la
                # cola del anterior.
                self.unidades.append({'tramos': [(-1 if s['raiz'] and s['ini'] == 0 else s['ini'], fin)], 'clave': []})
                continue
            clave = [(s['ini'], s['elementos'][0])] if s['ini'] is not None else []
            for i, a in enumerate(s['elementos']):
                b = s['elementos'][i + 1] if i + 1 < len(s['elementos']) else fin
                self.unidades.append({'tramos': [(a, b)] + (clave if i == 0 else []), 'clave': clave})
        for u in self.unidades:
            u['inicio'] = min(x for x, _ in u['tramos'])
        # La apertura de la raíz (el `---` y las directivas del documento, el `{`, el `[`, el ancla, la etiqueta y su
        # comentario) va con la primera unidad de la raíz.
        for a, k in marcas:
            if k in ('o', 'd'):
                siguientes = [u for u in self.unidades if u['inicio'] >= a]
                if siguientes:
                    primera = min(siguientes, key=lambda u: u['inicio'])
                    primera['tramos'].append((a, primera['inicio']))
        self.anclas = []  # (nombre, desde, hasta): un nombre puede repetirse (en otro documento, o redefinido)
        for a, k in marcas:
            if not k.startswith('A:'):
                continue
            nombre = k[2:]
            cierres = [x for x, y in marcas if y == '/' + nombre and x > a]
            if cierres:
                fin = min(cierres)
            else:
                enlazada = next((s for s in secciones if s['elementos'] and s['ini'] is not None
                                 and s['ini'] <= a < s['elementos'][0]), None)
                fin = fin_seccion[id(enlazada)] if enlazada else min(c for c in cortes if c > a)
            self.anclas.append((nombre, a, fin))
        self.alias = [(m.start(), m[1]) for m in _ALIAS.finditer(self.texto)]
        self.visibles = {}
        for m in _VISIBLE.finditer(self.texto):
            self.visibles.setdefault(m[0], []).append(m.start())
        # Lo que lleva cada unidad: para los comentarios, sus tramos y lo que expanden sus alias (también los de su
        # clave); para los extremos, además el texto de su clave.
        # Una entrada lleva líneas enteras: un extremo que comparte línea con el tramo (la clave `base` junto al `&b` de
        # su ancla) va con él.
        saltos = [0] + [m.end() for m in _SALTO.finditer(self.texto)] + [final]
        self.lleva = []
        for u in self.unidades:
            tramos = self.contenido(u['tramos'] + self.contenido(u['clave'])[len(u['clave']):])
            lineas = []
            for a, b in tramos + u['clave']:
                a, b = max(a, 0), b
                while a < b and self.texto[a] in ' \t\r\n\x85\u2028\u2029':
                    a += 1
                while b > a and self.texto[b - 1] in ' \t\r\n\x85\u2028\u2029':
                    b -= 1
                if a < b:
                    lineas.append((max(x for x in saltos if x <= a), min(x for x in saltos if x >= b)))
            self.lleva.append((tramos, {v for v, offs in self.visibles.items()
                                        if any(a <= o < b for o in offs for a, b in lineas)}))

    def contenido(self, spans):
        """Los tramos de una unidad más el rango lógico de la ancla de cada alias que haya en ellos, y los de los alias
        que haya dentro de estas (fijo: hasta que no entra ninguna más)."""
        out, vistos, entra = list(spans), set(), True
        while entra:
            entra = False
            for o, nombre in self.alias:
                # Un alias es el de la última ancla de su nombre que le precede.
                previas = [x for x in self.anclas if x[0] == nombre and x[1] < o]
                if previas and max(previas, key=lambda x: x[1]) not in vistos and any(a <= o < b for a, b in out):
                    ancla = max(previas, key=lambda x: x[1])
                    vistos.add(ancla)
                    out.append(ancla[1:])
                    entra = True
        return out

    def esperados(self, q):
        """{extremo visible}: los que comparten alguna unidad con un comentario insertado en el desplazamiento `q`
        (el comentario es de quien lleva el carácter anterior)."""
        out = set()
        for tramos, extremos in self.lleva:
            if any(a < q <= b for a, b in tramos):
                out |= extremos
        return out


def huecos(texto):
    """[(token anterior, token siguiente, fin del anterior, inicio del siguiente)] de cada par de tokens del escaneo de
    PyYAML con una posición distinta (los tokens sin anchura, como el fin de un bloque, comparten la del real)."""
    tokens = list(yaml.scan(texto, Loader=yaml.SafeLoader))
    vistos, out = set(), []
    for a, b in zip(tokens, tokens[1:]):
        p, s = a.end_mark.index, b.start_mark.index
        if (p, s) not in vistos:
            vistos.add((p, s))
            out.append((type(a).__name__, type(b).__name__, p, s))
    return out


def variantes(texto, sep, p, s):
    """[(forma, desplazamiento del comentario, texto con él)] para el hueco (p, s): a continuación del token anterior y,
    si el siguiente está en otra línea, en una línea propia al empezar el hueco y antes del siguiente (sin sangría y con
    la suya). La sangría del siguiente token se conserva: si lo que sigue iba en la misma línea, pasa a la siguiente."""
    saltos = [m.end() for m in _SALTO.finditer(texto, 0, s)]
    linea = max([x for x in saltos if x <= s] or [0])
    if _SALTO.search(texto, p, s):
        out, vistos = [('a continuación', p, texto[:p] + ' ' + COMENTARIO + texto[p:])], set()
        primera = min(x for x in saltos if x > p)
        for forma, q, sangria in (('propia, tras el token', primera, ''), ('propia, sin sangría', linea, ''),
                                  ('propia, con sangría', linea, ' ' * (s - linea))):
            nuevo = texto[:q] + sangria + COMENTARIO + sep + texto[q:]
            if q <= s and nuevo not in vistos:
                vistos.add(nuevo)
                out.append((forma, q, nuevo))
        return out
    columna = s - linea
    return [('a continuación', p, texto[:p] + ' ' + COMENTARIO + sep + ' ' * columna + texto[s:])]


def comprobar(dsl, entradas, sep='\n', descartes=None, formas=('a continuación', 'propia, tras el token',
                                                              'propia, sin sangría', 'propia, con sangría'),
              primero=False):
    """[(descripción, texto)]: las violaciones de la propiedad para el documento `dsl` con el separador `sep`.
    `entradas(texto)` devuelve las entradas de `_entradas_yaml`; `descartes` (un Counter) cuenta los huecos que no
    valen, por tipo de par de tokens, y `descartes['valen']` los que sí; con `primero`, para en la primera violación."""
    doc = Documento(dsl, sep)
    datos = list(yaml.safe_load_all(doc.texto))
    fallos = []
    for anterior, siguiente, p, s in huecos(doc.texto):
        for forma, q, texto in variantes(doc.texto, sep, p, s):
            if forma not in formas:
                continue
            try:
                igual = list(yaml.safe_load_all(texto)) == datos
            except yaml.YAMLError:
                igual = False
            if not igual:
                if descartes is not None:
                    descartes[f'{anterior}→{siguiente}'] += 1
                continue
            if descartes is not None:
                descartes['valen'] += 1
            vistas = entradas(texto)
            observados = {v for e in vistas if ':línea ' not in e[0] and COMENTARIO in e[1] for v in doc.visibles
                          if v in e[1]}
            esperados = doc.esperados(q)
            if observados != esperados:
                sobra, falta = sorted(observados - esperados), sorted(esperados - observados)
                fallos.append((f'{anterior}→{siguiente} {forma}: de más {sobra}, de menos {falta}', texto))
                if primero:
                    return fallos
    return fallos


def _texto_de(token, texto):
    """El texto que un token de `yaml.scan` aporta a una entrada, o None: el valor de un escalar, el ancla y el alias
    con su sigla, y la etiqueta y la directiva tal como se escriben."""
    if isinstance(token, yaml.ScalarToken):
        return token.value.strip() or None
    if isinstance(token, yaml.AnchorToken):
        return '&' + token.value
    if isinstance(token, yaml.AliasToken):
        return '*' + token.value
    if isinstance(token, (yaml.TagToken, yaml.DirectiveToken)):
        return texto[token.start_mark.index:token.end_mark.index]
    return None


def cobertura(texto, entradas):
    """[descripción]: lo que `entradas(texto)` no conserva del texto (AC241 y AC242): todo token con texto de
    `yaml.scan` (directiva, etiqueta, ancla, alias, escalar) y todo comentario está en el texto de alguna entrada, y en
    el texto propio de alguna: en un `Propio.escalares`, o en el texto entero de una entrada sin él (una línea suelta,
    un documento escalar o vacío). Lo cubierto se deriva de las entradas, no de los trozos que se reparten."""
    vistas = entradas(texto)
    enteros = '\n'.join(e[1] for e in vistas)
    propios = [x for e in vistas if e[6] is not None for x in e[6].escalares] + [e[1] for e in vistas if e[6] is None]
    faltan = []
    for token in yaml.scan(texto, Loader=yaml.SafeLoader):
        dicho = _texto_de(token, texto)
        if dicho is None:
            continue
        if dicho not in enteros:
            faltan.append(f'{type(token).__name__} {dicho!r} no está en ninguna entrada')
        elif not any(dicho in x for x in propios):
            faltan.append(f'{type(token).__name__} {dicho!r} no está en el texto propio de ninguna')
    for comentario in re.findall(r'#[^\r\n\x85\u2028\u2029]*Terciano[^\r\n\x85\u2028\u2029]*', texto):
        if not any(comentario in x for x in propios):
            faltan.append(f'el comentario {comentario!r} no está en el texto propio de ninguna')
    return faltan


def comprobar_cobertura(dsl, entradas, sep='\n', primero=False):
    """[(descripción, texto)]: `cobertura` del documento `dsl` y de cada variante con un comentario en un hueco entre
    tokens (los mismos huecos que `comprobar`)."""
    doc = Documento(dsl, sep)
    fallos = [(f, doc.texto) for f in cobertura(doc.texto, entradas)]
    for anterior, siguiente, p, s in huecos(doc.texto):
        for forma, q, texto in variantes(doc.texto, sep, p, s):
            try:
                yaml.safe_load_all(texto)
                list(yaml.safe_load_all(texto))
            except yaml.YAMLError:
                continue
            for f in cobertura(texto, entradas):
                fallos.append((f'{anterior}→{siguiente} {forma}: {f}', texto))
                if primero:
                    return fallos
    return fallos


CLASES_DE_COMILLAS = (('«', '»'), ('“', '”'), ('"', '"'))


def comprobar_citas(modulo, semilla=7, n=400):
    """[(descripción, texto, tramos que da, tramos que se esperan)]: la propiedad de la cita (AC242). Para textos
    con 0..4 pares de comillas de cada clase, con relleno entre ellos que el documento también dice, saltos de línea
    dentro, pares vacíos y adyacentes y una comilla que abre y no cierra al final, los tramos que
    `modulo.tramos_citados` da como cita son EXACTAMENTE los pares completos (con sus comillas): el texto entre dos
    pares no lo es aunque el documento lo diga. Una comilla suelta (AC250: la que abre y no cierra al final, una
    anidación, una recta de más) anula la exención de TODO el escalar: no da ninguno."""
    azar = random.Random(semilla)
    fallos = []
    textos = []
    for k in range(n):
        piezas, esperados, dicho, pos = [], [], [], 0
        for i in range(azar.randint(0, 4)):
            relleno = azar.choice(['', ' ', f' relleno número {k}-{i} sin cita ', f'\nrelleno número {k}-{i} aparte\n'])
            interior = azar.choice(['', f'una declaración larga {k}-{i} del documento',
                                    f'una declaración {k}-{i}\nque cruza un salto de línea', f'otra cita {k}-{i} más'])
            abre, cierra = azar.choice(CLASES_DE_COMILLAS)
            par = abre + interior + cierra
            piezas += [relleno, par]
            dicho += [relleno, interior]
            pos += len(relleno)
            if interior:
                esperados.append((pos, pos + len(par)))
            pos += len(par)
        if azar.random() < .4:
            suelta = azar.choice(['«', '“', '"'])
            piezas.append(f' {suelta}una cita que no cierra {k}')
            dicho.append(f'una cita que no cierra {k}')
        texto = ''.join(piezas)
        if not bien_formada(texto):
            esperados = []  # AC250: una comilla suelta, delante o detrás de un par, anula la exención de todo
        textos.append((texto, esperados, ' '.join(dicho)))
    # Anidadas y mezcladas: ambiguas, propias.
    for texto in ('«uno "dos tres cuatro cinco seis" siete»', '“uno «dos tres cuatro cinco seis» siete”',
                  '"uno «dos tres cuatro cinco seis» siete"'):
        textos.append((texto, [], texto[1:-1]))
    # El caso de Codex: un salto de línea dentro de la primera cita y un texto del documento entre las dos.
    codex = '"Una declaración con\nsalto de línea" Terciano salió sin declarar "Otra declaración"'
    textos.append((codex, [(0, codex.index('línea"') + 6), (codex.index('"Otra'), len(codex))],
                   'Una declaración con salto de línea Terciano salió sin declarar Otra declaración'))
    for texto, esperados, dicho in textos:
        documentos = {'d': modulo._llano(' '.join(dicho.split()))}
        dados = modulo.tramos_citados(texto, {'d'}, documentos)
        if sorted(dados) != sorted(esperados):
            fallos.append((f'{len(esperados)} pares', texto, sorted(dados), sorted(esperados)))
    return fallos


ALFABETO_DE_COMILLAS = '«»“”"x'
_PAREJA_DE_COMILLAS = re.compile('«»|“”|""|.', re.S)


def modelo_de_pares(texto):
    """[(inicio, fin)]: el modelo de referencia de la exención de las comillas (AC244, AC246 y AC250), deliberadamente
    simple e independiente de `registro_check`: se queda con las comillas de `texto` (con su posición) y las lee de
    izquierda a derecha en parejas contiguas `«»`, `“”` y `""`; lo que no es una pareja de esas es una comilla suelta.
    TODO O NADA: si todas las comillas forman pareja, se eximen todas las parejas; con una sola suelta, ninguna.

    Lo que comparan este modelo y el código NO es la prueba de seguridad: los dos pueden compartir una lectura (la de
    la e33, «con rectas impares la suelta es la última», la compartían y el defecto de 32a no se veía). La prueba de
    seguridad es `comprobar_eximido`, que no usa el modelo para decidir lo eximido: mira lo que `tramos_citados` exime
    de verdad ante la edición de una comilla."""
    posiciones = [i for i, c in enumerate(texto) if c in '«»“”"']
    solo_comillas = ''.join(texto[i] for i in posiciones)
    parejas = _PAREJA_DE_COMILLAS.findall(solo_comillas)
    if any(len(p) == 1 for p in parejas):
        return []
    return [(posiciones[2 * k], posiciones[2 * k + 1] + 1) for k in range(len(parejas))]


def cadenas_de_comillas(largo_maximo=7, aleatorias=3000, semilla=11):
    """Todas las cadenas de hasta `largo_maximo` símbolos de `ALFABETO_DE_COMILLAS` (la `x` es relleno) y `aleatorias`
    cadenas de hasta 40 con saltos de línea, vacías y adyacentes."""
    todas, nivel = [''], ['']
    for _ in range(largo_maximo):
        nivel = [t + c for t in nivel for c in ALFABETO_DE_COMILLAS]
        todas += nivel
    azar = random.Random(semilla)
    for _ in range(aleatorias):
        todas.append(''.join(azar.choice(ALFABETO_DE_COMILLAS + '\n  ') for _ in range(azar.randint(0, 40))))
    return todas


def bien_formada(texto):
    """Si todas las comillas de `texto` forman una pareja contigua `«»`, `“”` o `""` (ninguna suelta), según el modelo:
    un texto entrecomillado como lo escribió quien lo escribió."""
    solo_comillas = ''.join(c for c in texto if c in '«»“”"')
    return all(len(m.group()) == 2 for m in _PAREJA_DE_COMILLAS.finditer(solo_comillas))


def ediciones_de_comillas(texto):
    """[(descripción, texto editado, posición del editado en `texto` o None)]: cada edición de UNA comilla: borrar
    cada una de las que tiene (la apertura o el cierre perdidos) e insertar cada clase de comilla suelta en cada hueco
    (una sobrante)."""
    for p, c in enumerate(texto):
        if c in '«»“”"':
            yield (f'borrar la comilla {c} de la posición {p}', texto[:p] + texto[p + 1:],
                   lambda j, p=p: j if j < p else j + 1)
    for g in range(len(texto) + 1):
        for c in '«»“”"':
            yield (f'insertar {c} en la posición {g}', texto[:g] + c + texto[g:],
                   lambda j, g=g: j if j < g else (None if j == g else j - 1))


def textos_entrecomillados(largo_maximo=7, aleatorios=3000, semilla=13):
    """Los textos bien formados de hasta `largo_maximo` símbolos del alfabeto (todos) y `aleatorios` de varias parejas
    de cada clase con relleno, saltos de línea, vacíos y adyacentes."""
    azar = random.Random(semilla)
    out = [t for t in cadenas_de_comillas(largo_maximo, 0) if bien_formada(t)]
    for _ in range(aleatorios):
        piezas = []
        for _ in range(azar.randint(0, 5)):
            abre, cierra = azar.choice(CLASES_DE_COMILLAS)
            piezas += [azar.choice(['', ' ', 'x', 'relleno\ndel texto', ' y ']),
                       abre + azar.choice(['', 'x', 'una cita\ncon salto', ' y ']) + cierra]
        out.append(''.join(piezas) + azar.choice(['', 'x']))
    return out


def comprobar_monotonia(pares, **opciones):
    """[(texto, edición, texto editado, posición eximida de más)]: la monotonía de las comillas (AC246) sobre los PARES
    sintácticos que da `pares`, independiente del modelo. NO es la prueba de seguridad desde AC250: un par más estrecho
    puede eximir más (`comprobar_eximido` mira lo que `tramos_citados` exime de verdad). Para todo texto bien formado
    (`textos_entrecomillados`) y cada edición de UNA comilla (`ediciones_de_comillas`: la apertura o el cierre
    perdidos, o una sobrante), lo que `pares` (`[(inicio, fin, interior)]` de un texto) exime del texto editado, llevado
    a las posiciones del original, está contenido en lo que exime del original: una comilla perdida o sobrante nunca
    expone un carácter que era texto propio. Se exige del texto bien formado y no de cualquiera: una edición puede
    REPARAR un texto mal entrecomillado (insertar el `»` que faltaba) y entonces exime lo que antes no, que es lo que
    se espera de una reparación."""
    fallos = []
    for texto in textos_entrecomillados(**opciones):
        eximido = {k for a, b, _ in pares(texto) for k in range(a, b)}
        for descripcion, editado, mapa in ediciones_de_comillas(texto):
            de_mas = sorted({mapa(k) for a, b, _ in pares(editado) for k in range(a, b)} - {None} - eximido)
            if de_mas:
                fallos.append((texto, descripcion, editado, de_mas))
                break
    return fallos


# AC250: la fuente de la propiedad sobre lo EFECTIVAMENTE eximido. Dos rellenos que forman citas fieles (están en el
# documento y pasan de `CITA_MIN`) y uno que no (el documento no lo dice), más las cinco comillas: un tramo entre
# comillas se exime o no según su relleno, de modo que una comilla de más que parte «F y N» en «F» y N» cambia lo
# eximido de verdad.
DOCUMENTO_DE_CITAS = ('Terciano salió sin declarar nada de lo que le pidieron. Una declaración anterior. '
                      'Otra declaración posterior.')
RELLENOS_DE_CITAS = ('Terciano salió sin declarar', ' y esto me lo invento ', 'Una declaración anterior')
SIMBOLOS_DE_CITAS = tuple('«»“”"') + RELLENOS_DE_CITAS


def eximido(modulo, texto):
    """{posición}: los caracteres de `texto` que `modulo.tramos_citados` (el real, con la fuente de
    `DOCUMENTO_DE_CITAS`) exime de verdad."""
    por_documento = {'d': modulo._llano(DOCUMENTO_DE_CITAS)}
    return {k for a, b in modulo.tramos_citados(texto, {'d'}, por_documento) for k in range(a, b)}


def _ediciones_en(simbolos):
    """(descripción, texto editado, posición del editado en el original o None): cada edición de UNA comilla entre los
    símbolos de un texto: borrar cada una de las que tiene e insertar cada clase en cada hueco entre símbolos."""
    inicio = [0]
    for s in simbolos:
        inicio.append(inicio[-1] + len(s))
    for i, s in enumerate(simbolos):
        if s in '«»“”"':
            p = inicio[i]
            yield (f'borrar la comilla {s} del símbolo {i}', ''.join(simbolos[:i] + simbolos[i + 1:]),
                   lambda j, p=p: j if j < p else j + 1)
    for g in range(len(simbolos) + 1):
        p = inicio[g]
        for c in '«»“”"':
            yield (f'insertar {c} tras el símbolo {g}', ''.join(simbolos[:g] + [c] + simbolos[g:]),
                   lambda j, p=p: j if j < p else (None if j == p else j - 1))


def comprobar_eximido(modulo, largo_maximo=7, aleatorios=300, semilla=17):
    """[(texto, edición, texto editado, posiciones eximidas de más)]: la prueba de seguridad de las comillas (AC250),
    independiente del modelo y sobre lo EFECTIVO. Para todo texto BALANCEADO (todas sus comillas en pares válidos) y
    cada edición de UNA comilla (borrarla, o insertar una suelta de cualquier clase en un hueco), lo que
    `modulo.tramos_citados` exime del texto editado, llevado a las posiciones del original, está contenido en lo que
    exime del original. Los textos: todas las cadenas de hasta `largo_maximo` símbolos de `SIMBOLOS_DE_CITAS` (huecos
    entre símbolos) y `aleatorios` textos de varios pares con relleno y saltos de línea, con las ediciones en cada
    hueco entre CARACTERES. Se exige del balanceado y no de cualquiera: reparar el entrecomillado de un escalar
    ambiguo (borrar la suelta) puede recuperar una exención, y eso es corregirlo, no ampliarlo."""
    fallos = []

    def revisa(simbolos, ediciones):
        texto = ''.join(simbolos)
        base = eximido(modulo, texto)
        for descripcion, editado, mapa in ediciones:
            de_mas = sorted({mapa(k) for k in eximido(modulo, editado)} - {None} - base)
            if de_mas:
                fallos.append((texto, descripcion, editado, de_mas))
                return

    for k in range(largo_maximo + 1):
        for combinacion in itertools.product(SIMBOLOS_DE_CITAS, repeat=k):
            texto = ''.join(combinacion)
            if bien_formada(texto):
                revisa(list(combinacion), _ediciones_en(list(combinacion)))
    azar = random.Random(semilla)
    for _ in range(aleatorios):
        piezas = []
        for _ in range(azar.randint(0, 5)):
            abre, cierra = azar.choice(CLASES_DE_COMILLAS)
            piezas += [azar.choice(['', ' ', 'x', 'relleno\ndel texto', ' y ', RELLENOS_DE_CITAS[1]]),
                       abre + azar.choice(['', RELLENOS_DE_CITAS[0], RELLENOS_DE_CITAS[2], RELLENOS_DE_CITAS[1],
                                           'una cita\ncon salto']) + cierra]
        texto = ''.join(piezas) + azar.choice(['', 'x'])
        caracteres = list(texto)
        revisa(caracteres, _ediciones_en(caracteres))
    return fallos


def comprobar_pares(modulo, **opciones):
    """[(texto, tramos que da, tramos que se esperan)]: `modulo._pares_de_comillas` contra `modelo_de_pares` para
    todas las cadenas de `cadenas_de_comillas` (AC244): lo eximido es EXACTAMENTE lo que el modelo exime (cada pareja
    sin comilla suelta delante), y el interior que da es el texto entre sus comillas."""
    fallos = []
    for texto in cadenas_de_comillas(**opciones):
        dados = modulo._pares_de_comillas(texto)
        if [(a, b) for a, b, _ in dados] != modelo_de_pares(texto) \
                or any(texto[a + 1:b - 1] != interior for a, b, interior in dados):
            fallos.append((texto, dados, modelo_de_pares(texto)))
    return fallos


# Los documentos de la tabla de Codex (AC237), los de AC233 y AC235 y variantes con anclas, alias anidados, claves de
# fusión, listas y mapas de flujo, claves explícitas, secciones vacías, varios documentos y raíces de cada forma.
CORPUS = {
    'ancla: la clave vecina no entra en la expansión': '⟦s⟧base: ⟦A:b⟧&b\n  entity_id: §1\n⟦s⟧other:\n  x: §2\n'
                                                       '⟦s⟧value: *b\n',
    'ancla: la clave vecina, tras la sección que usa el alias': '⟦s⟧base: ⟦A:b⟧&b\n  entity_id: §1\n⟦s⟧value: *b\n'
                                                                  '⟦s⟧other:\n  x: §2\n',
    'ancla: cola antes de la clave que la usa': '⟦s⟧base: ⟦A:b⟧&b\n⟦e⟧- neutro\n⟦s⟧§1: *b\n',
    'ancla de flujo antes de la clave que la usa': '⟦s⟧base: ⟦A:b⟧&b [neutro]\n⟦s⟧§1: *b\n',
    'alias anidado en una clave': '⟦s⟧base: ⟦A:b⟧&b\n  x: §2\n⟦s⟧mid: ⟦A:m⟧&m\n  y: *b\n⟦s⟧§1: *m\n',
    'raíz de flujo, apertura': '⟦s⟧[\n {source_id: §1}\n]\n',
    'raíz de flujo, varios elementos': '⟦s⟧[ {source_id: §1},\n  {source_id: §2} ]\n',
    'sección con lista vacía': '⟦s⟧§1: []\n⟦s⟧otra: x\n',
    'sección con lista vacía, sin clave visible': '⟦s⟧a: []\n⟦s⟧b: §1\n⟦s⟧c: {}\n⟦s⟧d:\n',
    'clave explícita de una lista': '⟦s⟧? items\n:\n⟦e⟧- source_id: §1\n⟦e⟧- source_id: §2\n',
    'clave explícita de un escalar': '⟦s⟧? §1\n: neutro\n⟦s⟧x: §2\n',
    'clave explícita con valor en su línea': '⟦s⟧? items\n: §1\n⟦s⟧x: §2\n',
    'alias al final': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items:\n⟦e⟧- *b\n',
    'alias en medio, con un elemento antes y otro después': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items:\n⟦e⟧- n: 1\n'
                                                             '⟦e⟧- *b\n⟦e⟧- n: §2\n',
    'alias seguido de un elemento con extremo': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items:\n⟦e⟧- *b\n⟦e⟧- n: §2\n',
    'varios alias del mismo ancla': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items:\n⟦e⟧- *b\n⟦e⟧- *b\n⟦e⟧- *b\n',
    'elementos sin alias': '⟦s⟧items:\n⟦e⟧- source_id: §1\n  reason: neutro\n⟦e⟧- source_id: §2\n  reason: neutro\n',
    'cabecera de un elemento': '⟦s⟧items:\n⟦e⟧-\n  source_id: §1\n⟦e⟧- n: §2\n',
    'sección que no es lista, mapa': '⟦s⟧valor:\n  source_id: §1\n⟦s⟧otro: §2\n',
    'sección que no es lista, alias': '⟦s⟧base: ⟦A:b⟧&b {x: §1}\n⟦s⟧valor: *b\n⟦s⟧lista: []\n',
    'sección que no es lista, escalar': '⟦s⟧valor: §1\n⟦s⟧otro: §2\n',
    'segundo documento': '⟦s⟧x: §1\n⟦d⟧---\n⟦s⟧base: ⟦A:b⟧&b {x: §2}\n⟦s⟧items:\n⟦e⟧- *b\n',
    'segundo documento con directiva': '⟦s⟧x: §1\n...\n⟦d⟧%YAML 1.1\n---\n⟦s⟧base: ⟦A:b⟧&b {x: §2}\n'
                                       '⟦s⟧items:\n⟦e⟧- *b\n',
    'tres documentos': '⟦s⟧a: §1\n⟦d⟧---\n⟦s⟧b: §2\n⟦d⟧---\n⟦e⟧- §3\n⟦e⟧- §4\n',
    'lista de flujo, la última sección': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items: [*b]\n',
    'lista de flujo de varias líneas': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items: [\n  *b,\n  n\n]\n⟦s⟧otra: §2\n',
    'lista de flujo con un ancla dentro': '⟦s⟧items: [ ⟦A:b⟧&b {x: §1},\n  *b,\n  n ]\n⟦s⟧otra: §2\n',
    'clave de fusión': '⟦s⟧base: ⟦A:b⟧&b {source_id: §1}\n⟦s⟧items:\n⟦e⟧- <<: *b\n  n: 1\n⟦e⟧- {<<: *b, n: 1}\n'
                       '⟦e⟧- n: §2\n',
    'claves de fusión con varios anclas': '⟦s⟧a: ⟦A:a⟧&a {x: §1}\n⟦s⟧b: ⟦A:b⟧&b {y: §2}\n⟦s⟧items:\n'
                                           '⟦e⟧- <<: [*a, *b]\n  n: 1\n⟦e⟧- n: §3\n',
    'sección que es un alias de una lista': '⟦s⟧base: ⟦A:s⟧&s\n⟦e⟧- {source_id: §1}\n⟦e⟧- {source_id: §2}\n'
                                             '⟦s⟧items: *s\n⟦s⟧otra: §3\n',
    'raíz escalar': '⟦r⟧# cabecera\n§1\n',
    'raíz secuencia con ancla y alias': '⟦e⟧- ⟦A:b⟧&b {source_id: §1}\n⟦e⟧- *b\n⟦e⟧- n: §2\n',
    'raíz secuencia con cabecera': '⟦e⟧- source_id: §1\n⟦e⟧-\n  source_id: §2\n',
    'escalar de bloque': '⟦s⟧quote: |\n  texto §1\n  más texto\n⟦s⟧otra: §2\n',
    'escalar de bloque en un elemento': '⟦s⟧items:\n⟦e⟧- quote: >-\n    texto §1\n  n: 1\n⟦e⟧- n: §2\n',
    'alias como clave': '⟦s⟧base: ⟦A:k⟧&k §1\n⟦s⟧*k : neutro\n⟦s⟧otra: §2\n',
    'alias dentro de un alias de flujo': '⟦s⟧base: ⟦A:b⟧&b {x: §1}\n⟦s⟧items:\n⟦e⟧- [*b, {y: *b}]\n⟦e⟧- n: §2\n',
    'ancla dentro de un elemento, con hermanos tras ella': '⟦s⟧items:\n⟦e⟧- a: ⟦A:b⟧&b\n    x: §1\n⟦/b⟧  c: §2\n'
                                                           '⟦e⟧- *b\n',
    'ancla en un elemento y alias en otra sección': '⟦s⟧items:\n⟦e⟧- ⟦A:b⟧&b {x: §1}\n⟦e⟧- n: §2\n⟦s⟧value: *b\n'
                                                    '⟦s⟧otra: §3\n',
    'mapa de flujo raíz, un par por línea': '⟦o⟧{\n  ⟦s⟧a: §1,\n  ⟦s⟧b: [x, §2],\n  ⟦s⟧c: §3\n}\n',
    'listas anidadas': '⟦s⟧items:\n⟦e⟧- - §1\n  - x\n⟦e⟧-\n  - §2\n⟦e⟧- §3\n',
    'ancla y alias con etiqueta': '⟦s⟧base: ⟦A:b⟧&b !!map {x: §1}\n⟦s⟧items:\n⟦e⟧- *b\n⟦e⟧- !!str §2\n',
    'alias en su propia línea': '⟦s⟧base: ⟦A:b⟧&b {x: §1}\n⟦s⟧items:\n⟦e⟧-\n  *b\n⟦e⟧- n: §2\n',
    'bloque con cabecera tras un alias': '⟦s⟧base: ⟦A:b⟧&b {x: §1}\n⟦s⟧items:\n⟦e⟧- *b\n'
                                         '⟦e⟧- reason: >-\n    cuerpo §2\n  n: 1\n',
    'ancla y alias en elementos contiguos': '⟦s⟧items:\n⟦e⟧- ⟦A:b⟧&b {x: §1}\n⟦e⟧- *b\n⟦e⟧- y: §2\n',
    # AC239: el alias como clave, la apertura de la raíz y el cierre de un escalar de bloque raíz.
    'alias como clave, escalar': '⟦s⟧base: ⟦A:k⟧&k neutral\n⟦s⟧*k : valor\n⟦s⟧otra: x\n',
    'alias como clave, lista de bloque': '⟦s⟧base: ⟦A:k⟧&k clave\n⟦s⟧*k :\n⟦e⟧- a\n⟦e⟧- b\n⟦s⟧otra: x\n',
    'alias como clave, mapa': '⟦s⟧base: ⟦A:k⟧&k clave\n⟦s⟧*k :\n  source_id: x\n  otro: y\n⟦s⟧otra: z\n',
    'alias como clave, flujo': '⟦s⟧base: ⟦A:k⟧&k clave\n⟦s⟧*k : [a, b]\n⟦s⟧otra: {c: d}\n',
    'alias como clave, valor alias anidado': '⟦s⟧a: ⟦A:k⟧&k clave\n⟦s⟧b: ⟦A:l⟧&l {x: *k}\n⟦s⟧*k : *l\n',
    'alias como clave, con fusión': '⟦s⟧b: ⟦A:b⟧&b {x: y}\n⟦s⟧c: ⟦A:k⟧&k clave\n⟦s⟧*k :\n  <<: *b\n  z: 1\n',
    'alias como clave, dos veces': '⟦s⟧base: ⟦A:k⟧&k clave\n⟦s⟧*k :\n⟦e⟧- a\n⟦s⟧*k : otro\n',
    'mapa de flujo raíz, apertura y cierre': '⟦o⟧{\n  ⟦s⟧a: x,\n  ⟦s⟧b: y\n}\n',
    'mapa de flujo raíz con ancla': '⟦o⟧&r {\n  ⟦s⟧a: x,\n  ⟦s⟧b: [y, z]\n}\n',
    'mapa de flujo raíz con ancla y una lista de flujo primera': '⟦o⟧&r {\n  ⟦s⟧a: [x, y],\n  ⟦s⟧b: z\n}\n',
    'clave explícita en otra línea': '⟦s⟧otra: x\n⟦s⟧?\n  items\n:\n⟦e⟧- a\n⟦e⟧- b\n⟦s⟧fin: y\n',
    'primer documento con --- y directivas': '⟦o⟧%YAML 1.1\n---\n⟦s⟧a: x\n⟦s⟧b: y\n',
    'primer documento con ---': '⟦o⟧---\n⟦e⟧- a\n⟦e⟧- b\n',
    'segundo documento con --- y ancla': '⟦s⟧a: x\n⟦d⟧---\n⟦o⟧&r\n⟦s⟧b: y\n⟦s⟧c: z\n',
    # AC241 y AC242: la raíz vacía, las directivas y los tokens de cada documento.
    'raíz vacía, lista': '⟦r⟧[]\n',
    'raíz vacía, mapa': '⟦r⟧{}\n',
    'raíz vacía con ancla': '⟦r⟧&e1 []\n',
    'raíz vacía con etiqueta': '⟦r⟧!!map {}\n',
    'raíz vacía con ancla y etiqueta': '⟦r⟧&e2 !!seq []\n',
    'raíz vacía con ---': '⟦r⟧---\n[]\n',
    'raíz vacía con directiva TAG': '⟦r⟧%TAG !x! Person:v90-fictez\n---\n[]\n',
    'raíz vacía de mapa con directiva TAG': '⟦r⟧%TAG !x! Person:v91-fictez\n---\n{}\n',
    'raíz vacía con directiva YAML': '⟦r⟧%YAML 1.1\n---\n[]\n',
    'raíz vacía de un segundo documento': '⟦s⟧x: neutral\n⟦d⟧⟦r⟧---\n[]\n',
    'raíz vacía de un segundo documento con directiva': '⟦s⟧x: neutral\n⟦d⟧⟦r⟧%TAG !x! Person:v92-fictez\n---\n'
                                                        '[]\n',
    'raíz vacía de un segundo documento con ancla': '⟦s⟧x: neutral\n⟦d⟧⟦r⟧---\n&e3 {}\n',
    'raíz vacía seguida de otro documento': '⟦r⟧%TAG !x! Person:v93-fictez\n---\n[]\n⟦d⟧---\n'
                                           '⟦s⟧y: neutral\n',
    'raíz con directiva TAG de nombre propio': '⟦o⟧%TAG !Terciano! tag:yaml.org,2002:\n---\n'
                                              '⟦s⟧items: [{x: y}]\n',
    'raíz con directiva TAG y mapa de bloque': '⟦o⟧%TAG !t1! tag:yaml.org,2002:\n---\n⟦s⟧a: !t1!str b\n⟦s⟧c: d\n',
    'raíz con directivas y secuencia': '⟦o⟧%YAML 1.1\n%TAG !t2! tag:yaml.org,2002:\n---\n⟦e⟧- a\n⟦e⟧- !t2!str b\n',
    'secuencia raíz con ancla': '⟦o⟧&a\n⟦e⟧- k: v\n⟦e⟧- w\n',
    'secuencia raíz con etiqueta': '⟦o⟧!!seq\n⟦e⟧- k: v\n⟦e⟧- w\n',
    'secuencia raíz con ancla y etiqueta': '⟦o⟧&a !!seq\n⟦e⟧- k: v\n⟦e⟧- w\n',
    'mapa de bloque raíz con ancla': '⟦o⟧&a\n⟦s⟧k: v\n⟦s⟧w: z\n',
    'mapa de bloque raíz con etiqueta': '⟦o⟧!!map\n⟦s⟧k: v\n⟦s⟧w: z\n',
    'lista de flujo raíz con ancla': '⟦s⟧&a [ b,\n  c ]\n',
    'escalar de bloque raíz y otro documento': '⟦r⟧|\n  texto\n⟦d⟧---\n⟦s⟧x: neutral\n',
    'escalar de bloque raíz plegado y otro documento': '⟦r⟧>\n  texto\n  más\n⟦d⟧---\n⟦s⟧x: neutral\n',
    'escalar de bloque raíz y otro documento con fin': '⟦r⟧|\n  texto\n...\n⟦d⟧---\n⟦s⟧x: neutral\n',
    'escalar de bloque raíz y directivas': '⟦r⟧|\n  texto\n...\n⟦d⟧%YAML 1.1\n---\n⟦s⟧x: neutral\n',
    'escalar de bloque raíz y otro escalar': '⟦r⟧|\n  texto\n⟦d⟧⟦r⟧---\nplano\n',
    'escalar plano raíz': '⟦r⟧texto\n',
    'escalar plano raíz y otro documento': '⟦r⟧texto\n⟦d⟧---\n⟦s⟧x: neutral\n',
    'comentarios del documento': '# cabecera\n⟦s⟧a: §1\n# entre\n⟦s⟧b:\n⟦e⟧- §2\n# fin\n',
}
