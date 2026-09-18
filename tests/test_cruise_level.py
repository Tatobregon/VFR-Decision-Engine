"""
Barrera del NIVEL de crucero en los checkpoints en ruta.

Fija lo que se corrigio en septiembre de 2026, cuando el piloto encontro un
checkpoint con todas las barras en 0 %:

  * el veredicto de un checkpoint no dependia de la altitud de crucero (medido:
    mismo R a 5.500, 7.500 y 10.000 ft en 12 puntos de las cinco regiones);
  * un punto sin datos se pintaba GO;
  * "factor dominante: visibility" aparecia con riesgo cero;
  * la base de nubes medias (8.000 ft fijos) se mostraba como techo pronosticado;
  * se le mandaba a Open-Meteo una elevacion inventada (0 m en las aerovias).

Todo sin red: el pronostico se inyecta armado a mano.
"""

import time

import pytest

from data.fetcher_openmeteo import RawNWP, RawNWPHour
from parsers.openmeteo_adapter import MAX_LOW_CLOUD_BASE_FT, OpenMeteoAdapter
from risk.aircraft_profiles import get_profile
from risk.cruise_level import CruiseLevelCheck, cruise_level_floor, worst_of


# ── La barrera, sola ──────────────────────────────────────────────────────────

def _piso(alt=7500, reglas="VFR", nube=0, t=10.0, baja=0, ts=20.0, td=12.0, terreno=1000.0):
    return cruise_level_floor(alt, reglas, nube, t, baja, ts, td, terreno)


@pytest.mark.parametrize("nube, esperado", [
    (0, "GO"), (37, "GO"), (62, "GO"),        # hasta SCT se esquiva
    (63, "CAUTION"), (87, "CAUTION"),         # BKN: el mismo corte que el techo
    (88, "NO GO"), (100, "NO GO"),            # OVC: no hay por donde
])
def test_en_vfr_la_nube_en_el_nivel_usa_los_cortes_del_techo(nube, esperado):
    assert _piso(nube=nube).floor == esperado


def test_en_ifr_la_nube_en_el_nivel_no_veta_por_si_sola():
    assert _piso(reglas="IFR", nube=100, t=5.0).floor == "GO"


@pytest.mark.parametrize("reglas", ["VFR", "IFR"])
@pytest.mark.parametrize("t", [0.0, -3.0, -25.0])
def test_nube_bajo_cero_es_engelamiento_en_los_dos_regimenes(reglas, t):
    c = _piso(reglas=reglas, nube=70, t=t)
    assert c.floor == "NO GO"
    assert any("engelamiento" in r for r in c.reasons)


def test_bajo_cero_sin_nube_no_es_engelamiento():
    assert _piso(nube=20, t=-10.0).floor == "GO"


def test_nube_sobre_cero_no_es_engelamiento():
    c = _piso(reglas="IFR", nube=100, t=0.5)
    assert c.floor == "GO" and not c.reasons


def test_capa_baja_por_debajo_del_crucero_es_caution_en_vfr():
    # spread 2 C -> base Espy 800 ft AGL; terreno 1000 ft -> ~1800 ft MSL
    c = _piso(alt=7500, baja=80, ts=12.0, td=10.0, terreno=1000.0)
    assert c.floor == "CAUTION"
    assert any("por debajo del crucero" in r for r in c.reasons)


def test_capa_baja_por_encima_del_crucero_no_pesa():
    # spread 8 C -> base 3200 ft AGL; terreno 1000 -> 4200 ft MSL; crucero 3500
    assert _piso(alt=3500, baja=80, ts=18.0, td=10.0, terreno=1000.0).floor == "GO"


def test_el_techo_bajo_el_crucero_no_aplica_en_ifr():
    assert _piso(reglas="IFR", alt=7500, baja=80, ts=12.0, td=10.0).floor == "GO"


def test_el_techo_se_mide_sobre_el_terreno_del_modelo():
    # Misma base AGL, distinto terreno: sobre la llanura queda bajo el crucero,
    # sobre la sierra queda por encima.
    llano  = _piso(alt=6000, baja=80, ts=20.0, td=10.0, terreno=300.0)    # 4000+300
    sierra = _piso(alt=6000, baja=80, ts=20.0, td=10.0, terreno=3000.0)   # 4000+3000
    assert llano.floor == "CAUTION" and sierra.floor == "GO"


