"""
optimizer.py
============
Interfaz unificada del optimizador de rutas VFR.

Punto de entrada unico para la GUI y el engine.
Delega al algoritmo correcto segun el modo:
  shortest  -> A*  (minimiza distancia)
  fastest   -> A*  (minimiza tiempo con viento)
  safest    -> A*  (minimiza distancia ponderada por riesgo)
  suggested -> A*  con corredor geografico: solo aerodromos dentro de un
               banda alrededor de la ruta directa, ancho adaptativo
               max(80, dist_directa * 0.20) km a cada lado.

Ademas verifica interseccion con espacio aereo, calcula el resumen de
performance segun el perfil de aeronave, evalua meteo en aerodromos
intermedios y sugiere un aerodromo alternativo automatico.
"""

import math as _math
import time as _time_module
import logging as _logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_logger = _logging.getLogger(__name__)

try:
    from data.airports import AIRPORTS, AIRPORTS_PUBLIC, AirportInfo
    from data.airspace import zones_along_route, AirspaceZone
    from route.graph import RouteGraph, build_graph, get_edge
    from route.astar import astar, AStarResult

    from route.performance import (
        haversine_km, bearing_deg, leg_time_hours, leg_fuel_liters,
        route_summary, needs_fuel_stop, safe_altitude_ft,
        CRUISE_KT, FUEL_FLOW_LPH, FUEL_USABLE_L,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from route.weather_sampler import sample_route_weather, blocked_legs, RouteWeatherPoint
    from route.airway_router import find_airways_for_leg
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS, AIRPORTS_PUBLIC, AirportInfo
    from data.airspace import zones_along_route, AirspaceZone
    from route.graph import RouteGraph, build_graph, get_edge
    from route.astar import astar, AStarResult

    from route.performance import (
        haversine_km, bearing_deg, leg_time_hours, leg_fuel_liters,
        route_summary, needs_fuel_stop, safe_altitude_ft,
        CRUISE_KT, FUEL_FLOW_LPH, FUEL_USABLE_L,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from route.weather_sampler import sample_route_weather, blocked_legs, RouteWeatherPoint
    from route.airway_router import find_airways_for_leg


VALID_MODES = ("shortest", "fastest", "safest", "suggested")


# ────────────────────────────────────────────────────────────────────────────
# Helpers: corredor geografico para modo "suggested"
# ────────────────────────────────────────────────────────────────────────────

def _dist_point_to_segment_km(
    plat: float, plon: float,
    alat: float, alon: float,
    blat: float, blon: float,
) -> float:
    """
    Distancia aproximada (km) del punto P al segmento AB usando proyeccion plana.
    Valida para distancias < 1000 km en latitudes de Argentina.
    """
    cos_lat = _math.cos(_math.radians((alat + blat) / 2))
    px = (plon - alon) * 111.0 * cos_lat
    py = (plat - alat) * 111.0
    bx = (blon - alon) * 111.0 * cos_lat
    by = (blat - alat) * 111.0
    seg_sq = bx * bx + by * by
    if seg_sq < 1e-9:
        return _math.sqrt(px * px + py * py)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg_sq))
    dx = px - t * bx
    dy = py - t * by
    return _math.sqrt(dx * dx + dy * dy)


# Cap de longitud de tramo (km) para forzar paradas intermedias.
# Tramos mas cortos permiten que el airway_router encuentre aerovias en cada
# tramo (las aerovias se buscan dentro de 150 km de cada extremo). Un tramo de
# 1500 km no puede ser cubierto por aerovias aunque exista una cadena de ellas.
# Este valor es generico (no depende de un aeropuerto o aeronave especifica):
# legos mas largos que esto rara vez tienen aerovias dentro del limite de desvio.
DEFAULT_LEG_CAP_KM = 500.0


