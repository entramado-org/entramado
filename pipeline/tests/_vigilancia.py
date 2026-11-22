"""Spec 070 (AC253 a AC255, enmienda 58): la guarda de relojes de las pruebas. Sin dependencias de `src`.

El proceso de las pruebas no mide: toda medida corre en un hijo del servidor limpio (`en_hijo`, `test_seudonimos.py`).
En lugar de inventariar las formas de leer un reloj (por nombre, por identidad, por decorador, una a una; cinco pasadas
seguidas hallaron la siguiente), el reloj mismo se niega a responder: al importar este módulo, UNA vez por proceso y sin
soltarse nunca, sustituye cada lector de reloj (`time` por reflexión, `os.times`, `resource.getrusage`,
`signal.getitimer`) por una envoltura que decide AL LLAMAR, por el fichero del marco que llama. Si es un fichero de
`pipeline/tests` (comparado por ruta resuelta) que no figura en `EXENCIONES` y el proceso es el de las pruebas, lanza
`AssertionError`; cualquier otro llamador (biblioteca estándar, `src`, `structlog`, `yaml`, terceros) pasa al reloj
real. Deniega por defecto: un módulo, ayudante, método enlazado, instancia, callback de `addCleanup` o `lru_cache`
nuevos quedan cubiertos sin que nadie los inventaríe, y toda referencia al reloj original, guardada ANTES (en cualquier
módulo ya cargado, `threading._time`) o DESPUÉS de importar este módulo, es ya la envoltura (AC256).

`test_seudonimos.py` y `test_qa_esc_apply.py` lo importan en su PRIMERA línea (`tests` no es un paquete: el CI corre
`python -m unittest test_seudonimos test_qa_esc_apply` con `pipeline/tests` en el `PYTHONPATH`); una prueba dinámica
(AC254, AC265: un intérprete nuevo que importa esta guarda primero y después cada módulo de `pipeline/tests`) exige que
ninguno lance `AssertionError` al importarse.

Los hijos de `en_hijo` heredan la envoltura del servidor (que importó los módulos de pruebas) y miden con relojes
reales: la envoltura solo actúa si `multiprocessing.parent_process()` es `None`. Los metatests ejercitan la guarda en un
hijo con `instalar(forzar=True)`.

Límites declarados (no son hallazgo): una lectura MEDIADA por la biblioteca estándar, porque la guarda solo mira al
llamador Python inmediato y, si es de la biblioteca estándar, deja pasar aunque la referencia se haya guardado después
de instalarla (`timeit.default_timer`, `threading.Timer`, `sched.scheduler(timefunc=...)`, un `mock` con `side_effect`,
`dataclasses.field(default_factory=time.perf_counter)`, `addCleanup(time.perf_counter)`); la ocultación deliberada
(`ctypes`, `exec`, `importlib` con una cadena calculada); y el código de `pipeline/tests` ejecutado con un nombre de
fichero que no está en `pipeline/tests` (`exec` de una cadena). El residuo práctico es pequeño: la tabla de exenciones
y la lista cerrada de cuerpos de hijo de `test_seudonimos.py` rechazan toda mención de un reloj en el código de los
dos módulos de pruebas, y la prueba dinámica de importación (AC254, AC265) rechaza toda lectura de un reloj al
importar cualquier módulo de `pipeline/tests`, sea cual sea la forma, así que una referencia a un reloj solo puede
nacer fuera de ellos. Los límites de procesos (solo POSIX, un hijo detenido con `SIGSTOP` cuyo padre muere por
`SIGKILL`, un control con memoria que sobreviva a un proceso nuevo) están en `en_hijo`.

AC262 (enmienda 60), estrechado por AC266 (enmienda 62): una lectura ejecutada AL IMPORTAR un módulo de
`pipeline/tests` por `importlib.import_module('time')` o por un atributo privado de la biblioteca estándar (`from
threading import _time`) YA NO es un límite para los módulos del árbol: la prueba dinámica de importación (AC265)
importa cada uno con la guarda instalada primero y la denuncia, como a cualquier otra forma. Siguen fuera, declarados:
lo leído por código FUERA de `pipeline/tests` y la ocultación deliberada que evade incluso a la guarda instalada
primero (`ctypes`, llamadas directas al sistema). Un módulo NUEVO de `pipeline/tests` que lea un reloj al importarse
lo ve esa prueba en cada ejecución del CI.

AC267 (enmienda 62, complemento), límites declarados. (1) Un módulo que ATRAPA el `AssertionError` de la guarda (`try:
time.perf_counter()` con `except Exception`, o la lectura en un `Thread` que lo atrapa) no se denuncia en la prueba
dinámica de importación; no es un riesgo de medida, porque la guarda ya RECHAZÓ la lectura (el reloj no devuelve
ningún valor, así que no hay medida contaminada ni calentamiento por esa vía) y solo se pierde el aviso; anotar cada
rechazo exigiría estado en la guarda. (2) Esa prueba tolera, con aviso, los tres módulos que el CI no puede importar
(`openai`, `fitz` y `specs/` ausentes), por la tabla cerrada `ERRORES_DE_ENTORNO` de `test_seudonimos.py` (par exacto
tipo de excepción y nombre): hasta ese fallo se comprueban, y en un entorno completo se importan enteros.

AC258 (enmienda 59), límites declarados: la guarda supone `co_filename` ABSOLUTO, como lo da el sistema de importación
(`import`, `discover`, `-m unittest`; un metatest lo comprueba en los módulos de pruebas y sus ayudantes); un código
cargado por ruta relativa con `runpy` y un `chdir` previo puede escapar. Y un callable del reloj guardado en un cierre,
un argumento por omisión, un diccionario o un `functools.partial` no es una referencia de módulo: `instalar` no lo
sustituye."""
import functools
import multiprocessing
import os
import resource
import signal
import sys
import time
import types

