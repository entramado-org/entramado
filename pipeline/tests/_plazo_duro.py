"""AC299, AC302 (enmiendas 73 y 74): el PLAZO DURO DE PROCESO de las dos clases de guarda, en UN solo sitio.

Un test que se cuelga (en un `waitpid`, un `acquire`, un `addCleanup`, lo que sea: no depende de reconocer la forma) vuelca la traza de
todos los hilos y termina el proceso con un código distinto de 0 cuando vence `T`. El plazo vive en `run`, no en `setUp`/`tearDown`:
`TestCase.run` ejecuta los `addCleanup` DESPUÉS de `tearDown` y omite `tearDown` si `setUp` falla o salta, así que solo envolviendo
`run` queda cubierto el ciclo entero (`setUp`, el cuerpo, `tearDown` y todos los `doCleanups`) y se cancela aunque `run` lance.

Uso: `class X(PlazoDuro, unittest.TestCase)` (el mixin ANTES: si no, `TestCase.run` lo tapa), sin sobrescribir `run`. Hay UN temporizador
por proceso: una prueba que ejecuta otras con `run()` (el barrido de AC293, o la de la otra clase) no lo rearma ni lo cancela (`_ARMADOS`).
Este fichero es el único de `pipeline/tests` que llama a `faulthandler` (un test lo exige, AC303); no tiene efectos al importarlo.
`TestCase.debug()` no pasa por `run`; el ejecutor del CI, `python -m unittest`, no lo usa. Los fixtures de clase y de módulo (`setUpClass`,
`addModuleCleanup`...) corren fuera de `run`: un test los prohíbe en las dos clases y sus dos módulos.
"""
import faulthandler
import sys

PLAZO_DURO_DE_UN_TEST = 1500.0   # segundos por test: 5 veces la cota declarada `MAS_LENTO_MEDIDO` (300 s) de `test_seudonimos.py`
_ARMADOS = []


class PlazoDuro:
    PLAZO_DURO = PLAZO_DURO_DE_UN_TEST

    def run(self, result=None):
        if not _ARMADOS:
            faulthandler.dump_traceback_later(self.PLAZO_DURO, exit=True, file=sys.stderr)
        _ARMADOS.append(self.PLAZO_DURO)
        try:
            return super().run(result)
        finally:
            _ARMADOS.pop()
            if not _ARMADOS:
                faulthandler.cancel_dump_traceback_later()
