"""
app.py
======
FastAPI backend del VFR Decision Engine.
Expone el engine como REST API y sirve el frontend HTML.

Uso:
    cd Tesis_2.0
    pip install fastapi uvicorn
    uvicorn web.app:app --reload --port 8000
"""

import sys
import os
import time
import logging
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from data.airports import AIRPORTS, AirportInfo
from data.airspace import AIRSPACE_ZONES
from data.fir_zones import get_fir
from data.fetcher_aviationweather import AviationWeatherFetcher
from data.fetcher_openmeteo import OpenMeteoFetcher
from parsers.openmeteo_adapter import OpenMeteoAdapter
from decision.engine import DecisionEngine
from decision.enroute import evaluate_nwp_at_coord, nwp_series_at_coord
from route.optimizer import optimize
from route.airway_router import find_airways_for_leg, find_airways_for_route_legs
from route.vfr_corridors import corridor_path_for_leg
from output.briefing import generate_briefing
from output.flight_plan import build_flight_plan
from risk.aircraft_profiles import PROFILE_NAMES, get_profile, AircraftProfile
from risk.personal_minima import get_minima, LEVEL_NAMES
from risk.soft_scoring import compute_soft_score
from risk.hard_blockers import check_hard_blockers_from_weather
from risk.weights import apply_decision_threshold
from features.crosswind import compute_crosswind
from features.density_altitude import advisory as da_advisory
from features.vfr_altitude import hemispheric_vfr_altitude
from features.daylight import daylight_status
from features.notam_impact import assess_notam_impact, runway_designators
from route.performance import haversine_km, bearing_deg, effective_groundspeed_kt, leg_time_hours, safe_altitude_ft
from data.terrain import get_elevations_m, M_TO_FT
from config import NWP_HOURS_AHEAD

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="VFR Decision Engine", version="1.3.0")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Modelos ───────────────────────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    origin: str
    dest: str
    origin_runway: Optional[int] = None   # None = auto (pista favorable al viento)
    dest_runway: Optional[int] = None
    aircraft: str = "Pipistrel Alpha Trainer"
    departure_time: str = ""       # "HH:MM" UTC; vacío = ahora + 1h
    avoid_airspace: bool = True    # si True, la ruta evita zonas R/P/D
    flight_rules: str = "VFR"      # "VFR" (default) | "IFR" — define routing/altitud
    experience: str = "PPL"        # "Alumno" | "PPL" | "Avanzado" — mínimos personales
    # Altitud de crucero VFR elegida por el piloto (ft MSL). Solo aplica en VFR:
    # en IFR la altitud la fija la MEA de la aerovía y este valor se ignora.
    # None = el sistema la deriva por la regla de los semicírculos.
    cruise_alt_ft: Optional[int] = None
    # NOTA: la duración del vuelo NO es un parámetro de entrada. Se deriva de la
    # ruta calculada (distancia / velocidad de crucero de la aeronave), porque
    # una duración declarada por el usuario que no coincida con la ruta real
    # desplazaría la ventana meteorológica evaluada.


class Notam(BaseModel):
    notam_id: str
    message: str
    start_date: str = ""
    end_date: str = ""
    q_code: str = ""


class WeatherCard(BaseModel):
    station_id: str
    name: str
    elev_ft: int
    decision: str
    r_total: float
    weather_source: str
    raw_metar: Optional[str] = None    # texto crudo del METAR (si la fuente es metar)
    notams: List[Notam] = []           # NOTAMs activos del aeródromo
    hard_blocked: bool
    blocker_summary: str
    wind_dir: Optional[int] = None
    wind_spd_kt: Optional[float] = None
    wind_gust_kt: Optional[float] = None
    wind_variable: bool = False
    visibility_km: Optional[float] = None
    ceiling_ft: Optional[int] = None
    temp_c: Optional[float] = None
    dewpoint_c: Optional[float] = None
    spread_c: Optional[float] = None
    altimeter_hpa: Optional[float] = None
    wx_codes: List[str] = []
    flight_category: Optional[str] = None
    obs_time: int = 0
    xwind_kt: Optional[float] = None
    headwind_kt: Optional[float] = None
    runway_heading: int = 0
    density_alt_ft: Optional[float] = None
    pressure_alt_ft: Optional[float] = None
    isa_deviation_c: Optional[float] = None
    da_level: Optional[str] = None
    r_vis: Optional[float] = None
    r_ceil: Optional[float] = None
    r_xwind: Optional[float] = None
    r_gust: Optional[float] = None
    r_wx: Optional[float] = None
    r_fog: Optional[float] = None
    r_taf: Optional[float] = None
    dominant_factor: Optional[str] = None
    guardrail_reason: str = ""          # motivo del piso conjuntivo (showstopper individual)
    # Momento de la muestra que produjo el veredicto. En el camino NWP se evalua
    # el peor caso de TODA la ventana de vuelo, que rara vez coincide con la hora
    # que muestra la tarjeta (`obs_time`): sin esto el piloto lee "Xwind 1.1 kt"
    # y un cartel que dice "viento cruzado 10 kt", y no puede reconciliarlos.
    worst_obs_time: Optional[int] = None
    # True si la hora de salida pedida cae fuera del horizonte del pronostico y
    # hubo que evaluar la mas cercana disponible.
    forecast_out_of_range: bool = False
    next_go_from: Optional[int] = None
    # Luz diurna (bloqueo nocturno)
    is_night: bool = False
    daylight_tight: bool = False        # de día pero a < 45 min del ocaso
    sunrise_unix: Optional[int] = None
    sunset_unix: Optional[int] = None
    min_to_sunset: Optional[int] = None
    # NOTAM operacional
    notam_blocked: bool = False
    # Nubes: capa BKN/OVC por debajo del crucero (volaría sobre la capa)
    cloud_below_cruise: bool = False
    fetch_ok: bool = True
    error_message: str = ""


class DiversionAirport(BaseModel):
    code: str
    name: str
    lat: float
    lon: float
    dist_km: float
    elev_ft: int
    province: str = ""


class CheckpointWeather(BaseModel):
    """
    Condiciones NWP en un checkpoint intermedio de la ruta.

    Un punto de ruta tiene DOS realidades a la vez y este modelo las separa en
    vez de confundirlas: el suelo que queda debajo y el aire por el que el
    avion efectivamente lo cruza.

      * Los campos SIN prefijo son de SUPERFICIE. La visibilidad y el techo
        siguen ahi porque Open-Meteo no los publica por nivel de presion, y
        porque son los que alimentan r_vis y r_ceil del puntaje.

      * Los campos `level_*` son las condiciones EN EL NIVEL DE CRUCERO:
        temperatura, punto de rocio, humedad y nubosidad del nivel, mas su
        altura geopotencial real. Vienen en la MISMA peticion que el viento,
        asi que no cuestan una llamada extra.

    El viento (`wind_*`) ya es del nivel cuando se pidio altitud de crucero.

    Antes esta clase decia "altitud de crucero" y devolvia la temperatura de
    superficie: a 15.000 ft eso es un error de decenas de grados. Los nombres
    ahora dicen de donde sale cada dato.
    """
    wind_dir: Optional[int] = None
    wind_spd_kt: Optional[float] = None
    wind_gust_kt: Optional[float] = None
    wind_variable: bool = False
    visibility_km: Optional[float] = None
    ceiling_ft: Optional[int] = None
    temp_c: Optional[float] = None
    spread_c: Optional[float] = None
    wx_codes: List[str] = []
    cloud_cover_pct: Optional[int] = None    # cobertura de nubes 0-100 %
    precip_mm: Optional[float] = None        # precipitación horaria en mm
    flight_category: Optional[str] = None
    r_vis: Optional[float] = None
    r_ceil: Optional[float] = None
    r_gust: Optional[float] = None
    r_wx: Optional[float] = None
    r_fog: Optional[float] = None
    dominant_factor: Optional[str] = None

    # ── Condiciones EN EL NIVEL DE CRUCERO ────────────────────────────────────
    level_temp_c: Optional[float] = None       # temperatura en el nivel
    level_dewpoint_c: Optional[float] = None   # punto de rocio en el nivel
    level_rh_pct: Optional[int] = None         # humedad relativa en el nivel
    level_cloud_pct: Optional[int] = None      # nubosidad EN el nivel
    level_altitude_ft: Optional[int] = None    # altura geopotencial real
    level_below_zero: bool = False             # el nivel esta bajo cero


class RouteWaypoint(BaseModel):
    code: str
    name: str
    lat: float
    lon: float
    r_total: float
    decision: str
    diversions: List[DiversionAirport] = []
    is_checkpoint: bool = False
    cruise_alt_ft: Optional[int] = None
    chk_weather: Optional[CheckpointWeather] = None   # meteo NWP del checkpoint
    alt_via: Optional[DiversionAirport] = None         # aeródromo alternativo si NO GO
    # Aerovías
    is_airway_waypoint: bool = False
    airway_name: Optional[str] = None       # ej. "W6"
    airway_mea_ft: Optional[int] = None     # MEA del segmento
    fir_contact: Optional[str] = None       # "Córdoba Control" — solo en entry
    is_airway_entry: bool = False
    is_airway_exit: bool = False
    # Corredores VFR (transito de TMA BA / Córdoba)
    is_corridor_waypoint: bool = False
    corridor_id: Optional[str] = None
    corridor_name: Optional[str] = None       # nombre del corredor (ej. "BRANDSEN - CAÑUELAS")
    corridor_region: Optional[str] = None     # "TMA Buenos Aires" | "TMA Córdoba"
    corridor_limit_ft: Optional[int] = None   # límite superior publicado
    corridor_limit_ref: Optional[str] = None  # "MSL" | "AGL"
    # Aeródromos intermedios (fuera de la línea de aerovía)
    is_emergency_airport: bool = False      # aeródromo cercano para emergencia/referencia
    is_fuel_stop: bool = False              # escala de combustible recomendada
    dist_from_prev_km: Optional[float] = None  # distancia desde el aeródromo previo de la ruta
    # Estimación de tiempo en ruta
    eta_utc: Optional[int] = None           # Unix UTC estimado de paso por el waypoint
    elapsed_min: Optional[int] = None       # minutos transcurridos desde el despegue


