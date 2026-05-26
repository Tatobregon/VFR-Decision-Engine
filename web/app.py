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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from data.fetcher_openmeteo import OpenMeteoFetcher
from parsers.openmeteo_adapter import OpenMeteoAdapter
from decision.engine import DecisionEngine
from route.optimizer import optimize
from output.briefing import generate_briefing
from risk.aircraft_profiles import PROFILE_NAMES, get_profile, AircraftProfile
from risk.soft_scoring import compute_soft_score
from risk.hard_blockers import check_hard_blockers_from_weather
from features.crosswind import compute_crosswind
from features.density_altitude import advisory as da_advisory
from route.performance import haversine_km, bearing_deg
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
    origin_runway: int = 180
    dest_runway: int = 180
    aircraft: str = "Pipistrel Alpha Trainer"
    departure_time: str = ""       # "HH:MM" UTC; vacío = ahora + 1h
    duration_hours: float = Field(default=2.0, ge=0.3, le=12.0)
    mock: bool = False
    avoid_airspace: bool = True    # si True, la ruta evita zonas R/P/D
    duration_hours: float = 0.0   # ignorado; calculado internamente desde la ruta


class WeatherCard(BaseModel):
    station_id: str
    name: str
    elev_ft: int
    decision: str
    r_total: float
    weather_source: str
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
    next_go_from: Optional[int] = None
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
    """Condiciones NWP en un checkpoint intermedio (altitud de crucero)."""
    wind_dir: Optional[int] = None
    wind_spd_kt: Optional[float] = None
    wind_gust_kt: Optional[float] = None
    wind_variable: bool = False
    visibility_km: Optional[float] = None
    ceiling_ft: Optional[int] = None
    temp_c: Optional[float] = None
    spread_c: Optional[float] = None
    wx_codes: List[str] = []
    flight_category: Optional[str] = None
    r_vis: Optional[float] = None
    r_ceil: Optional[float] = None
    r_gust: Optional[float] = None
    r_wx: Optional[float] = None
    r_fog: Optional[float] = None
    dominant_factor: Optional[str] = None


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


class RouteCard(BaseModel):
    found: bool
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


def _to_card(result, runway_heading: int, ap: AirportInfo) -> WeatherCard:
    wx = result.weather

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
        next_go_from    = result.next_go_from,
        fetch_ok        = result.fetch_ok,
        error_message   = result.error_message,
    )


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


# ── Helper: evaluar NWP en coordenadas arbitrarias (waypoints intermedios) ────