def _build_corridor_graph(
    origin      : str,
    dest        : str,
    airports    : Dict[str, "AirportInfo"],
    r_map       : Dict[str, float],
    aircraft    : "AircraftProfile",
    max_leg_cap : Optional[float] = DEFAULT_LEG_CAP_KM,
) -> "RouteGraph":
    """
    Construye un grafo A* limitado a aerodromos dentro del corredor geografico
    alrededor de la ruta directa origen-destino.

    Ancho del corredor: max(80 km, 20% de la distancia directa) a cada lado.
    Siempre incluye origen y destino aunque queden fuera del corredor.

    max_leg_cap limita la longitud de cada tramo del grafo. Tramos cortos
    fuerzan paradas intermedias, lo que permite cobertura de aerovias en mas
    tramos. Si es None, se usa el alcance de la aeronave (sin cap adicional).
    """
    orig_ap  = airports[origin]
    dest_ap  = airports[dest]
    ref_dist = haversine_km(orig_ap.lat, orig_ap.lon, dest_ap.lat, dest_ap.lon)
    # Ancho del corredor: 20% de la distancia, acotado a [80, 250] km.
    # El tope superior evita que rutas largas generen un corredor de cientos de
    # km de ancho (que incluiria casi todos los aeropuertos del pais, haciendo
    # el grafo enorme y el filtrado de zonas muy lento). 250 km a cada lado es
    # holgura suficiente para encontrar aeropuertos intermedios alineados con
    # las aerovias en cualquier corredor real de Argentina.
    corridor = min(250.0, max(80.0, ref_dist * 0.20))

    # Limite de longitud de tramo: el menor entre el alcance de la aeronave y el
    # cap generico. Nunca menor al alcance si el cap es None.
    if max_leg_cap is None:
        max_leg = aircraft.range_km
    else:
        max_leg = min(aircraft.range_km, max_leg_cap)

    def _in_corridor(ap_lat, ap_lon):
        return _dist_point_to_segment_km(
            ap_lat, ap_lon,
            orig_ap.lat, orig_ap.lon, dest_ap.lat, dest_ap.lon,
        ) <= corridor

    # Tolerancia latitudinal: un aeropuerto puede estar hasta este margen
    # "en dirección contraria" a la del destino. Impide que A* haga desvíos
    # innecesarios hacia el norte para rutas hacia el sur (y viceversa).
    lat_tolerance = 3.0   # grados de latitud de margen
    lat_limit_min = min(orig_ap.lat, dest_ap.lat) - lat_tolerance
    lat_limit_max = max(orig_ap.lat, dest_ap.lat) + lat_tolerance

    corridor_aps: Dict[str, "AirportInfo"] = {origin: orig_ap, dest: dest_ap}
    for code, ap in airports.items():
        if code in (origin, dest):
            continue
        if not _in_corridor(ap.lat, ap.lon):
            continue
        # Excluir aeropuertos fuera de la banda latitudinal del trayecto
        # (previene desvios extremos en sentido contrario al destino)
        if not (lat_limit_min <= ap.lat <= lat_limit_max):
            continue
        corridor_aps[code] = ap

    return build_graph(
        mode       = "shortest",
        r_map      = r_map,
        airports   = corridor_aps,
        aircraft   = aircraft,
        max_leg_km = max_leg,
    )


# ────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class LegDetail:
    """Detalle de un tramo de la ruta."""
    origin      : str
    dest        : str
    distance_km : float
    bearing_deg : float
    time_hours  : float
    fuel_liters : float
    r_dest      : float   # riesgo meteorologico del destino del tramo


@dataclass
class IntermediateResult:
    """Resultado meteorologico de un aerodromo intermedio de la ruta."""
    code       : str
    name       : str
    decision   : str    # "GO" | "CAUTION" | "NO GO" | "SIN DATOS"
    r_total    : float
    dist_from_prev_km: float


@dataclass
class AlternateInfo:
    """Informacion del aerodromo alternativo sugerido."""
    code          : str
    name          : str
    decision      : str    # decision meteorologica
    r_total       : float
    dist_from_dest_km: float
    time_from_dest_h : float


@dataclass
class OptimizeResult:
    """Resultado completo del optimizador de rutas."""
    found           : bool
    mode            : str
    path            : List[str]               # codigos ICAO de la ruta
    legs            : List[LegDetail]         # detalle por tramo
    total_dist_km   : float
    total_time_h    : float
    total_fuel_l    : float
    needs_fuel_stop : bool
    fuel_ok         : bool
    airspace_conflicts: List[AirspaceZone]    # zonas que intersecta la ruta
    aircraft        : Optional[AircraftProfile] = None
    intermediate_results: List[IntermediateResult] = field(default_factory=list)
    alternate       : Optional[AlternateInfo]  = None
    error           : str = ""               # mensaje si found=False

    # Meteorologia en ruta (weather_reroute=True)
    route_weather_points: List = field(default_factory=list)  # List[RouteWeatherPoint]
    blocked_path        : Optional[List[str]] = None          # ruta original bloqueada
    was_rerouted        : bool = False


