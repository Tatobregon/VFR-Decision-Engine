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
from data.fetcher_openmeteo import RawNWP, RawNWPHour
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


@pytest.mark.parametrize("crudo,esperado", [
    ("GENERAL ACHA - (ACH / SAEA) - DRCE - PÚBLICO NO CONTROLADO", "GENERAL ACHA"),
    ("CALCHAQUÍ – (CCI) - DRNE - PRIVADO NO CONTROLADO", "CALCHAQUÍ"),
    ("ROLDÁN / LA ILUSIÓN (RLI) - DRCE - PRIVADO NO CONTROLADO", "ROLDÁN / LA ILUSIÓN"),
    ("PERITO MORENO - JALIL HAMER (PTM / SAWP) - DRSU - PÚBLICO NO CONTROLADO",
     "PERITO MORENO - JALIL HAMER"),
    ("﻿PUEBLO / AEROCLUB - (PAE) - DRCE - PÚBLICO NO CONTROLADO", "PUEBLO / AEROCLUB"),
    ("PUEBLO (PROVINCIA) - (PPR) - DRNE - PÚBLICO NO CONTROLADO", "PUEBLO (PROVINCIA)"),
])
def test_el_nombre_se_limpia_en_todos_los_formatos_del_registro(crudo, esperado):
    """
    El registro separa el nombre de sus identificadores con guion, con guion
    largo o sin separador, y algunos nombres llevan un guion o un parentesis
    propio. Un formato no reconocido deja el sufijo administrativo pegado al
    nombre que ve el piloto.
    """
    from data.airports import _extract_name
    assert _extract_name(crudo) == esperado


def test_ningun_nombre_conserva_el_sufijo_administrativo():
    """Integridad del dato sobre el registro real: nombres limpios y sin BOM."""
    import re
    sucios = [(c, a.name) for c, a in AIRPORTS.items()
              if a.name.startswith("﻿")
              or re.search(r"\bDR(CE|NO|NE|SU)\b|CONTROLADO", a.name)]
    assert sucios == []


def test_un_codigo_repetido_conserva_el_registro_que_lo_declara(tmp_path):
    """
    El registro oficial trae registros que repiten el identificador local, las
    coordenadas y la provincia de OTRO aerodromo; solo el nombre declara el
    identificador propio. Quedarse con el ultimo que aparece le cambia el
    nombre a un aerodromo real. Tiene que ganar el consistente en cualquier orden.
    """
    import json
    from data.airports import _load_from_madhel

    def registro(nombre):
        return {
            "type": "AD",
            "human_readable_identifier": nombre,
            "metadata": {
                "identifiers": {"local": "XYZ", "icao": None, "iata": None},
                "localization": {"coordinates": {"lat": -35.0, "lng": -60.0},
                                 "elevation": 100, "state": "BUENOS AIRES"},
                "condition": "PUBLICO", "control": "NON-CONTROLLED",
            },
            "data": {},
        }

    propio = registro("PUEBLO REAL - (XYZ) - DRCE - PÚBLICO NO CONTROLADO")
    ajeno  = registro("OTRO LUGAR – (ABC) - DRNO – PRIVADO NO CONTROLADO")

    for orden in ([propio, ajeno], [ajeno, propio]):
        cache = tmp_path / "madhel.json"
        cache.write_text(json.dumps({"airports": orden}), encoding="utf-8")
        cargados = _load_from_madhel(str(cache))
        assert list(cargados) == ["XYZ"]
        assert cargados["XYZ"].name == "PUEBLO REAL"


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
    # El camino NWP consulta el anillo de puntos, no un punto suelto
    engine._nwp_fetch.get_forecast_ring = lambda **kw: []
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


# ── Muestreo en anillo (incertidumbre orografica) ─────────────────────────────

def _bloque_nwp(base_ts, *, nubes_bajas, temp_c, dew_c, wind_dir, wind_kt, gust_kt,
                elev_m=1138.0):
    """Un RawNWP sintetico de 3 horas, todas con las mismas condiciones."""
    horas = [
        RawNWPHour(
            valid_time_iso      = f"h{i}",
            valid_time_utc      = base_ts + i * 3600,
            windspeed_10m_kt    = wind_kt,
            winddirection_10m   = wind_dir,
            windgusts_10m_kt    = gust_kt,
            visibility_m        = 20000.0,
            cloudcover_low_pct  = nubes_bajas,
            cloudcover_mid_pct  = 0,
            cloudcover_high_pct = 0,
            precipitation_mm    = 0.0,
            temperature_2m_c    = temp_c,
            dewpoint_2m_c       = dew_c,
            weathercode         = 0,
        )
        for i in range(3)
    ]
    return RawNWP(lat=-31.0, lon=-64.5, elevation_m=elev_m,
                  fetch_time="2026-01-01T00:00:00Z", hours=horas)


def _evaluar_con_anillo(anillo, dep):
    engine = DecisionEngine()
    engine._aw.get_metar_and_taf = lambda sid: (None, None)
    engine._nwp_fetch.get_forecast_ring = lambda **kw: anillo
    return engine.evaluate("SACC", runway_heading=320,
                           departure_time=dep, flight_duration_h=1.0)