class RouteCard(BaseModel):
    found: bool
    flight_rules: str = "VFR"          # "VFR" | "IFR" — modo de la ruta calculada
    path: List[str] = []
    waypoints: List[RouteWaypoint] = []
    total_dist_km: float = 0.0
    total_time_h: float = 0.0
    total_fuel_l: float = 0.0
    fuel_ok: bool = True
    needs_fuel_stop: bool = False
    legs: List[Dict] = []
    alternate: Optional[Dict] = None
    airspace_conflicts: List[Dict] = []
    error: str = ""


class EvaluateResponse(BaseModel):
    global_decision: str
    origin: WeatherCard
    dest: WeatherCard
    route: RouteCard
    briefing: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dep_time(s: str) -> int:
    if not s.strip():
        return int(time.time()) + 3600
    try:
        h, m = map(int, s.strip().split(":"))
        now = datetime.now(tz=timezone.utc)
        dep = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dep < now:
            dep += timedelta(days=1)
        return int(dep.timestamp())
    except Exception:
        return int(time.time()) + 3600


def _clamp_vfr_alt(alt_ft, aircraft: AircraftProfile) -> Optional[int]:
    """
    Valida la altitud de crucero VFR elegida por el piloto.

    La altitud es una decisión del piloto, no del sistema: se respeta el valor
    pedido y solo se lo acota al TECHO DE SERVICIO de la aeronave, que es un
    límite físico y no una preferencia. Por debajo se exige un piso simbólico de
    1000 ft para descartar entradas absurdas; el margen real contra el terreno
    lo verifica el perfil vertical, que conoce el relieve del tramo.

    Devuelve None si no se pidió altitud (el sistema la deriva por la regla de
    los semicírculos) o si el valor no es interpretable.
    """
    if alt_ft is None:
        return None
    try:
        alt = int(alt_ft)
    except (TypeError, ValueError):
        return None
    if alt <= 0:
        return None
    return max(1000, min(alt, aircraft.service_ceiling_ft))


def _corridor_alts_msl(corridor_wps: list, ap: AirportInfo,
                       next_ap: AirportInfo) -> List[int]:
    """
    Convierte el limite superior de un corredor VFR a altitud MSL.

    Los corredores visuales publican su techo en **AGL** (`limit_reference`),
    es decir sobre el terreno, no sobre el nivel del mar. Usarlo tal cual como
    altitud de crucero produce disparates: el corredor de la TMA Cordoba tiene
    1500 ft AGL y, tomado como MSL, deja al avion 2200 ft POR DEBAJO del propio
    aerodromo de salida (SACC esta a 3734 ft). Ademas envenena la consulta NWP,
    que termina pidiendo el nivel de presion de 1500 ft para un vuelo que va a
    5200.

    El terreno se resuelve con una UNICA peticion SRTM para todos los puntos del
    tramo. Si falla, se degrada a interpolar entre las elevaciones de los dos
    aerodromos: peor estimacion, pero del orden correcto.
    """
    if not corridor_wps:
        return []

    puntos = [(cw["lat"], cw["lon"]) for cw in corridor_wps]
    try:
        elevaciones = get_elevations_m(puntos)
    except Exception as e:
        logger.warning(f"No se pudo consultar el terreno del corredor VFR: {e}")
        elevaciones = [None] * len(puntos)

    salida: List[int] = []
    n = max(1, len(corridor_wps) - 1)
    for i, cw in enumerate(corridor_wps):
        limite = cw.get("upper_limit_ft") or 0
        ref = str(cw.get("limit_reference") or "").upper()

        if "AGL" not in ref:
            # Ya viene en MSL: se usa tal cual.
            salida.append(int(limite))
            continue

        elev_m = elevaciones[i] if i < len(elevaciones) else None
        if elev_m is not None:
            terreno_ft = elev_m * M_TO_FT
        else:
            frac = i / n
            terreno_ft = ap.elev_ft + frac * (next_ap.elev_ft - ap.elev_ft)
        salida.append(int(round(terreno_ft + limite)))
    return salida


def _to_card(result, runway_heading: int, ap: AirportInfo, notams: list = None) -> WeatherCard:
    wx = result.weather
    notam_models = [
        Notam(
            notam_id   = n.notam_id,
            message    = n.message,
            start_date = n.start_date,
            end_date   = n.end_date,
            q_code     = n.q_code,
        )
        for n in (notams or [])
    ]

    xwind = headwind = None
    if wx:
        try:
            cw = compute_crosswind(
                wx.wind_dir, wx.wind_spd_kt, runway_heading,
                wx.wind_gust_kt, wx.wind_variable,
            )
            xwind   = round(cw.crosswind_kt, 1)
            headwind = round(cw.headwind_kt, 1)
        except Exception:
            pass

    da_ft = pa_ft = isa_dev = da_lev = None
    if result.density_altitude:
        da = result.density_altitude
        da_ft   = round(da.density_alt_ft)
        pa_ft   = round(da.pressure_alt_ft)
        isa_dev = round(da.isa_deviation_c, 1)
        try:
            da_lev = da_advisory(da)
        except Exception:
            da_lev = "NORMAL"

    sb = result.score_breakdown
    return WeatherCard(
        station_id      = result.station_id,
        name            = ap.name,
        elev_ft         = ap.elev_ft,
        decision        = result.decision,
        r_total         = round(result.r_total, 3),
        weather_source  = result.weather_source,
        raw_metar       = (wx.raw_string if (wx and result.weather_source == "metar") else None),
        notams          = notam_models,
        hard_blocked    = result.hard_blocked,
        blocker_summary = result.blocker_summary,
        wind_dir        = wx.wind_dir if wx else None,
        wind_spd_kt     = wx.wind_spd_kt if wx else None,
        wind_gust_kt    = wx.wind_gust_kt if wx else None,
        wind_variable   = wx.wind_variable if wx else False,
        visibility_km   = wx.visibility_km if wx else None,
        ceiling_ft      = wx.ceiling_ft if wx else None,
        temp_c          = wx.temp_c if wx else None,
        dewpoint_c      = wx.dewpoint_c if wx else None,
        spread_c        = wx.spread_c if wx else None,
        altimeter_hpa   = wx.altimeter_hpa if wx else None,
        wx_codes        = list(wx.wx_codes) if wx else [],
        flight_category = wx.flight_category if wx else None,
        obs_time        = result.obs_time,
        xwind_kt        = xwind,
        headwind_kt     = headwind,
        runway_heading  = runway_heading,
        density_alt_ft  = da_ft,
        pressure_alt_ft = pa_ft,
        isa_deviation_c = isa_dev,
        da_level        = da_lev,
        r_vis           = round(sb.r_vis, 3)   if sb else None,
        r_ceil          = round(sb.r_ceil, 3)  if sb else None,
        r_xwind         = round(sb.r_xwind, 3) if sb else None,
        r_gust          = round(sb.r_gust, 3)  if sb else None,
        r_wx            = round(sb.r_wx, 3)    if sb else None,
        r_fog           = round(sb.r_fog, 3)   if sb else None,
        r_taf           = round(sb.r_taf, 3)   if sb else None,
        dominant_factor = sb.dominant_factor   if sb else None,
        guardrail_reason= sb.guardrail_reason  if sb else "",
        worst_obs_time  = getattr(result, 'worst_obs_time', None),
        forecast_out_of_range = getattr(result, 'forecast_out_of_range', False),
        next_go_from    = result.next_go_from,
        fetch_ok        = result.fetch_ok,
        error_message   = result.error_message,
    )