# ────────────────────────────────────────────────────────────────────────────
# Evaluacion meteorologica de aerodromos auxiliares
# ────────────────────────────────────────────────────────────────────────────

def _evaluate_airport(
    code    : str,
    aircraft: AircraftProfile,
    mock    : bool = False,
) -> tuple:
    """
    Evalua las condiciones meteorologicas de un aerodromo.
    Retorna (decision: str, r_total: float).
    """
    try:
        from decision.engine import DecisionEngine
    except ImportError:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from decision.engine import DecisionEngine

    aps = AIRPORTS
    if code not in aps:
        return ("SIN DATOS", 0.5)

    airport = aps[code]
    heading = airport.runways[0].heading if airport.runways else 180

    try:
        engine = DecisionEngine(mock=mock, aircraft=aircraft)
        result = engine.evaluate(
            station_id     = code,
            runway_heading = heading,
            departure_time = int(_time_module.time()),
            flight_duration_h = 1.0,
        )
        return (result.decision, result.r_total)
    except Exception:
        return ("SIN DATOS", 0.5)


def _find_alternate(
    dest_code : str,
    origin_code: str,
    r_map     : Dict[str, float],
    aircraft  : AircraftProfile,
    airports  : Dict[str, AirportInfo],
    mock      : bool = False,
) -> Optional[AlternateInfo]:
    """
    Sugiere el mejor aerodromo alternativo al destino.

    Criterio: aerodromo dentro del rango de la aeronave desde el destino,
    excluyendo el destino y el origen, con el menor r_total conocido.
    Si r_map no contiene el candidato, se asume r=0 (sin penalizacion) —
    nunca se hace un fetch meteorologico en esta funcion para evitar
    cientos de requests HTTP con 710 aerodromos cargados.
    """
    dest_ap = airports.get(dest_code)
    if dest_ap is None:
        return None

    max_range_km = aircraft.range_km * 0.80   # 80% del rango como limite seguro

    candidates = []
    for code, ap in airports.items():
        if code in (dest_code, origin_code):
            continue
        dist = haversine_km(dest_ap.lat, dest_ap.lon, ap.lat, ap.lon)
        if dist <= max_range_km:
            candidates.append((code, ap, dist))

    if not candidates:
        return None

    best_code  = None
    best_r     = float("inf")
    best_dist  = 0.0
    best_name  = ""

    for code, ap, dist in candidates:
        # Solo usar r_map; nunca hacer HTTP request por cada candidato
        r_val = r_map.get(code, 0.0)
        if r_val < best_r:
            best_r    = r_val
            best_code = code
            best_dist = dist
            best_name = ap.name

    if best_code is None:
        return None

    time_h   = leg_time_hours(best_dist, aircraft.cruise_kt)
    r_final  = r_map.get(best_code, best_r)
    if best_code in r_map:
        if r_final < 0.25:
            decision = "GO"
        elif r_final < 0.50:
            decision = "CAUTION"
        else:
            decision = "NO GO"
    else:
        decision = "SIN DATOS"

    return AlternateInfo(
        code             = best_code,
        name             = best_name,
        decision         = decision,
        r_total          = round(r_final, 3),
        dist_from_dest_km= round(best_dist, 1),
        time_from_dest_h = round(time_h, 3),
    )


# ────────────────────────────────────────────────────────────────────────────
# Filtro de aristas que cruzan zonas restringidas / de peligro
# ────────────────────────────────────────────────────────────────────────────