def test_el_anillo_toma_la_peor_masa_de_aire_del_entorno():
    """
    El punto central esta despejado y seco; un punto del anillo tiene nubosidad
    baja cerrada y aire saturado. El motor debe puntuar el peor de los dos.

    Es el nucleo del tratamiento de la incertidumbre orografica: la grilla del
    modelo promedia el valle y la ladera, asi que se muestrea el entorno.
    """
    dep = int(time.time()) + 3600
    centro = _bloque_nwp(dep, nubes_bajas=0,  temp_c=20.0, dew_c=2.0,
                         wind_dir=320, wind_kt=4.0, gust_kt=6.0)
    ladera = _bloque_nwp(dep, nubes_bajas=95, temp_c=12.0, dew_c=11.5,
                         wind_dir=320, wind_kt=4.0, gust_kt=6.0, elev_m=1600.0)

    solo_centro = _evaluar_con_anillo([centro], dep)
    con_anillo  = _evaluar_con_anillo([centro, ladera], dep)

    assert con_anillo.r_total > solo_centro.r_total
    assert con_anillo.score_breakdown.r_ceil > solo_centro.score_breakdown.r_ceil


def test_el_anillo_no_importa_el_viento_de_los_puntos_vecinos():
    """
    El viento cruzado se define contra LA PISTA y el maximo demostrado del avion.
    Un punto del anillo puede tener rafaga fuerte y cruzada (canalizacion de
    ladera) sin que eso signifique que el avion no pueda aterrizar en el campo.
    El motor debe conservar el viento del aerodromo.

    Sin esta regla el muestreo produce NO GO por viento en dias de calma en el
    aerodromo (observado en SACC con rafaga de ladera del 056 contra pista 320).
    """
    dep = int(time.time()) + 3600
    centro = _bloque_nwp(dep, nubes_bajas=0, temp_c=20.0, dew_c=2.0,
                         wind_dir=320, wind_kt=3.0, gust_kt=5.0)
    cordon = _bloque_nwp(dep, nubes_bajas=0, temp_c=20.0, dew_c=2.0,
                         wind_dir=50, wind_kt=25.0, gust_kt=40.0, elev_m=1600.0)

    solo_centro = _evaluar_con_anillo([centro], dep)
    con_anillo  = _evaluar_con_anillo([centro, cordon], dep)

    assert con_anillo.score_breakdown.r_xwind == solo_centro.score_breakdown.r_xwind
    assert con_anillo.decision == solo_centro.decision


def test_el_anillo_es_inocuo_cuando_el_entorno_es_homogeneo():
    """En llanura todos los puntos dan lo mismo: el veredicto no debe cambiar."""
    dep = int(time.time()) + 3600
    p = lambda: _bloque_nwp(dep, nubes_bajas=10, temp_c=20.0, dew_c=8.0,
                            wind_dir=320, wind_kt=5.0, gust_kt=8.0)
    uno   = _evaluar_con_anillo([p()], dep)
    siete = _evaluar_con_anillo([p() for _ in range(7)], dep)
    assert siete.r_total == pytest.approx(uno.r_total)
    assert siete.decision == uno.decision


def test_el_anillo_ignora_la_niebla_del_valle_por_debajo_del_aerodromo():
    """
    Niebla de radiacion en el fondo del valle, con el aerodromo despejado en la
    ladera. El motor NO debe tomarla: se forma por drenaje de aire frio, es una
    capa estable y no asciende. Un aerodromo a 1138 m no se ve afectado por
    niebla cuyo tope esta a 730.

    Sin esta regla el sistema daba NO GO en La Cumbre con la pista despejada
    (r_vis=1 y r_ceil=1 -> R=0.714), que es la falsa alarma que haria que un
    instructor deje de usar la herramienta.
    """
    dep = int(time.time()) + 3600
    campo  = _bloque_nwp(dep, nubes_bajas=0,  temp_c=18.0, dew_c=2.0,
                         wind_dir=320, wind_kt=4.0, gust_kt=6.0, elev_m=1138.0)
    valle  = _bloque_nwp(dep, nubes_bajas=100, temp_c=6.0, dew_c=6.0,
                         wind_dir=320, wind_kt=4.0, gust_kt=6.0, elev_m=730.0)

    solo_campo = _evaluar_con_anillo([campo], dep)
    con_valle  = _evaluar_con_anillo([campo, valle], dep)

    assert con_valle.r_total == pytest.approx(solo_campo.r_total)
    assert con_valle.decision == solo_campo.decision == "GO"


