"""
vfr_corridors.py
================
Ruteo VFR por corredores visuales de las TMA Buenos Aires y Córdoba.

En VFR, transitar el área terminal de Buenos Aires o Córdoba exige volar por los
**corredores visuales publicados** (no se puede cruzar en línea recta el espacio
aéreo controlado). Este módulo:

  1. Carga los corredores (GeoJSON) y arma un GRAFO por región: los vértices que
     coinciden (<MERGE_KM) se fusionan, de modo que los corredores que comparten
     extremos quedan conectados en una red navegable.
  2. Define el "área terminal" de cada región como el casco convexo de su red de
     corredores (proxy del espacio controlado; ver nota de limitación abajo).
  3. Dado un tramo directo a->b, si atraviesa esa área, calcula el camino más
     corto por la red de corredores (Dijkstra) entre el punto de entrada y el de
     salida, y devuelve los waypoints del corredor para insertarlos en la ruta.

Limitación documentada: el disparador usa el casco convexo de la red de
corredores como proxy del área terminal controlada. Es self-contained y razonable
(los corredores viven dentro/alrededor del área terminal), pero en producción
puede afinarse con los polígonos TMA/CTR oficiales (ya disponibles en
data/airspace.py: TMA CORDOBA, CTR Ezeiza/Aeroparque/etc.).
"""

import os
import json
import heapq
import logging
from math import radians, cos, hypot
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from route.performance import haversine_km
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from route.performance import haversine_km

MERGE_KM       = 1.5    # vértices a < esta distancia se consideran el mismo nodo
HULL_PAD_KM    = 4.0    # margen alrededor del casco convexo (área terminal)
MIN_TRANSIT_KM = 4.0    # transito mínimo dentro del área para justificar corredor
_SAMPLE_KM     = 1.5    # paso de muestreo del segmento para entrada/salida

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_FILES = {
    "BA":  "corredores_vfr_TMA_BA.geojson",
    "CBA": "corredores_vfr_TMA_CBA.geojson",
}
_REGION_NAME = {"BA": "TMA Buenos Aires", "CBA": "TMA Córdoba"}


# ──────────────────────────────────────────────────────────────────────────────
# Estructura del grafo de una región
# ──────────────────────────────────────────────────────────────────────────────

class _CorridorGraph:
    """Grafo navegable de los corredores VFR de una región."""

    def __init__(self, region: str):
        self.region = region
        self.nodes: List[Tuple[float, float]] = []          # (lat, lon) por nodo
        self.adj: Dict[int, List[Tuple[int, float]]] = {}   # nodo -> [(vecino, km)]
        self.edge_meta: Dict[Tuple[int, int], dict] = {}    # (a,b) -> {corridor_id, name, ...}
        # Nombre publicado de cada PUNTO, derivado del nombre del corredor (que
        # lista sus puntos en orden). El id del corredor se repite en sus dos
        # extremos, asi que sin esto no hay como nombrar donde se vira.
        self.point_names: Dict[int, str] = {}
        # Cada cluster (componente conexa) tiene su propio casco. La red de
        # corredores de una región puede estar fragmentada en varios clusters
        # (ej. BA: norte y sur, separados por los CTR centrales): rutear por
        # cluster evita pretender un transito que no existe en la realidad.
        self.clusters: List[dict] = []                      # [{nodes:set, hull:[(lat,lon)]}]

    def _node_for(self, lat: float, lon: float) -> int:
        """Devuelve el índice del nodo existente cercano, o crea uno nuevo."""
        for i, (nlat, nlon) in enumerate(self.nodes):
            if haversine_km(lat, lon, nlat, nlon) <= MERGE_KM:
                return i
        self.nodes.append((lat, lon))
        self.adj[len(self.nodes) - 1] = []
        return len(self.nodes) - 1

    def add_edge(self, a: int, b: int, meta: dict):
        if a == b:
            return
        d = haversine_km(*self.nodes[a], *self.nodes[b])
        self.adj[a].append((b, d))
        self.adj[b].append((a, d))
        self.edge_meta[(a, b)] = meta
        self.edge_meta[(b, a)] = meta

    def nearest_node_in(self, lat: float, lon: float, node_set) -> Optional[int]:
        best, best_d = None, 1e18
        for i in node_set:
            d = haversine_km(lat, lon, *self.nodes[i])
            if d < best_d:
                best, best_d = i, d
        return best

    def compute_clusters(self):
        """Detecta componentes conexas y calcula el casco convexo de cada una."""
        seen = set()
        self.clusters = []
        for s in range(len(self.nodes)):
            if s in seen:
                continue
            stack, comp = [s], []
            while stack:
                u = stack.pop()
                if u in seen:
                    continue
                seen.add(u)
                comp.append(u)
                for v, _ in self.adj.get(u, []):
                    stack.append(v)
            if len(comp) < 2:
                continue
            hull = _pad_hull(_convex_hull([self.nodes[i] for i in comp]), HULL_PAD_KM)
            if len(hull) >= 3:
                self.clusters.append({"nodes": set(comp), "hull": hull})

    def shortest_path(self, src: int, dst: int) -> List[int]:
        """Dijkstra entre dos nodos. Devuelve lista de nodos o [] si no conecta."""
        dist = {src: 0.0}
        prev: Dict[int, int] = {}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                break
            if d > dist.get(u, 1e18):
                continue
            for v, w in self.adj.get(u, []):
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if dst not in dist and src != dst:
            return []
        path = [dst]
        while path[-1] != src:
            if path[-1] not in prev:
                return []
            path.append(prev[path[-1]])
        path.reverse()
        return path