def _filter_restricted_edges(graph: "RouteGraph", airports: Dict) -> "RouteGraph":
    """
    Devuelve una copia del grafo sin las aristas que cruzan zonas R, P o D.
    Usa bbox pre-check por zona para evitar calculos costosos en aristas lejanas.
    """
    try:
        from data.airspace import AIRSPACE_ZONES, route_intersects_zone
    except ImportError:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from data.airspace import AIRSPACE_ZONES, route_intersects_zone

    avoidance_zones = [z for z in AIRSPACE_ZONES if z.is_restricted or z.zone_type == "D"]
    if not avoidance_zones:
        return graph

    # Pre-computa bbox (grados) de cada zona para rechazo rapido
    zone_bboxes = []
    for z in avoidance_zones:
        if z.polygon:
            lats = [p[0] for p in z.polygon]
            lons = [p[1] for p in z.polygon]
            zone_bboxes.append((min(lats), max(lats), min(lons), max(lons)))
        else:
            dlat = z.radius_km / 111.0
            cos_lat = max(abs(_math.cos(_math.radians(z.center_lat))), 0.001)
            dlon = z.radius_km / (111.0 * cos_lat)
            zone_bboxes.append((
                z.center_lat - dlat, z.center_lat + dlat,
                z.center_lon - dlon, z.center_lon + dlon,
            ))

    new_edges: Dict[str, List] = {}
    for orig_code, edge_list in graph.edges.items():
        orig_info = airports[orig_code]
        lat1, lon1 = orig_info.lat, orig_info.lon
        filtered = []
        for edge in edge_list:
            dest_info = airports[edge.dest]
            lat2, lon2 = dest_info.lat, dest_info.lon

            seg_min_lat = min(lat1, lat2)
            seg_max_lat = max(lat1, lat2)
            seg_min_lon = min(lon1, lon2)
            seg_max_lon = max(lon1, lon2)

            blocked = False
            for i, zone in enumerate(avoidance_zones):
                z_min_lat, z_max_lat, z_min_lon, z_max_lon = zone_bboxes[i]
                if (seg_max_lat < z_min_lat or seg_min_lat > z_max_lat or
                        seg_max_lon < z_min_lon or seg_min_lon > z_max_lon):
                    continue
                if route_intersects_zone(lat1, lon1, lat2, lon2, zone):
                    blocked = True
                    break

            if not blocked:
                filtered.append(edge)

        new_edges[orig_code] = filtered

    return RouteGraph(nodes=graph.nodes, edges=new_edges, r_map=graph.r_map, mode=graph.mode)


# ────────────────────────────────────────────────────────────────────────────
# Optimizador principal
# ────────────────────────────────────────────────────────────────────────────

