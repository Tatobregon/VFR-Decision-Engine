"""
weather_sampler.py
==================
Muestrea condiciones meteorologicas NWP a lo largo de una ruta VFR.

Para cada tramo del path:
  - Si el tramo <= INTERP_THRESHOLD_KM: solo evalua los extremos (aerodromos)
  - Si el tramo >  INTERP_THRESHOLD_KM: agrega puntos interpolados cada ~60 km

El ETA de cada punto se calcula como:
  eta = dep_time + (dist_acum_nm / cruise_kt) * 3600

Todos los puntos usan Open-Meteo NWP obtenido en paralelo
(ThreadPoolExecutor) para minimizar la latencia total.
"""

import math
import logging
import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses         import dataclass
from typing              import Dict, List, Optional

logger = logging.getLogger(__name__)

INTERP_THRESHOLD_KM = 80.0
INTERP_SPACING_KM   = 60.0
KM_TO_NM            = 0.539957
_MAX_WORKERS        = 8


# ─────────────────────────────────────────────────────────────────────────────
# Tipo de datos de salida
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RouteWeatherPoint:
    """Condicion meteorologica en un punto de la ruta."""
    name        : str
    lat         : float
    lon         : float
    eta_unix    : int             # ETA estimado en Unix UTC
    dist_km     : float           # distancia acumulada desde origen
    decision    : str  = "SIN DATOS"
    r_total     : float = 0.5
    is_airport  : bool  = False
    airport_code: Optional[str]   = None
    temp_c      : Optional[float] = None
    wind_dir    : Optional[int]   = None
    wind_spd_kt : Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _interp(lat1, lon1, lat2, lon2, t):
    """Interpolacion lineal entre dos coordenadas. t en [0, 1]."""
    return lat1 + t * (lat2 - lat1), lon1 + t * (lon2 - lon1)


def _coord_name(lat: float, lon: float) -> str:
    """Nombre legible de una coordenada en formato aeronautico argentino."""
    ns = "S" if lat < 0 else "N"
    eo = "O" if lon < 0 else "E"
    return f"{abs(lat):.1f}°{ns} {abs(lon):.1f}°{eo}"


def _collect_points(
    path      : List[str],
    airports  : Dict,
    dep_time  : int,
    cruise_kt : float,
) -> List[RouteWeatherPoint]:
    """
    Genera la lista de RouteWeatherPoint sin weather todavia.
    Incluye aerodromos del path + puntos interpolados en tramos largos.
    """
    try:
        from route.performance import haversine_km
    except ImportError:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from route.performance import haversine_km

    points: List[RouteWeatherPoint] = []
    cum_km = 0.0

    for i, code in enumerate(path):
        ap = airports.get(code)
        if not ap:
            continue

        eta = dep_time + int((cum_km * KM_TO_NM / cruise_kt) * 3600)
        points.append(RouteWeatherPoint(
            name         = ap.name,
            lat          = ap.lat,
            lon          = ap.lon,
            eta_unix     = eta,
            dist_km      = round(cum_km, 1),
            is_airport   = True,
            airport_code = code,
        ))

        if i < len(path) - 1:
            next_ap = airports.get(path[i + 1])
            if not next_ap:
                continue
            leg_km = haversine_km(ap.lat, ap.lon, next_ap.lat, next_ap.lon)

            if leg_km > INTERP_THRESHOLD_KM:
                n_pts = int(leg_km / INTERP_SPACING_KM)
                for j in range(1, n_pts + 1):
                    t = j * INTERP_SPACING_KM / leg_km
                    if t >= 1.0:
                        break
                    ilat, ilon = _interp(ap.lat, ap.lon, next_ap.lat, next_ap.lon, t)
                    i_km  = cum_km + j * INTERP_SPACING_KM
                    i_eta = dep_time + int((i_km * KM_TO_NM / cruise_kt) * 3600)
                    points.append(RouteWeatherPoint(
                        name     = _coord_name(ilat, ilon),
                        lat      = round(ilat, 4),
                        lon      = round(ilon, 4),
                        eta_unix = i_eta,
                        dist_km  = round(i_km, 1),
                    ))

            cum_km += leg_km

    return points


def _fetch_one(
    point        : RouteWeatherPoint,
    fetcher,
    adapter,
    leg_bearing  : int,
    aircraft     = None,
    cruise_alt_ft: Optional[int] = None,
) -> RouteWeatherPoint:
    """
    Obtiene NWP para un punto y calcula su decision.

    Origen y destino (is_airport=True) : fetch a superficie, perfil normal.
    Waypoints intermedios (is_airport=False): fetch a altitud de crucero,
    perfil relajado (xwind 30 kt, gust 40 kt) porque en vuelo crucero
    esos limites no aplican igual que en pista.
    """
    try:
        from risk.soft_scoring      import compute_soft_score
        from risk.aircraft_profiles import ALPHA_TRAINER
    except ImportError:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from risk.soft_scoring      import compute_soft_score
        from risk.aircraft_profiles import ALPHA_TRAINER

    ac = aircraft or ALPHA_TRAINER

    # Waypoints intermedios: viento en altitud de crucero + limites relajados
    if not point.is_airport and cruise_alt_ft:
        fetch_alt    = cruise_alt_ft
        scoring_ac   = dataclasses.replace(ac, crosswind_max_kt=30.0, gust_max_kt=40.0)
    else:
        fetch_alt    = None
        scoring_ac   = ac

    try:
        raw = fetcher.get_forecast(
            lat           = point.lat,
            lon           = point.lon,
            elevation_m   = 0,
            cruise_alt_ft = fetch_alt,
        )
        if raw is None:
            return point

        all_wx = adapter.adapt_all(raw, station_id="ROUTE_PT")
        if not all_wx:
            return point

        wx    = min(all_wx, key=lambda w: abs(w.obs_time - point.eta_unix))
        # Este modulo puntua con el viento DEL NIVEL. El fetcher ya no lo
        # escribe sobre el de superficie, asi que se toma de su campo.
        hora = next((h for h in raw.hours if h.valid_time_utc == wx.obs_time), None)
        if fetch_alt and hora is not None and hora.level_wind_spd_kt is not None:
            wx = dataclasses.replace(wx, wind_dir=hora.level_wind_dir,
                                     wind_spd_kt=hora.level_wind_spd_kt,
                                     wind_gust_kt=None)
        score = compute_soft_score(wx, leg_bearing, scoring_ac)

        point.decision    = score.decision
        point.r_total     = score.r_total
        point.temp_c      = wx.temp_c
        point.wind_dir    = wx.wind_dir
        point.wind_spd_kt = wx.wind_spd_kt

    except Exception as exc:
        logger.warning(f"NWP fallido para {point.name}: {exc}")

    return point


