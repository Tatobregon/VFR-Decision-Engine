"""
airway_router.py
================
Enrutamiento por aerovias inferiores.

Para cada tramo (origen → destino), busca si existe una ruta por aerovias
cuya distancia total no supere MAX_DETOUR_PCT sobre la distancia directa
y cuyo MEA sea accesible para la aeronave.

Algoritmo:
  1. Buscar los 3 nodos de aerovia mas cercanos al origen (<= 100 km).
  2. Buscar los 3 nodos de aerovia mas cercanos al destino (<= 100 km).
  3. Para cada combinacion entry x exit: Dijkstra MEA-filtrado.
  4. Seleccionar la ruta de menor distancia total que cumpla el limite de desvio.
  5. Retornar lista ordenada de AirwayWaypoint.

Estructura devuelta:
  AirwayWaypoint(node_id, lat, lon, airway_name, mea_ft,
                 fir_contact, is_entry, is_exit)
"""

import heapq
import math
from dataclasses import dataclass, field
from typing import Optional

from data.airways import AIRWAY_NODES, AIRWAY_GRAPH, find_nearest_nodes
from data.fir_zones import get_fir

MAX_DETOUR_PCT   = 0.20   # maximo 20% sobre distancia directa
MAX_ENTRY_KM     = 150.0  # radio maximo para buscar entry/exit nodes
TOP_N_CANDIDATES = 5      # cantidad de candidatos entry/exit a evaluar


# ── Dataclass resultado ────────────────────────────────────────────────────────

@dataclass
class AirwayWaypoint:
    node_id    : str
    lat        : float
    lon        : float
    airway_name: str            # nombre de la aerovia (ej. "W6")
    mea_ft     : int            # MEA del segmento de llegada a este nodo
    fir_contact: Optional[str]  # solo en el punto de entrada
    is_entry   : bool = False
    is_exit    : bool = False


# ── Haversine local ────────────────────────────────────────────────────────────

def _hav(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ── Dijkstra ───────────────────────────────────────────────────────────────────

def _dijkstra(start_id: str, end_id: str, max_mea_ft: int) -> tuple[list, float]:
    """
    Dijkstra desde start_id hasta end_id con restriccion MEA <= max_mea_ft.
    Retorna (lista_de_nodos_con_info, distancia_total) o ([], inf).
    """
    if start_id not in AIRWAY_GRAPH or end_id not in AIRWAY_NODES:
        return [], math.inf

    dist_map = {start_id: 0.0}
    prev_map: dict[str, tuple] = {}   # node -> (prev_node, edge_dict)
    pq = [(0.0, start_id)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist_map.get(u, math.inf):
            continue
        if u == end_id:
            break
        for edge in AIRWAY_GRAPH.get(u, []):
            if edge["mea"] > max_mea_ft:
                continue
            v = edge["to"]
            nd = d + edge["dist"]
            if nd < dist_map.get(v, math.inf):
                dist_map[v] = nd
                prev_map[v] = (u, edge)
                heapq.heappush(pq, (nd, v))

    if end_id not in dist_map:
        return [], math.inf

    # Reconstruir camino
    path_nodes = []
    cur = end_id
    while cur != start_id:
        prev_node, edge = prev_map[cur]
        path_nodes.append({"node": cur, "ruta": edge["ruta"], "mea": edge["mea"]})
        cur = prev_node
    path_nodes.append({"node": start_id, "ruta": None, "mea": None})
    path_nodes.reverse()

    return path_nodes, dist_map[end_id]


# ── Funcion principal ──────────────────────────────────────────────────────────

def find_airways_for_leg(
    orig_lat: float, orig_lon: float,
    dest_lat: float, dest_lon: float,
    cruise_alt_ft: int,
    max_detour_pct: float = MAX_DETOUR_PCT,
) -> list[AirwayWaypoint]:
    """
    Busca la mejor ruta por aerovias inferiores para el tramo dado.
    Retorna lista de AirwayWaypoint ordenados (entry → ... → exit),
    o lista vacia si no hay ruta adecuada.

    max_detour_pct: desvio maximo permitido sobre la distancia directa.
    Para una ruta completa (origen→destino) conviene un valor mas alto que
    para un tramo corto, porque las aerovias zigzaguean y el camino end-to-end
    es mas eficiente que concatenar caminos por tramo.
    """
    direct_km = _hav(orig_lat, orig_lon, dest_lat, dest_lon)

    # Nodos candidatos cerca del origen y destino
    entry_candidates = find_nearest_nodes(orig_lat, orig_lon, max_km=MAX_ENTRY_KM, top_n=TOP_N_CANDIDATES)
    exit_candidates  = find_nearest_nodes(dest_lat, dest_lon, max_km=MAX_ENTRY_KM, top_n=TOP_N_CANDIDATES)

    if not entry_candidates or not exit_candidates:
        return []

    best_total  = math.inf
    best_path   = []
    best_entry_dist = 0.0
    best_exit_dist  = 0.0

    for entry_id, dist_to_entry in entry_candidates:
        for exit_id, dist_to_exit in exit_candidates:
            if entry_id == exit_id:
                continue

            path_nodes, airway_km = _dijkstra(entry_id, exit_id, cruise_alt_ft)
            if not path_nodes:
                continue

            total_km = dist_to_entry + airway_km + dist_to_exit

            # Criterio: total <= direct * (1 + max_detour_pct)
            if total_km <= direct_km * (1 + max_detour_pct) and total_km < best_total:
                best_total       = total_km
                best_path        = path_nodes
                best_entry_dist  = dist_to_entry
                best_exit_dist   = dist_to_exit

    if not best_path:
        return []

    # Si el camino entero es de aristas XFR (transferencia entre nodos co-ubicados
    # en el mismo aeropuerto), no es una ruta de aerovia real.
    real_edges = [s for s in best_path if s.get("ruta") and s["ruta"] != "XFR"]
    if not real_edges:
        return []

    # Construir lista de AirwayWaypoint
    result: list[AirwayWaypoint] = []
    entry_node_id = best_path[0]["node"]
    exit_node_id  = best_path[-1]["node"]

    # FIR del punto de entrada
    entry_pos = AIRWAY_NODES[entry_node_id]
    fir_name  = get_fir(entry_pos["lat"], entry_pos["lon"])

    # Obtener la ruta real (no XFR) del primer segmento real del camino
    first_real_ruta = next((s["ruta"] for s in best_path if s["ruta"] and s["ruta"] != "XFR"), "?")
    first_real_mea  = next((s["mea"]  for s in best_path if s["mea"]  and s["ruta"] != "XFR"), 0)

    for i, step in enumerate(best_path):
        nid   = step["node"]
        pos   = AIRWAY_NODES[nid]
        ruta  = step["ruta"] if (step["ruta"] and step["ruta"] != "XFR") else first_real_ruta
        mea   = step["mea"]  if (step["mea"]  and step["ruta"] != "XFR") else first_real_mea
        is_en = (nid == entry_node_id)
        is_ex = (nid == exit_node_id)

        result.append(AirwayWaypoint(
            node_id    = nid,
            lat        = pos["lat"],
            lon        = pos["lon"],
            airway_name= ruta,
            mea_ft     = mea,
            fir_contact= fir_name if is_en else None,
            is_entry   = is_en,
            is_exit    = is_ex,
        ))

    return result


# ── Ruta completa: aerovias end-to-end distribuidas por tramo ───────────────────

# Desvio maximo permitido para el camino de aerovia de TODA la ruta.
# Mas alto que el de tramo porque las aerovias zigzaguean y, sobre una ruta
# larga, el camino global sigue siendo eficiente aunque cada tramo aislado
# parezca un desvio grande.
ROUTE_DETOUR_PCT = 0.45


def _dist_point_to_segment_km(plat, plon, alat, alon, blat, blon):
    """Distancia aprox (km) del punto P al segmento AB (proyeccion plana local)."""
    cos_lat = math.cos(math.radians((alat + blat) / 2))
    px = (plon - alon) * 111.0 * cos_lat
    py = (plat - alat) * 111.0
    bx = (blon - alon) * 111.0 * cos_lat
    by = (blat - alat) * 111.0
    seg_sq = bx * bx + by * by
    if seg_sq < 1e-9:
        return math.sqrt(px * px + py * py)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg_sq))
    dx = px - t * bx
    dy = py - t * by
    return math.sqrt(dx * dx + dy * dy)