def optimize(
    origin                : str,
    dest                  : str,
    mode                  : str                          = "suggested",
    r_map                 : Optional[Dict[str, float]]   = None,
    wind_dir              : Optional[int]                = None,
    wind_spd_kt           : Optional[float]              = None,
    avoid_restricted_zones: bool                         = False,
    airports              : Optional[Dict[str, AirportInfo]] = None,
    aircraft              : Optional[AircraftProfile]    = None,
    ga_seed               : Optional[int]                = None,
    evaluate_intermediate : bool                         = False,
    suggest_alternate     : bool                         = False,
    mock                  : bool                         = False,
    dep_time              : int                          = 0,
    weather_reroute       : bool                         = False,
) -> OptimizeResult:
    """
    Calcula la ruta optima entre dos aerodromos.

    Parametros
    ----------
    origin                : codigo ICAO del origen
    dest                  : codigo ICAO del destino
    mode                  : "shortest" | "fastest" | "safest" | "suggested"
    r_map                 : {codigo: r_total [0,1]} — riesgo meteorologico
    wind_dir              : direccion del viento FROM (grados) — para modo fastest
    wind_spd_kt           : velocidad del viento (kt) — para modo fastest
    avoid_restricted_zones: si True, modifica la ruta para no cruzar zonas R/P/D.
                            Siempre se muestran los conflictos CTR/TMA en el resultado.
                            Para modo "suggested", el GA se reemplaza por A* shortest.
    airports              : diccionario de aerodromos (por defecto: AIRPORTS global)
    aircraft              : perfil de aeronave (por defecto: ALPHA_TRAINER)
    ga_seed               : semilla para el GA (reproducibilidad en tests)
    evaluate_intermediate : si True, evalua meteo en aerodromos intermedios del path
    suggest_alternate     : si True, busca y evalua el mejor alternativo al destino
    mock                  : si True, usa datos mock para evaluaciones meteorologicas

    Retorna
    -------
    OptimizeResult con la ruta, performance y conflictos de espacio aereo.
    """
    if mode not in VALID_MODES:
        return OptimizeResult(
            found=False, mode=mode, path=[], legs=[],
            total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
            needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
            error=f"Modo invalido: {mode!r}. Usar uno de {VALID_MODES}",
        )

    # Usar aerodromos publicos para routing; agregar origen/destino si son privados
    aps = airports if airports is not None else dict(AIRPORTS_PUBLIC)
    r   = r_map if r_map is not None else {}
    ac  = aircraft if aircraft is not None else ALPHA_TRAINER

    # Asegurar que origen y destino esten en el grafo aunque sean privados
    for node in (origin, dest):
        if node not in aps and node in AIRPORTS:
            aps[node] = AIRPORTS[node]

    if origin not in aps:
        return OptimizeResult(
            found=False, mode=mode, path=[], legs=[],
            total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
            needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
            error=f"Aerodromo origen desconocido: {origin!r}",
        )
    if dest not in aps:
        return OptimizeResult(
            found=False, mode=mode, path=[], legs=[],
            total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
            needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
            error=f"Aerodromo destino desconocido: {dest!r}",
        )

    # ── Llamar al algoritmo correspondiente ─────────────────────────────────
    def _suggested_graph(max_leg_cap):
        """
        Construye el grafo del corredor para modo 'suggested' con el cap de
        tramo indicado, aplicando filtro de zonas restringidas si corresponde.
        """
        g = _build_corridor_graph(origin, dest, aps, r, ac, max_leg_cap=max_leg_cap)
        if avoid_restricted_zones:
            g = _filter_restricted_edges(g, aps)
        return g

    if mode == "suggested":
        # Estrategia escalable (sin hubs hardcodeados):
        # Se generan varias rutas candidatas con distintos caps de tramo y se
        # elige la que MAXIMIZA la cobertura de aerovias (km cubiertos por aerovia).
        #
        # Por que varios caps:
        #   - Cap corto fuerza paradas intermedias → util en zonas donde la
        #     aerovia requiere conectar varios aeropuertos (ej. Patagonia).
        #   - Sin cap permite tramos directos largos → util cuando una sola
        #     aerovia continua cubre todo el tramo (ej. A428 Corrientes→BA).
        # Elegir por cobertura real evita tanto el desvio innecesario como la
        # fragmentacion que pierde aerovias. Funciona para cualquier par O/D.
        # El filtro de banda latitudinal (en _build_corridor_graph) evita desvios
        # en sentido contrario al destino.

        def _coverage_km(path_codes):
            """Km del path cubiertos por aerovia accesible para la aeronave."""
            cov = 0.0
            for i in range(len(path_codes) - 1):
                a = aps.get(path_codes[i]); b = aps.get(path_codes[i + 1])
                if not a or not b:
                    continue
                aw = find_airways_for_leg(a.lat, a.lon, b.lat, b.lon, ac.cruise_alt_ft)
                if aw:
                    cov += haversine_km(a.lat, a.lon, b.lat, b.lon)
            return cov

        # Generar candidatos con distintos caps. None = sin cap (tramos directos).
        candidates = []
        for cap in (DEFAULT_LEG_CAP_KM, None):
            cand = astar(_suggested_graph(cap), origin, dest)
            if cand.found:
                candidates.append(cand)

        # Fallback: grafo completo sin corredor (ultimo recurso si nada conecto)
        if not candidates:
            _full = build_graph(
                mode="shortest", r_map=r, airports=aps,
                aircraft=ac, max_leg_km=ac.range_km,
            )
            if avoid_restricted_zones:
                _full = _filter_restricted_edges(_full, aps)
            _full_res = astar(_full, origin, dest)
            if _full_res.found:
                candidates.append(_full_res)

        if not candidates:
            return OptimizeResult(
                found=False, mode=mode, path=[], legs=[],
                total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
                needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
                error=(
                    "No se encontro ruta que evite todas las zonas restringidas. "
                    "Desactivar 'Evitar espacios aereos' para ver ruta disponible."
                ) if avoid_restricted_zones else "No se encontro ruta sugerida",
            )

        # Elegir el candidato con mayor cobertura de aerovia; desempate por menor
        # distancia total (ruta mas directa).
        _res = max(
            candidates,
            key=lambda c: (round(_coverage_km(c.path)), -c.total_dist_km),
        )
        path = _res.path

    else:
        # Modo A*: shortest/fastest/safest
        astar_mode = mode if mode != "suggested" else "shortest"
        graph = build_graph(
            mode        = astar_mode,
            r_map       = r,
            wind_dir    = wind_dir,
            wind_spd_kt = wind_spd_kt,
            airports    = aps,
            aircraft    = ac,
            max_leg_km  = ac.range_km,
        )
        if avoid_restricted_zones:
            graph = _filter_restricted_edges(graph, aps)
        astar_res = astar(graph, origin, dest)
        if not astar_res.found:
            return OptimizeResult(
                found=False, mode=mode, path=[], legs=[],
                total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
                needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
                error=(
                    "No se encontro ruta que evite todas las zonas restringidas. "
                    "Desactivar 'Evitar espacios aereos' para ver ruta disponible."
                ) if avoid_restricted_zones else f"A* no encontro ruta en modo {mode!r}",
            )
        path = astar_res.path

    # ── Calcular detalle de tramos ───────────────────────────────────────────
    legs = []
    leg_dicts = []
    for i in range(len(path) - 1):
        a = aps[path[i]]
        b = aps[path[i + 1]]
        dist = haversine_km(a.lat, a.lon, b.lat, b.lon)
        brng = bearing_deg(a.lat, a.lon, b.lat, b.lon)
        time = leg_time_hours(dist, ac.cruise_kt)
        fuel = leg_fuel_liters(time, ac.fuel_flow_lph)
        r_dest = r.get(path[i + 1], 0.0)

        legs.append(LegDetail(
            origin      = path[i],
            dest        = path[i + 1],
            distance_km = round(dist, 1),
            bearing_deg = round(brng, 1),
            time_hours  = round(time, 3),
            fuel_liters = round(fuel, 1),
            r_dest      = round(r_dest, 3),
        ))
        leg_dicts.append({
            "distance_km" : dist,
            "time_hours"  : time,
            "fuel_liters" : fuel,
        })

    summary = route_summary(leg_dicts, aircraft=ac)

    # ── Verificar espacio aereo (todos los tramos) ───────────────────────────
    # Siempre se muestran CTR/TMA y zonas R/P/D independientemente de la evasion.
    conflicts: List[AirspaceZone] = []
    seen_zone_names = set()
    for leg in legs:
        a, b = aps[leg.origin], aps[leg.dest]
        zones = zones_along_route(
            a.lat, a.lon, b.lat, b.lon,
            check_restricted=True,
            check_controlled=True,
        )
        for z in zones:
            if z.name not in seen_zone_names:
                conflicts.append(z)
                seen_zone_names.add(z.name)

    # ── Meteo en aerodromos intermedios ─────────────────────────────────────
    intermediate_results: List[IntermediateResult] = []
    if evaluate_intermediate and len(path) > 2:
        intermediates = path[1:-1]
        for i, code in enumerate(intermediates):
            ap = aps.get(code)
            if ap is None:
                continue
            decision, r_val = _evaluate_airport(code, ac, mock=mock)
            prev_code = path[i]   # en la lista path, el previo es path[i] porque intermediates[i] = path[i+1]
            prev_ap = aps.get(prev_code)
            dist_prev = haversine_km(prev_ap.lat, prev_ap.lon, ap.lat, ap.lon) if prev_ap else 0.0
            intermediate_results.append(IntermediateResult(
                code             = code,
                name             = ap.name,
                decision         = decision,
                r_total          = round(r_val, 3),
                dist_from_prev_km= round(dist_prev, 1),
            ))

    # ── Alternativo automatico ───────────────────────────────────────────────
    alternate: Optional[AlternateInfo] = None
    if suggest_alternate:
        alternate = _find_alternate(dest, origin, r, ac, aps, mock=mock)

    # ── Meteorologia en ruta con rerouteo automatico ─────────────────────────
    route_wx_points: List = []
    blocked_path_result: Optional[List[str]] = None
    was_rerouted = False

    if weather_reroute and dep_time > 0:
        _effective_dep = dep_time
        _current_path  = list(path)
        _blocked_edges: set = set()

        for _iteration in range(3):
            _points = sample_route_weather(
                path      = _current_path,
                airports  = aps,
                dep_time  = _effective_dep,
                cruise_kt = ac.cruise_kt,
                aircraft  = ac,
                mock      = mock,
            )
            _bad_legs = blocked_legs(_current_path, _points, aps)
            if not _bad_legs:
                route_wx_points = _points
                break

            if not was_rerouted:
                blocked_path_result = list(_current_path)
                was_rerouted = True

            _blocked_edges.update(_bad_legs)

            # Recalcular con A* eliminando las aristas bloqueadas
            _graph = build_graph(
                mode        = "shortest",
                r_map       = r,
                airports    = aps,
                aircraft    = ac,
                max_leg_km  = ac.range_km,
            )
            # Filtrar aristas meteorologicamente bloqueadas
            _new_edges: Dict[str, List] = {}
            for _orig_code, _edge_list in _graph.edges.items():
                _new_edges[_orig_code] = [
                    e for e in _edge_list
                    if (_orig_code, e.dest) not in _blocked_edges
                ]
            from route.graph import RouteGraph
            _filtered = RouteGraph(
                nodes  = _graph.nodes,
                edges  = _new_edges,
                r_map  = _graph.r_map,
                mode   = _graph.mode,
            )
            _alt_res = astar(_filtered, origin, dest)
            if not _alt_res.found:
                _logger.warning("Rerouteo meteo: no se encontro ruta alternativa")
                route_wx_points = _points
                break
            _current_path = _alt_res.path
        else:
            # Agotamos iteraciones — samplear la ruta final
            route_wx_points = sample_route_weather(
                path      = _current_path,
                airports  = aps,
                dep_time  = _effective_dep,
                cruise_kt = ac.cruise_kt,
                aircraft  = ac,
                mock      = mock,
            )

        if was_rerouted:
            # Recalcular tramos con la nueva ruta
            path = _current_path
            legs = []
            leg_dicts = []
            for i in range(len(path) - 1):
                a = aps[path[i]]
                b = aps[path[i + 1]]
                dist = haversine_km(a.lat, a.lon, b.lat, b.lon)
                brng = bearing_deg(a.lat, a.lon, b.lat, b.lon)
                t    = leg_time_hours(dist, ac.cruise_kt)
                fuel = leg_fuel_liters(t, ac.fuel_flow_lph)
                legs.append(LegDetail(
                    origin      = path[i],
                    dest        = path[i + 1],
                    distance_km = round(dist, 1),
                    bearing_deg = round(brng, 1),
                    time_hours  = round(t, 3),
                    fuel_liters = round(fuel, 1),
                    r_dest      = r.get(path[i + 1], 0.0),
                ))
                leg_dicts.append({"distance_km": dist, "time_hours": t, "fuel_liters": fuel})
            summary = route_summary(leg_dicts, aircraft=ac)

    return OptimizeResult(
        found                = True,
        mode                 = mode,
        path                 = path,
        legs                 = legs,
        total_dist_km        = summary["total_distance_km"],
        total_time_h         = summary["total_time_hours"],
        total_fuel_l         = summary["total_fuel_liters"],
        needs_fuel_stop      = summary["needs_fuel_stop"],
        fuel_ok              = summary["fuel_ok"],
        airspace_conflicts   = conflicts,
        aircraft             = ac,
        intermediate_results = intermediate_results,
        alternate            = alternate,
        route_weather_points = route_wx_points,
        blocked_path         = blocked_path_result,
        was_rerouted         = was_rerouted,
    )


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: route/optimizer.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    def print_result(label, res):
        if res.found:
            path_str = " -> ".join(res.path)
            ac_name  = res.aircraft.name if res.aircraft else "N/A"
            print(f"\n  {label}:")
            print(f"    Aeronave : {ac_name}")
            print(f"    Ruta     : {path_str}")
            print(f"    Distancia: {res.total_dist_km:.1f} km")
            h = int(res.total_time_h)
            m = int((res.total_time_h - h) * 60)
            print(f"    Tiempo   : {h}h {m:02d}min")
            print(f"    Combus.  : {res.total_fuel_l:.1f} L")
            print(f"    Escala   : {'SI' if res.needs_fuel_stop else 'NO'}")
            if res.airspace_conflicts:
                for z in res.airspace_conflicts:
                    flag = "[R]" if z.is_restricted else "[C]" if z.is_controlled else "[D]"
                    print(f"    Espacio  : {flag} {z.name}")
            if res.intermediate_results:
                print(f"    Intermedios:")
                for ir in res.intermediate_results:
                    print(f"      {ir.code} ({ir.name}): {ir.decision}  R={ir.r_total:.2f}")
            if res.alternate:
                alt = res.alternate
                print(f"    Alternativo: {alt.code} ({alt.name})  "
                      f"{alt.decision}  R={alt.r_total:.2f}  "
                      f"{alt.dist_from_dest_km:.0f}km desde destino")
        else:
            print(f"\n  {label}: ERROR - {res.error}")

    from risk.aircraft_profiles import ALPHA_TRAINER, CESSNA_172, CESSNA_152

    # ── Modo shortest, Alpha Trainer (default) ──
    r1 = optimize("SACC", "SAOE", mode="shortest")
    check("shortest SACC-SAOE: found",        r1.found)
    check("shortest SACC-SAOE: path correcto", r1.found and r1.path[0] == "SACC" and r1.path[-1] == "SAOE")
    check("shortest SACC-SAOE: legs correctos", r1.found and len(r1.legs) == len(r1.path) - 1)
    check("shortest SACC-SAOE: fuel_ok",      r1.found and r1.fuel_ok)
    check("shortest SACC-SAOE: aircraft = ALPHA_TRAINER", r1.aircraft is ALPHA_TRAINER)
    print_result("shortest SACC-SAOE (Alpha Trainer)", r1)

    # ── Modo shortest con C172 (mayor velocidad y consumo) ──
    r1c = optimize("SACC", "SAOE", mode="shortest", aircraft=CESSNA_172)
    check("C172: aircraft correcto",   r1c.found and r1c.aircraft is CESSNA_172)
    check("C172: fuel_flow mayor",     r1c.found and r1c.total_fuel_l > r1.total_fuel_l)
    check("C172: tiempo menor (mas rapido)", r1c.found and r1c.total_time_h < r1.total_time_h)
    print_result("shortest SACC-SAOE (Cessna 172)", r1c)

    # ── Modo fastest ──
    r2 = optimize("SACC", "SAOM", mode="fastest", wind_dir=270, wind_spd_kt=15.0)
    check("fastest SACC-SAOM: found",  r2.found)
    print_result("fastest SACC-SAOM (viento 270/15kt)", r2)

    # ── Modo safest ──
    r_map = {code: 0.0 for code in AIRPORTS}
    r_map["SACC"] = 0.1
    r3 = optimize("SACC", "SAOL", mode="safest", r_map=r_map)
    check("safest SACC-SAOL: found",  r3.found)
    print_result("safest SACC-SAOL", r3)

    # ── Modo suggested ──
    r4 = optimize("SACC", "SAOM", mode="suggested", ga_seed=42)
    check("suggested SACC-SAOM: found", r4.found)
    check("suggested SACC-SAOM: legs correctos",
          r4.found and len(r4.legs) == len(r4.path) - 1)
    print_result("suggested SACC-SAOM (GA)", r4)

    # ── Alternativo automatico (mock) ──
    r5 = optimize("SACC", "SAOM", mode="shortest",
                  suggest_alternate=True, mock=True)
    check("Alternativo: found",          r5.found)
    check("Alternativo: no None",        r5.alternate is not None)
    if r5.alternate:
        check("Alternativo: no es origen ni destino",
              r5.alternate.code not in ("SACC", "SAOM"))
        check("Alternativo: dist_from_dest > 0",
              r5.alternate.dist_from_dest_km > 0)
    print_result("shortest con alternativo", r5)

    # ── Meteo intermedia (mock, ruta con intermedios) ──
    # GA puede producir rutas con intermedios
    r6 = optimize("SACC", "SAOE", mode="suggested",
                  evaluate_intermediate=True, mock=True, ga_seed=7)
    check("Meteo intermedia: found", r6.found)
    if len(r6.path) > 2:
        check("Meteo intermedia: results presentes",
              len(r6.intermediate_results) == len(r6.path) - 2)
    print_result("suggested SACC-SAOE con meteo intermedia", r6)

    # ── C152: rango menor afecta fuel_ok ──
    r7 = optimize("SACC", "SAOM", mode="shortest", aircraft=CESSNA_152)
    check("C152: aircraft guardado", r7.found and r7.aircraft is CESSNA_152)

    # ── Error: modo invalido ──
    r_bad = optimize("SACC", "SAOE", mode="turbo")
    check("Modo invalido: not found",  not r_bad.found)
    check("Modo invalido: error string", "invalido" in r_bad.error.lower())

    # ── Error: nodo desconocido ──
    r_bad2 = optimize("XXXX", "SAOE")
    check("Nodo desconocido: not found", not r_bad2.found)

    # ── Todos los modos corren en subset de pares ──
    codes = list(AIRPORTS.keys())
    any_fail = False
    for mode in VALID_MODES:
        for src in codes[:3]:
            for dst in codes[:3]:
                if src != dst:
                    res = optimize(src, dst, mode=mode, ga_seed=0)
                    if not res.found:
                        any_fail = True
    check("Todos los modos corren en subset de pares", not any_fail)

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
