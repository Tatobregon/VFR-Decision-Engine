"""
astar.py
========
Algoritmo A* para encontrar la ruta optima entre dos aerodromos.

Soporta 3 modos (determinan los pesos del grafo):
  shortest  : minimiza distancia total en km
  fastest   : minimiza tiempo de vuelo (considera viento)
  safest    : minimiza distancia ponderada por riesgo meteorologico

La heuristica es siempre la distancia en linea recta al destino
(admisible en todos los modos -> garantiza optimalidad global).
"""

import heapq
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    from data.airports import AIRPORTS
    from route.graph import RouteGraph, build_graph, get_edge, neighbors
    from route.performance import haversine_km, CRUISE_KT
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS
    from route.graph import RouteGraph, build_graph, get_edge, neighbors
    from route.performance import haversine_km, CRUISE_KT


# ────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class AStarResult:
    """Resultado de una busqueda A*."""
    found        : bool
    path         : List[str]          # [origen, ..., destino] en codigos ICAO
    total_weight : float              # suma de pesos segun el modo
    total_dist_km: float              # distancia real total en km
    mode         : str
    nodes_explored: int               # nodos expandidos (para analisis)


# ────────────────────────────────────────────────────────────────────────────
# Heuristica
# ────────────────────────────────────────────────────────────────────────────

def _heuristic(node: str, goal: str, graph: RouteGraph) -> float:
    """
    Heuristica admisible para A*.

    En todos los modos usa la distancia en linea recta al destino, normalizada al
    mismo espacio que los pesos del grafo:

      shortest : distancia_km               (h <= costo real: el peso ES la distancia)
      safest   : distancia_km               (h <= distancia * (1+r), con r >= 0)
      fastest  : distancia_km / max_gs_kt   (h <= tiempo real)

    En "fastest" el peso de arista es distancia / velocidad_de_tierra, de modo que
    la heuristica solo es admisible si divide por una COTA SUPERIOR de la velocidad
    de tierra alcanzable. Esa cota la calcula el grafo como crucero + viento, y
    depende por lo tanto de la aeronave seleccionada.

    Este punto tuvo un defecto: la heuristica dividia por la constante de modulo
    CRUISE_KT (97 kt, el Pipistrel Alpha Trainer) en lugar de por la velocidad de
    la aeronave en uso. Para los perfiles mas rapidos —PA-28 108 kt, C172 110 kt,
    DA40 130 kt— eso SOBRESTIMABA el costo restante, con lo que la heuristica
    dejaba de ser admisible y A* podia devolver una ruta subóptima. Ademas ataba
    la capa de ruta a una aeronave concreta, en contra de la regla de alcance del
    proyecto. Tampoco acotaba el viento de cola, que puede llevar la velocidad de
    tierra por encima del crucero incluso para el Alpha.
    """
    n_info = graph.nodes.get(node)
    g_info = graph.nodes.get(goal)
    if n_info is None or g_info is None:
        return 0.0

    dist = haversine_km(n_info.lat, n_info.lon, g_info.lat, g_info.lon)

    if graph.mode == "fastest":
        max_gs = getattr(graph, "max_gs_kt", None) or CRUISE_KT
        return dist / max_gs      # cota inferior del tiempo restante
    else:
        return dist               # shortest y safest: h = distancia directa


# ────────────────────────────────────────────────────────────────────────────
# A*
# ────────────────────────────────────────────────────────────────────────────

