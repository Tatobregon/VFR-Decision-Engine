"""
enroute.py
==========
Evaluacion meteorologica en puntos arbitrarios de la ruta, a altitud de crucero.

Por que existe este modulo
--------------------------
Estas funciones vivian como helpers privados dentro de `web/app.py`. Al
necesitarlas tambien el copiloto, se extrajeron aca: que la capa de lenguaje
natural importe de la capa web seria invertir la direccion de dependencias del
proyecto (`web/` es el punto de entrada, no una biblioteca).

Diferencia con `decision/engine.py`
-----------------------------------
`DecisionEngine.evaluate()` produce el veredicto GO / CAUTION / NO GO de un
AERODROMO: mide despegue y aterrizaje, con el viento cruzado contra la pista y
la visibilidad y el techo en superficie.

Lo de aca es otra cosa: condiciones del AIRE en un punto y a una altitud. No
hay pista contra la cual medir un cruzado, y por eso:

  * El viento cruzado se anula alineando la direccion con la derrota. En
    crucero el piloto corrige deriva; el cruzado es un concepto de pista y
    meterlo aca seria ruido. La rafaga si se conserva: es turbulencia.

  * Se aplica la regla VFR de visibilidad en altura: por encima de 10.000 ft
    el minimo VFR pasa de 5 a 8 km.

  * El veredicto es el PEOR entre el puntaje de superficie debajo del punto
    y la barrera del NIVEL de crucero (`risk/cruise_level.py`): nube en el
    nivel, engelamiento y techo por debajo del crucero. Hasta septiembre de
    2026 el aire del nivel se mostraba pero no se evaluaba, y el riesgo de un
    checkpoint era el mismo a cualquier altitud.

  * Sin datos NO hay veredicto. Si la consulta falla, se devuelve None en vez
    de GO: un punto sin evaluar pintado de verde es informacion falsa.
"""

import copy
import logging
from typing import List, Optional

try:
    from config import NWP_HOURS_AHEAD
    from data.fetcher_openmeteo import OpenMeteoFetcher
    from parsers.openmeteo_adapter import OpenMeteoAdapter
    from risk.aircraft_profiles import AircraftProfile
    from risk.cruise_level import cruise_level_floor, worst_of
    from risk.hard_blockers import check_hard_blockers_from_weather
    from risk.soft_scoring import _worst_verdict, compute_soft_score
except ImportError:                                    # ejecucion como script
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import NWP_HOURS_AHEAD
    from data.fetcher_openmeteo import OpenMeteoFetcher
    from parsers.openmeteo_adapter import OpenMeteoAdapter
    from risk.aircraft_profiles import AircraftProfile
    from risk.cruise_level import cruise_level_floor, worst_of
    from risk.hard_blockers import check_hard_blockers_from_weather
    from risk.soft_scoring import _worst_verdict, compute_soft_score

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Evaluacion en un punto, a altitud de crucero
# ──────────────────────────────────────────────────────────────────────────────

# Un punto sin datos queda SIN veredicto. Devolver GO con R=0 lo pintaba de
# verde en el mapa: la ausencia de dato presentada como buen tiempo.
_SIN_DATOS = (None, None, None, None, None, None)