# ──────────────────────────────────────────────────────────────────────────────
# Geometría: casco convexo y punto-en-polígono (plano local, OK a esta escala)
# ──────────────────────────────────────────────────────────────────────────────

def _convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Casco convexo (monotone chain) sobre (lat, lon). Devuelve el anillo."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[1]-o[1])*(b[0]-o[0]) - (a[0]-o[0])*(b[1]-o[1])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _centroid(ring: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(ring) or 1
    return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n)


def _pad_hull(ring: List[Tuple[float, float]], pad_km: float) -> List[Tuple[float, float]]:
    """Expande el casco empujando cada vértice ~pad_km desde el centroide."""
    if len(ring) < 3:
        return ring
    clat, clon = _centroid(ring)
    out = []
    for lat, lon in ring:
        dlat = lat - clat
        dlon = (lon - clon) * cos(radians(clat))
        d = hypot(dlat, dlon) or 1e-9
        scale = (d + pad_km / 111.0) / d
        out.append((clat + dlat * scale, clon + (lon - clon) * scale))
    return out


def _point_in_ring(lat: float, lon: float, ring: List[Tuple[float, float]]) -> bool:
    """Ray casting. ring = lista de (lat, lon)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if ((xi > lon) != (xj > lon)) and \
           (lat < (yj - yi) * (lon - xi) / ((xj - xi) or 1e-12) + yi):
            inside = not inside
        j = i
    return inside


# ──────────────────────────────────────────────────────────────────────────────
# Carga y construcción de los grafos (cacheada)
# ──────────────────────────────────────────────────────────────────────────────

_GRAPHS: Optional[Dict[str, _CorridorGraph]] = None


def _build_graphs() -> Dict[str, _CorridorGraph]:
    global _GRAPHS
    if _GRAPHS is not None:
        return _GRAPHS
    graphs: Dict[str, _CorridorGraph] = {}
    for region, fname in _FILES.items():
        g = _CorridorGraph(region)
        try:
            with open(os.path.join(_DATA_DIR, fname), encoding="utf-8") as f:
                fc = json.load(f)
        except Exception as e:
            logger.warning(f"No se pudieron cargar corredores {region}: {e}")
            graphs[region] = g
            continue
        all_pts: List[Tuple[float, float]] = []
        for ft in fc.get("features", []):
            props = ft.get("properties", {}) or {}
            meta = {
                "corridor_id":    props.get("corridor_id", ""),
                "name":           props.get("name", ""),
                "upper_limit_ft": props.get("upper_limit_ft"),
                "limit_reference": props.get("limit_reference", ""),
                "region":         region,
            }
            coords = ft.get("geometry", {}).get("coordinates", [])

            # El `name` del corredor LISTA SUS PUNTOS EN ORDEN, uno por
            # coordenada: "ASCOCHINGA - AD. LA CUMBRE" son sus dos extremos, y
            # "RIO SEGUNDO - TOLEDO - AD CORONEL OLMEDO" sus tres. Verificado
            # sobre los 22 corredores publicados de las dos TMA: en todos
            # coincide la cantidad de partes con la de coordenadas.
            #
            # Importa porque sin esto el unico rotulo de un punto es el id del
            # CORREDOR, que se repite en sus dos extremos: la tabla de tramos
            # diria "VFR-COR-04 -> VFR-COR-04" y el piloto no sabria donde virar.
            partes = [x.strip() for x in str(props.get("name", "")).split(" - ")]
            if len(partes) != len(coords):
                partes = []          # no cumple la convencion: no se inventa

            prev = None
            for idx_c, c in enumerate(coords):
                lat, lon = c[1], c[0]
                all_pts.append((lat, lon))
                node = g._node_for(lat, lon)
                if partes:
                    g.point_names.setdefault(node, partes[idx_c])
                if prev is not None:
                    g.add_edge(prev, node, meta)
                prev = node
        g.compute_clusters()
        graphs[region] = g
    _GRAPHS = graphs
    return graphs


# ──────────────────────────────────────────────────────────────────────────────
# API pública
# ──────────────────────────────────────────────────────────────────────────────

def _segment_inside_extent(a: Tuple[float, float], b: Tuple[float, float],
                           ring: List[Tuple[float, float]]) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """
    Porción del segmento a->b que cae dentro del polígono `ring`.
    Devuelve (entrada, salida) o None si no lo atraviesa de forma significativa.
    """
    total = haversine_km(a[0], a[1], b[0], b[1])
    if total <= 0:
        return None
    n = max(2, int(total / _SAMPLE_KM))
    inside_pts = []
    for i in range(n + 1):
        t = i / n
        lat = a[0] + t * (b[0] - a[0])
        lon = a[1] + t * (b[1] - a[1])
        if _point_in_ring(lat, lon, ring):
            inside_pts.append((lat, lon))
    if len(inside_pts) < 2:
        return None
    entry, exit_ = inside_pts[0], inside_pts[-1]
    if haversine_km(entry[0], entry[1], exit_[0], exit_[1]) < MIN_TRANSIT_KM:
        return None
    return entry, exit_


def corridor_path_for_leg(a_lat: float, a_lon: float,
                          b_lat: float, b_lon: float) -> List[dict]:
    """
    Si el tramo directo a->b atraviesa el área terminal de BA o Córdoba, devuelve
    los waypoints del corredor VFR a usar para transitarla. Si no, devuelve [].

    Cada waypoint: {lat, lon, corridor_id, corridor_name, region, region_name,
                    upper_limit_ft, limit_reference}.
    """
    graphs = _build_graphs()
    a, b = (a_lat, a_lon), (b_lat, b_lon)
    for region, g in graphs.items():
        for cluster in g.clusters:
            seg = _segment_inside_extent(a, b, cluster["hull"])
            if seg is None:
                continue
            entry, exit_ = seg
            n_in = g.nearest_node_in(entry[0], entry[1], cluster["nodes"])
            n_out = g.nearest_node_in(exit_[0], exit_[1], cluster["nodes"])
            if n_in is None or n_out is None or n_in == n_out:
                continue
            path = g.shortest_path(n_in, n_out)
            if len(path) < 2:
                continue
            out: List[dict] = []
            for k, node in enumerate(path):
                lat, lon = g.nodes[node]
                # metadatos del corredor del tramo que llega a este nodo
                meta = g.edge_meta.get((path[k - 1], node), {}) if k > 0 else \
                       g.edge_meta.get((node, path[k + 1]), {}) if k + 1 < len(path) else {}
                out.append({
                    "lat": round(lat, 5), "lon": round(lon, 5),
                    "corridor_id":    meta.get("corridor_id", ""),
                    "corridor_name":  meta.get("name", ""),
                    # Nombre del PUNTO, no del corredor. Es lo que permite decir
                    # donde se vira; vacio si el corredor no declara sus puntos.
                    "point_name":     g.point_names.get(node, ""),
                    "region":         region,
                    "region_name":    _REGION_NAME.get(region, region),
                    "upper_limit_ft": meta.get("upper_limit_ft"),
                    "limit_reference": meta.get("limit_reference", ""),
                })
            return out
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Test standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  TEST: vfr_corridors.py — ruteo por corredores VFR")
    print("=" * 64)

    all_pass = True
    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    graphs = _build_graphs()
    for r, g in graphs.items():
        print(f"\n  Región {r}: {len(g.nodes)} nodos, "
              f"{sum(len(v) for v in g.adj.values())//2} aristas, {len(g.clusters)} cluster(s)")
        check(f"{r}: grafo no vacío", len(g.nodes) > 0)
        check(f"{r}: al menos 1 cluster", len(g.clusters) >= 1)

    # Cada cluster real: un cruce por su centroide debe rutear por corredor.
    for r, g in graphs.items():
        for ci, cl in enumerate(g.clusters):
            clat, clon = _centroid(cl["hull"])
            # cruce perpendicular corto que entra y sale del cluster
            path = corridor_path_for_leg(clat + 0.5, clon, clat - 0.5, clon)
            if len(path) < 2:  # probar el otro eje si el cluster es alargado
                path = corridor_path_for_leg(clat, clon - 0.5, clat, clon + 0.5)
            print(f"\n  -- {r} cluster {ci+1} ({len(cl['nodes'])} nodos): {len(path)} waypoints --")
            for w in path[:5]:
                print(f"      {w['corridor_id']:12s} ({w['lat']},{w['lon']}) lim {w['upper_limit_ft']} {w['limit_reference']}")
            check(f"{r} cluster {ci+1}: cruce genera corredor", len(path) >= 2)

    # Ruta lejana (Patagonia): NO debe generar corredores
    print("\n  -- Ruta lejana (no toca áreas terminales) --")
    far = corridor_path_for_leg(-50.0, -69.0, -51.0, -70.0)
    check("Patagonia: sin corredores", len(far) == 0)

    print("\n" + "=" * 64)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 64)