def _apply_operational_blockers(
    card: WeatherCard, ap: AirportInfo, when_unix: int, notams: list, phase: str,
    flight_rules: str = "VFR", cruise_alt_ft: int = 7500,
    result = None,
) -> None:
    """
    Aplica los bloqueos operacionales NO meteorológicos sobre una ficha ya
    construida (muta el card y su decisión):

      - Luz diurna (SOLO VFR): si el momento (`when_unix`) cae de noche/anochecer
        en el aeródromo → NO GO. Si es de día pero a < 45 min del ocaso → CAUTION.
        En IFR no aplica: el vuelo nocturno es válido (con habilitación/equipo).
      - NOTAM (todo régimen): aeródromo cerrado o todas sus pistas cerradas → NO GO.

    `phase` ("despegue" | "aterrizaje") solo se usa para el mensaje.
    """
    # ── Luz diurna (solo VFR) ──
    if flight_rules == "VFR":
        dl = daylight_status(ap.lat, ap.lon, when_unix)
        card.sunrise_unix  = dl["sunrise_unix"]
        card.sunset_unix   = dl["sunset_unix"]
        card.min_to_sunset = dl["min_to_sunset"]
        if not dl["is_day"]:
            card.is_night        = True
            card.decision        = "NO GO"
            card.hard_blocked    = True
            card.blocker_summary = (f"Vuelo nocturno: el {phase} cae fuera de luz diurna. "
                                    f"VFR diurno solamente.")
        elif dl["tight"]:
            card.daylight_tight = True
            if card.decision == "GO":
                card.decision = "CAUTION"

    # ── NOTAM operacional ──
    designators = runway_designators([r.heading for r in ap.runways])
    impact = assess_notam_impact(notams, designators)
    if impact.blocking:
        card.notam_blocked   = True
        card.decision        = "NO GO"
        card.hard_blocked    = True
        card.blocker_summary = (card.blocker_summary + " · " if card.blocker_summary else "") + impact.reason

    # ── Nubes: capa BKN/OVC por debajo de la altitud de crucero (solo VFR) ──
    # Si la base de la capa (MSL = elev del aerodromo + techo AGL) queda por
    # debajo del crucero, el piloto VFR volaria SOBRE la capa, perdiendo
    # referencia con la superficie. Dato de nubes estimado -> solo CAUTION.
    if flight_rules == "VFR" and card.ceiling_ft is not None:
        cloud_base_msl = (ap.elev_ft or 0) + card.ceiling_ft
        if cloud_base_msl < cruise_alt_ft:
            card.cloud_below_cruise = True
            if card.decision == "GO":
                card.decision = "CAUTION"

    # ── Propagar el veredicto final al DecisionResult ──
    # El briefing se arma desde el DecisionResult, no desde la ficha. Sin esta
    # sincronizacion el briefing podia afirmar GO mientras la tarjeta mostraba
    # NO GO por vuelo nocturno o por NOTAM de cierre: dos veredictos distintos
    # para el mismo vuelo en la misma pantalla.
    if result is not None:
        result.decision        = card.decision
        result.hard_blocked    = card.hard_blocked
        result.blocker_summary = card.blocker_summary


# ── Helper: aeródromos de desvío por waypoint ────────────────────────────────

def _find_diversions(
    waypoints: List[RouteWaypoint],
    max_km: float = 80.0,
    n: int = 2,
) -> dict:
    """
    Para cada waypoint devuelve los n aeródromos más cercanos no en la ruta,
    dentro de max_km. Nunca hace requests HTTP.
    """
    route_codes = {wp.code for wp in waypoints}
    result: dict = {}
    for wp in waypoints:
        nearby = []
        for code, ap in AIRPORTS.items():
            if code in route_codes:
                continue
            d = haversine_km(wp.lat, wp.lon, ap.lat, ap.lon)
            if d <= max_km:
                nearby.append((d, code, ap))
        nearby.sort(key=lambda x: x[0])
        result[wp.code] = [
            DiversionAirport(
                code=c, name=a.name, lat=a.lat, lon=a.lon,
                dist_km=round(d, 1), elev_ft=a.elev_ft, province=a.province,
            )
            for d, c, a in nearby[:n]
        ]
    return result


# ── Meteo en ruta: extraida a decision/enroute.py ─────────────────────────────
# Estas dos funciones vivian aca como helpers privados. Se movieron a
# `decision/enroute.py` porque el copiloto tambien las necesita, y que la capa
# de lenguaje natural importara de `web/` invertiria la direccion de
# dependencias del proyecto. Se reexportan con los nombres viejos para no tocar
# el resto de este archivo.
_evaluate_nwp_at_coord = evaluate_nwp_at_coord
_nwp_series_at_coord   = nwp_series_at_coord


# ── Helper: aeródromo alternativo más cercano a un checkpoint NO GO ────────────

def _find_best_alt_via(
    chk_lat: float, chk_lon: float, route_codes: set, max_km: float = 150.0
) -> Optional[DiversionAirport]:
    """Devuelve el aeródromo más cercano al checkpoint que no está en la ruta."""
    best_dist = max_km + 1.0
    best = None
    for code, ap in AIRPORTS.items():
        if code in route_codes:
            continue
        d = haversine_km(chk_lat, chk_lon, ap.lat, ap.lon)
        if d < best_dist:
            best_dist = d
            best = (code, ap, d)
    if best is None:
        return None
    code, ap, d = best
    return DiversionAirport(
        code=code, name=ap.name, lat=ap.lat, lon=ap.lon,
        dist_km=round(d, 1), elev_ft=ap.elev_ft, province=ap.province or "",
    )


# ── Helper: generar waypoints completos de la ruta con checkpoints intermedios ─

_FUSION_KM = 50.0   # radio de fusión checkpoint ↔ waypoint aerovía


