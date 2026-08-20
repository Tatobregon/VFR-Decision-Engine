"""
cache.py
========
Cache en memoria con expiracion (TTL) para las respuestas de las APIs externas.

Por que existe
--------------
Una sola evaluacion dispara decenas de llamadas HTTP: METAR y TAF de origen y
destino, NOTAMs de ambos, y un pronostico NWP por cada checkpoint de la ruta.
Muchas se repiten entre evaluaciones consecutivas (el piloto prueba otra hora de
salida, otra aeronave, o consulta la ficha del mismo aerodromo), y los datos de
origen no cambian a esa velocidad: un METAR se emite cada 30-60 minutos y un
modelo NWP se actualiza cada 1-6 horas. Sin cache, el sistema vuelve a pedir lo
mismo, lo que se nota sobre todo en el plan gratuito de Render.

Diseno
------
- TTL por tipo de dato, acorde a la frecuencia con que la fuente lo actualiza.
- Cache a nivel de MODULO, no de instancia: los fetchers se crean por request,
  asi que una cache por instancia no serviria de nada.
- Thread-safe: las evaluaciones de checkpoints corren en ThreadPoolExecutor.
- La llamada a la API se hace FUERA del lock, para no serializar los requests.
  Dos hilos que pidan la misma clave a la vez pueden llamar ambos: se prefiere
  esa duplicacion ocasional antes que bloquear a todos los hilos mientras uno
  espera la red.
- Nunca cachea respuestas vacias o None: si la API fallo, el proximo intento
  debe volver a preguntar en vez de arrastrar el error hasta que expire el TTL.
"""

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# TTL por tipo de dato (segundos)
# ──────────────────────────────────────────────────────────────────────────────

TTL_METAR_S = 10 * 60    # METAR: emision tipica cada 30-60 min
TTL_TAF_S   = 30 * 60    # TAF: emision cada 6 h
TTL_NOTAM_S = 30 * 60    # NOTAM: cambian con baja frecuencia
TTL_NWP_S   = 30 * 60    # NWP: los modelos corren cada 1-6 h

_MAX_ENTRIES = 512       # tope de entradas por cache (evita crecimiento sin fin)


class TTLCache:
    """Cache clave -> valor con expiracion por tiempo, segura entre hilos."""

    def __init__(self, ttl_s: float, name: str = "", max_entries: int = _MAX_ENTRIES):
        self._ttl   = ttl_s
        self._name  = name
        self._max   = max_entries
        self._data  = {}                  # key -> (expires_at, value)
        self._lock  = threading.Lock()
        self.hits   = 0
        self.misses = 0

    # ── API publica ───────────────────────────────────────────────────────────

    def get_or_call(self, key: Any, fn: Callable[[], Any]) -> Any:
        """
        Devuelve el valor cacheado para `key`, o ejecuta `fn()` y lo guarda.

        No se cachean los resultados "vacios" (None o lista vacia): representan
        un fallo o una ausencia de datos, y conviene reintentar en la proxima.
        """
        now = time.time()

        with self._lock:
            entry = self._data.get(key)
            if entry is not None and entry[0] > now:
                self.hits += 1
                logger.debug(f"cache[{self._name}] HIT  {key}")
                return entry[1]

        self.misses += 1
        logger.debug(f"cache[{self._name}] MISS {key}")
        value = fn()

        if value is None or (isinstance(value, (list, tuple, dict)) and len(value) == 0):
            return value

        with self._lock:
            if len(self._data) >= self._max:
                self._purge_locked(time.time())
            self._data[key] = (time.time() + self._ttl, value)
        return value

    def clear(self) -> None:
        """Vacia la cache (usado por los tests)."""
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self._name, "entries": len(self._data),
                "hits": self.hits, "misses": self.misses, "ttl_s": self._ttl,
            }

    # ── Interno ───────────────────────────────────────────────────────────────

    def _purge_locked(self, now: float) -> None:
        """Elimina lo expirado; si aun asi esta lleno, descarta lo mas viejo."""
        expirados = [k for k, (exp, _) in self._data.items() if exp <= now]
        for k in expirados:
            del self._data[k]
        if len(self._data) >= self._max:
            sobrantes = sorted(self._data.items(), key=lambda kv: kv[1][0])
            for k, _ in sobrantes[: max(1, self._max // 4)]:
                del self._data[k]


# ──────────────────────────────────────────────────────────────────────────────
# Instancias compartidas por todo el sistema
# ──────────────────────────────────────────────────────────────────────────────

METAR_CACHE = TTLCache(TTL_METAR_S, "metar")
TAF_CACHE   = TTLCache(TTL_TAF_S,   "taf")
NOTAM_CACHE = TTLCache(TTL_NOTAM_S, "notam")
NWP_CACHE   = TTLCache(TTL_NWP_S,   "nwp")

ALL_CACHES = (METAR_CACHE, TAF_CACHE, NOTAM_CACHE, NWP_CACHE)


def clear_all() -> None:
    """Vacia todas las caches (tests y desarrollo)."""
    for c in ALL_CACHES:
        c.clear()


def all_stats() -> list:
    return [c.stats() for c in ALL_CACHES]


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: data/cache.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    llamadas = {"n": 0}

    def _fn():
        llamadas["n"] += 1
        return f"valor-{llamadas['n']}"

    c = TTLCache(ttl_s=1.0, name="test")

    v1 = c.get_or_call("k", _fn)
    v2 = c.get_or_call("k", _fn)
    check("segunda llamada sale de la cache", v1 == v2 and llamadas["n"] == 1)
    check("cuenta 1 hit y 1 miss", c.hits == 1 and c.misses == 1)

    v3 = c.get_or_call("otra", _fn)
    check("clave distinta vuelve a llamar", v3 != v1 and llamadas["n"] == 2)

    time.sleep(1.1)
    v4 = c.get_or_call("k", _fn)
    check("tras expirar el TTL vuelve a llamar", v4 != v1 and llamadas["n"] == 3)

    # No cachear resultados vacios
    vacias = {"n": 0}
    def _fn_vacia():
        vacias["n"] += 1
        return []
    c.get_or_call("vacia", _fn_vacia)
    c.get_or_call("vacia", _fn_vacia)
    check("no cachea listas vacias (reintenta)", vacias["n"] == 2)

    none_calls = {"n": 0}
    def _fn_none():
        none_calls["n"] += 1
        return None
    c.get_or_call("none", _fn_none)
    c.get_or_call("none", _fn_none)
    check("no cachea None (reintenta)", none_calls["n"] == 2)

    # Tope de entradas
    c2 = TTLCache(ttl_s=60.0, name="tope", max_entries=8)
    for i in range(40):
        c2.get_or_call(f"k{i}", lambda i=i: f"v{i}")
    check(f"respeta el tope de entradas  (got {c2.stats()['entries']})",
          c2.stats()["entries"] <= 8)

    c.clear()
    check("clear() vacia la cache", c.stats()["entries"] == 0)

    # Seguridad entre hilos
    import threading as _th
    c3 = TTLCache(ttl_s=60.0, name="hilos")
    errores = []
    def _worker(n):
        try:
            for i in range(200):
                c3.get_or_call(f"key{i % 20}", lambda i=i: f"v{i}")
        except Exception as e:
            errores.append(e)
    hilos = [_th.Thread(target=_worker, args=(i,)) for i in range(8)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    check("uso concurrente sin errores", not errores)

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
