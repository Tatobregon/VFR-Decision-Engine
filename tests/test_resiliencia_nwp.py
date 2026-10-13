"""
Que una caida del pronostico numerico no deje la ficha en blanco.

Encontrado en uso (13/10/2026): la ficha de un aerodromo sin estacion mostraba
"Sin datos meteorologicos disponibles" sin decir por que, y bastaba una falla
puntual de la fuente para perder toda la informacion del aerodromo.

Tres redes de seguridad, en orden:
  1. si la peticion del anillo (7 puntos) falla, se pide el punto solo;
  2. si tampoco responde, se usa el ULTIMO pronostico conocido, declarando su edad;
  3. si no hay ninguno, la ficha dice la CAUSA, no solo "sin datos".

Sin red: la fuente se simula.
"""

import time

import pytest

from data.cache import TTL_GRACIA_S, TTLCache
from data.fetcher_openmeteo import OpenMeteoFetcher, _mock_nwp_sacc, _motivo


# ── 1. La cache guarda lo vencido por si la fuente deja de responder ─────────

def test_mientras_este_fresco_no_se_llama_a_la_fuente():
    c = TTLCache(ttl_s=60, name="t")
    assert c.get_or_call_stale("k", lambda: "nuevo") == ("nuevo", None)
    assert c.get_or_call_stale("k", lambda: "otro") == ("nuevo", None)


def test_si_la_fuente_falla_se_usa_lo_ultimo_conocido_con_su_edad():
    c = TTLCache(ttl_s=0.01, name="t")
    c.get_or_call_stale("k", lambda: "viejo")
    time.sleep(0.02)
    valor, edad = c.get_or_call_stale("k", lambda: None)
    assert valor == "viejo"
    assert edad is not None and edad >= 0.02


def test_si_la_fuente_responde_lo_vencido_se_reemplaza():
    c = TTLCache(ttl_s=0.01, name="t")
    c.get_or_call_stale("k", lambda: "viejo")
    time.sleep(0.02)
    assert c.get_or_call_stale("k", lambda: "fresco") == ("fresco", None)


def test_un_pronostico_demasiado_viejo_no_se_usa():
    c = TTLCache(ttl_s=60, name="t")
    c.get_or_call_stale("k", lambda: "antiguo")
    # Se lo envejece a mano mas alla de la gracia: ya no describe el momento.
    with c._lock:
        c._data["k"] = (time.time() - TTL_GRACIA_S - 60, "antiguo")
    assert c.get_or_call_stale("k", lambda: None) == (None, None)


def test_sin_nada_guardado_el_fallo_sigue_siendo_fallo():
    c = TTLCache(ttl_s=60, name="t")
    assert c.get_or_call_stale("k", lambda: None) == (None, None)


# ── 2. El anillo cae al punto solo ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cache_limpia():
    from data.fetcher_openmeteo import NWP_CACHE
    NWP_CACHE.clear()
    yield
    NWP_CACHE.clear()


def test_si_el_anillo_no_responde_se_pide_el_punto_solo(monkeypatch):
    """7 puntos en una peticion larga fallan donde el punto solo pasa."""
    intentos = []

    def _get(self, params):
        intentos.append(params["latitude"])
        if "," in str(params["latitude"]):          # el anillo
            raise ConnectionError("No se pudo conectar a Open-Meteo")
        return _mock_nwp_sacc(6)

    monkeypatch.setattr(OpenMeteoFetcher, "_get", _get)
    f = OpenMeteoFetcher(mock=False)
    puntos = f.get_forecast_ring(-31.0, -64.5, 1138.0, hours_ahead=6)

    assert len(puntos) == 1, "tiene que quedar el pronostico del aerodromo"
    assert puntos[0].hours
    assert len(intentos) == 2, "primero el anillo, despues el punto solo"


def test_si_no_responde_ninguno_no_se_inventa_nada(monkeypatch):
    monkeypatch.setattr(OpenMeteoFetcher, "_get",
                        lambda self, params: (_ for _ in ()).throw(
                            ConnectionError("sin conexion")))
    assert OpenMeteoFetcher(mock=False).get_forecast_ring(-31.0, -64.5, 1138.0) == []


def test_el_pronostico_viejo_llega_declarando_su_edad(monkeypatch):
    """La segunda consulta falla: se entrega el anterior, con `stale_age_s`."""
    from data.fetcher_openmeteo import NWP_CACHE
    estado = {"responder": True}

    def _get(self, params):
        if not estado["responder"]:
            raise ConnectionError("sin conexion")
        return _mock_nwp_sacc(6)

    monkeypatch.setattr(OpenMeteoFetcher, "_get", _get)
    monkeypatch.setattr(NWP_CACHE, "_ttl", 0.01)
    f = OpenMeteoFetcher(mock=False)
    assert f.get_forecast_ring(-31.0, -64.5, 1138.0, hours_ahead=6)[0].stale_age_s is None

    estado["responder"] = False
    time.sleep(0.02)
    puntos = f.get_forecast_ring(-31.0, -64.5, 1138.0, hours_ahead=6)
    assert puntos and puntos[0].stale_age_s is not None


# ── 3. La causa se dice ──────────────────────────────────────────────────────

@pytest.mark.parametrize("texto, esperado", [
    ("Open-Meteo respondio con status 429: too many requests", "limite de consultas"),
    ("No se pudo conectar. Ultimo error: Timeout en intento 3", "tardo demasiado"),
    ("Error de conexion: Max retries exceeded", "sin conexion"),
])
def test_la_causa_del_fallo_se_traduce(texto, esperado):
    assert esperado in _motivo(ConnectionError(texto))


def test_la_ficha_sin_datos_dice_la_causa(monkeypatch):
    """Antes decia solo "sin datos": no distinguia un corte de red de un
    limite de consultas, y el piloto no sabia si reintentar servia."""
    from decision.engine import DecisionEngine
    from risk.aircraft_profiles import get_profile

    def _get(self, params):
        raise ValueError("Open-Meteo respondio con status 429: rate limit")

    monkeypatch.setattr(OpenMeteoFetcher, "_get", _get)
    motor = DecisionEngine(aircraft=get_profile("Cessna 172 Skyhawk"), mock=False)
    monkeypatch.setattr(motor._aw, "get_metar", lambda *a, **k: None)
    monkeypatch.setattr(motor._aw, "get_taf", lambda *a, **k: None)

    r = motor.evaluate("SACC", None, int(time.time()) + 3600, 1.0)
    assert r.fetch_ok is False and r.decision == "NO GO"
    assert "limite de consultas" in r.error_message


def test_el_purgado_no_tira_la_red_de_seguridad():
    """Lo recien vencido se conserva: es lo que se sirve si la fuente cae."""
    c = TTLCache(ttl_s=0.01, name="t")
    c.get_or_call_stale("k", lambda: "ultimo")
    time.sleep(0.02)
    with c._lock:
        c._purge_locked(time.time())
        assert "k" in c._data
    assert c.get_or_call_stale("k", lambda: None)[0] == "ultimo"