def test_el_anillo_si_toma_la_nubosidad_de_ladera_por_encima_del_aerodromo():
    """
    La contracara del test anterior: la misma masa de aire saturada, pero en un
    cordon POR ENCIMA del campo, si debe contar. Es el aire que la aeronave
    atraviesa al despegar.

    Los dos tests juntos fijan la ASIMETRIA: el filtro por elevacion no es un
    recorte arbitrario del muestreo sino una distincion fisica.
    """
    dep = int(time.time()) + 3600
    campo  = _bloque_nwp(dep, nubes_bajas=0,   temp_c=18.0, dew_c=2.0,
                         wind_dir=320, wind_kt=4.0, gust_kt=6.0, elev_m=1138.0)
    ladera = _bloque_nwp(dep, nubes_bajas=100, temp_c=6.0,  dew_c=6.0,
                         wind_dir=320, wind_kt=4.0, gust_kt=6.0, elev_m=1600.0)

    solo_campo = _evaluar_con_anillo([campo], dep)
    con_ladera = _evaluar_con_anillo([campo, ladera], dep)

    assert con_ladera.r_total > solo_campo.r_total


# ── Robustez y concurrencia del motor ─────────────────────────────────────────

@pytest.mark.parametrize("payload,caso", [
    ({"latitude": -31.0, "longitude": -64.5, "elevation": 1138, "hourly": {}}, "hourly vacio"),
    ({"latitude": -31.0, "longitude": -64.5, "elevation": 1138}, "sin clave hourly"),
    ({"latitude": -31.0, "longitude": -64.5, "elevation": 1138,
      "hourly": {"time": []}}, "serie de tiempo vacia"),
])
def test_una_respuesta_200_sin_datos_no_rompe_el_parseo(payload, caso):
    """
    Open-Meteo puede responder 200 con el bloque horario vacio. El fetcher debe
    devolver un RawNWP sin horas, no lanzar: el motor traduce la ausencia de
    datos a NO GO conservador, y una excepcion aca se convierte en HTTP 500.
    """
    from data.fetcher_openmeteo import OpenMeteoFetcher
    nwp = OpenMeteoFetcher()._parse_response(payload, 12)
    assert nwp.hours == [], caso


def test_sin_horas_en_el_pronostico_el_veredicto_es_conservador():
    """Un anillo cuyo punto central no trae horas se degrada a 'sin datos'."""
    from data.fetcher_openmeteo import RawNWP
    vacio = RawNWP(lat=-31.0, lon=-64.5, elevation_m=1138.0,
                   fetch_time="2026-01-01T00:00:00Z", hours=[])
    engine = DecisionEngine()
    engine._aw.get_metar_and_taf = lambda sid: (None, None)
    engine._nwp_fetch.get_forecast_ring = lambda **kw: [vacio]
    res = engine.evaluate("SACC", runway_heading=320,
                          departure_time=int(time.time()) + 3600)
    assert res.decision == "NO GO"
    assert not res.fetch_ok