def evaluate_nwp_at_coord(
    lat: float,
    lon: float,
    elev_m: Optional[float],
    dep_time: int,
    duration_hours: float,
    aircraft: AircraftProfile,
    mock: bool,
    cruise_alt_ft: int = 7500,
    track_bearing: int = 0,
    flight_rules: str = "VFR",
) -> tuple:
    """
    Evalúa riesgo NWP en coordenadas arbitrarias (no airport code).

    Devuelve (r_total, decision, ref_wx, score, level_hour, level_check).

    `elev_m=None` es lo que corresponde a un punto de ruta: Open-Meteo usa su
    propio modelo de terreno y devuelve la altura que uso, que es ademas la
    referencia de la base de nubes AGL que se compara con el crucero.

    `level_check` es la barrera del nivel (`CruiseLevelCheck`): su piso ya esta
    aplicado en `decision`, y sus razones y faltantes se devuelven para que la
    pantalla diga POR QUE. `r_total` sigue siendo el puntaje de superficie.

    Si no hay datos, devuelve `(None, None, None, None, None, None)`: el punto
    queda sin evaluar, no en GO.

    `ref_wx` es un ParsedWeather de SUPERFICIE, viento y rafaga incluidos.
    `level_hour` es el RawNWPHour de esa misma hora, que ademas trae viento,
    temperatura, rocio, humedad y nubosidad EN EL NIVEL de presion.

    Los dos se devuelven por separado a proposito. Un punto de ruta tiene dos
    realidades simultaneas —el suelo debajo y el aire por el que se lo cruza— y
    mezclarlas en una sola estructura fue un bug real: la temperatura de
    superficie terminaba mostrandose como si fuera la de crucero, con decenas
    de grados de error.
    """
    try:
        fetcher = OpenMeteoFetcher(mock=mock)
        adapter = OpenMeteoAdapter()
        raw_nwp = fetcher.get_forecast(
            lat=lat, lon=lon, elevation_m=elev_m,
            hours_ahead=NWP_HOURS_AHEAD,
            cruise_alt_ft=cruise_alt_ft,
        )
        if raw_nwp is None:
            return _SIN_DATOS

        chk_id = f"CHK_{abs(lat):.1f}_{abs(lon):.1f}"
        all_wx = adapter.adapt_all(raw_nwp, station_id=chk_id)
        if not all_wx:
            return _SIN_DATOS

        # Hora cruda por timestamp, para poder devolver las condiciones DEL
        # NIVEL junto con las de superficie.
        crudas = {h.valid_time_utc: h for h in raw_nwp.hours}

        window_end = dep_time + int(duration_hours * 3600)
        window_wx = [w for w in all_wx if dep_time <= w.obs_time <= window_end]
        if not window_wx:
            window_wx = [min(all_wx, key=lambda w: abs(w.obs_time - dep_time))]

        ref_wx = min(window_wx, key=lambda w: abs(w.obs_time - dep_time))
        level_hour = crudas.get(ref_wx.obs_time)

        # Barrera del NIVEL de crucero, sobre toda la ventana (peor caso en el
        # tiempo, como el puntaje). La elevacion es la que uso el modelo: la base
        # de nubes que estima es AGL respecto de ese terreno, no de otro.
        terrain_ft = (raw_nwp.elevation_m * 3.28084
                      if raw_nwp.elevation_m is not None else None)
        level_check = worst_of(
            cruise_level_floor(
                cruise_alt_ft      = cruise_alt_ft,
                flight_rules       = flight_rules,
                level_cloud_pct    = h.level_cloud_pct,
                level_temp_c       = h.level_temp_c,
                low_cloud_pct      = h.cloudcover_low_pct,
                surface_temp_c     = h.temperature_2m_c,
                surface_dewpoint_c = h.dewpoint_2m_c,
                terrain_elev_ft    = terrain_ft,
            )
            for h in (crudas.get(w.obs_time) for w in window_wx) if h is not None
        )

        for wx in window_wx:
            blocker = check_hard_blockers_from_weather(wx)
            if blocker.is_blocked:
                return 1.0, "NO GO", ref_wx, None, level_hour, level_check

        # Un punto de ruta no tiene pista: el cruzado se anula alineando la
        # direccion con el track. r_gust mide la rafaga de SUPERFICIE, que es
        # la que se encuentra al descender o aterrizar ahi.
        def _inflight_wx(w):
            wx2 = copy.copy(w)
            wx2.wind_dir = track_bearing
            wx2.wind_variable = False
            return wx2

        scores = [compute_soft_score(_inflight_wx(w), track_bearing, aircraft)
                  for w in window_wx]
        worst = max(scores, key=lambda s: s.r_total)
        decision = _worst_verdict(worst.decision, level_check.floor)

        # Regla de visibilidad VFR a altitud: a FL100 (10.000 ft) o más, el
        # mínimo VFR es 8 km, no 5. Si en ruta, a esa altitud, la visibilidad
        # cae por debajo de 8 km, la operación VFR queda marginal → CAUTION.
        # (Solo VFR: en IFR no se requiere VMC.) Es una regla DEL NIVEL, asi que
        # se registra en la barrera del nivel: antes cambiaba el veredicto sin
        # dejar razon, y la pantalla mostraba un CAUTION inexplicado. La
        # visibilidad es la de superficie, porque la fuente no la da por nivel.
        if flight_rules == "VFR" and cruise_alt_ft >= 10000 and decision == "GO" \
                and ref_wx is not None and ref_wx.visibility_km is not None \
                and ref_wx.visibility_km < 8.0:
            decision = "CAUTION"
            level_check.floor = _worst_verdict(level_check.floor, "CAUTION")
            level_check.reasons.append(
                f"visibilidad {ref_wx.visibility_km:g} km (en superficie): por "
                f"encima de 10.000 ft el minimo VFR es 8 km")

        return worst.r_total, decision, ref_wx, worst, level_hour, level_check
    except Exception as e:
        logger.warning(f"Error evaluando NWP en coord ({lat:.2f},{lon:.2f}): {e}")
        return _SIN_DATOS