SIN_RELOJ = frozenset({'sleep', 'strftime', 'strptime', 'gmtime', 'localtime', 'mktime', 'asctime', 'ctime'})
EXTERNOS = ((os, 'times'), (resource, 'getrusage'), (signal, 'getitimer'))
# Quién SÍ puede leer un reloj en el proceso de las pruebas, por (fichero relativo a `pipeline/tests`, nombre
# CUALIFICADO de la función: un método o una función anidada homónimos de una exenta no lo son): el soporte que de
# verdad cronometra aquí. `_ahora` (el plazo del padre; solo responde a la supervisión de `en_hijo`),
# `_con_temporizador` (lo que queda de la alarma exterior) y `quemar` (gasta CPU, no la mide). Un metatest exige que
# cada entrada exista y siga leyendo un reloj y que ninguna otra función de los dos módulos lo haga fuera de un hijo.
EXENCIONES = frozenset({('test_seudonimos.py', '_ahora'), ('test_seudonimos.py', '_con_temporizador'),
                        ('test_seudonimos.py', 'quemar')})
_RAIZ = os.path.dirname(os.path.realpath(__file__)) + os.sep
_RELATIVAS = {}
_ENVOLTURAS = {}   # id del reloj original -> (el original, su envoltura)
_FORZADA = False


def _relojes_de_time():
    """Todo invocable de `time` (no una clase) salvo los que no miden: dormir, dar formato a una fecha o convertirla.
    `time` y `time_ns` SÍ cuentan: fechar un registro desde un hijo o desde `src` o `structlog` pasa igual (la guarda
    mira al llamador, no al reloj); desde una prueba, no. Una versión de Python con relojes nuevos los trae sola y un
    metatest exige clasificar los que no."""
    return tuple(sorted(nombre for nombre in dir(time) if callable(getattr(time, nombre))
                        and not isinstance(getattr(time, nombre), type) and nombre not in SIN_RELOJ))


RELOJES = _relojes_de_time()
OBJETIVOS = tuple((time, nombre) for nombre in RELOJES) + EXTERNOS


def _relativa(fichero):
    """La ruta de `fichero` relativa a `pipeline/tests` (resuelta: enlaces y `..` no engañan), o `None` si está fuera"""
    if fichero not in _RELATIVAS:
        real = os.path.realpath(fichero)
        _RELATIVAS[fichero] = real[len(_RAIZ):] if real.startswith(_RAIZ) else None
    return _RELATIVAS[fichero]


def _envolver(nombre, original):
    @functools.wraps(original)
    def reloj(*args, **kwargs):
        try:
            codigo = sys._getframe(1).f_code
        except ValueError:   # llamado desde C sin marco de Python
            return original(*args, **kwargs)
        relativa = _relativa(codigo.co_filename)
        if (relativa is not None and (relativa, codigo.co_qualname) not in EXENCIONES
                and (_FORZADA or multiprocessing.parent_process() is None)):
            raise AssertionError(f'`{nombre}` leído desde {relativa}:{codigo.co_qualname} en el proceso de la prueba: '
                                 f'toda medida va en un hijo (en_hijo)')
        return original(*args, **kwargs)
    reloj._vigilado = True
    return reloj


def instalada() -> bool:
    return all(getattr(getattr(modulo, nombre), '_vigilado', False) for modulo, nombre in OBJETIVOS)


_DICCIONARIO_DE_UN_MODULO = types.ModuleType.__dict__['__dict__']


def _sustituir_alias():
    """AC256, AC259 (enmienda 59, 60): en cada módulo ya cargado, todo valor que SEA (`is`) un reloj original pasa a ser
    su envoltura. Un módulo anterior a la guarda guarda el original con `from time import perf_counter as clock` y lo
    reexporta, y la biblioteca estándar lo trae de serie (`threading._time` es `time.monotonic`: `multiprocessing` carga
    `threading` antes de esta guarda): `time.<reloj>` ya es la envoltura, pero esas referencias no. Solo identidad con
    el original, sin heurística de nombres; la envoltura deja pasar a todo llamador que no es de `pipeline/tests`, así
    que `threading` sigue funcionando. Idempotente. El diccionario REAL de un módulo se lee con el descriptor de
    `ModuleType`, no con `vars()`: una subclase cuyo `__dict__` devuelve una vista, o un módulo perezoso, no engañan ni
    se cargan; lo que no es un `ModuleType` (`None`, una clase que ocupa el sitio de un módulo) se omite."""
    for modulo in list(sys.modules.values()):
        if not issubclass(type(modulo), types.ModuleType):
            continue
        espacio = _DICCIONARIO_DE_UN_MODULO.__get__(modulo)
        for nombre, valor in list(espacio.items()):
            par = _ENVOLTURAS.get(id(valor))
            if par is not None and valor is par[0]:
                espacio[nombre] = par[1]


def instalar(forzar=False):
    """Sustituye los lectores de reloj por la envoltura, en `time`, `os`, `resource` y `signal` y en todo módulo ya
    cargado que guarde el original (`_sustituir_alias`). Idempotente (una vez por proceso, también si el módulo se
    importa desde dos nombres); `forzar` hace que actúe también en un hijo (los metatests)."""
    global _FORZADA
    _FORZADA = _FORZADA or forzar
    for modulo, nombre in OBJETIVOS:
        actual = getattr(modulo, nombre)
        if not getattr(actual, '_vigilado', False):
            envoltura = _envolver(f'{modulo.__name__}.{nombre}', actual)
            _ENVOLTURAS[id(actual)] = (actual, envoltura)
            setattr(modulo, nombre, envoltura)
    _sustituir_alias()


instalar()
