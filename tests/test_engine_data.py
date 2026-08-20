"""
test_engine_data.py
===================
Pipeline de decision completo (con datos mock, sin red), registro de aerodromos,
cache de datos y degradacion ante fallos de las APIs externas.
"""

import time

import pytest

from decision.engine import DecisionEngine
from data.airports import AIRPORTS, AIRPORTS_PUBLIC, get_by_code, search_airports
from data.cache import TTLCache, METAR_CACHE
from data.fetcher_aviationweather import AviationWeatherFetcher
from risk.aircraft_profiles import get_profile


# ── Registro de aerodromos ────────────────────────────────────────────────────

def test_el_registro_es_nacional():
    """Cobertura de todo el pais, no de una provincia."""
    assert len(AIRPORTS) >= 500
    provincias = {a.province for a in AIRPORTS.values() if a.province}
    assert len(provincias) >= 15


def test_los_publicos_son_subconjunto_del_registro():
    assert set(AIRPORTS_PUBLIC).issubset(set(AIRPORTS))


def test_todo_aerodromo_tiene_coordenadas_validas():
    for code, ap in AIRPORTS.items():
        assert -90 <= ap.lat <= 90, code
        assert -180 <= ap.lon <= 180, code


def test_no_hay_helipuertos():
    """El sistema es de ala fija: los HEL se excluyen al cargar."""
    assert all(not a.name.upper().startswith("HELIPUERTO") for a in AIRPORTS.values())


def test_busqueda_por_nombre_y_codigo():
    assert get_by_code("SACO") is not None
    assert search_airports("cordoba")
    assert search_airports("zzz-inexistente-zzz") == []


# ── Pipeline de decision ──────────────────────────────────────────────────────

def test_pipeline_completo_devuelve_veredicto():
    engine = DecisionEngine(mock=True)
    res = engine.evaluate("SACO", runway_heading=180,
                          departure_time=int(time.time()) + 3600,
                          flight_duration_h=1.0)
    assert res.decision in {"GO", "CAUTION", "NO GO"}
    assert 0.0 <= res.r_total <= 1.0
    assert res.fetch_ok


def test_normaliza_el_codigo_de_aerodromo():
    engine = DecisionEngine(mock=True)
    res = engine.evaluate("saco", runway_heading=180,
                          departure_time=int(time.time()) + 3600)
    assert res.station_id == "SACO"


def test_fenomeno_peligroso_en_el_taf_bloquea():
    """El mock de SACO tiene un TEMPO con TSRA; volar dentro de esa ventana veta."""
    engine = DecisionEngine(mock=True)
    res = engine.evaluate("SACO", runway_heading=180,
                          departure_time=1706390000, flight_duration_h=1.0)
    assert res.decision == "NO GO"
    assert res.hard_blocked


def test_sin_datos_el_veredicto_es_conservador():
    """Si ninguna fuente responde, el sistema no puede decir GO."""
    engine = DecisionEngine(mock=True)
    engine._aw.get_metar_and_taf = lambda sid: (None, None)
    engine._nwp_fetch.get_forecast = lambda **kw: None
    res = engine.evaluate("SACC", runway_heading=150,
                          departure_time=int(time.time()) + 3600)
    assert res.decision == "NO GO"
    assert not res.fetch_ok
    assert res.error_message


def test_pista_automatica_cuando_no_se_especifica():
    engine = DecisionEngine(mock=True)
    res = engine.evaluate("SACO", runway_heading=None,
                          departure_time=int(time.time()) + 3600)
    assert 0 <= res.runway_heading < 360


@pytest.mark.parametrize("avion", ["Pipistrel Alpha Trainer", "Cessna 172 Skyhawk",
                                   "Diamond DA40"])
def test_el_pipeline_corre_con_distintas_aeronaves(avion):
    engine = DecisionEngine(mock=True, aircraft=get_profile(avion))
    res = engine.evaluate("SACO", runway_heading=180,
                          departure_time=int(time.time()) + 3600)
    assert res.decision in {"GO", "CAUTION", "NO GO"}


# ── Degradacion ante fallos de las APIs ───────────────────────────────────────

class _RespuestaCaida:
    """Simula la caida del proveedor (502 del gateway, observada en produccion)."""
    status_code = 502
    text = "<html>502 Bad Gateway</html>"

    def json(self):
        raise ValueError("no es JSON")


class _SesionCaida:
    def __init__(self):
        self.headers = {}

    def get(self, *a, **kw):
        return _RespuestaCaida()


def test_una_caida_del_proveedor_no_propaga_excepcion(monkeypatch):
    """
    La capa de datos reporta ausencia de datos, nunca excepciones: si esto se
    rompe, el endpoint vuelve a devolver HTTP 500 al piloto.
    """
    import data.fetcher_aviationweather as faw
    monkeypatch.setattr(faw, "RETRY_DELAY", 0)

    fetcher = AviationWeatherFetcher(mock=False)
    fetcher.session = _SesionCaida()

    assert fetcher.get_metar("SACO") is None
    assert fetcher.get_taf("SACO") is None


# ── Cache ─────────────────────────────────────────────────────────────────────

def test_la_cache_evita_la_segunda_llamada():
    cache = TTLCache(ttl_s=60.0, name="t")
    llamadas = []

    def _fn():
        llamadas.append(1)
        return "valor"

    assert cache.get_or_call("k", _fn) == "valor"
    assert cache.get_or_call("k", _fn) == "valor"
    assert len(llamadas) == 1


def test_la_cache_expira():
    cache = TTLCache(ttl_s=0.05, name="t")
    llamadas = []

    def _fn():
        llamadas.append(1)
        return "v"

    cache.get_or_call("k", _fn)
    time.sleep(0.1)
    cache.get_or_call("k", _fn)
    assert len(llamadas) == 2


def test_no_se_cachean_las_respuestas_vacias():
    """Un fallo no debe quedar congelado hasta que venza el TTL."""
    cache = TTLCache(ttl_s=60.0, name="t")
    llamadas = []

    def _fn():
        llamadas.append(1)
        return []

    cache.get_or_call("k", _fn)
    cache.get_or_call("k", _fn)
    assert len(llamadas) == 2


def test_la_cache_es_segura_entre_hilos():
    """Los checkpoints de ruta se evaluan en paralelo."""
    import threading

    cache = TTLCache(ttl_s=60.0, name="t")
    errores = []

    def _worker():
        try:
            for i in range(300):
                cache.get_or_call(f"k{i % 25}", lambda i=i: i)
        except Exception as exc:      # pragma: no cover
            errores.append(exc)

    hilos = [threading.Thread(target=_worker) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores


def test_el_modo_mock_no_usa_la_cache():
    """Los tests con mock no deben contaminar la cache compartida."""
    METAR_CACHE.clear()
    AviationWeatherFetcher(mock=True).get_metar("SACO")
    assert METAR_CACHE.stats()["entries"] == 0