def _generate_route_waypoints(
    path: List[str],
    aircraft: AircraftProfile,
    dep_time: int,
    duration_hours: float,
    r_map: dict,
    mock: bool,
    step_km: float = 230.0,
    airway_map: Optional[dict] = None,
    flight_rules: str = "VFR",
    vfr_cruise_alt_ft: Optional[int] = None,
) -> List[RouteWaypoint]:
    """
    Construye la lista completa de waypoints de la ruta.
    Los checkpoints NWP se evalúan en paralelo para minimizar latencia.
    Cuando existe aerovia para un tramo, inserta sus waypoints y fusiona
    los checkpoints meteo que caigan a menos de FUSION_KM de un waypoint aerovía.
    """
    if airway_map is None:
        airway_map = {}

    leg_dists: List[float] = []
    for i in range(len(path) - 1):
        a = AIRPORTS.get(path[i])
        b = AIRPORTS.get(path[i + 1])
        leg_dists.append(haversine_km(a.lat, a.lon, b.lat, b.lon) if a and b else 0.0)
    total_route_dist = sum(leg_dists)

    # Determinar escalas de combustible: caminando la ruta por distancia
    # acumulada, cuando el próximo tramo superaría el alcance útil, el aeródromo
    # actual (intermedio) se marca como escala de combustible recomendada.
    fuel_stop_indices: set = set()
    usable_range = aircraft.range_km * 0.90   # margen de seguridad sobre alcance útil
    acc = 0.0
    for i in range(len(leg_dists)):
        leg = leg_dists[i]
        if acc + leg > usable_range and i != 0:
            fuel_stop_indices.add(i)   # repostar en el aeródromo i antes del tramo
            acc = leg
        else:
            acc += leg

    last_idx = len(path) - 1

    # Paso 1: construir secuencia ordenada — aeródromos ya completos,
    # waypoints de aerovía ya completos, checkpoints como specs pendientes.
    sequence: list = []
    cumulative_km = 0.0

    for i, code in enumerate(path):
        ap = AIRPORTS.get(code)
        if not ap:
            continue

        # Un aeródromo intermedio sale de la línea de ruta (pasa a ser marcador
        # de emergencia) SOLO si la aerovía es continua a su alrededor, es decir,
        # si ambos tramos adyacentes tienen aerovía. Así la aerovía es la espina
        # de la ruta y el aeródromo queda al costado como referencia.
        # Si algún tramo adyacente no tiene aerovía, el aeródromo es parte real
        # de la línea (ruta directa entre aeródromos).
        is_intermediate = (i != 0 and i != last_idx)
        leg_before_aw = (path[i-1], path[i]) in airway_map if i > 0 else False
        leg_after_aw  = (path[i], path[i+1]) in airway_map if i < last_idx else False
        is_off_spine = is_intermediate and leg_before_aw and leg_after_aw

        r_val = r_map.get(code, 0.0)
        # Umbrales calibrados (risk/weights.py), nunca hardcodeados: el mismo R
        # tiene que dar el mismo veredicto en la ficha, el mapa y la timeline.
        dec = apply_decision_threshold(r_val)
        sequence.append(RouteWaypoint(
            code=code, name=ap.name,
            lat=ap.lat, lon=ap.lon,
            r_total=r_val, decision=dec,
            is_checkpoint=False, cruise_alt_ft=None,
            is_emergency_airport=is_off_spine,
            is_fuel_stop=(i in fuel_stop_indices and is_intermediate),
            dist_from_prev_km=round(leg_dists[i-1], 1) if i > 0 and i-1 < len(leg_dists) else None,
        ))

        if i >= len(path) - 1:
            break

        next_ap = AIRPORTS.get(path[i + 1])
        if not next_ap:
            continue

        leg_km = leg_dists[i] if i < len(leg_dists) else 0.0
        track = int(bearing_deg(ap.lat, ap.lon, next_ap.lat, next_ap.lon))
        if flight_rules == "VFR":
            # Altitud VFR por la regla de los semicírculos: depende del rumbo del
            # tramo y del crucero planificado del avión (NO de la MEA de aerovía).
            # Punto medio del tramo para la declinación magnética. Si el terreno
            # supera esta altitud, el perfil vertical lo marca como conflicto y
            # sugiere recalcular en IFR (no se sube por encima del crucero del
            # avión: un VFR no puede trepar arbitrariamente a librar la cordillera).
            # Si el piloto eligió una altitud a mano, manda la suya: es una
            # decisión operativa y él sabe a qué altura quiere volar. El sistema
            # solo la acota al techo de servicio del avión (ver _clamp_vfr_alt).
            if vfr_cruise_alt_ft is not None:
                cruise_alt = vfr_cruise_alt_ft
            else:
                mid_lat = (ap.lat + next_ap.lat) / 2.0
                mid_lon = (ap.lon + next_ap.lon) / 2.0
                cruise_alt = hemispheric_vfr_altitude(track, mid_lat, mid_lon,
                                                      aircraft.cruise_alt_ft)
        else:
            # IFR sin aerovía disponible: altitud segura del tramo (terreno + buffer),
            # aproximada con la elevación de los aeródromos del tramo.
            cruise_alt = max(7500, max(ap.elev_ft, next_ap.elev_ft) + 3000)

        # VFR: si el tramo directo atraviesa la TMA de Buenos Aires o Córdoba,
        # se debe transitar por el corredor visual publicado. Insertamos los
        # waypoints del corredor (la línea de ruta pasa a seguir el corredor).
        corridor_wps: list = []
        if flight_rules == "VFR":
            try:
                corridor_wps = corridor_path_for_leg(ap.lat, ap.lon, next_ap.lat, next_ap.lon)
            except Exception as e:
                logger.warning(f"Error ruteando corredor VFR {code}->{path[i+1]}: {e}")
        corridor_alts = _corridor_alts_msl(corridor_wps, ap, next_ap)
        for ci, cw in enumerate(corridor_wps):
            sequence.append(RouteWaypoint(
                code=cw["corridor_id"] or "VFR-COR",
                name=f"Corredor {cw['corridor_id']}",
                lat=cw["lat"], lon=cw["lon"],
                r_total=0.0, decision="GO",
                is_checkpoint=False,
                is_corridor_waypoint=True,
                corridor_id=cw["corridor_id"],
                corridor_name=cw["corridor_name"],
                corridor_region=cw["region_name"],
                corridor_limit_ft=cw["upper_limit_ft"],
                corridor_limit_ref=cw["limit_reference"],
                # El techo del corredor es AGL: la altitud de vuelo es MSL.
                cruise_alt_ft=corridor_alts[ci] if ci < len(corridor_alts)
                              else cw["upper_limit_ft"],
            ))

        # Waypoints de aerovía para este tramo
        aw_wps: list = airway_map.get((code, path[i + 1]), [])

        # Altitud de vuelo en la aerovía = MEA del segmento. La meteo en ruta se
        # calcula a la altitud que el avión realmente vuela (la de la aerovía),
        # NO a la altitud de crucero del avión. Fallback a la altitud segura del
        # tramo si el MEA no está disponible.
        def _aw_flight_alt(aw):
            return aw.mea_ft if aw.mea_ft else cruise_alt

        # Insertar waypoints de aerovía como RouteWaypoints (sin meteo aún).
        # Se registra el índice de cada uno para actualizarlo después de la evaluación NWP.
        aw_ncp_start = len(sequence)
        for aw in aw_wps:
            dist_to_chk = cumulative_km + haversine_km(ap.lat, ap.lon, aw.lat, aw.lon)
            frac_total  = dist_to_chk / total_route_dist if total_route_dist > 0 else 0.5
            aw_name = f"{'→ ' if aw.is_entry else '← ' if aw.is_exit else ''}{aw.node_id}"
            sequence.append(RouteWaypoint(
                code=aw.node_id,
                name=aw_name,
                lat=aw.lat, lon=aw.lon,
                r_total=0.0, decision="GO",
                is_checkpoint=False,
                is_airway_waypoint=True,
                airway_name=aw.airway_name,
                airway_mea_ft=aw.mea_ft,
                fir_contact=aw.fir_contact,
                is_airway_entry=aw.is_entry,
                is_airway_exit=aw.is_exit,
                cruise_alt_ft=_aw_flight_alt(aw),
            ))

        if aw_wps:
            # Specs NWP para cada waypoint de aerovía.
            # '_update_idx' apunta al RouteWaypoint no-checkpoint a actualizar con los datos NWP.
            for j, aw in enumerate(aw_wps):
                dist_to_aw = cumulative_km + haversine_km(ap.lat, ap.lon, aw.lat, aw.lon)
                frac_total = dist_to_aw / total_route_dist if total_route_dist > 0 else 0.5
                chk_time   = dep_time + int(frac_total * duration_hours * 3600)
                sequence.append({
                    'seq_code':    aw.node_id,
                    'seq_name':    aw.node_id,
                    'lat':         aw.lat,
                    'lon':         aw.lon,
                    'elev_m':      0.0,
                    'cruise_alt':  _aw_flight_alt(aw),   # meteo a la altitud de la aerovía (MEA)
                    'track':       track,
                    'dep_time':    chk_time,
                    '_fused_aw':   aw,
                    '_update_idx': aw_ncp_start + j,
                })
        elif leg_km > step_km and not corridor_wps:
            # Tramo sin aerovía ni corredor: checkpoints interpolados cada step_km
            n_chk = int(leg_km // step_km)
            for j in range(1, n_chk + 1):
                frac = (j * step_km) / leg_km
                if frac >= 1.0:
                    break
                chk_lat = ap.lat + frac * (next_ap.lat - ap.lat)
                chk_lon = ap.lon + frac * (next_ap.lon - ap.lon)
                dist_to_chk = cumulative_km + j * step_km
                frac_total  = dist_to_chk / total_route_dist if total_route_dist > 0 else 0.5
                chk_time    = dep_time + int(frac_total * duration_hours * 3600)
                sequence.append({
                    'seq_code':  f"WP{i+1}-{j}",
                    'seq_name':  f"En ruta · {round(j * step_km)} km desde {code}",
                    'lat':       chk_lat,
                    'lon':       chk_lon,
                    'elev_m':    (ap.elev_ft + frac * (next_ap.elev_ft - ap.elev_ft)) * 0.3048,
                    'cruise_alt': cruise_alt,
                    'track':     track,
                    'dep_time':  chk_time,
                    '_fused_aw': None,
                })

        cumulative_km += leg_km

    # Paso 2: evaluar todos los checkpoints pendientes en paralelo.
    chk_items = [(idx, spec) for idx, spec in enumerate(sequence) if isinstance(spec, dict)]

    def _eval_chk(idx_spec):
        idx, spec = idx_spec
        r, dec, ref_wx, worst, lvl = _evaluate_nwp_at_coord(
            lat=spec['lat'], lon=spec['lon'], elev_m=spec['elev_m'],
            dep_time=spec['dep_time'], duration_hours=1.0,
            aircraft=aircraft, mock=mock,
            cruise_alt_ft=spec['cruise_alt'], track_bearing=spec['track'],
            flight_rules=flight_rules,
        )
        return idx, spec, r, dec, ref_wx, worst, lvl

    route_codes = set(path)

    if chk_items:
        with ThreadPoolExecutor(max_workers=min(8, len(chk_items))) as ex:
            futures = [ex.submit(_eval_chk, item) for item in chk_items]
            for fut in futures:
                idx, spec, r, dec, ref_wx, worst, lvl = fut.result()

                # Construir resumen meteo del checkpoint si hay datos NWP
                chk_wx = None
                if ref_wx is not None:
                    chk_wx = CheckpointWeather(
                        wind_dir=ref_wx.wind_dir,
                        wind_spd_kt=ref_wx.wind_spd_kt,
                        wind_gust_kt=ref_wx.wind_gust_kt,
                        wind_variable=ref_wx.wind_variable,
                        visibility_km=ref_wx.visibility_km,
                        ceiling_ft=ref_wx.ceiling_ft,
                        temp_c=ref_wx.temp_c,
                        spread_c=ref_wx.spread_c,
                        wx_codes=ref_wx.wx_codes or [],
                        cloud_cover_pct=getattr(ref_wx, 'cloud_cover_pct', None),
                        precip_mm=getattr(ref_wx, 'precip_mm', None),
                        flight_category=ref_wx.flight_category,
                        r_vis=getattr(worst, 'r_vis', None),
                        r_ceil=getattr(worst, 'r_ceil', None),
                        r_gust=getattr(worst, 'r_gust', None),
                        r_wx=getattr(worst, 'r_wx', None),
                        r_fog=getattr(worst, 'r_fog', None),
                        dominant_factor=getattr(worst, 'dominant_factor', None),
                        # Condiciones del nivel de crucero (misma petición)
                        level_temp_c=getattr(lvl, 'level_temp_c', None),
                        level_dewpoint_c=getattr(lvl, 'level_dewpoint_c', None),
                        level_rh_pct=getattr(lvl, 'level_rh_pct', None),
                        level_cloud_pct=getattr(lvl, 'level_cloud_pct', None),
                        level_altitude_ft=getattr(lvl, 'level_altitude_ft', None),
                        level_below_zero=bool(
                            getattr(lvl, 'level_temp_c', None) is not None
                            and lvl.level_temp_c < 0
                        ),
                    )

                # Para checkpoints NO GO, sugerir aeródromo alternativo cercano
                alt_via = None
                if dec == "NO GO":
                    alt_via = _find_best_alt_via(spec['lat'], spec['lon'], route_codes)

                fused_aw   = spec.get('_fused_aw')
                update_idx = spec.get('_update_idx')

                if fused_aw is not None and update_idx is not None:
                    # Actualizar el RouteWaypoint no-checkpoint con los datos NWP.
                    # Mantiene is_checkpoint=False para que _buildAirwayLayer() lo dibuje como círculo.
                    orig = sequence[update_idx]
                    sequence[update_idx] = RouteWaypoint(
                        code=orig.code, name=orig.name,
                        lat=orig.lat, lon=orig.lon,
                        r_total=r, decision=dec,
                        is_checkpoint=False,
                        cruise_alt_ft=spec['cruise_alt'],
                        chk_weather=chk_wx,
                        alt_via=alt_via,
                        is_airway_waypoint=True,
                        airway_name=orig.airway_name,
                        airway_mea_ft=orig.airway_mea_ft,
                        fir_contact=orig.fir_contact,
                        is_airway_entry=orig.is_airway_entry,
                        is_airway_exit=orig.is_airway_exit,
                    )
                    sequence[idx] = None  # descartar el spec dict (ya procesado)
                else:
                    sequence[idx] = RouteWaypoint(
                        code=spec['seq_code'], name=spec['seq_name'],
                        lat=spec['lat'], lon=spec['lon'],
                        r_total=r, decision=dec,
                        is_checkpoint=True, cruise_alt_ft=spec['cruise_alt'],
                        chk_weather=chk_wx,
                        alt_via=alt_via,
                    )

    final_wps = [wp for wp in sequence if isinstance(wp, RouteWaypoint)]

    # Paso 3: ETA acumulado por waypoint a lo largo de la espina de la ruta.
    # Se usa la velocidad de crucero ajustada por el viento NWP de cada punto
    # (cuando está disponible). Los aeródromos de emergencia (fuera de la espina)
    # heredan el ETA del último punto de la espina.
    elapsed_h = 0.0
    prev_pt = None        # (lat, lon) del punto de espina previo
    last_spine_h = 0.0
    for wp in final_wps:
        if wp.is_emergency_airport:
            wp.elapsed_min = round(last_spine_h * 60)
            wp.eta_utc     = dep_time + int(last_spine_h * 3600)
            continue
        if prev_pt is not None:
            dist_km = haversine_km(prev_pt[0], prev_pt[1], wp.lat, wp.lon)
            if dist_km > 0.1:
                track = bearing_deg(prev_pt[0], prev_pt[1], wp.lat, wp.lon)
                gs = aircraft.cruise_kt
                cw = wp.chk_weather
                if cw and cw.wind_spd_kt is not None and cw.wind_dir is not None and not cw.wind_variable:
                    gs = effective_groundspeed_kt(aircraft.cruise_kt, cw.wind_dir, cw.wind_spd_kt, track)
                elapsed_h += leg_time_hours(dist_km, gs)
        wp.elapsed_min = round(elapsed_h * 60)
        wp.eta_utc     = dep_time + int(elapsed_h * 3600)
        last_spine_h   = elapsed_h
        prev_pt        = (wp.lat, wp.lon)

    return final_wps


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/airports")
async def search_airports(q: str = "", limit: int = 10):
    """Búsqueda de aeródromos por código o nombre."""
    q_lower = q.lower().strip()
    if len(q_lower) < 2:
        return []

    results = []
    for code, ap in AIRPORTS.items():
        if q_lower in code.lower() or q_lower in ap.name.lower():
            results.append({
                "code":     code,
                "name":     ap.name,
                "lat":      ap.lat,
                "lon":      ap.lon,
                "elev_ft":  ap.elev_ft,
                "province": ap.province,
                "runways":  [
                    {"heading": r.heading, "label": r.label, "length_m": r.length_m}
                    for r in ap.runways
                ],
            })

    results.sort(key=lambda x: (
        0 if x["code"].lower() == q_lower else
        1 if x["code"].lower().startswith(q_lower) else
        2 if q_lower in x["code"].lower() else 3,
        x["name"],
    ))
    return results[:limit]


@app.get("/api/airport/{code}")
async def airport_info(code: str):
    """
    Ficha completa de un aeródromo: datos físicos (MADHEL/ANAC), pistas,
    servicios, meteo en vivo (METAR/NWP + categoría + density altitude),
    NOTAMs activos (ANAC), espacio aéreo cercano y aeródromos cercanos.
    """
    code = code.upper().strip()
    ap = AIRPORTS.get(code)
    if ap is None:
        raise HTTPException(404, f"Aeródromo desconocido: {code}")

    rwh = ap.runways[0].heading if ap.runways else 180

    # NOTAMs (ANAC) y meteo (engine) en paralelo
    notams = []
    weather_card = None
    try:
        av = AviationWeatherFetcher(mock=False)
    except Exception:
        av = None

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_notam = ex.submit(av.get_notams, code) if av else None
        def _eval_weather():
            try:
                engine = DecisionEngine(mock=False, aircraft=get_profile("Pipistrel Alpha Trainer"))
                return engine.evaluate(code, rwh, int(time.time()) + 600, 1.0)
            except Exception as e:
                logger.warning(f"Error meteo ficha {code}: {e}")
                return None
        fut_wx = ex.submit(_eval_weather)
        if fut_notam is not None:
            try:
                notams = fut_notam.result() or []
            except Exception:
                notams = []
        wx_result = fut_wx.result()

    if wx_result is not None:
        try:
            weather_card = _to_card(wx_result, rwh, ap, notams)
        except Exception as e:
            logger.warning(f"Error armando card ficha {code}: {e}")

    # Si la meteo falló, igual exponer los NOTAMs sueltos
    notam_models = [
        Notam(notam_id=n.notam_id, message=n.message, start_date=n.start_date,
              end_date=n.end_date, q_code=n.q_code)
        for n in notams
    ]

    # Espacio aéreo: zonas cuyo aeródromo cae dentro o muy cerca
    airspace = []
    for z in AIRSPACE_ZONES:
        d = haversine_km(ap.lat, ap.lon, z.center_lat, z.center_lon)
        if d <= z.radius_km + 15.0:   # dentro de la zona o en su borde
            airspace.append({
                "name": z.name, "zone_type": z.zone_type,
                "is_restricted": z.is_restricted, "is_controlled": z.is_controlled,
                "floor_ft": z.floor_ft, "ceiling_ft": z.ceiling_ft,
                "dist_km": round(d, 1), "notes": z.notes,
            })
    airspace.sort(key=lambda x: x["dist_km"])

    # Aeródromos cercanos (otros, dentro de 60 km)
    nearby = []
    for c2, ap2 in AIRPORTS.items():
        if c2 == code:
            continue
        d = haversine_km(ap.lat, ap.lon, ap2.lat, ap2.lon)
        if d <= 60.0:
            nearby.append({"code": c2, "name": ap2.name, "dist_km": round(d, 1),
                           "elev_ft": ap2.elev_ft, "lat": ap2.lat, "lon": ap2.lon})
    nearby.sort(key=lambda x: x["dist_km"])

    return {
        "airport": {
            "code": ap.code, "name": ap.name,
            "icao": ap.icao_code, "local_id": ap.local_id, "iata": ap.iata_code,
            "lat": ap.lat, "lon": ap.lon,
            "elev_ft": ap.elev_ft, "elev_estimated": ap.elev_estimated,
            "province": ap.province, "municipality": ap.municipality,
            "fir": get_fir(ap.lat, ap.lon),
            "is_public": ap.is_public, "condition": ap.condition, "control": ap.control,
            "fuel": ap.fuel, "schedule": ap.schedule, "phones": list(ap.phones),
            "norms": ap.norms_particular,
            "runways": [
                {"label": r.label, "heading": r.heading, "length_m": r.length_m,
                 "width_m": r.width_m, "surface": r.surface,
                 "thr_lat": r.thr_lat, "thr_lon": r.thr_lon}
                for r in ap.runways
            ],
        },
        "weather": weather_card,
        "notams": notam_models,
        "airspace": airspace[:8],
        "nearby": nearby[:6],
    }


@app.get("/api/aircraft")
async def list_aircraft():
    """Lista de perfiles de aeronave disponibles."""
    out = []
    for name in PROFILE_NAMES:
        p = get_profile(name)
        out.append({
            "name":              p.name,
            "category":          p.category,
            "cruise_kt":         p.cruise_kt,
            "crosswind_max_kt":  p.crosswind_max_kt,
            "range_km":          round(p.range_km),
            # El frontend los usa para el selector de altitud VFR manual:
            # cruise_alt_ft es la sugerencia inicial, service_ceiling_ft el tope.
            "cruise_alt_ft":     p.cruise_alt_ft,
            "service_ceiling_ft": p.service_ceiling_ft,
        })
    return out


@app.get("/api/airports/map")
async def airports_for_map():
    """Lista compacta de todos los aeródromos para la capa de mapa Leaflet."""
    return [
        {
            "code":      code,
            "name":      ap.name,
            "lat":       ap.lat,
            "lon":       ap.lon,
            "elev_ft":   ap.elev_ft,
            "province":  ap.province,
            "fuel":      ap.fuel,
            "schedule":  ap.schedule,
            "phones":    list(ap.phones),
            "condition": ap.condition,
            "control":   ap.control,
            "runways":   [{"heading": r.heading, "length_m": r.length_m, "surface": r.surface} for r in ap.runways],
        }
        for code, ap in AIRPORTS.items()
    ]


@app.get("/api/airspace")
async def get_airspace():
    """Zonas de espacio aéreo para la capa Leaflet."""
    result = []
    for z in AIRSPACE_ZONES:
        entry = {
            "name":          z.name,
            "zone_type":     z.zone_type,
            "is_restricted": z.is_restricted,
            "is_controlled": z.is_controlled,
            "center_lat":    z.center_lat,
            "center_lon":    z.center_lon,
            "radius_km":     z.radius_km,
            "floor_ft":      z.floor_ft,
            "ceiling_ft":    z.ceiling_ft,
            "notes":         z.notes,
            "polygon":       [list(pt) for pt in z.polygon] if z.polygon else None,
        }
        result.append(entry)
    return result


# Corredores VFR de las TMA Buenos Aires y Córdoba (los únicos publicados en el
# país). Datos oficiales (cartas VAC / corredores VFR de ANAC) cargados desde
# GeoJSON. Se cachean en memoria tras la primera lectura.
_VFR_CORRIDORS_CACHE: Optional[dict] = None


def _load_vfr_corridors() -> dict:
    """Carga y fusiona los GeoJSON de corredores VFR, etiquetando la región."""
    global _VFR_CORRIDORS_CACHE
    if _VFR_CORRIDORS_CACHE is not None:
        return _VFR_CORRIDORS_CACHE
    import json
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    feats: list = []
    for region, fname in (("BA", "corredores_vfr_TMA_BA.geojson"),
                          ("CBA", "corredores_vfr_TMA_CBA.geojson")):
        try:
            with open(os.path.join(base, fname), encoding="utf-8") as f:
                fc = json.load(f)
            for ft in fc.get("features", []):
                ft.setdefault("properties", {})["region"] = region
                feats.append(ft)
        except Exception as e:
            logger.warning(f"No se pudieron cargar los corredores VFR {region}: {e}")
    _VFR_CORRIDORS_CACHE = {"type": "FeatureCollection", "features": feats}
    return _VFR_CORRIDORS_CACHE


@app.get("/api/vfr_corridors")
async def vfr_corridors():
    """Corredores VFR publicados de las TMA Buenos Aires y Córdoba (GeoJSON)."""
    return _load_vfr_corridors()


class FlightPlanRequest(BaseModel):
    # Datos auto (de la evaluación)
    origin: str
    dest: str
    aircraft: str = "Pipistrel Alpha Trainer"
    flight_rules: str = "VFR"
    departure_time: str = ""            # "HH:MM" UTC
    eet_min: int = 0                    # tiempo estimado en ruta (min)
    path: List[str] = []               # ICAOs de la ruta (casilla 15)
    alternate: str = ""
    # Datos que completa el piloto
    registration: str = ""             # casilla 7 (matrícula)
    flight_type: str = "G"             # casilla 8
    equip_radio: str = "S"             # casilla 10a
    equip_ssr: str = "C"               # casilla 10b (transponder)
    pob: str = ""                      # casilla 19 P/
    pic: str = ""                      # casilla 19 C/
    color_markings: str = ""           # casilla 19 A/
    endurance: str = ""                # casilla 19 E/ (HHMM; vacío = autonomía total)
    radio_emerg: str = ""              # casilla 19 R/ (lo provee el piloto)
    survival: str = ""                 # casilla 19 S/
    jackets: str = ""                  # casilla 19 J/
    dinghies: str = ""                 # casilla 19 D/
    alternate2: str = ""               # casilla 16 (2da alternativa)
    remarks: str = ""                  # casilla 18 RMK/


@app.post("/api/flightplan")
async def flightplan(req: FlightPlanRequest):
    """
    Arma el plan de vuelo OACI (borrador) a partir de los datos de la evaluación
    + los campos que completa el piloto. NO lo radica: el piloto lo presenta.
    """
    origin = req.origin.upper().strip()
    dest   = req.dest.upper().strip()
    if origin not in AIRPORTS or dest not in AIRPORTS:
        raise HTTPException(400, "Aeródromo desconocido")
    try:
        ac = get_profile(req.aircraft)
    except KeyError:
        raise HTTPException(400, f"Aeronave desconocida: {req.aircraft}")

    o = AIRPORTS[origin]
    d = AIRPORTS[dest]
    rules = "I" if (req.flight_rules or "VFR").upper() == "IFR" else "V"

    # Casilla 13/16: indicador de lugar OACI de 4 letras. Si el aeródromo no
    # tiene ICAO asignado, va ZZZZ y el nombre/lugar en la casilla 18 (DEP//DEST/).
    dep_icao  = o.icao_code if (getattr(o, "icao_code", "") and len(o.icao_code) == 4) else "ZZZZ"
    dest_icao = d.icao_code if (getattr(d, "icao_code", "") and len(d.icao_code) == 4) else "ZZZZ"

    # EOBT + DOF desde la hora de salida
    dep_unix = _parse_dep_time(req.departure_time)
    dt = datetime.fromtimestamp(dep_unix, tz=timezone.utc)
    eobt = dt.strftime("%H%M")
    dof  = dt.strftime("%y%m%d")

    # EET total
    em  = max(0, int(req.eet_min))
    eet = f"{em // 60:02d}{em % 60:02d}"

    # Nivel de crucero: VFR por regla de semicírculos, IFR el crucero del avión
    if rules == "V":
        trk = bearing_deg(o.lat, o.lon, d.lat, d.lon)
        level_ft = hemispheric_vfr_altitude(trk, (o.lat + d.lat) / 2, (o.lon + d.lon) / 2, ac.cruise_alt_ft)
    else:
        level_ft = ac.cruise_alt_ft

    # Ruta (casilla 15): vuelo fuera de rutas ATS → DCT entre puntos sucesivos.
    inter = [c for c in req.path if c not in (origin, dest)]
    route = ("DCT " + " DCT ".join(inter) + " DCT") if inter else "DCT"

    # Autonomía: pre-calcular (tanque lleno) si el piloto no la ingresó
    endurance = req.endurance.strip()
    if not endurance and ac.fuel_flow_lph > 0:
        end_h = ac.fuel_capacity_l / ac.fuel_flow_lph
        endurance = f"{int(end_h):02d}{int(round((end_h % 1) * 60)):02d}"

    fp = build_flight_plan(
        registration=req.registration, flight_rules=rules, flight_type=req.flight_type,
        icao_type=ac.icao_type, wake=ac.wake_cat,
        equip_radio=req.equip_radio, equip_ssr=req.equip_ssr,
        dep_icao=dep_icao, dep_name=o.name, eobt=eobt,
        speed_kt=ac.cruise_kt, level_ft=level_ft, route=route,
        dest_icao=dest_icao, dest_name=d.name, eet=eet,
        alternate=req.alternate, alternate2=req.alternate2,
        dof=dof, endurance=endurance, pob=req.pob, pic=req.pic,
        color_markings=req.color_markings, radio_emerg=req.radio_emerg,
        survival=req.survival, jackets=req.jackets, dinghies=req.dinghies,
        remarks=req.remarks, aircraft_name=ac.name,
    )
    return {
        "fields":  fp.fields,
        "message": fp.message,
        "meta": {
            "origin": origin, "dest": dest, "aircraft": ac.name,
            "level_ft": level_ft, "speed_kt": ac.cruise_kt,
            "flight_rules": req.flight_rules,
        },
    }


class TimelineRequest(BaseModel):
    origin: str
    dest: str
    aircraft: str = "Pipistrel Alpha Trainer"
    origin_runway: int = 180
    dest_runway: int = 180
    hours: int = Field(default=24, ge=6, le=48)


@app.post("/api/timeline")
async def timeline(req: TimelineRequest):
    """
    Serie horaria de R_total (GO/CAUTION/NO GO) para origen y destino a lo largo
    de las próximas `hours` horas. Permite ver a qué hora conviene despegar y
    derivar la tendencia (mejora/empeora) en la ventana del vuelo.
    """
    origin = req.origin.upper().strip()
    dest   = req.dest.upper().strip()
    if origin not in AIRPORTS:
        raise HTTPException(400, f"Aeródromo desconocido: {origin}")
    if dest not in AIRPORTS:
        raise HTTPException(400, f"Aeródromo desconocido: {dest}")
    try:
        aircraft = get_profile(req.aircraft)
    except KeyError:
        raise HTTPException(400, f"Aeronave desconocida: {req.aircraft}")

    o_ap, d_ap = AIRPORTS[origin], AIRPORTS[dest]
    # Inicio: próxima hora en punto UTC
    now = int(time.time())
    start_utc = ((now // 3600) + 1) * 3600

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_o = ex.submit(_nwp_series_at_coord, o_ap.lat, o_ap.lon, o_ap.elev_ft * 0.3048,
                          req.origin_runway, aircraft, False, start_utc, req.hours)
        fut_d = ex.submit(_nwp_series_at_coord, d_ap.lat, d_ap.lon, d_ap.elev_ft * 0.3048,
                          req.dest_runway, aircraft, False, start_utc, req.hours)
        ser_o = fut_o.result()
        ser_d = fut_d.result()

    # Combinar por hora: la peor de origen/destino manda
    d_by_t = {p['t']: p for p in ser_d}
    combined: List[dict] = []
    for po in ser_o:
        pd = d_by_t.get(po['t'])
        if pd is None:
            continue
        r_max = max(po['r'], pd['r'])
        dec = apply_decision_threshold(r_max)
        combined.append({
            'hour_utc': po['t'],
            'r_origin': po['r'], 'dec_origin': po['dec'],
            'r_dest':   pd['r'], 'dec_dest':   pd['dec'],
            'r_max': round(r_max, 3), 'dec': dec,
        })

    return {
        'origin': origin, 'dest': dest,
        'start_utc': start_utc, 'hours': req.hours,
        'series': combined,
    }


class ProfilePoint(BaseModel):
    lat: float
    lon: float
    mea_ft: Optional[int] = None        # MEA de la aerovía en este punto (si aplica)
    is_airport: bool = False
    code: Optional[str] = None
    is_fuel_stop: bool = False


class ProfileRequest(BaseModel):
    points: List[ProfilePoint]          # espina de la ruta, en orden
    aircraft: Optional[str] = None      # nombre del avión → altitud de crucero real
    cruise_alt_ft: int = 7500           # fallback si no se pasa aircraft
    sample_km: float = Field(default=15.0, ge=5.0, le=50.0)
    flight_rules: str = "VFR"           # "VFR" | "IFR" — define la altitud del perfil


@app.post("/api/profile")
async def profile(req: ProfileRequest):
    """
    Perfil vertical de la ruta: terreno (SRTM), MEA de aerovías y altitud de
    crucero a lo largo de la distancia. Recibe la espina ya calculada (no
    recalcula la ruta) y muestrea el terreno entre sus puntos.
    """
    pts = req.points
    if len(pts) < 2:
        return {"profile": [], "airports": [], "total_km": 0.0}

    # Altitud de crucero del perfil vertical.
    #
    # En IFR es la característica del avión —la que determina qué aerovías puede
    # usar—, no la del segmento. En VFR, en cambio, la altitud la elige el piloto
    # (o se deriva por semicírculos) y viaja en el request desde los waypoints ya
    # calculados: pisarla con el crucero del perfil dibujaría el terreno contra
    # una altura que el vuelo no va a volar.
    cruise_alt = req.cruise_alt_ft
    if req.aircraft and req.flight_rules != "VFR":
        try:
            cruise_alt = get_profile(req.aircraft).cruise_alt_ft
        except KeyError:
            pass

    # Distancia acumulada de cada punto de la espina
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + haversine_km(pts[i-1].lat, pts[i-1].lon, pts[i].lat, pts[i].lon))
    total = cum[-1]
    if total <= 0:
        return {"profile": [], "airports": [], "total_km": 0.0}

    is_vfr = (req.flight_rules or "VFR").upper() != "IFR"

    # Muestrear densamente a lo largo de la polilínea (cap de puntos para no
    # saturar la API de terreno: el paso se agranda en rutas muy largas).
    sample_km = max(req.sample_km, total / 200.0)
    sampled: list = []   # (lat, lon, dist_km, mea_ft, vfr_alt_ft)
    d = 0.0
    seg = 0
    while d <= total + 1e-6:
        while seg < len(pts) - 2 and cum[seg + 1] < d:
            seg += 1
        seg_len = (cum[seg + 1] - cum[seg]) or 1e-9
        t = max(0.0, min(1.0, (d - cum[seg]) / seg_len))
        lat = pts[seg].lat + t * (pts[seg + 1].lat - pts[seg].lat)
        lon = pts[seg].lon + t * (pts[seg + 1].lon - pts[seg].lon)
        # MEA del segmento: el del waypoint destino (donde Dijkstra guarda el MEA),
        # con respaldo en el de origen.
        mea = pts[seg + 1].mea_ft or pts[seg].mea_ft
        # En VFR, la altitud de crucero del segmento surge de la regla de los
        # semicírculos (rumbo del segmento + crucero del avión).
        vfr_alt = None
        if is_vfr:
            seg_track = bearing_deg(pts[seg].lat, pts[seg].lon, pts[seg + 1].lat, pts[seg + 1].lon)
            vfr_alt = hemispheric_vfr_altitude(seg_track, lat, lon, cruise_alt)
        sampled.append((lat, lon, d, mea, vfr_alt))
        d += sample_km

    # Terreno de los puntos muestreados (real SRTM o mock)
    coords = [(s[0], s[1]) for s in sampled]
    try:
        elevs_m = get_elevations_m(coords, mock=False)
    except Exception as e:
        logger.warning(f"Error obteniendo terreno: {e}")
        elevs_m = [None] * len(coords)

    # Margen de terreno: 500 ft sobre el obstáculo más alto (RAAC VFR).
    VFR_TERRAIN_CLEARANCE_FT = 500

    profile_pts: List[dict] = []
    vfr_conflict       = False
    vfr_conflict_dist  = None
    vfr_max_terrain    = 0
    vfr_cruise_ref     = None
    for (lat, lon, dist, mea, vfr_alt), em in zip(sampled, elevs_m):
        terrain_ft = round(em * M_TO_FT) if em is not None else 0
        entry = {
            "dist_km":    round(dist, 1),
            "terrain_ft": terrain_ft,
            "mea_ft":     mea,
            "safe_alt_ft": safe_altitude_ft(terrain_ft),
        }
        if vfr_alt is not None:
            entry["vfr_alt_ft"] = vfr_alt
            vfr_cruise_ref = vfr_alt if vfr_cruise_ref is None else min(vfr_cruise_ref, vfr_alt)
            # Conflicto: el terreno + margen supera la altitud VFR alcanzable.
            if terrain_ft + VFR_TERRAIN_CLEARANCE_FT > vfr_alt:
                if not vfr_conflict:
                    vfr_conflict_dist = round(dist, 1)
                vfr_conflict    = True
                vfr_max_terrain = max(vfr_max_terrain, terrain_ft)
        profile_pts.append(entry)

    # Aeropuertos de la espina (origen, destino, escalas en línea)
    airports: List[dict] = []
    for i, p in enumerate(pts):
        if not p.is_airport:
            continue
        elev_ft = AIRPORTS[p.code].elev_ft if (p.code and p.code in AIRPORTS) else None
        airports.append({
            "dist_km":  round(cum[i], 1),
            "code":     p.code,
            "elev_ft":  elev_ft,
            "is_fuel_stop": p.is_fuel_stop,
        })

    # Todos los waypoints de la espina con su altura representativa.
    # alt_ft = MEA si el punto está en aerovía; sino la elevación del aeródromo;
    # sino la altitud de crucero (punto en ruta sin aerovía).
    waypoints: List[dict] = []
    for i, p in enumerate(pts):
        elev_ft = AIRPORTS[p.code].elev_ft if (p.code and p.code in AIRPORTS) else None
        if p.mea_ft:
            alt_ft = p.mea_ft
        elif p.is_airport and elev_ft is not None:
            alt_ft = elev_ft
        else:
            alt_ft = cruise_alt
        waypoints.append({
            "dist_km":  round(cum[i], 1),
            "code":     p.code,
            "alt_ft":   alt_ft,
            "mea_ft":   p.mea_ft,
            "is_airport": p.is_airport,
            "is_fuel_stop": p.is_fuel_stop,
        })

    return {
        "profile": profile_pts,
        "airports": airports,
        "waypoints": waypoints,
        "total_km": round(total, 1),
        "cruise_alt_ft": cruise_alt,
        "flight_rules": "VFR" if is_vfr else "IFR",
        # Fase 2 — conflicto de terreno en VFR: si el terreno supera la altitud
        # VFR alcanzable, el frontend avisa y ofrece recalcular en IFR.
        "vfr_terrain_conflict": vfr_conflict,
        "vfr_conflict_dist_km": vfr_conflict_dist,
        "vfr_max_terrain_ft":   vfr_max_terrain if vfr_conflict else None,
        "vfr_cruise_ft":        vfr_cruise_ref,
    }


@app.post("/api/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest):
    """Evaluación meteorológica completa: origen + destino + ruta + briefing."""
    origin = req.origin.upper().strip()
    dest   = req.dest.upper().strip()

    if origin not in AIRPORTS:
        raise HTTPException(400, f"Aeródromo desconocido: {origin}")
    if dest not in AIRPORTS:
        raise HTTPException(400, f"Aeródromo desconocido: {dest}")

    try:
        aircraft = get_profile(req.aircraft)
    except KeyError:
        raise HTTPException(400, f"Aeronave desconocida: {req.aircraft}")

    dep_time = _parse_dep_time(req.departure_time)
    engine   = DecisionEngine(mock=False, aircraft=aircraft,
                              personal_minima=get_minima(req.experience))

    # Reglas de vuelo: VFR (default) o IFR. Definen el routing y la altitud.
    flight_rules = (req.flight_rules or "VFR").upper()
    if flight_rules not in ("VFR", "IFR"):
        flight_rules = "VFR"

    # Estimación rápida de duración para ventana meteorológica inicial
    orig_ap = AIRPORTS[origin]
    dest_ap = AIRPORTS[dest]
    rough_duration = max(0.5, haversine_km(orig_ap.lat, orig_ap.lon, dest_ap.lat, dest_ap.lon)
                         / (aircraft.cruise_kt * 1.852) * 1.3)

    # Fase 1: origin eval + dest eval en paralelo
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_o = ex.submit(engine.evaluate, origin, req.origin_runway, dep_time, rough_duration)
        fut_d = ex.submit(engine.evaluate, dest,   req.dest_runway,   dep_time, rough_duration)
        origin_result = fut_o.result()
        dest_result   = fut_d.result()

    r_map = {origin: origin_result.r_total, dest: dest_result.r_total}
    route_result = optimize(
        origin=origin, dest=dest,
        mode="suggested",
        r_map=r_map,
        aircraft=aircraft,
        suggest_alternate=True,
        evaluate_intermediate=False,
        avoid_restricted_zones=req.avoid_airspace,
        mock=False,
        dep_time=dep_time,
    )

    # Duración real desde la ruta calculada
    actual_duration = route_result.total_time_h if (route_result.found and route_result.total_time_h > 0) else rough_duration

    # Buscar aerovías para la ruta — SOLO en modo IFR.
    # En VFR el piloto no navega por aerovías: puede pasar por los mismos puntos
    # geográficos, pero no notifica en los fixes ni sigue el FL asignado a la
    # aerovía. Por eso en VFR la ruta es línea directa (great circle) y la
    # altitud surge de la regla de los semicírculos (ver _generate_route_waypoints).
    # Estrategia IFR: primero un camino de aerovía CONTINUO de origen a destino
    # (end-to-end), repartido entre los tramos. Si no hay camino end-to-end, se
    # cae al método por-tramo (útil cuando solo algunos tramos tienen aerovía).
    airway_map: dict = {}
    if flight_rules == "IFR" and route_result.found and len(route_result.path) >= 2:
        leg_airports = [
            (c, AIRPORTS[c].lat, AIRPORTS[c].lon)
            for c in route_result.path if c in AIRPORTS
        ]
        try:
            airway_map = find_airways_for_route_legs(leg_airports, aircraft.cruise_alt_ft)
        except Exception as e:
            logger.warning(f"Error buscando aerovia end-to-end: {e}")
            airway_map = {}

        # Fallback por-tramo: para tramos que el camino end-to-end no cubrió,
        # intentar encontrar una aerovía local.
        for i in range(len(route_result.path) - 1):
            leg_orig = route_result.path[i]
            leg_dest = route_result.path[i + 1]
            if (leg_orig, leg_dest) in airway_map:
                continue
            orig_ap_aw = AIRPORTS.get(leg_orig)
            dest_ap_aw = AIRPORTS.get(leg_dest)
            if orig_ap_aw and dest_ap_aw:
                try:
                    aw_wps = find_airways_for_leg(
                        orig_ap_aw.lat, orig_ap_aw.lon,
                        dest_ap_aw.lat, dest_ap_aw.lon,
                        aircraft.cruise_alt_ft,
                    )
                    if aw_wps:
                        airway_map[(leg_orig, leg_dest)] = aw_wps
                except Exception as e:
                    logger.warning(f"Error buscando aerovia {leg_orig}->{leg_dest}: {e}")

    notams_orig = notams_dest = []
    try:
        from data.fetcher_aviationweather import AviationWeatherFetcher
        av = AviationWeatherFetcher(mock=False)
        notams_orig = av.get_notams(origin)
        notams_dest = av.get_notams(dest)
    except Exception:
        pass

    # Fichas + bloqueos operacionales no meteorológicos (noche / NOTAM).
    # El despegue se evalúa a la hora de salida; el aterrizaje, a la ETA (para
    # que un vuelo que aterriza de noche sea NO GO aunque despegue de día).
    arr_time = dep_time + int(actual_duration * 3600)
    origin_card = _to_card(origin_result, origin_result.runway_heading, orig_ap, notams_orig)
    dest_card   = _to_card(dest_result,   dest_result.runway_heading,   dest_ap, notams_dest)
    _apply_operational_blockers(origin_card, orig_ap, dep_time, notams_orig, "despegue",
                                flight_rules, aircraft.cruise_alt_ft, result=origin_result)
    _apply_operational_blockers(dest_card,   dest_ap, arr_time, notams_dest, "aterrizaje",
                                flight_rules, aircraft.cruise_alt_ft, result=dest_result)

    # El briefing se genera DESPUES de los bloqueos operacionales: si se armara
    # antes, describiria solo la meteorologia e ignoraria un NO GO por noche o
    # por NOTAM de cierre.
    briefing_text = generate_briefing(
        origin_result, dest_result,
        route_result if route_result.found else None,
        notams_orig=notams_orig,
        notams_dest=notams_dest,
    )

    decisions = [origin_card.decision, dest_card.decision]
    global_dec = "NO GO" if "NO GO" in decisions else "CAUTION" if "CAUTION" in decisions else "GO"

    # Generar waypoints con checkpoints intermedios cada ~230 km
    waypoints = _generate_route_waypoints(
        path=route_result.path,
        aircraft=aircraft,
        dep_time=dep_time,
        duration_hours=actual_duration,
        r_map=r_map,
        mock=False,
        airway_map=airway_map,
        flight_rules=flight_rules,
        vfr_cruise_alt_ft=_clamp_vfr_alt(req.cruise_alt_ft, aircraft)
                          if flight_rules == "VFR" else None,
    )

    # Aeródromos de desvío por waypoint (sin requests HTTP)
    divs = _find_diversions(waypoints)
    waypoints = [
        RouteWaypoint(**{**wp.model_dump(), "diversions": divs.get(wp.code, [])})
        for wp in waypoints
    ]

    route_card = RouteCard(
        found          = route_result.found,
        flight_rules   = flight_rules,
        path           = route_result.path,
        waypoints      = waypoints,
        total_dist_km  = round(route_result.total_dist_km, 1),
        total_time_h   = round(route_result.total_time_h, 2),
        total_fuel_l   = round(route_result.total_fuel_l, 1),
        fuel_ok        = route_result.fuel_ok,
        needs_fuel_stop= route_result.needs_fuel_stop,
        legs=[{
            "origin":      l.origin,
            "dest":        l.dest,
            "distance_km": l.distance_km,
            "bearing_deg": l.bearing_deg,
            "time_hours":  l.time_hours,
            "fuel_liters": l.fuel_liters,
        } for l in route_result.legs],
        alternate={
            "code":              route_result.alternate.code,
            "name":              route_result.alternate.name,
            "decision":          route_result.alternate.decision,
            "r_total":           route_result.alternate.r_total,
            "dist_from_dest_km": route_result.alternate.dist_from_dest_km,
        } if route_result.alternate else None,
        airspace_conflicts=[{
            "name": z.name,
            "type": "R" if z.is_restricted else "D" if getattr(z, "zone_type", "") == "D" else "C",
        } for z in route_result.airspace_conflicts],
        error=route_result.error,
    )

    return EvaluateResponse(
        global_decision = global_dec,
        origin          = origin_card,
        dest            = dest_card,
        route           = route_card,
        briefing        = briefing_text,
    )


# ══════════════════════════════════════════════════════════════════════════════
# COPILOTO — asistente de planificacion en lenguaje natural
# ══════════════════════════════════════════════════════════════════════════════
# El LLM no decide, no calcula y no aporta conocimiento propio: interpreta la
# pregunta, llama a las herramientas deterministas de copilot/tools.py y
# redacta con lo que ellas devolvieron. Ver copilot/__init__.py.

class CopilotRequest(BaseModel):
    message: str = Field(..., max_length=600)
    history: Optional[List[Dict]] = None
    # Estado del formulario de la pantalla (origen, destino, aeronave, regimen,
    # altitud elegida). Permite que "como esta el destino?" no obligue al
    # asistente a repreguntar lo que el piloto ya cargo.
    context: Optional[Dict] = None


class CopilotResponse(BaseModel):
    text             : str
    intent           : str
    tools_used       : List[str]
    grounded         : bool
    verdict_enforced : bool
    code_corrected   : bool
    latency_s        : float
    model_version    : str
    history          : List[Dict]
    available        : bool = True


# El agente se construye una sola vez y se reusa. Se hace perezosamente para
# que la app arranque igual si no hay credencial: el copiloto queda no
# disponible y el resto del sistema sigue funcionando.
_copilot_agent = None
_copilot_error = ""


def _get_copilot():
    global _copilot_agent, _copilot_error
    if _copilot_agent is None and not _copilot_error:
        try:
            from copilot.agent import CopilotAgent
            from copilot.client import GeminiClient
            _copilot_agent = CopilotAgent(GeminiClient())
            logger.info("Copiloto inicializado")
        except Exception as e:
            _copilot_error = str(e)
            logger.warning(f"Copiloto no disponible: {e}")
    return _copilot_agent


# Limitador crudo por proceso. El endpoint queda expuesto en internet y la
# cuota gratuita es finita: sin cota, cualquiera la agota.
_COPILOT_MAX_PER_MIN = 12
_copilot_hits: List[float] = []


@app.get("/api/copilot/status")
async def copilot_status():
    """Informa si el asistente esta disponible, para que el frontend se adapte."""
    return {"available": _get_copilot() is not None, "detail": _copilot_error or None}


@app.post("/api/copilot", response_model=CopilotResponse)
def copilot(req: CopilotRequest):
    """
    Consulta en lenguaje natural sobre aerodromos y condiciones de vuelo.

    Se define como `def` y no `async def` a proposito: el agente hace llamadas
    de red bloqueantes, y asi FastAPI lo corre en su threadpool en vez de
    trabar el bucle de eventos del resto de la API.
    """
    agente = _get_copilot()
    if agente is None:
        return CopilotResponse(
            text="El asistente no está configurado en este servidor. "
                 "El resto del sistema funciona normalmente.",
            intent="fuera_de_alcance", tools_used=[], grounded=False,
            verdict_enforced=False, code_corrected=False, latency_s=0.0,
            model_version="", history=[], available=False,
        )

    ahora = time.time()
    _copilot_hits[:] = [t for t in _copilot_hits if ahora - t < 60]
    if len(_copilot_hits) >= _COPILOT_MAX_PER_MIN:
        raise HTTPException(429, "Demasiadas consultas al asistente. "
                                 "Esperá un minuto e intentá de nuevo.")
    _copilot_hits.append(ahora)

    ans = agente.ask(req.message, history=req.history, context=req.context)

    return CopilotResponse(
        text             = ans.text,
        intent           = ans.intent,
        tools_used       = [i.name for i in ans.invocations],
        grounded         = ans.grounded,
        verdict_enforced = ans.verdict_enforced,
        code_corrected   = ans.code_corrected,
        latency_s        = round(ans.latency_s, 2),
        model_version    = ans.model_version,
        history          = ans.history,
    )