def _evaluate_nwp_at_coord(
    lat: float,
    lon: float,
    elev_m: float,
    dep_time: int,
    duration_hours: float,
    aircraft: AircraftProfile,
    mock: bool,
    cruise_alt_ft: int = 7500,
    track_bearing: int = 0,
) -> tuple:
    """
    Evalúa riesgo NWP en coordenadas arbitrarias (no airport code).
    Usa altitud de crucero para obtener viento en el nivel de presión correcto.
    Devuelve (r_total, decision, ref_wx_or_None).
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
            return 0.0, "GO", None, None

        chk_id = f"CHK_{abs(lat):.1f}_{abs(lon):.1f}"
        all_wx = adapter.adapt_all(raw_nwp, station_id=chk_id)
        if not all_wx:
            return 0.0, "GO", None, None

        window_end = dep_time + int(duration_hours * 3600)
        window_wx = [w for w in all_wx if dep_time <= w.obs_time <= window_end]
        if not window_wx:
            window_wx = [min(all_wx, key=lambda w: abs(w.obs_time - dep_time))]

        ref_wx = min(window_wx, key=lambda w: abs(w.obs_time - dep_time))

        for wx in window_wx:
            blocker = check_hard_blockers_from_weather(wx)
            if blocker.is_blocked:
                return 1.0, "NO GO", ref_wx, None

        # En vuelo crucero el viento cruzado no es peligroso (el piloto crabea).
        # Se zeroa el crosswind alineando wind_dir con el track; r_gust sigue
        # capturando turbulencia por ráfagas.
        def _inflight_wx(w):
            wx2 = copy.copy(w)
            wx2.wind_dir = track_bearing
            wx2.wind_variable = False
            return wx2

        scores = [compute_soft_score(_inflight_wx(w), track_bearing, aircraft) for w in window_wx]
        worst = max(scores, key=lambda s: s.r_total)
        return worst.r_total, worst.decision, ref_wx, worst
    except Exception as e:
        logger.warning(f"Error evaluando NWP en coord ({lat:.2f},{lon:.2f}): {e}")
        return 0.0, "GO", None, None


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

def _generate_route_waypoints(
    path: List[str],
    aircraft: AircraftProfile,
    dep_time: int,
    duration_hours: float,
    r_map: dict,
    mock: bool,
    step_km: float = 230.0,
) -> List[RouteWaypoint]:
    """
    Construye la lista completa de waypoints de la ruta.
    Los checkpoints NWP se evalúan en paralelo para minimizar latencia.
    """
    leg_dists: List[float] = []
    for i in range(len(path) - 1):
        a = AIRPORTS.get(path[i])
        b = AIRPORTS.get(path[i + 1])
        leg_dists.append(haversine_km(a.lat, a.lon, b.lat, b.lon) if a and b else 0.0)
    total_route_dist = sum(leg_dists)

    # Paso 1: construir secuencia ordenada — aeródromos ya completos,
    # checkpoints como specs (dict) pendientes de evaluación NWP.
    sequence: list = []
    cumulative_km = 0.0

    for i, code in enumerate(path):
        ap = AIRPORTS.get(code)
        if not ap:
            continue

        r_val = r_map.get(code, 0.0)
        dec = "GO" if r_val < 0.25 else "CAUTION" if r_val < 0.50 else "NO GO"
        sequence.append(RouteWaypoint(
            code=code, name=ap.name,
            lat=ap.lat, lon=ap.lon,
            r_total=r_val, decision=dec,
            is_checkpoint=False, cruise_alt_ft=None,
        ))

        if i >= len(path) - 1:
            break

        next_ap = AIRPORTS.get(path[i + 1])
        if not next_ap:
            continue

        leg_km = leg_dists[i] if i < len(leg_dists) else 0.0
        track = int(bearing_deg(ap.lat, ap.lon, next_ap.lat, next_ap.lon))
        cruise_alt = max(7500, max(ap.elev_ft, next_ap.elev_ft) + 3000)

        if leg_km > step_km:
            n_chk = int(leg_km // step_km)
            for j in range(1, n_chk + 1):
                frac = (j * step_km) / leg_km
                if frac >= 1.0:
                    break

                dist_to_chk = cumulative_km + j * step_km
                frac_total = dist_to_chk / total_route_dist if total_route_dist > 0 else 0.5
                sequence.append({
                    'seq_code':  f"WP{i+1}-{j}",
                    'seq_name':  f"En ruta · {round(j * step_km)} km desde {code}",
                    'lat':       ap.lat + frac * (next_ap.lat - ap.lat),
                    'lon':       ap.lon + frac * (next_ap.lon - ap.lon),
                    'elev_m':    (ap.elev_ft + frac * (next_ap.elev_ft - ap.elev_ft)) * 0.3048,
                    'cruise_alt': cruise_alt,
                    'track':     track,
                    'dep_time':  dep_time + int(frac_total * duration_hours * 3600),
                })

        cumulative_km += leg_km

    # Paso 2: evaluar todos los checkpoints pendientes en paralelo.
    chk_items = [(idx, spec) for idx, spec in enumerate(sequence) if isinstance(spec, dict)]

    def _eval_chk(idx_spec):
        idx, spec = idx_spec
        r, dec, ref_wx, worst = _evaluate_nwp_at_coord(
            lat=spec['lat'], lon=spec['lon'], elev_m=spec['elev_m'],
            dep_time=spec['dep_time'], duration_hours=1.0,
            aircraft=aircraft, mock=mock,
            cruise_alt_ft=spec['cruise_alt'], track_bearing=spec['track'],
        )
        return idx, spec, r, dec, ref_wx, worst

    route_codes = set(path)

    if chk_items:
        with ThreadPoolExecutor(max_workers=min(8, len(chk_items))) as ex:
            futures = [ex.submit(_eval_chk, item) for item in chk_items]
            for fut in futures:
                idx, spec, r, dec, ref_wx, worst = fut.result()

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
                        flight_category=ref_wx.flight_category,
                        r_vis=getattr(worst, 'r_vis', None),
                        r_ceil=getattr(worst, 'r_ceil', None),
                        r_gust=getattr(worst, 'r_gust', None),
                        r_wx=getattr(worst, 'r_wx', None),
                        r_fog=getattr(worst, 'r_fog', None),
                        dominant_factor=getattr(worst, 'dominant_factor', None),
                    )

                # Para checkpoints NO GO, sugerir aeródromo alternativo cercano
                alt_via = None
                if dec == "NO GO":
                    alt_via = _find_best_alt_via(spec['lat'], spec['lon'], route_codes)

                sequence[idx] = RouteWaypoint(
                    code=spec['seq_code'], name=spec['seq_name'],
                    lat=spec['lat'], lon=spec['lon'],
                    r_total=r, decision=dec,
                    is_checkpoint=True, cruise_alt_ft=spec['cruise_alt'],
                    chk_weather=chk_wx,
                    alt_via=alt_via,
                )

    return [wp for wp in sequence if isinstance(wp, RouteWaypoint)]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


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
    engine   = DecisionEngine(mock=req.mock, aircraft=aircraft)

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
        mock=req.mock,
        dep_time=dep_time,
    )

    # Duración real desde la ruta calculada
    actual_duration = route_result.total_time_h if (route_result.found and route_result.total_time_h > 0) else rough_duration

    notams_orig = notams_dest = []
    try:
        from data.fetcher_aviationweather import AviationWeatherFetcher
        av = AviationWeatherFetcher(mock=req.mock)
        notams_orig = av.get_notams(origin)
        notams_dest = av.get_notams(dest)
    except Exception:
        pass

    briefing_text = generate_briefing(
        origin_result, dest_result,
        route_result if route_result.found else None,
        notams_orig=notams_orig,
        notams_dest=notams_dest,
    )

    decisions = [origin_result.decision, dest_result.decision]
    global_dec = "NO GO" if "NO GO" in decisions else "CAUTION" if "CAUTION" in decisions else "GO"

    # Generar waypoints con checkpoints intermedios cada ~230 km
    waypoints = _generate_route_waypoints(
        path=route_result.path,
        aircraft=aircraft,
        dep_time=dep_time,
        duration_hours=actual_duration,
        r_map=r_map,
        mock=req.mock,
    )

    # Aeródromos de desvío por waypoint (sin requests HTTP)
    divs = _find_diversions(waypoints)
    waypoints = [
        RouteWaypoint(**{**wp.model_dump(), "diversions": divs.get(wp.code, [])})
        for wp in waypoints
    ]

    route_card = RouteCard(
        found          = route_result.found,
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
        origin          = _to_card(origin_result, req.origin_runway, orig_ap),
        dest            = _to_card(dest_result,   req.dest_runway,   dest_ap),
        route           = route_card,
        briefing        = briefing_text,
    )