def test_con_la_base_en_su_tope_no_se_inventa_la_comparacion():
    # spread enorme -> Espy topea en MAX_LOW_CLOUD_BASE_FT: solo se sabe que la
    # base esta MAS arriba. No hay piso; se declara.
    c = _piso(alt=MAX_LOW_CLOUD_BASE_FT + 5000, baja=80, ts=35.0, td=0.0, terreno=0.0)
    assert c.floor == "GO"
    assert any("tope" in m or "no comparable" in m for m in c.missing)


@pytest.mark.parametrize("campo, faltante", [
    ("nube", "nubosidad en el nivel"),
    ("baja", "nubosidad baja"),
])
def test_un_dato_que_no_llega_se_declara_y_no_se_toma_como_despejado(campo, faltante):
    c = _piso(**{campo: None})
    assert faltante in c.missing
    assert c.floor == "GO"


def test_la_capa_baja_sin_temperatura_no_usa_la_base_de_respaldo():
    # Sin T/Td la base de Espy caeria a la constante de 2000 ft: no se compara.
    c = _piso(alt=7500, baja=80, ts=None, td=None)
    assert c.floor == "GO" and c.missing


def test_el_peor_momento_de_la_ventana_manda():
    total = worst_of([
        CruiseLevelCheck("GO"),
        CruiseLevelCheck("CAUTION", ["a"]),
        CruiseLevelCheck("NO GO", ["b"], ["x"]),
        CruiseLevelCheck("CAUTION", ["a"]),
    ])
    assert total.floor == "NO GO"
    assert total.reasons == ["a", "b"] and total.missing == ["x"]


# ── Integrada en la evaluacion en ruta ────────────────────────────────────────

def _pronostico(t0, nube_nivel=0, t_nivel=10.0, baja=0, t2m=20.0, td2m=10.0,
                elevacion_m=300.0, visibilidad_m=50000.0):
    horas = [RawNWPHour(
        valid_time_iso="", valid_time_utc=t0 + k * 3600,
        windspeed_10m_kt=15.0, winddirection_10m=270, windgusts_10m_kt=None,
        visibility_m=visibilidad_m,
        cloudcover_low_pct=baja, cloudcover_mid_pct=0, cloudcover_high_pct=0,
        precipitation_mm=0.0, temperature_2m_c=t2m, dewpoint_2m_c=td2m,
        weathercode=0,
        level_temp_c=t_nivel, level_dewpoint_c=t_nivel - 5, level_rh_pct=60,
        level_cloud_pct=nube_nivel, level_altitude_ft=7400,
    ) for k in range(-1, 4)]
    return RawNWP(lat=-31.0, lon=-64.0, elevation_m=elevacion_m,
                  fetch_time="", hours=horas)


@pytest.fixture
def inyectar(monkeypatch):
    """Reemplaza el fetcher por uno que devuelve el pronostico armado."""
    import decision.enroute as enroute
    llamadas = []

    def _instalar(raw):
        class _Fetcher:
            def __init__(self, mock=False):
                pass

            def get_forecast(self, lat, lon, elevation_m, hours_ahead=12, cruise_alt_ft=None):
                llamadas.append(elevation_m)
                return raw
        monkeypatch.setattr(enroute, "OpenMeteoFetcher", _Fetcher)
        return llamadas
    return _instalar


def _evaluar(reglas="VFR", avion="Cessna 172 Skyhawk", alt=7500, t0=None):
    from decision.enroute import evaluate_nwp_at_coord
    return evaluate_nwp_at_coord(
        lat=-31.0, lon=-64.0, elev_m=None,
        dep_time=t0, duration_hours=1.0, aircraft=get_profile(avion),
        mock=False, cruise_alt_ft=alt, track_bearing=90, flight_rules=reglas,
    )