def astar(
    graph  : RouteGraph,
    origin : str,
    dest   : str,
) -> AStarResult:
    """
    Busqueda A* sobre el grafo de aerodromos.

    Parametros
    ----------
    graph  : RouteGraph construido con build_graph()
    origin : codigo ICAO del aerodromo de origen
    dest   : codigo ICAO del aerodromo de destino

    Retorna
    -------
    AStarResult con la ruta optima o found=False si no existe camino.
    """
    if origin not in graph.nodes:
        return AStarResult(found=False, path=[], total_weight=0.0,
                           total_dist_km=0.0, mode=graph.mode, nodes_explored=0)
    if dest not in graph.nodes:
        return AStarResult(found=False, path=[], total_weight=0.0,
                           total_dist_km=0.0, mode=graph.mode, nodes_explored=0)
    if origin == dest:
        return AStarResult(found=True, path=[origin], total_weight=0.0,
                           total_dist_km=0.0, mode=graph.mode, nodes_explored=0)

    # open_set: (f_score, g_score, node)
    open_set: list = []
    h0 = _heuristic(origin, dest, graph)
    heapq.heappush(open_set, (h0, 0.0, origin))

    came_from: Dict[str, Optional[str]] = {origin: None}
    g_score: Dict[str, float] = {origin: 0.0}
    nodes_explored = 0

    while open_set:
        f, g, current = heapq.heappop(open_set)
        nodes_explored += 1

        if current == dest:
            # Reconstruir camino
            path = []
            node = dest
            while node is not None:
                path.append(node)
                node = came_from.get(node)
            path.reverse()

            # Calcular distancia real total
            total_dist = 0.0
            for i in range(len(path) - 1):
                e = get_edge(graph, path[i], path[i + 1])
                if e:
                    total_dist += e.distance_km

            return AStarResult(
                found         = True,
                path          = path,
                total_weight  = g_score[dest],
                total_dist_km = round(total_dist, 1),
                mode          = graph.mode,
                nodes_explored= nodes_explored,
            )

        # Nodo ya fue procesado con un costo menor
        if g > g_score.get(current, float("inf")):
            continue

        for edge in neighbors(graph, current):
            tentative_g = g + edge.weight
            if tentative_g < g_score.get(edge.dest, float("inf")):
                g_score[edge.dest]   = tentative_g
                came_from[edge.dest] = current
                h = _heuristic(edge.dest, dest, graph)
                heapq.heappush(open_set, (tentative_g + h, tentative_g, edge.dest))

    return AStarResult(found=False, path=[], total_weight=0.0,
                       total_dist_km=0.0, mode=graph.mode, nodes_explored=nodes_explored)


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: route/astar.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ── Modo shortest: SACC → SAOM (La Cumbre → Marcos Juarez) ──
    g = build_graph("shortest")
    r1 = astar(g, "SACC", "SAOM")
    check("shortest SACC-SAOM: found", r1.found)
    check("shortest SACC-SAOM: empieza en SACC", r1.found and r1.path[0] == "SACC")
    check("shortest SACC-SAOM: termina en SAOM", r1.found and r1.path[-1] == "SAOM")
    check("shortest SACC-SAOM: distancia positiva", r1.found and r1.total_dist_km > 0)
    print(f"\n  shortest SACC-SAOM: {' -> '.join(r1.path)}  "
          f"{r1.total_dist_km:.1f}km  (nodos explorados: {r1.nodes_explored})")

    # Ruta directa debe ser optima en modo shortest (grafo completo)
    # En un grafo completo, A* debe encontrar ruta directa si es la mas corta
    direct_edge = get_edge(g, "SACC", "SAOM")
    check("shortest: ruta directa es la optima en grafo completo",
          r1.found and r1.total_dist_km <= direct_edge.distance_km + 0.1)

    # ── Modo shortest: origen == destino ──
    r_same = astar(g, "SACC", "SACC")
    check("origen == destino: found", r_same.found)
    check("origen == destino: path de un elemento", r_same.found and r_same.path == ["SACC"])
    check("origen == destino: peso 0", r_same.found and r_same.total_weight == 0.0)

    # ── Modo shortest: nodo inexistente ──
    r_bad = astar(g, "XXXX", "SAOM")
    check("nodo inexistente: not found", not r_bad.found)

    # ── Modo fastest ──
    g_fast = build_graph("fastest", wind_dir=270, wind_spd_kt=20.0)
    r2 = astar(g_fast, "SACC", "SACB")
    check("fastest SACC-SACB: found", r2.found)
    check("fastest SACC-SACB: path valido", r2.found and len(r2.path) >= 2)
    print(f"  fastest SACC-SACB: {' -> '.join(r2.path)}  "
          f"{r2.total_dist_km:.1f}km")

    # ── Modo safest ──
    # Marcar SAOM como muy riesgoso para forzar ruta alternativa
    r_map = {code: 0.0 for code in AIRPORTS}
    r_map["SAOM"] = 0.99   # casi NO GO
    g_safe = build_graph("safest", r_map=r_map)
    r3 = astar(g_safe, "SACC", "SACB")
    check("safest SACC-SACB: found", r3.found)
    print(f"  safest (SAOM riesgo 0.99): {' -> '.join(r3.path)}  "
          f"{r3.total_dist_km:.1f}km")

    # ── Todos los pares origen-destino son alcanzables (grafo completo) ──
    codes = list(AIRPORTS.keys())
    all_reachable = True
    for src in codes:
        for dst in codes:
            if src != dst:
                res = astar(g, src, dst)
                if not res.found:
                    all_reachable = False
                    print(f"    NO ALCANZABLE: {src} -> {dst}")
    check(f"Todos los {len(codes)*(len(codes)-1)} pares son alcanzables", all_reachable)

    # ── Simetria de la distancia ──
    r_ab = astar(g, "SACC", "SABV")
    r_ba = astar(g, "SABV", "SACC")
    check("Simetria distancia SACC-SABV vs SABV-SACC",
          r_ab.found and r_ba.found and
          abs(r_ab.total_dist_km - r_ba.total_dist_km) < 0.5)

    print("\n  Rutas mas cortas desde SACC:")
    for dst in codes:
        if dst != "SACC":
            res = astar(g, "SACC", dst)
            name = AIRPORTS[dst].name
            print(f"    SACC-{dst} ({name:<18}): {' -> '.join(res.path):<25}  "
                  f"{res.total_dist_km:.1f}km")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