def test_el_motor_es_seguro_entre_hilos():
    """
    Los checkpoints de una ruta se evaluan en paralelo, de modo que varias
    llamadas a evaluate() coexisten. Ninguna debe lanzar ni contaminar a otra:
    con la misma entrada, todas deben devolver el mismo veredicto.
    """
    import threading

    engine = DecisionEngine(mock=True)
    dep = int(time.time()) + 3600
    resultados, errores = [], []
    lock = threading.Lock()

    def _worker():
        try:
            for _ in range(20):
                r = engine.evaluate("SACO", runway_heading=180,
                                    departure_time=dep, flight_duration_h=1.0)
                with lock:
                    resultados.append((r.decision, round(r.r_total, 6)))
        except Exception as exc:                      # pragma: no cover
            with lock:
                errores.append(exc)

    hilos = [threading.Thread(target=_worker) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, f"el motor lanzo bajo concurrencia: {errores[:2]}"
    assert len(resultados) == 160
    assert len(set(resultados)) == 1, (
        f"la misma entrada dio resultados distintos entre hilos: {set(resultados)}")


# ══════════════════════════════════════════════════════════════════════════════
# Checkpoints de ruta: el nivel de crucero no es la superficie
# ══════════════════════════════════════════════════════════════════════════════
# Un punto de ruta tiene dos realidades a la vez: el suelo que queda debajo y
# el aire por el que el avion lo cruza. Describir el segundo con datos del
# primero da errores de decenas de grados, y fue un bug real.

def test_el_pronostico_con_altitud_trae_condiciones_del_nivel():
    from data.fetcher_openmeteo import OpenMeteoFetcher

    f = OpenMeteoFetcher(mock=True)
    con_alt = f.get_forecast(-31.0, -64.0, 500.0, hours_ahead=3, cruise_alt_ft=15000)
    h = con_alt.hours[0]
    assert h.level_temp_c is not None
    assert h.level_altitude_ft is not None
    assert h.level_temp_c != h.temperature_2m_c, (
        "la temperatura del nivel no puede ser la de superficie"
    )


def test_sin_altitud_no_se_inventan_datos_de_nivel():
    """Pedir superficie tiene que devolver superficie, sin rellenar el nivel."""
    from data.fetcher_openmeteo import OpenMeteoFetcher

    h = OpenMeteoFetcher(mock=True).get_forecast(
        -31.0, -64.0, 500.0, hours_ahead=3).hours[0]
    assert h.level_temp_c is None
    assert h.level_altitude_ft is None


def test_a_mayor_altitud_de_crucero_menor_temperatura_en_el_nivel():
    from data.fetcher_openmeteo import OpenMeteoFetcher

    f = OpenMeteoFetcher(mock=True)
    temps = [
        f.get_forecast(-31.0, -64.0, 500.0, hours_ahead=2,
                       cruise_alt_ft=alt).hours[0].level_temp_c
        for alt in (3000, 8000, 15000)
    ]
    assert temps[0] > temps[1] > temps[2], temps


def test_la_evaluacion_en_ruta_devuelve_superficie_y_nivel_por_separado():
    """
    `evaluate_nwp_at_coord` devuelve seis valores: el quinto son las
    condiciones DEL NIVEL y el sexto la barrera que se aplico sobre ellas. Se
    entregan aparte del ParsedWeather —que es un contrato de superficie— para
    que nadie las confunda de nuevo.
    """
    import time

    from decision.enroute import evaluate_nwp_at_coord

    r, dec, ref_wx, worst, lvl, nivel = evaluate_nwp_at_coord(
        lat=-31.0, lon=-64.0, elev_m=500.0,
        dep_time=int(time.time()) + 3600, duration_hours=1.0,
        aircraft=get_profile("Cessna 172 Skyhawk"), mock=True,
        cruise_alt_ft=15000, track_bearing=90, flight_rules="VFR",
    )
    assert ref_wx is not None and lvl is not None
    assert lvl.level_temp_c is not None
    assert lvl.level_temp_c != ref_wx.temp_c


def test_los_nombres_de_variable_del_nivel_coinciden_entre_pedido_y_lectura():
    """
    Open-Meteo acepta "windspeed" y "wind_speed" pero devuelve la grafia que se
    pidio. Si el pedido y la lectura usan grafias distintas, no se encuentra
    nada y el viento cae EN SILENCIO al de superficie. Paso una vez.
    """
    import inspect

    from data import fetcher_openmeteo as fo

    pedido = inspect.getsource(fo.OpenMeteoFetcher.get_forecast)
    lectura = inspect.getsource(fo.OpenMeteoFetcher._parse_response)
    assert "_UPPER_AIR_VARS" in pedido
    for var in ("wind_speed", "wind_direction"):
        assert var in fo._UPPER_AIR_VARS
        assert f'{var}_{{pressure_lvl}}hPa' in lectura, (
            f"_parse_response no lee {var} con la misma grafia con que se pide"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Horizonte del pronostico: tiene que cubrir la salida que el piloto pidio
# ══════════════════════════════════════════════════════════════════════════════
# Bug real: el horizonte era la constante NWP_HOURS_AHEAD (12 h). Una salida
# planificada para dentro de 19 h quedaba fuera del filtro, y el motor caia en
# "la hora disponible mas cercana" SIN DECIRLO. El piloto pedia las 15:00 y
# recibia las 06:00, con 8 C de diferencia, presentados como si fueran suyos.

def test_el_horizonte_sigue_a_la_salida_pedida():
    from config import NWP_HOURS_AHEAD
    from decision.engine import MAX_FORECAST_HOURS, _horizonte_necesario

    ahora = int(time.time())
    # Una salida cercana no necesita mas que el minimo
    assert _horizonte_necesario(ahora + 3600, 1.0) == NWP_HOURS_AHEAD
    # Una lejana, si: el horizonte tiene que alcanzarla
    for horas in (14, 19, 30):
        h = _horizonte_necesario(ahora + horas * 3600, 1.0)
        assert h > horas, f"con salida en {horas} h el horizonte fue {h}"
    # Y no se pide mas de lo que la fuente entrega
    assert _horizonte_necesario(ahora + 200 * 3600, 1.0) == MAX_FORECAST_HOURS


def test_el_horizonte_cubre_tambien_la_duracion_del_vuelo():
    """La ventana termina al ATERRIZAR, no al despegar."""
    from decision.engine import _horizonte_necesario

    ahora = int(time.time())
    corto = _horizonte_necesario(ahora + 10 * 3600, 1.0)
    largo = _horizonte_necesario(ahora + 10 * 3600, 8.0)
    assert largo > corto


def test_una_salida_fuera_del_horizonte_se_declara():
    """
    Devolver condiciones de otro momento como si fueran las pedidas es peor que
    no responder: el piloto no tiene como notarlo.
    """
    from decision.engine import DecisionResult
    assert "forecast_out_of_range" in DecisionResult.__dataclass_fields__
    assert DecisionResult.__dataclass_fields__["forecast_out_of_range"].default is False


def test_el_resultado_dice_de_que_muestra_salio_el_veredicto():
    """
    En el camino NWP el veredicto sale del PEOR caso de la ventana, que rara vez
    es la hora que se muestra. Sin `worst_obs_time` el piloto lee "Xwind 1.1 kt"
    junto a un cartel que dice "viento cruzado 10 kt" y no puede reconciliarlos.
    """
    from decision.engine import DecisionResult
    assert "worst_obs_time" in DecisionResult.__dataclass_fields__


# ── Ventana mas corta que una hora: NO es estar fuera de alcance ──────────────
# Falso positivo real: un vuelo de 14 min que sale 20:11 no contiene ninguna
# hora del pronostico (ni 20:00 ni 21:00), y el aviso de "fuera del alcance"
# saltaba aunque la hora mas cercana estuviera a 11 minutos. Lo que distingue
# los dos casos es el DESVIO REAL, no que la ventana quede vacia.

@pytest.mark.parametrize("offset_min,duracion_h", [
    (11, 14 / 60),      # el caso reportado
    (5,  10 / 60),
    (30, 0.5),
    (45, 0.25),
])
def test_un_vuelo_corto_no_dispara_el_aviso_de_fuera_de_alcance(offset_min, duracion_h):
    base = ((int(time.time()) // 3600) + 1) * 3600
    r = DecisionEngine(mock=True).evaluate(
        "SACC", runway_heading=320,
        departure_time=base + offset_min * 60,
        flight_duration_h=duracion_h,
    )
    assert r.fetch_ok
    assert r.forecast_out_of_range is False, (
        f"salida a :{offset_min:02d} con vuelo de {duracion_h*60:.0f} min no puede "
        f"considerarse fuera del horizonte del pronostico"
    )


def test_el_umbral_de_desvio_tolera_el_redondeo_horario():
    """
    El pronostico es horario: redondear al slot mas cercano nunca cuesta mas de
    media hora. El umbral tiene que estar por encima de eso o marcaria como
    fuera de alcance cualquier salida que no caiga en punto.
    """
    from decision.engine import DESVIO_MAX_ACEPTABLE_H
    assert DESVIO_MAX_ACEPTABLE_H > 0.5


# ══════════════════════════════════════════════════════════════════════════════
# Corredores VFR: el techo publicado es AGL, la altitud de vuelo es MSL
# ══════════════════════════════════════════════════════════════════════════════
# Bug real: el techo del corredor (1500 ft AGL en la TMA Cordoba) se usaba tal
# cual como altitud de crucero. Tomado como MSL dejaba al avion 2200 ft POR
# DEBAJO del propio aerodromo de salida (SACC esta a 3734 ft), disparaba un
# falso "terreno por encima de tu altitud VFR" y ademas pedia el pronostico en
# el nivel de presion de 1500 ft para un vuelo que va a 5200.

def test_el_techo_agl_de_un_corredor_se_convierte_a_msl(monkeypatch):
    from web import app as webapp

    # Terreno sintetico: 1000 m (~3281 ft) en los tres puntos del corredor.
    monkeypatch.setattr(webapp, "get_elevations_m",
                        lambda pts, **kw: [1000.0] * len(pts))

    corridor = [{"lat": -31.0, "lon": -64.5, "upper_limit_ft": 1500,
                 "limit_reference": "AGL"} for _ in range(3)]
    alts = webapp._corridor_alts_msl(corridor, AIRPORTS["SACC"], AIRPORTS["JES"])

    esperado = int(round(1000.0 * 3.28084 + 1500))
    assert alts == [esperado] * 3
    assert all(a > AIRPORTS["SACC"].elev_ft for a in alts), (
        "la altitud de vuelo no puede quedar por debajo del aerodromo de salida"
    )


def test_un_techo_ya_en_msl_no_se_toca(monkeypatch):
    from web import app as webapp

    monkeypatch.setattr(webapp, "get_elevations_m",
                        lambda pts, **kw: [1000.0] * len(pts))
    corridor = [{"lat": -31.0, "lon": -64.5, "upper_limit_ft": 4500,
                 "limit_reference": "MSL"}]
    alts = webapp._corridor_alts_msl(corridor, AIRPORTS["SACC"], AIRPORTS["JES"])
    assert alts == [4500]


def test_si_falla_el_terreno_se_degrada_interpolando_los_aerodromos():
    """Peor estimacion que el SRTM, pero del orden correcto: nunca 1500 ft MSL."""
    from web import app as webapp

    def _explota(pts):
        raise ConnectionError("sin red")

    original = webapp.get_elevations_m
    webapp.get_elevations_m = _explota
    try:
        corridor = [{"lat": -31.0, "lon": -64.5, "upper_limit_ft": 1500,
                     "limit_reference": "AGL"} for _ in range(3)]
        alts = webapp._corridor_alts_msl(corridor, AIRPORTS["SACC"], AIRPORTS["JES"])
    finally:
        webapp.get_elevations_m = original

    assert len(alts) == 3
    assert all(a > 1500 for a in alts)
    # Interpola entre los dos aerodromos, asi que desciende como ellos
    assert alts[0] > alts[-1]


# ══════════════════════════════════════════════════════════════════════════════
# Cada extremo del vuelo se evalua PARA SU MOMENTO
# ══════════════════════════════════════════════════════════════════════════════
# El origen importa cuando se despega; el destino, cuando se aterriza. Antes los
# dos se evaluaban con la hora de salida y la ventana del vuelo entero, asi que
# la tarjeta de destino mostraba temperatura y viento de la hora de SALIDA: en un
# vuelo de tres horas, condiciones de un momento en el que el avion no esta ahi.
# Es informacion de seguridad presentada como si fuera del momento pedido.

def test_el_momento_se_redondea_al_slot_horario_mas_cercano():
    """
    El pronostico es horario. Una ventana que arranca exacto en el momento del
    vuelo puede dejar afuera el slot mas cercano: para una llegada a las 14:49,
    [14:49, 15:49] excluye las 14:00 y termina mostrando las 16:00. Redondeando
    primero, el desvio maximo baja a MEDIA hora, que es el piso teorico.
    """
    from web.app import _hora_redonda

    def ts(h, m):
        return h * 3600 + m * 60

    assert _hora_redonda(ts(14, 49)) == ts(15, 0)
    assert _hora_redonda(ts(15, 1))  == ts(15, 0)
    assert _hora_redonda(ts(15, 29)) == ts(15, 0)
    assert _hora_redonda(ts(15, 31)) == ts(16, 0)
    assert _hora_redonda(ts(16, 0))  == ts(16, 0)


@pytest.mark.parametrize("minutos", list(range(0, 60, 7)))
def test_el_redondeo_nunca_se_aleja_mas_de_media_hora(minutos):
    """Cota dura: con datos horarios no se puede hacer mejor que 30 minutos."""
    from web.app import _hora_redonda

    momento = 12 * 3600 + minutos * 60
    assert abs(_hora_redonda(momento) - momento) <= 30 * 60


def test_la_ventana_de_cada_extremo_es_acotada():
    """
    Si la ventana fuera la del vuelo entero, el origen quedaria juzgado por
    condiciones de horas despues de haberse ido y el destino por horas antes de
    llegar. Una hora cubre la demora de un despegue o la espera de un
    aterrizaje sin traer momentos ajenos al vuelo.
    """
    from web.app import VENTANA_EXTREMO_H

    assert 0.5 <= VENTANA_EXTREMO_H <= 2.0


def test_la_ficha_declara_que_momento_describe():
    """
    Dos tarjetas con horas distintas parecen inconsistentes si no dicen que
    cada una habla de su punto del vuelo.
    """
    from web.app import WeatherCard

    campos = WeatherCard.model_fields
    assert "moment" in campos
    assert "window_start" in campos


def test_con_metar_el_dato_es_una_observacion_y_no_el_momento_evaluado():
    """
    Un METAR es una OBSERVACION del pasado reciente: no existe "el METAR de las
    19:00". Por eso la ficha lleva los dos datos por separado — cuando se
    observo y para cuando se evaluo— y no se puede presentar la observacion
    como si fueran las condiciones del aterrizaje.
    """
    import inspect

    from web import app as webapp

    fuente = inspect.getsource(webapp)
    # El endpoint pasa el momento de cada extremo por separado
    assert 'moment="salida"' in fuente and 'moment="llegada"' in fuente
    assert "window_start=dep_time" in fuente
    assert "window_start=arr_time" in fuente


# ── El TAF como fuente de las condiciones del momento evaluado ────────────────
#
# Un aerodromo con estacion tiene TRES fuentes y hay que elegir una por
# magnitud, segun para QUE MOMENTO se pregunte:
#
#   la observacion   describe el instante en que se tomo
#   el TAF           pronostica visibilidad, techo, viento y fenomenos
#   el modelo (NWP)  aporta temperatura y punto de rocio, que el TAF no publica
#
# Lo que estos tests fijan es la PROCEDENCIA de cada numero, no el veredicto:
# el modo de falla que importa es mostrar un pronostico con cara de observacion.

from decision.engine import VIGENCIA_OBSERVACION_H
from parsers.metar_parser import ParsedWeather
from parsers.taf_parser import ParsedTaf, ParsedTafPeriod

_H = 3600


def _obs(ts, **kw):
    """Observacion METAR de referencia: dia despejado, calmo y templado."""
    base = dict(
        source="metar", station_id="SAXX", obs_time=ts,
        wind_dir=180, wind_spd_kt=5.0, wind_gust_kt=None, wind_variable=False,
        visibility_km=10.0, ceiling_ft=None, sky_layers=[],
        temp_c=20.0, dewpoint_c=4.0, spread_c=16.0,
        altimeter_hpa=1013.0, wx_codes=[], flight_category="VFR",
        raw_string="METAR SAXX 101600Z 18005KT CAVOK 20/04 Q1013",
    )
    base.update(kw)
    return ParsedWeather(**base)


def _periodo(desde, hasta, *, indicador=None, transitorio=False, **kw):
    base = dict(
        time_from=desde, time_to=hasta,
        change_indicator=indicador, probability=None, is_transient=transitorio,
        wind_dir=270, wind_spd_kt=12.0, wind_gust_kt=None, wind_variable=False,
        visibility_km=4.0, ceiling_ft=900,
        sky_layers=[{"cover": "BKN", "base_ft": 900}],
        wx_codes=["-RA"],
    )
    base.update(kw)
    return ParsedTafPeriod(**base)


def _taf(desde, hasta, periodos, sid="SAXX"):
    return ParsedTaf(
        station_id=sid, raw_string="TAF SAXX 101100Z", issue_time="",
        valid_from=desde, valid_to=hasta, periods=periodos,
    )


def _nwp(ts, temp_c=8.0, dewpoint_c=7.0):
    return ParsedWeather(
        source="nwp", station_id="SAXX", obs_time=ts, nwp_estimated=True,
        wind_dir=45, wind_spd_kt=30.0, wind_variable=False,
        visibility_km=2.0, ceiling_ft=300,
        sky_layers=[{"cover": "OVC", "base_ft": 300}],
        temp_c=temp_c, dewpoint_c=dewpoint_c,
        spread_c=round(temp_c - dewpoint_c, 1),
        altimeter_hpa=990.0, wx_codes=["BR"],
    )


def _motor_con_nwp(nwp_wx, avion=None):
    """Motor con el complemento NWP fijado, para no depender de la red."""
    eng = (DecisionEngine(mock=True, aircraft=get_profile(avion)) if avion
           else DecisionEngine(mock=True))
    eng._nwp_para_el_momento = lambda sid, momento: nwp_wx
    return eng


def _condiciones(eng, momento, obs, taf):
    resultado = eng._taf_analyzer.analyze(
        taf, departure_time=momento, flight_duration_h=1.0) if taf else None
    return eng._condiciones_para_el_momento("SAXX", momento, obs, resultado, taf)


def test_dentro_de_la_vigencia_gana_la_observacion():
    """Un dato medido del momento le gana a cualquier pronostico del momento."""
    t = 1_789_000_000
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = _motor_con_nwp(_nwp(t))

    wx, procedencia = _condiciones(eng, t + int(0.5 * _H), obs, taf)

    assert wx is obs, "dentro de la vigencia no se toca la observacion"
    assert procedencia == "observacion METAR"


def test_pasada_la_vigencia_las_condiciones_salen_del_taf():
    """Visibilidad, techo, viento y fenomenos: los pronosticados, no los observados."""
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = _motor_con_nwp(_nwp(momento))

    wx, procedencia = _condiciones(eng, momento, obs, taf)

    assert wx.visibility_km == 4.0          # el TAF, no los 10 km observados
    assert wx.ceiling_ft == 900             # el TAF, no el cielo despejado
    assert wx.wind_dir == 270 and wx.wind_spd_kt == 12.0
    assert wx.wx_codes == ["-RA"]
    assert wx.source == "metar+taf"
    assert wx.obs_time == momento
    assert wx.nwp_estimated is True, "es un pronostico: no puede pasar por observacion"
    # Recalculada con los valores del TAF: vis 4 km y techo 900 ft es
    # "VFR marginal" por la vis; la observacion daba "VFR".
    assert wx.flight_category == "VFR marginal"
    assert obs.flight_category == "VFR"
    assert procedencia.startswith("pronostico TAF")


def test_la_temperatura_y_el_rocio_los_completa_el_nwp():
    """El TAF no publica termodinamica; sin el NWP el spread quedaria en el del METAR."""
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t, temp_c=20.0, dewpoint_c=4.0, spread_c=16.0)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = _motor_con_nwp(_nwp(momento, temp_c=8.0, dewpoint_c=7.0))

    wx, procedencia = _condiciones(eng, momento, obs, taf)

    assert wx.temp_c == 8.0
    assert wx.dewpoint_c == 7.0
    assert wx.spread_c == 1.0, "spread de 1 C es riesgo de niebla; el del METAR era 16"
    assert "NWP" in procedencia


def test_sin_nwp_la_temperatura_se_declara_como_de_la_observacion():
    """Si el modelo no responde se degrada, pero se dice de donde salio el dato."""
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = DecisionEngine(mock=True)
    eng._nwp_para_el_momento = lambda sid, m: None

    wx, procedencia = _condiciones(eng, momento, obs, taf)

    assert wx.temp_c == 20.0
    assert "sin NWP" in procedencia


def test_el_taf_fuera_de_su_vigencia_no_se_usa():
    """
    Regresion: el corte tiene que mirar la vigencia DEL TAF.

    Compararlo contra la ventana de vuelo no verifica nada, porque la ventana
    ES el momento pedido: la condicion se cumple siempre y un TAF vencido se
    usaria como si cubriera el momento.
    """
    t = 1_789_000_000
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 6 * _H, [_periodo(t - 4 * _H, t + 6 * _H)])
    eng = _motor_con_nwp(_nwp(t + 30 * _H))

    wx, procedencia = _condiciones(eng, t + 30 * _H, obs, taf)

    assert wx is obs
    assert "no cubre" in procedencia


def test_sin_taf_se_devuelve_la_observacion_declarada():
    t = 1_789_000_000
    obs = _obs(t)
    eng = _motor_con_nwp(_nwp(t + 5 * _H))

    wx, procedencia = _condiciones(eng, t + 5 * _H, obs, None)

    assert wx is obs
    assert "sin TAF" in procedencia


def test_el_viento_se_toma_entero_de_una_sola_fuente():
    """
    VRB en el TAF es informacion, no un hueco.

    Rellenar la direccion ausente con la del modelo produce un viento que
    ninguna de las dos fuentes pronostico.
    """
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [
        _periodo(t - 4 * _H, t + 20 * _H,
                 wind_dir=None, wind_spd_kt=3.0, wind_variable=True),
    ])
    eng = _motor_con_nwp(_nwp(momento))   # el NWP trae 45/30

    wx, _ = _condiciones(eng, momento, obs, taf)

    assert wx.wind_variable is True
    assert wx.wind_dir is None, "la direccion del modelo no completa un VRB del TAF"
    assert wx.wind_spd_kt == 3.0


def test_el_cielo_sin_capa_significativa_del_taf_borra_el_techo_observado():
    """
    NSC en el TAF significa SIN TECHO, no "dato ausente".

    Si se tratara como hueco, el techo observado horas antes sobreviviria al
    pronostico que dice que ya no esta.
    """
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t, ceiling_ft=800, sky_layers=[{"cover": "OVC", "base_ft": 800}],
               visibility_km=3.0, flight_category="IFR")
    taf = _taf(t - 4 * _H, t + 20 * _H, [
        _periodo(t - 4 * _H, t + 20 * _H, visibility_km=10.0, ceiling_ft=None,
                 sky_layers=[{"cover": "NSC", "base_ft": None}], wx_codes=[]),
    ])
    eng = _motor_con_nwp(_nwp(momento))

    wx, _ = _condiciones(eng, momento, obs, taf)

    assert wx.ceiling_ft is None
    assert wx.sky_layers == [{"cover": "NSC", "base_ft": None}]
    assert wx.flight_category == "VFR"