@pytest.mark.parametrize("avion", ["Cessna 152", "Diamond DA40"])
def test_superficie_perfecta_y_nivel_dentro_de_nube_ya_no_es_go(inyectar, avion):
    t0 = int(time.time()) // 3600 * 3600 + 3600
    inyectar(_pronostico(t0, nube_nivel=95))
    r, dec, wx, score, lvl, nivel = _evaluar(avion=avion, t0=t0)
    assert r == 0.0                        # la superficie no aporta riesgo...
    assert dec == "NO GO"                  # ...pero se vuela dentro de la nube
    assert nivel.floor == "NO GO" and nivel.reasons


def test_el_mismo_punto_da_distinto_segun_el_aire_del_nivel(inyectar):
    """Lo que antes no pasaba: el veredicto cambia con lo que hay EN el nivel."""
    t0 = int(time.time()) // 3600 * 3600 + 3600
    inyectar(_pronostico(t0, nube_nivel=0))
    limpio = _evaluar(t0=t0)[1]
    inyectar(_pronostico(t0, nube_nivel=70, t_nivel=-4.0))
    helado = _evaluar(t0=t0)[1]
    assert (limpio, helado) == ("GO", "NO GO")


def test_en_ifr_el_hielo_veta_y_la_nube_sola_no(inyectar):
    t0 = int(time.time()) // 3600 * 3600 + 3600
    inyectar(_pronostico(t0, nube_nivel=95, t_nivel=5.0))
    assert _evaluar(reglas="IFR", t0=t0)[1] == "GO"
    inyectar(_pronostico(t0, nube_nivel=95, t_nivel=-5.0))
    assert _evaluar(reglas="IFR", t0=t0)[1] == "NO GO"


def test_sin_datos_no_hay_veredicto(inyectar):
    inyectar(None)
    assert _evaluar(t0=int(time.time()) + 3600) == (None,) * 6


def test_el_punto_de_ruta_no_le_impone_elevacion_al_modelo(inyectar):
    t0 = int(time.time()) // 3600 * 3600 + 3600
    llamadas = inyectar(_pronostico(t0))
    _evaluar(t0=t0)
    assert llamadas == [None]


def test_el_fetcher_no_envia_elevacion_si_no_se_le_da(monkeypatch):
    """Con None el parametro no viaja: Open-Meteo usa su propio terreno."""
    from data import fetcher_openmeteo as fo
    enviados = []

    def _get(self, params):
        enviados.append(dict(params))
        raise ConnectionError("sin red en los tests")

    monkeypatch.setattr(fo.OpenMeteoFetcher, "_get", _get)
    f = fo.OpenMeteoFetcher(mock=False)
    f.get_forecast(-29.123, -66.456, None, cruise_alt_ft=7500)
    f.get_forecast(-29.124, -66.457, 812.0, cruise_alt_ft=7500)
    assert "elevation" not in enviados[0]
    assert enviados[1]["elevation"] == 812.0


# ── Lo que se muestra ─────────────────────────────────────────────────────────

def test_sin_riesgo_no_hay_factor_dominante():
    from parsers.metar_parser import ParsedWeather
    from risk.soft_scoring import compute_soft_score
    wx = ParsedWeather(source="nwp", station_id="X", obs_time=0,
                       wind_dir=90, wind_spd_kt=3.0, visibility_km=10.0,
                       temp_c=20.0, dewpoint_c=5.0, spread_c=15.0)
    s = compute_soft_score(wx, 90, get_profile("Cessna 172 Skyhawk"))
    assert s.r_total == 0.0 and s.dominant_factor is None


def test_las_capas_declaran_si_su_base_es_de_referencia():
    a = OpenMeteoAdapter()
    con_espy = a._estimate_sky_layers(80, 80, 80, temp_c=20.0, dewpoint_c=12.0)
    sin_espy = a._estimate_sky_layers(80, None, None, temp_c=None, dewpoint_c=None)
    ref = {l["base_ft"]: l["base_reference"] for l in con_espy}
    assert sorted(ref.values()) == [False, True, True]     # baja pronosticada
    assert sin_espy[0]["base_reference"] is True           # baja de respaldo


# ── Capa web: el checkpoint y el veredicto de ruta ────────────────────────────

