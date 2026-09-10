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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_logger = _logging.getLogger(__name__)

try:
    from data.airports import AIRPORTS, AIRPORTS_PUBLIC, AirportInfo
    from data.airspace import zones_along_route, AirspaceZone
    from route.graph import RouteGraph, build_graph
    from route.astar import astar

    from route.performance import (
        haversine_km, bearing_deg, leg_time_hours, leg_fuel_liters,
        route_summary,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from risk.weights import apply_decision_threshold
    from route.weather_sampler import sample_route_weather, blocked_legs
    from route.airway_router import find_airways_for_leg
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS, AIRPORTS_PUBLIC, AirportInfo
    from data.airspace import zones_along_route, AirspaceZone
    from route.graph import RouteGraph, build_graph
    from route.astar import astar

    from route.performance import (
        haversine_km, bearing_deg, leg_time_hours, leg_fuel_liters,
        route_summary,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from risk.weights import apply_decision_threshold
    from route.weather_sampler import sample_route_weather, blocked_legs
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

@dataclass(frozen=True)
class ViaPoint:
    """
    Punto de paso intermedio pedido por el piloto.

    `is_stop` distingue dos cosas que NO son lo mismo, aunque den la misma
    geometria de ruta:

      * SOBREVUELO (is_stop=False) — "quiero pasar por Rosario". Vuelo continuo:
        el combustible se calcula sobre todo el trayecto y el aerodromo es solo
        un punto de forma de la ruta.

      * ESCALA (is_stop=True) — "quiero hacer escala en Rosario". Se aterriza
        ahi, y de eso se siguen dos consecuencias que el sistema tiene que
        respetar: el aerodromo debe ser ATERRIZABLE (su veredicto cuenta) y el
        combustible se evalua POR ETAPA entre escalas, no sobre el total.
    """
    code    : str
    is_stop : bool = False


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
    # True si el piloto pidio ATERRIZAR aca. Un aerodromo de paso solo se
    # sobrevuela; uno de escala tiene que ser operable, y su veredicto pesa
    # sobre el vuelo igual que el del origen y el del destino.
    is_stop    : bool = False
    # Momento para el que se evaluo (Unix UTC). En una ruta con escalas los
    # tramos no salen todos a la misma hora: el segundo arranca cuando termina
    # el primero, y evaluarlo a la hora de despegue seria mirar otro momento.
    evaluated_at: int = 0


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
    code     : str,
    aircraft : AircraftProfile,
    mock     : bool = False,
    when_unix: int  = 0,
) -> tuple:
    """
    Evalua las condiciones meteorologicas de un aerodromo.

    `when_unix`: momento para el que se evalua (Unix UTC). 0 = ahora.
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

    try:
        engine = DecisionEngine(mock=mock, aircraft=aircraft)
        result = engine.evaluate(
            station_id     = code,
            runway_heading = None,   # auto: cabecera favorable al viento
            departure_time = when_unix if when_unix > 0 else int(_time_module.time()),
            flight_duration_h = 1.0,
        )
        return (result.decision, result.r_total)
    except Exception as exc:
        _logger.debug(f"No se pudo evaluar {code} como alternativo: {exc}")
        return ("SIN DATOS", 0.5)


# Candidatos a alternativo que se evaluan meteorologicamente. Acotado a
# proposito: cada evaluacion es un fetch (METAR o NWP) y el objetivo es hallar
# el mas cercano APTO, no rankear los 561 aerodromos del pais.
ALTERNATE_MAX_CANDIDATES = 8

# Radio maximo de busqueda desde el destino. Un alternativo lejano no sirve:
# hay que poder alcanzarlo con la reserva reglamentaria.
ALTERNATE_MAX_DIST_KM = 250.0


def _find_alternate(
    dest_code : str,
    origin_code: str,
    r_map     : Dict[str, float],
    aircraft  : AircraftProfile,
    airports  : Dict[str, AirportInfo],
    mock      : bool = False,
    when_unix : int  = 0,
) -> Optional[AlternateInfo]:
    """
    Sugiere el aerodromo alternativo al destino: el MAS CERCANO que ademas sea
    METEOROLOGICAMENTE APTO.

    Criterio, en orden:
      1. Candidatos dentro de ALTERNATE_MAX_DIST_KM del destino y dentro del
         alcance util de la aeronave, excluyendo origen y destino.
      2. Se ordenan por distancia y se evaluan en paralelo los
         ALTERNATE_MAX_CANDIDATES mas cercanos.
      3. Gana el mas cercano con decision GO. Si ninguno da GO, el mas cercano
         con CAUTION. Si tampoco, el mas cercano evaluado con su decision real.

    Por que cercania Y aptitud: un alternativo existe para desviarse cuando el
    destino no es utilizable. Si esta lejos no se alcanza con la reserva, y si
    no es apto no es alternativo. Un criterio puramente geometrico proponia
    aerodromos con la meteorologia sin verificar ("SIN DATOS").
    """
    dest_ap = airports.get(dest_code)
    if dest_ap is None:
        return None

    # Limite de busqueda: el menor entre el radio razonable de desvio y el
    # alcance util de ESTA aeronave (escalable a todos los perfiles).
    max_dist_km = min(ALTERNATE_MAX_DIST_KM, aircraft.range_km * 0.80)

    candidates = []
    for code, ap in airports.items():
        if code in (dest_code, origin_code):
            continue
        dist = haversine_km(dest_ap.lat, dest_ap.lon, ap.lat, ap.lon)
        if dist <= max_dist_km:
            candidates.append((dist, code, ap))

    if not candidates:
        return None

    # Los mas cercanos primero: se evalua solo esa franja
    candidates.sort(key=lambda c: c[0])
    shortlist = candidates[:ALTERNATE_MAX_CANDIDATES]

    def _eval(item):
        dist, code, ap = item
        # Si ya se evaluo en esta corrida (origen/destino/ruta), reusar el valor
        if code in r_map:
            r_val = r_map[code]
            return (dist, code, ap, apply_decision_threshold(r_val), r_val)
        decision, r_val = _evaluate_airport(code, aircraft, mock=mock, when_unix=when_unix)
        return (dist, code, ap, decision, r_val)

    with ThreadPoolExecutor(max_workers=min(ALTERNATE_MAX_CANDIDATES, len(shortlist))) as ex:
        evaluated = list(ex.map(_eval, shortlist))

    # Ya vienen ordenados por distancia: el primero que cumpla, gana.
    best = (
        next((e for e in evaluated if e[3] == "GO"), None)
        or next((e for e in evaluated if e[3] == "CAUTION"), None)
        or evaluated[0]
    )

    dist, code, ap, decision, r_val = best
    _logger.info(
        f"Alternativo de {dest_code}: {code} a {dist:.0f} km [{decision}] "
        f"(evaluados {len(evaluated)} candidatos)"
    )

    return AlternateInfo(
        code             = code,
        name             = ap.name,
        decision         = decision,
        r_total          = round(r_val, 3),
        dist_from_dest_km= round(dist, 1),
        time_from_dest_h = round(leg_time_hours(dist, aircraft.cruise_kt), 3),
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

    # max_gs_kt se propaga: la heuristica de A* lo necesita para ser admisible
    return RouteGraph(nodes=graph.nodes, edges=new_edges, r_map=graph.r_map,
                      mode=graph.mode, max_gs_kt=graph.max_gs_kt)


# ────────────────────────────────────────────────────────────────────────────
# Optimizador principal
# ────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────
# Costo de un desvio
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class DetourCost:
    """
    Lo que cuesta pasar por un punto, comparado con la ruta directa.

    Existe para que una propuesta de desvio sea una decision informada y no un
    boton a ciegas: el piloto ve cuanto agrega antes de aceptar.

    `fuel_ok_antes` y `fuel_ok_despues` se informan por separado a proposito:
    un desvio que deja la ruta sin autonomia suficiente NO es un detalle de
    magnitud, es un cambio de viabilidad, y merece decirse aparte de los
    kilometros.
    """
    found            : bool
    dist_km_extra    : float
    time_min_extra   : float
    fuel_l_extra     : float
    path_antes       : List[str]
    path_despues     : List[str]
    fuel_ok_antes    : bool
    fuel_ok_despues  : bool
    needs_stop_antes : bool
    needs_stop_despues: bool
    error            : str = ""

    @property
    def rompe_la_autonomia(self) -> bool:
        """El desvio convierte una ruta viable en una que no cierra."""
        return self.fuel_ok_antes and not self.fuel_ok_despues


def detour_cost(base: OptimizeResult, con_via: OptimizeResult) -> DetourCost:
    """Compara la ruta directa contra la que pasa por los puntos pedidos."""
    if not base.found or not con_via.found:
        return DetourCost(
            found=False, dist_km_extra=0.0, time_min_extra=0.0, fuel_l_extra=0.0,
            path_antes=list(base.path), path_despues=list(con_via.path),
            fuel_ok_antes=base.fuel_ok, fuel_ok_despues=con_via.fuel_ok,
            needs_stop_antes=base.needs_fuel_stop,
            needs_stop_despues=con_via.needs_fuel_stop,
            error=con_via.error or base.error or "No se pudo calcular la ruta.",
        )
    return DetourCost(
        found              = True,
        dist_km_extra      = round(con_via.total_dist_km - base.total_dist_km, 1),
        time_min_extra     = round((con_via.total_time_h - base.total_time_h) * 60, 0),
        fuel_l_extra       = round(con_via.total_fuel_l - base.total_fuel_l, 1),
        path_antes         = list(base.path),
        path_despues       = list(con_via.path),
        fuel_ok_antes      = base.fuel_ok,
        fuel_ok_despues    = con_via.fuel_ok,
        needs_stop_antes   = base.needs_fuel_stop,
        needs_stop_despues = con_via.needs_fuel_stop,
    )


# ────────────────────────────────────────────────────────────────────────────
# Ruta con puntos de paso
# ────────────────────────────────────────────────────────────────────────────
# La ruta se resuelve por segmentos —origen->via1->via2->destino— y se fusiona.
# Cada segmento usa `optimize()` sin `via`, de modo que no hay una segunda
# implementacion del ruteo que pueda divergir de la primera.
#
# Lo unico que NO se puede fusionar sumando es el combustible, y ahi esta la
# diferencia entre sobrevuelo y escala:
#
#   * Sin escalas el vuelo es continuo: la autonomia se mide sobre el trayecto
#     COMPLETO. Dos tramos que por separado entran en el tanque pueden no
#     entrar juntos, y sumar dos `fuel_ok=True` daria un falso positivo.
#
#   * Con escalas el vuelo se parte en ETAPAS. Cada etapa se mide por separado
#     porque en el medio se aterriza (y eventualmente se reposta).


def _merge_summaries(etapas: "List[dict]") -> dict:
    """
    Combina los resumenes de cada etapa en uno solo.

    Distancia, tiempo y combustible se suman —son magnitudes del vuelo entero—
    pero la FACTIBILIDAD no: basta con que UNA etapa no entre en el tanque para
    que la ruta no sea viable, y basta con que UNA supere el umbral para que
    haga falta parar a cargar.
    """
    return {
        "total_distance_km": round(sum(e["total_distance_km"] for e in etapas), 1),
        "total_time_hours" : round(sum(e["total_time_hours"]  for e in etapas), 3),
        "total_fuel_liters": round(sum(e["total_fuel_liters"] for e in etapas), 1),
        "needs_fuel_stop"  : any(e["needs_fuel_stop"] for e in etapas),
        "fuel_ok"          : all(e["fuel_ok"] for e in etapas),
    }


def _optimize_via(
    origin, dest, via, mode, r_map, wind_dir, wind_spd_kt,
    avoid_restricted_zones, airports, aircraft, evaluate_intermediate,
    suggest_alternate, mock, dep_time,
) -> OptimizeResult:
    """Arma la ruta pasando por los puntos pedidos y fusiona los segmentos."""
    ac    = aircraft if aircraft is not None else ALPHA_TRAINER
    orden = [origin] + [v.code for v in via] + [dest]

    # Un punto repetido no agrega nada y puede generar un tramo de longitud
    # cero, que rompe el calculo de rumbo.
    for a, b in zip(orden, orden[1:]):
        if a == b:
            return OptimizeResult(
                found=False, mode=mode, path=[], legs=[],
                total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
                needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
                aircraft=ac,
                error=f"El punto de paso {a} esta repetido o coincide con "
                      f"el origen o el destino.",
            )

    # Hora de salida de CADA segmento. El segundo tramo no despega a la misma
    # hora que el primero: arranca cuando el primero termina. Evaluar todo a la
    # hora de despegue seria mirar la meteorologia de otro momento, que es
    # exactamente el error que ya costo caro en el motor de decision.
    escalas = {v.code for v in via if v.is_stop}
    segmentos: List[OptimizeResult] = []
    salida_seg = dep_time
    salidas: List[int] = []

    for a, b in zip(orden, orden[1:]):
        salidas.append(salida_seg)
        seg = optimize(
            origin=a, dest=b, mode=mode, r_map=r_map,
            wind_dir=wind_dir, wind_spd_kt=wind_spd_kt,
            avoid_restricted_zones=avoid_restricted_zones, airports=airports,
            aircraft=ac, evaluate_intermediate=evaluate_intermediate,
            suggest_alternate=False,     # el alternativo es del destino final
            mock=mock, dep_time=salida_seg,
        )
        if not seg.found:
            return OptimizeResult(
                found=False, mode=mode, path=[], legs=[],
                total_dist_km=0.0, total_time_h=0.0, total_fuel_l=0.0,
                needs_fuel_stop=False, fuel_ok=False, airspace_conflicts=[],
                aircraft=ac,
                error=f"No hay ruta de {a} a {b}: {seg.error}",
            )
        segmentos.append(seg)
        if salida_seg > 0:
            salida_seg += int(seg.total_time_h * 3600)

    # ── Camino y tramos: concatenar sin duplicar los empalmes ────────────────
    path: List[str] = list(segmentos[0].path)
    legs: List[LegDetail] = list(segmentos[0].legs)
    for seg in segmentos[1:]:
        path.extend(seg.path[1:])       # el primero ya esta como final del previo
        legs.extend(seg.legs)

    # ── Combustible: agrupar los tramos en ETAPAS separadas por las escalas ──
    etapas: List[List[dict]] = [[]]
    for leg in legs:
        etapas[-1].append({
            "distance_km": leg.distance_km,
            "time_hours" : leg.time_hours,
            "fuel_liters": leg.fuel_liters,
        })
        if leg.dest in escalas:
            etapas.append([])           # se aterriza aca: arranca otra etapa
    etapas = [e for e in etapas if e]

    summary = _merge_summaries([route_summary(e, aircraft=ac) for e in etapas])

    # ── Espacio aereo: union sin repetir zonas ───────────────────────────────
    conflicts: List[AirspaceZone] = []
    vistas = set()
    for seg in segmentos:
        for z in seg.airspace_conflicts:
            if z.name not in vistas:
                conflicts.append(z)
                vistas.add(z.name)

    intermedios: List[IntermediateResult] = []
    for seg in segmentos:
        intermedios.extend(seg.intermediate_results)

    # ── Las ESCALAS se evaluan como aerodromos ───────────────────────────────
    # Un punto de escala es el DESTINO de su segmento, asi que nunca cae en el
    # path[1:-1] que mira `evaluate_intermediate`: sin esto quedaba sin evaluar
    # justamente el aerodromo donde el piloto va a aterrizar.
    #
    # Se evalua a la hora de LLEGADA, no a la de despegue: si la escala esta a
    # dos horas de vuelo, su meteorologia a la hora de salida es la de otro
    # momento. `salidas[i+1]` es exactamente cuando se despega DESDE esa escala,
    # o sea cuando se estuvo ahi.
    aps_ref = airports if airports is not None else dict(AIRPORTS_PUBLIC)
    ya_evaluados = {i.code for i in intermedios}
    for idx, punto in enumerate(via):
        if not punto.is_stop or punto.code in ya_evaluados:
            continue
        ap_escala = aps_ref.get(punto.code) or AIRPORTS.get(punto.code)
        if ap_escala is None:
            continue
        cuando = salidas[idx + 1] if idx + 1 < len(salidas) else dep_time
        decision, r_val = _evaluate_airport(punto.code, ac, mock=mock,
                                            when_unix=cuando)
        prev = orden[idx]
        ap_prev = aps_ref.get(prev) or AIRPORTS.get(prev)
        dist = (haversine_km(ap_prev.lat, ap_prev.lon,
                             ap_escala.lat, ap_escala.lon)
                if ap_prev else 0.0)
        intermedios.append(IntermediateResult(
            code              = punto.code,
            name              = ap_escala.name,
            decision          = decision,
            r_total           = round(r_val, 3),
            dist_from_prev_km = round(dist, 1),
            is_stop           = True,
            evaluated_at      = cuando,
        ))

    # El alternativo es el del DESTINO FINAL, no el de cada segmento.
    alternate = None
    if suggest_alternate:
        _eta = (dep_time + int(summary["total_time_hours"] * 3600)) if dep_time > 0 else 0
        aps = airports if airports is not None else dict(AIRPORTS_PUBLIC)
        for node in (origin, dest):
            if node not in aps and node in AIRPORTS:
                aps[node] = AIRPORTS[node]
        alternate = _find_alternate(dest, origin, r_map or {}, ac, aps,
                                    mock=mock, when_unix=_eta)

    return OptimizeResult(
        found              = True,
        mode               = mode,
        path               = path,
        legs               = legs,
        total_dist_km      = summary["total_distance_km"],
        total_time_h       = summary["total_time_hours"],
        total_fuel_l       = summary["total_fuel_liters"],
        needs_fuel_stop    = summary["needs_fuel_stop"],
        fuel_ok            = summary["fuel_ok"],
        airspace_conflicts = conflicts,
        aircraft           = ac,
        intermediate_results = intermedios,
        alternate          = alternate,
    )


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
    evaluate_intermediate : bool                         = False,
    suggest_alternate     : bool                         = False,
    mock                  : bool                         = False,
    dep_time              : int                          = 0,
    weather_reroute       : bool                         = False,
    via                   : "Optional[List[ViaPoint]]"   = None,
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
    evaluate_intermediate : si True, evalua meteo en aerodromos intermedios del path
    suggest_alternate     : si True, busca y evalua el mejor alternativo al destino
    mock                  : si True, usa datos mock para evaluaciones meteorologicas
    via                   : puntos de paso intermedios pedidos por el piloto.
                            Cada uno declara si es SOBREVUELO o ESCALA (ver
                            ViaPoint): no es lo mismo pasar por encima que
                            aterrizar, aunque la linea de ruta sea la misma.

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

    # Con puntos de paso la ruta se arma por segmentos y se fusiona. Se delega
    # ANTES de tocar nada para no duplicar la logica de un tramo suelto: cada
    # segmento se resuelve con esta misma funcion, sin `via`.
    if via:
        if weather_reroute:
            # El rerouteo automatico reescribe el camino, lo que dejaria sin
            # efecto los puntos que el piloto pidio expresamente. Se declara en
            # vez de ignorarse en silencio.
            _logger.warning("weather_reroute no se aplica cuando hay puntos de paso: "
                         "el piloto fijo la ruta expresamente")
        return _optimize_via(
            origin=origin, dest=dest, via=via, mode=mode, r_map=r_map,
            wind_dir=wind_dir, wind_spd_kt=wind_spd_kt,
            avoid_restricted_zones=avoid_restricted_zones, airports=airports,
            aircraft=aircraft, evaluate_intermediate=evaluate_intermediate,
            suggest_alternate=suggest_alternate, mock=mock, dep_time=dep_time,
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
        # Cada intermedio se evalua para el momento en que se PASA por el, no
        # para la hora de despegue: en una ruta larga puede haber horas de
        # diferencia, y devolver la meteorologia de otro momento como si fuera
        # la del punto es el mismo error que ya costo caro en el motor.
        # (Se replica el criterio que este archivo ya usaba para el alternativo,
        # que se evalua a la hora estimada de arribo.)
        acumulado_h = 0.0
        for i, code in enumerate(intermediates):
            acumulado_h += legs[i].time_hours if i < len(legs) else 0.0
            cuando = dep_time + int(acumulado_h * 3600) if dep_time > 0 else 0
            ap = aps.get(code)
            if ap is None:
                continue
            decision, r_val = _evaluate_airport(code, ac, mock=mock,
                                                when_unix=cuando)
            prev_code = path[i]   # en la lista path, el previo es path[i] porque intermediates[i] = path[i+1]
            prev_ap = aps.get(prev_code)
            dist_prev = haversine_km(prev_ap.lat, prev_ap.lon, ap.lat, ap.lon) if prev_ap else 0.0
            intermediate_results.append(IntermediateResult(
                code             = code,
                name             = ap.name,
                decision         = decision,
                r_total          = round(r_val, 3),
                dist_from_prev_km= round(dist_prev, 1),
                evaluated_at     = cuando,
            ))

    # ── Alternativo automatico ───────────────────────────────────────────────
    alternate: Optional[AlternateInfo] = None
    if suggest_alternate:
        # El alternativo se usa AL LLEGAR: se evalua a la hora estimada de
        # arribo, no a la de despegue.
        _eta = (dep_time + int(summary["total_time_hours"] * 3600)) if dep_time > 0 else 0
        alternate = _find_alternate(dest, origin, r, ac, aps, mock=mock, when_unix=_eta)

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
    r1 = optimize("SACC", "SAOC", mode="shortest")
    check("shortest SACC-SAOC: found",        r1.found)
    check("shortest SACC-SAOC: path correcto", r1.found and r1.path[0] == "SACC" and r1.path[-1] == "SAOC")
    check("shortest SACC-SAOC: legs correctos", r1.found and len(r1.legs) == len(r1.path) - 1)
    check("shortest SACC-SAOC: fuel_ok",      r1.found and r1.fuel_ok)
    check("shortest SACC-SAOC: aircraft = ALPHA_TRAINER", r1.aircraft is ALPHA_TRAINER)
    print_result("shortest SACC-SAOC (Alpha Trainer)", r1)

    # ── Modo shortest con C172 (mayor velocidad y consumo) ──
    r1c = optimize("SACC", "SAOC", mode="shortest", aircraft=CESSNA_172)
    check("C172: aircraft correcto",   r1c.found and r1c.aircraft is CESSNA_172)
    check("C172: fuel_flow mayor",     r1c.found and r1c.total_fuel_l > r1.total_fuel_l)
    check("C172: tiempo menor (mas rapido)", r1c.found and r1c.total_time_h < r1.total_time_h)
    print_result("shortest SACC-SAOC (Cessna 172)", r1c)

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
    r4 = optimize("SACC", "SAOM", mode="suggested")
    check("suggested SACC-SAOM: found", r4.found)
    check("suggested SACC-SAOM: legs correctos",
          r4.found and len(r4.legs) == len(r4.path) - 1)
    print_result("suggested SACC-SAOM", r4)

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
    r6 = optimize("SACC", "SAOC", mode="suggested",
                  evaluate_intermediate=True, mock=True)
    check("Meteo intermedia: found", r6.found)
    if len(r6.path) > 2:
        check("Meteo intermedia: results presentes",
              len(r6.intermediate_results) == len(r6.path) - 2)
    print_result("suggested SACC-SAOC con meteo intermedia", r6)

    # ── C152: rango menor afecta fuel_ok ──
    r7 = optimize("SACC", "SAOM", mode="shortest", aircraft=CESSNA_152)
    check("C152: aircraft guardado", r7.found and r7.aircraft is CESSNA_152)

    # ── Error: modo invalido ──
    r_bad = optimize("SACC", "SAOC", mode="turbo")
    check("Modo invalido: not found",  not r_bad.found)
    check("Modo invalido: error string", "invalido" in r_bad.error.lower())

    # ── Error: nodo desconocido ──
    r_bad2 = optimize("XXXX", "SAOC")
    check("Nodo desconocido: not found", not r_bad2.found)

    # ── Todos los modos corren en subset de pares ──
    codes = list(AIRPORTS.keys())
    any_fail = False
    for mode in VALID_MODES:
        for src in codes[:3]:
            for dst in codes[:3]:
                if src != dst:
                    res = optimize(src, dst, mode=mode)
                    if not res.found:
                        any_fail = True
    check("Todos los modos corren en subset de pares", not any_fail)

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