# ──────────────────────────────────────────────────────────────────────────────
# Serie horaria en un punto (superficie)
# ──────────────────────────────────────────────────────────────────────────────

def nwp_series_at_coord(
    lat: float,
    lon: float,
    elev_m: float,
    runway_heading: int,
    aircraft: AircraftProfile,
    mock: bool,
    start_utc: int,
    hours: int = 24,
) -> List[dict]:
    """
    Devuelve la serie horaria de R_total para un punto, evaluando cada hora del
    pronóstico NWP como un despegue/aterrizaje (superficie, con crosswind real).
    Reutiliza un único fetch (el NWP ya trae todas las horas).
    Retorna: [{'t': unix_utc, 'r': float, 'dec': str}, ...] ordenada por hora.
    """
    try:
        fetcher = OpenMeteoFetcher(mock=mock)
        adapter = OpenMeteoAdapter()
        raw_nwp = fetcher.get_forecast(
            lat=lat, lon=lon, elevation_m=elev_m, hours_ahead=NWP_HOURS_AHEAD,
        )
        if raw_nwp is None:
            return []
        all_wx = adapter.adapt_all(raw_nwp, station_id=f"TL_{abs(lat):.1f}_{abs(lon):.1f}")
        end_utc = start_utc + hours * 3600
        series: List[dict] = []
        for w in sorted(all_wx, key=lambda x: x.obs_time):
            if not (start_utc <= w.obs_time <= end_utc):
                continue
            blocker = check_hard_blockers_from_weather(w)
            if blocker.is_blocked:
                series.append({'t': w.obs_time, 'r': 1.0, 'dec': "NO GO"})
                continue
            s = compute_soft_score(w, runway_heading, aircraft)
            series.append({'t': w.obs_time, 'r': round(s.r_total, 3), 'dec': s.decision})
        return series
    except Exception as e:
        logger.warning(f"Error serie NWP en coord ({lat:.2f},{lon:.2f}): {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    from risk.aircraft_profiles import get_profile

    print("=" * 72)
    print("  TEST: decision/enroute.py  (requiere red)")
    print("=" * 72)

    ac = get_profile("Pipistrel Alpha Trainer")
    ahora = int(time.time()) + 3600

    # Junin (SAAJ), a 7500 ft, rumbo este
    r, dec, wx, score, lvl, nivel = evaluate_nwp_at_coord(
        lat=-34.5459, lon=-60.9306, elev_m=None,
        dep_time=ahora, duration_hours=1.0, aircraft=ac, mock=False,
        cruise_alt_ft=7500, track_bearing=90, flight_rules="VFR",
    )
    if r is None:
        print("\n  Junin a 7500 ft : sin datos")
    else:
        print(f"\n  Junin a 7500 ft : R={r:.3f}  ({dec})")
        print(f"    barrera del nivel: {nivel.floor}  {'; '.join(nivel.reasons)}"
              f"{'  faltan: ' + ', '.join(nivel.missing) if nivel.missing else ''}")
    if wx is not None:
        print("    -- superficie --")
        print(f"    visibilidad: {wx.visibility_km} km   techo: {wx.ceiling_ft} ft")
        print(f"    temperatura: {wx.temp_c} C")
    if lvl is not None:
        print("    -- en el nivel de crucero --")
        print(f"    altura real: {lvl.level_altitude_ft} ft")
        print(f"    viento     : {lvl.level_wind_dir}/{lvl.level_wind_spd_kt} kt")
        print(f"    temperatura: {lvl.level_temp_c} C   rocio: {lvl.level_dewpoint_c} C")
        print(f"    nubosidad  : {lvl.level_cloud_pct} %   HR: {lvl.level_rh_pct} %")
        if lvl.level_temp_c is not None and wx is not None and wx.temp_c is not None:
            print(f"    diferencia con superficie: "
                  f"{abs(lvl.level_temp_c - wx.temp_c):.1f} C")

    serie = nwp_series_at_coord(
        lat=-34.5459, lon=-60.9306, elev_m=81.0, runway_heading=180,
        aircraft=ac, mock=False, start_utc=ahora, hours=12,
    )
    print(f"\n  Serie horaria   : {len(serie)} horas")
    for p in serie[:6]:
        print(f"    {time.strftime('%d/%m %H:%MZ', time.gmtime(p['t']))}  "
              f"R={p['r']:.3f}  {p['dec']}")

    print("\n" + "=" * 72)