def _wp(decision, en_ruta=True, **kw):
    from web.app import RouteWaypoint
    return RouteWaypoint(code="X", name="X", lat=0.0, lon=0.0,
                         r_total=None if decision is None else 0.1,
                         decision=decision, is_enroute_eval=en_ruta, **kw)


def test_el_veredicto_de_ruta_es_el_peor_de_los_puntos_evaluados():
    from web.app import _veredicto_en_ruta
    wps = [
        _wp("GO", en_ruta=False),                               # aerodromo
        _wp("GO", en_ruta=False, is_corridor_waypoint=True),    # relleno de corredor
        _wp("GO"), _wp("CAUTION"), _wp("NO GO"), _wp(None),
    ]
    dec, conteo = _veredicto_en_ruta(wps)
    assert dec == "NO GO"
    assert conteo == {"GO": 1, "CAUTION": 1, "NO GO": 1, "SIN DATOS": 1}


def test_sin_datos_en_ruta_no_es_go():
    from web.app import _veredicto_en_ruta
    assert _veredicto_en_ruta([_wp(None), _wp(None)]) == (
        None, {"GO": 0, "CAUTION": 0, "NO GO": 0, "SIN DATOS": 2})
    assert _veredicto_en_ruta([_wp("GO", en_ruta=False)]) == (None, {})


def _ruta_con_checkpoint(monkeypatch, resultado, avion="Cessna 172 Skyhawk"):
    """SAZN-SAZS (354 km, sin aerovia ni corredor) genera un checkpoint a 230 km."""
    import web.app as app
    monkeypatch.setattr(app, "_evaluate_nwp_at_coord", lambda **kw: resultado)
    wps = app._generate_route_waypoints(
        path=["SAZN", "SAZS"], aircraft=get_profile(avion),
        dep_time=int(time.time()) + 3600, duration_hours=2.0,
        r_map={}, mock=True,
    )
    return [w for w in wps if w.is_checkpoint]


@pytest.mark.parametrize("avion", ["Pipistrel Alpha Trainer", "Piper PA-28 Cherokee"])
def test_el_checkpoint_lleva_la_barrera_del_nivel_a_la_pantalla(monkeypatch, avion):
    from parsers.metar_parser import ParsedWeather
    wx = ParsedWeather(source="nwp", station_id="CHK", obs_time=0,
                       wind_dir=270, wind_spd_kt=18.0, wind_gust_kt=None,
                       visibility_km=50.0, ceiling_ft=8000,
                       sky_layers=[{"cover": "BKN", "base_ft": 8000,
                                    "estimated": True, "base_reference": True}])
    nivel = CruiseLevelCheck("NO GO", ["nivel dentro de nube: 95 % (OVC)"])
    chk = _ruta_con_checkpoint(monkeypatch, (0.0, "NO GO", wx, None, None, nivel), avion)
    assert chk, "la ruta de prueba tiene que generar al menos un checkpoint"
    cw = chk[0].chk_weather
    assert chk[0].decision == "NO GO" and chk[0].is_enroute_eval
    assert cw.level_floor == "NO GO" and cw.level_reasons
    assert cw.r_gust is None               # sin rafaga en el nivel: sin barra
    assert cw.ceiling_is_reference         # los 8.000 ft no son un pronostico


def test_un_checkpoint_sin_datos_queda_sin_veredicto(monkeypatch):
    chk = _ruta_con_checkpoint(monkeypatch, (None,) * 6)
    assert chk and chk[0].decision is None and chk[0].r_total is None
    assert chk[0].chk_weather is None and chk[0].is_enroute_eval


def test_la_regla_de_8_km_sobre_fl100_deja_su_razon(inyectar):
    """Antes cambiaba el veredicto a CAUTION sin decir por que."""
    t0 = int(time.time()) // 3600 * 3600 + 3600
    inyectar(_pronostico(t0, visibilidad_m=6000.0))
    r, dec, wx, score, lvl, nivel = _evaluar(alt=10500, t0=t0)
    assert dec == "CAUTION" and nivel.floor == "CAUTION"
    assert any("8 km" in x for x in nivel.reasons)
    # por debajo de FL100 el minimo es 5 km y 6 km alcanza
    assert _evaluar(alt=7500, t0=t0)[1] == "GO"