def _bearing(lat1, lon1, lat2, lon2) -> int:
    """Rumbo aproximado entre dos coordenadas (grados magneticos)."""
    dlon = math.radians(lon2 - lon1)
    la1  = math.radians(lat1)
    la2  = math.radians(lat2)
    x    = math.sin(dlon) * math.cos(la2)
    y    = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return int((math.degrees(math.atan2(x, y)) + 360) % 360)


# ─────────────────────────────────────────────────────────────────────────────
# Funcion publica
# ─────────────────────────────────────────────────────────────────────────────

def sample_route_weather(
    path      : List[str],
    airports  : Dict,
    dep_time  : int,
    cruise_kt : float = 97.0,
    aircraft  = None,
    mock      : bool  = False,
) -> List[RouteWeatherPoint]:
    """
    Muestrea condiciones meteorologicas NWP a lo largo de la ruta.

    Parameters
    ----------
    path      : Codigos de aerodromos ordenados de origen a destino.
    airports  : Diccionario {code: AirportInfo}.
    dep_time  : Hora de despegue en Unix timestamp UTC.
    cruise_kt : Velocidad de crucero en kt (default: 97kt Alpha Trainer).
    aircraft  : AircraftProfile a usar. Define cruise_alt_ft para waypoints
                intermedios. Si es None, usa ALPHA_TRAINER.
    mock      : Si True usa datos simulados.

    Returns
    -------
    Lista de RouteWeatherPoint con decision para cada punto de la ruta.
    Aerodromos (is_airport=True): evaluados a superficie con perfil normal.
    Waypoints (is_airport=False): evaluados a altitud de crucero con perfil relajado.
    """
    try:
        from data.fetcher_openmeteo    import OpenMeteoFetcher
        from parsers.openmeteo_adapter import OpenMeteoAdapter
        from risk.aircraft_profiles    import ALPHA_TRAINER
    except ImportError:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from data.fetcher_openmeteo    import OpenMeteoFetcher
        from parsers.openmeteo_adapter import OpenMeteoAdapter
        from risk.aircraft_profiles    import ALPHA_TRAINER

    ac           = aircraft or ALPHA_TRAINER
    cruise_alt   = getattr(ac, "cruise_alt_ft", 5500)

    points  = _collect_points(path, airports, dep_time, cruise_kt)
    fetcher = OpenMeteoFetcher(mock=mock)
    adapter = OpenMeteoAdapter()

    # Asignar bearing del tramo a cada punto para el crosswind score
    bearings: Dict[int, int] = {}
    for i, pt in enumerate(points):
        if i < len(points) - 1:
            bearings[i] = _bearing(pt.lat, pt.lon, points[i + 1].lat, points[i + 1].lon)
        else:
            bearings[i] = bearings.get(i - 1, 0)

    # Fetch en paralelo — waypoints intermedios usan altitud de crucero
    results = [None] * len(points)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        future_map = {
            ex.submit(
                _fetch_one, pt, fetcher, adapter, bearings[i],
                ac,
                cruise_alt if not pt.is_airport else None,
            ): i
            for i, pt in enumerate(points)
        }
        for fut in as_completed(future_map):
            idx = future_map[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                logger.warning(f"Punto {idx} fallido: {exc}")
                results[idx] = points[idx]

    return [r for r in results if r is not None]


# ─────────────────────────────────────────────────────────────────────────────
# Helper: detectar tramos bloqueados
# ─────────────────────────────────────────────────────────────────────────────

def blocked_legs(
    path    : List[str],
    points  : List[RouteWeatherPoint],
    airports: Dict,
) -> List[tuple]:
    """
    Devuelve lista de tuplas (orig_code, dest_code) de tramos con un punto NO GO.
    Usado por el optimizador para reroutear evitando esas aristas.
    """
    try:
        from route.performance import haversine_km
    except ImportError:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from route.performance import haversine_km

    if len(path) < 2:
        return []

    blocked = []
    for i in range(len(path) - 1):
        a_code = path[i]
        b_code = path[i + 1]
        ap_a   = airports.get(a_code)
        ap_b   = airports.get(b_code)
        if not ap_a or not ap_b:
            continue

        leg_start_km = sum(
            haversine_km(
                airports[path[j]].lat, airports[path[j]].lon,
                airports[path[j + 1]].lat, airports[path[j + 1]].lon,
            )
            for j in range(i)
            if airports.get(path[j]) and airports.get(path[j + 1])
        )
        leg_end_km = leg_start_km + haversine_km(ap_a.lat, ap_a.lon, ap_b.lat, ap_b.lon)

        for pt in points:
            if (not pt.is_airport
                    and leg_start_km < pt.dist_km < leg_end_km
                    and pt.decision == "NO GO"):
                blocked.append((a_code, b_code))
                break

    return blocked