def test_el_qnh_sigue_saliendo_de_la_observacion():
    """Es el unico dato que solo publica el METAR, y cambia despacio."""
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t, altimeter_hpa=1013.0)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = _motor_con_nwp(_nwp(momento))   # el NWP trae 990

    wx, _ = _condiciones(eng, momento, obs, taf)

    assert wx.altimeter_hpa == 1013.0


def test_el_deterioro_transitorio_del_taf_entra_en_las_condiciones():
    """
    Peor caso de la ventana, igual que en el camino NWP y que en el chequeo de
    fenomenos peligrosos del TAF: un TEMPO que degrada cuenta.
    """
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [
        _periodo(t - 4 * _H, t + 20 * _H, visibility_km=10.0, ceiling_ft=None,
                 sky_layers=[{"cover": "NSC", "base_ft": None}], wx_codes=[]),
        _periodo(momento - _H, momento + _H, indicador="TEMPO", transitorio=True,
                 visibility_km=1.2, ceiling_ft=400,
                 sky_layers=[{"cover": "OVC", "base_ft": 400}], wx_codes=["BR"]),
    ])
    eng = _motor_con_nwp(_nwp(momento))

    wx, _ = _condiciones(eng, momento, obs, taf)

    assert wx.visibility_km == 1.2
    assert wx.ceiling_ft == 400


