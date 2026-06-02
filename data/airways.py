"""
airways.py
==========
Carga aerovias_argentinas.json y construye un grafo bidireccional
de aerovias inferiores (sin prefijo U) para el airway_router.

Solo se incluyen aerovias cuyos segmentos no tienen prefijo U:
  W, T, G, A, B, L, M, VW, VG, VA, R

Cada nodo del grafo es un fix de ruta identificado por su codigo ICAO (5 letras)
o identificador de radioayuda (2-3 letras como CBA, ROS, TUC).

Colisiones de ID resueltas:
  PUNTO_INTERMEDIO aparece en tres rutas a distinto punto geografico.
  Se renombran los duplicados con sufijo de ruta para evitar atajos fantasma en Dijkstra.

Exporta:
  AIRWAY_NODES  : dict  id -> {lat, lon}
  AIRWAY_GRAPH  : dict  id -> list de {to, ruta, mea, dist}
  LOWER_PREFIXES: set de prefijos de aerovias inferiores
"""

import json
import math
import re
from pathlib import Path
from collections import defaultdict

# ── Constantes ────────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).parent

LOWER_PREFIXES = re.compile(r'^(W|T|G|A|B|L|M|VW|VG|VA|R)\d', re.IGNORECASE)

# Tolerancia (km) para detectar colision de ID (mismo nombre, distinta posicion)
_COLLISION_TOLERANCE_KM = 10.0

# Tolerancia (km) para detectar nodos co-ubicados (distinto nombre, misma posicion)
# — genera aristas de transferencia con MEA=0 para permitir cambio de aerovia
_COLOC_TOLERANCE_KM = 1.5


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _load() -> tuple:
    path = _DATA_DIR / "aerovias_argentinas.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    segments = data["airways"]

    # ── Paso 1: detectar colisiones de ID y asignar nombres canonicos ──────────
    # Si dos apariciones del mismo ID estan a mas de _COLLISION_TOLERANCE_KM km
    # entre si, el segundo recibe el sufijo "_<ruta>" para evitar mezcla en el grafo.

    # Primera pasada: registrar primera posicion conocida por ID
    canonical_pos: dict[str, tuple[float, float]] = {}   # id -> (lat, lon)
    id_remap: dict[tuple, str] = {}                       # (orig_id, ruta) -> canonical_id

    for seg in segments:
        ruta = seg["ruta"]
        if not LOWER_PREFIXES.match(ruta):
            continue
        for key in ["origen", "destino"]:
            orig_id = seg[key]["id"]
            lat     = seg[key]["lat"]
            lon     = seg[key]["lon"]
            key_tuple = (orig_id, ruta, lat, lon)

            if orig_id not in canonical_pos:
                canonical_pos[orig_id] = (lat, lon)
                id_remap[key_tuple] = orig_id
            else:
                clat, clon = canonical_pos[orig_id]
                if _haversine(lat, lon, clat, clon) > _COLLISION_TOLERANCE_KM:
                    # Colision: renombrar con sufijo de ruta
                    new_id = f"{orig_id}_{ruta.replace('-', '_')}"
                    if new_id not in canonical_pos:
                        canonical_pos[new_id] = (lat, lon)
                    id_remap[key_tuple] = new_id
                else:
                    id_remap[key_tuple] = orig_id

    def _resolve_id(seg, key):
        orig_id = seg[key]["id"]
        lat     = seg[key]["lat"]
        lon     = seg[key]["lon"]
        ruta    = seg["ruta"]
        return id_remap.get((orig_id, ruta, lat, lon), orig_id)

    # ── Paso 2: construir nodos y grafo, deduplicando aristas ─────────────────
    nodes: dict[str, dict] = {}
    graph: dict[str, list] = defaultdict(list)
    seen_edges: set[tuple] = set()   # (oid, did, ruta, mea) para evitar duplicados

    for seg in segments:
        ruta = seg["ruta"]
        if not LOWER_PREFIXES.match(ruta):
            continue

        oid  = _resolve_id(seg, "origen")
        did  = _resolve_id(seg, "destino")
        olat = seg["origen"]["lat"]
        olon = seg["origen"]["lon"]
        dlat = seg["destino"]["lat"]
        dlon = seg["destino"]["lon"]
        mea  = seg["mea_ft"]

        nodes[oid] = {"lat": olat, "lon": olon}
        nodes[did] = {"lat": dlat, "lon": dlon}

        dist = _haversine(olat, olon, dlat, dlon)

        # Desduplicar: ignorar aristas identicas ya agregadas
        edge_key_fwd = (oid, did, ruta, mea)
        edge_key_rev = (did, oid, ruta, mea)

        if edge_key_fwd not in seen_edges:
            seen_edges.add(edge_key_fwd)
            graph[oid].append({"to": did, "ruta": ruta, "mea": mea, "dist": dist})

        if edge_key_rev not in seen_edges:
            seen_edges.add(edge_key_rev)
            graph[did].append({"to": oid, "ruta": ruta, "mea": mea, "dist": dist})

    # ── Paso 3: aristas de transferencia entre nodos co-ubicados ─────────────
    # Nodos en el mismo punto fisico pero con distinto ID (ej. CBA y SACO en Cordoba)
    # reciben una arista de transferencia con mea=0 y dist=0 para permitir
    # transicion entre aerovias en el mismo aeropuerto.
    node_list = list(nodes.items())
    transfer_added: set[tuple] = set()

    for i, (a_id, a_pos) in enumerate(node_list):
        for j, (b_id, b_pos) in enumerate(node_list[i + 1:], i + 1):
            # Optimizacion: si la diferencia de latitud es grande, saltar
            if abs(a_pos["lat"] - b_pos["lat"]) > 0.1:
                continue
            d = _haversine(a_pos["lat"], a_pos["lon"], b_pos["lat"], b_pos["lon"])
            if d <= _COLOC_TOLERANCE_KM:
                if (a_id, b_id) not in transfer_added:
                    transfer_added.add((a_id, b_id))
                    transfer_added.add((b_id, a_id))
                    graph[a_id].append({"to": b_id, "ruta": "XFR", "mea": 0, "dist": d})
                    graph[b_id].append({"to": a_id, "ruta": "XFR", "mea": 0, "dist": d})

    return dict(nodes), dict(graph)


AIRWAY_NODES, AIRWAY_GRAPH = _load()


def find_nearest_nodes(lat: float, lon: float, max_km: float = 100.0, top_n: int = 3) -> list[tuple[str, float]]:
    """
    Devuelve los `top_n` nodos de aerovia mas cercanos a (lat, lon)
    dentro de `max_km` km, ordenados por distancia ascendente.
    """
    candidates = []
    for node_id, pos in AIRWAY_NODES.items():
        d = _haversine(lat, lon, pos["lat"], pos["lon"])
        if d <= max_km:
            candidates.append((node_id, d))
    candidates.sort(key=lambda x: x[1])
    return candidates[:top_n]
