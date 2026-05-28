"""
airways.py
==========
Carga aerovias_argentinas.json y construye un grafo bidireccional
de aerovias inferiores (sin prefijo U) para el airway_router.

Solo se incluyen aerovias cuyos segmentos no tienen prefijo U:
  W, T, G, A, B, L, M, VW, VG, VA, R

Cada nodo del grafo es un fix de ruta identificado por su codigo ICAO (5 letras)
o identificador de radioayuda (2-3 letras como CBA, ROS, TUC).

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

    nodes: dict[str, dict] = {}
    graph: dict[str, list] = defaultdict(list)

    for seg in segments:
        ruta = seg["ruta"]
        if not LOWER_PREFIXES.match(ruta):
            continue

        oid  = seg["origen"]["id"]
        did  = seg["destino"]["id"]
        olat = seg["origen"]["lat"]
        olon = seg["origen"]["lon"]
        dlat = seg["destino"]["lat"]
        dlon = seg["destino"]["lon"]
        mea  = seg["mea_ft"]

        nodes[oid] = {"lat": olat, "lon": olon}
        nodes[did] = {"lat": dlat, "lon": dlon}

        dist = _haversine(olat, olon, dlat, dlon)

        # Bidireccional — mismo MEA en ambos sentidos
        graph[oid].append({"to": did, "ruta": ruta, "mea": mea, "dist": dist})
        graph[did].append({"to": oid, "ruta": ruta, "mea": mea, "dist": dist})

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