def find_airways_for_route_legs(
    leg_airports: list,
    cruise_alt_ft: int,
) -> dict:
    """
    Calcula UN camino de aerovia continuo de origen a destino para toda la ruta
    y reparte sus waypoints entre los tramos (pares de aerodromos consecutivos)
    segun a que tramo pertenece geograficamente cada waypoint.

    Esto evita la fragmentacion del enfoque por-tramo: una red de aerovias
    continua se rechazaba tramo a tramo porque cada tramo aislado superaba el
    limite de desvio, aunque el camino global fuera razonable.

    leg_airports: lista de (code, lat, lon) de los aerodromos de la ruta, en orden.
    Retorna: dict {(code_i, code_j): [AirwayWaypoint, ...]} listo para airway_map.
    Si no hay camino de aerovia end-to-end, retorna {} (el caller puede caer al
    metodo por-tramo).
    """
    if len(leg_airports) < 2:
        return {}

    orig_code, orig_lat, orig_lon = leg_airports[0]
    dest_code, dest_lat, dest_lon = leg_airports[-1]

    # Camino de aerovia end-to-end con tolerancia de ruta completa
    full_path = find_airways_for_leg(
        orig_lat, orig_lon, dest_lat, dest_lon,
        cruise_alt_ft, max_detour_pct=ROUTE_DETOUR_PCT,
    )
    if not full_path:
        return {}

    # Repartir cada waypoint al tramo (segmento entre aerodromos consecutivos)
    # de menor distancia perpendicular.
    legs = [
        (leg_airports[i][0], leg_airports[i + 1][0],
         leg_airports[i][1], leg_airports[i][2],
         leg_airports[i + 1][1], leg_airports[i + 1][2])
        for i in range(len(leg_airports) - 1)
    ]

    airway_map: dict = {}
    for wp in full_path:
        best_leg = None
        best_d = math.inf
        for (ca, cb, alat, alon, blat, blon) in legs:
            d = _dist_point_to_segment_km(wp.lat, wp.lon, alat, alon, blat, blon)
            if d < best_d:
                best_d = d
                best_leg = (ca, cb)
        if best_leg is not None:
            airway_map.setdefault(best_leg, []).append(wp)

    # Ordenar los waypoints dentro de cada tramo por progreso a lo largo del tramo
    # (proyeccion sobre el segmento del tramo), para que la polilinea sea coherente.
    for (ca, cb), wps in airway_map.items():
        a = next((l for l in legs if l[0] == ca and l[1] == cb), None)
        if not a:
            continue
        _, _, alat, alon, blat, blon = a
        cos_lat = math.cos(math.radians((alat + blat) / 2))
        bx = (blon - alon) * 111.0 * cos_lat
        by = (blat - alat) * 111.0
        seg_sq = bx * bx + by * by or 1e-9

        def _progress(w):
            px = (w.lon - alon) * 111.0 * cos_lat
            py = (w.lat - alat) * 111.0
            return (px * bx + py * by) / seg_sq

        wps.sort(key=_progress)

    return airway_map