@pytest.mark.parametrize("horas,espera_taf", [
    (VIGENCIA_OBSERVACION_H - 0.1, False),
    (VIGENCIA_OBSERVACION_H + 0.1, True),
])
def test_la_frontera_de_vigencia_de_la_observacion(horas, espera_taf):
    """El corte es la vigencia declarada, no un umbral implicito."""
    t = 1_789_000_000
    momento = t + int(horas * _H)
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = _motor_con_nwp(_nwp(momento))

    wx, procedencia = _condiciones(eng, momento, obs, taf)

    assert (wx.source == "metar+taf") is espera_taf, procedencia


@pytest.mark.parametrize("avion", [
    "Pipistrel Alpha Trainer", "Cessna 152", "Cessna 172 Skyhawk",
    "Piper PA-28 Cherokee", "Diamond DA40",
])
def test_la_eleccion_de_fuente_no_depende_de_la_aeronave(avion):
    """
    REGLA DE ALCANCE: que fuente describe el momento es una cuestion de datos.
    Lo que cambia con la aeronave es el veredicto, no la procedencia.
    """
    t = 1_789_000_000
    momento = t + 5 * _H
    obs = _obs(t)
    taf = _taf(t - 4 * _H, t + 20 * _H, [_periodo(t - 4 * _H, t + 20 * _H)])
    eng = _motor_con_nwp(_nwp(momento), avion=avion)

    wx, procedencia = _condiciones(eng, momento, obs, taf)

    assert wx.source == "metar+taf"
    assert wx.visibility_km == 4.0
    assert procedencia.startswith("pronostico TAF")


def test_el_resultado_declara_de_donde_salieron_las_condiciones():
    """
    El campo existe para que la pantalla pueda decirlo. Un numero sin su
    procedencia es exactamente el modo de falla que este trabajo corrige.
    """
    eng = DecisionEngine(mock=True)
    res = eng.evaluate("SACO", runway_heading=180,
                       departure_time=int(time.time()) + 3600,
                       flight_duration_h=1.0)
    assert res.conditions_source
    assert res.weather_source in {"metar", "metar+taf", "nwp"}
