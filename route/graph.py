"""
graph.py
========
Grafo de conectividad entre aerodromos para el optimizador de rutas VFR.

Cada nodo es un aerodromo (codigo ICAO).
Cada arista es un par (origen, destino) con pesos dinamicos segun el modo.

Modos de peso:
  shortest  : peso = distancia_km
  fastest   : peso = distancia_km / groundspeed_kt  (tiempo en horas)
  safest    : peso = distancia_km * (1 + R_total_destino)  (penaliza riesgo meteo)

El grafo es completo y no dirigido — todos los aerodromos pueden conectarse
directamente entre si. No se imponen restricciones de alcance en el grafo
(la verificacion de combustible es responsabilidad del optimizador).
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    from data.airports import AIRPORTS, AirportInfo
    from route.performance import haversine_km, bearing_deg, effective_groundspeed_kt, CRUISE_KT
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS, AirportInfo
    from route.performance import haversine_km, bearing_deg, effective_groundspeed_kt, CRUISE_KT


# ────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class EdgeInfo:
    """Informacion de una arista del grafo (tramo entre dos aerodromos)."""
    origin      : str    # codigo ICAO origen
    dest        : str    # codigo ICAO destino
    distance_km : float
    bearing_deg : float  # rumbo verdadero origen -> destino
    weight      : float  # peso segun el modo activo


@dataclass
class RouteGraph:
    """
    Grafo de aerodromos con pesos calculados para un modo especifico.

    nodes   : {codigo: AirportInfo}
    edges   : {codigo_origen: [EdgeInfo, ...]}
    r_map   : {codigo: r_total} — riesgo meteorologico por aerodromo (modo safest)
    mode    : "shortest" | "fastest" | "safest"
    max_gs_kt : cota SUPERIOR de la velocidad de tierra alcanzable en este grafo
                (crucero de la aeronave + viento). La necesita la heuristica de
                A* en modo "fastest" para ser admisible: el tiempo restante nunca
                puede ser menor que distancia / max_gs_kt. Ver route/astar.py.
    """
    nodes  : Dict[str, AirportInfo]
    edges  : Dict[str, List[EdgeInfo]]
    r_map  : Dict[str, float]
    mode   : str
    max_gs_kt : float = CRUISE_KT


# ────────────────────────────────────────────────────────────────────────────
# Constructor del grafo
# ────────────────────────────────────────────────────────────────────────────

def build_graph(
    mode       : str,
    r_map      : Optional[Dict[str, float]] = None,
    wind_dir   : Optional[int]   = None,
    wind_spd_kt: Optional[float] = None,
    airports   : Optional[Dict[str, AirportInfo]] = None,
    aircraft   = None,   # Optional[AircraftProfile] — evita importacion circular
    max_leg_km : float = 500.0,  # limite de distancia por tramo (0 = sin limite)
) -> RouteGraph:
    """
    Construye el grafo de aerodromos con pesos segun el modo.

    Parametros
    ----------
    mode        : "shortest" | "fastest" | "safest"
    r_map       : {codigo: r_total [0,1]} — requerido para modo "safest".
                  Si es None, usa 0.0 para todos (sin penalizacion meteo).
    wind_dir    : direccion del viento FROM, grados (para modo "fastest")
    wind_spd_kt : velocidad del viento, kt (para modo "fastest")
    airports    : diccionario de aerodromos a usar (por defecto: AIRPORTS global)
    aircraft    : AircraftProfile para usar cruise_kt del perfil (modo "fastest")
    max_leg_km  : distancia maxima por tramo en km. Aristas mas largas se omiten.
                  0 construye el grafo completo (N*(N-1) aristas). Default: 500 km.

    Retorna
    -------
    RouteGraph con nodos y aristas calculados.
    """
    if mode not in ("shortest", "fastest", "safest"):
        raise ValueError(f"Modo invalido: {mode!r}. Usar 'shortest', 'fastest' o 'safest'.")

    nodes      = airports if airports is not None else AIRPORTS
    r          = r_map if r_map is not None else {}
    cruise_kt  = aircraft.cruise_kt if aircraft is not None else CRUISE_KT

    edges: Dict[str, List[EdgeInfo]] = {code: [] for code in nodes}

    # Pre-calcular limite de latitud (constante para todo el grafo)
    apply_limit = max_leg_km > 0
    max_dlat = (max_leg_km / 111.0) if apply_limit else float("inf")

    for orig_code, orig_info in nodes.items():
        # Limite de longitud depende de la latitud del origen
        if apply_limit:
            cos_lat = max(abs(math.cos(math.radians(orig_info.lat))), 0.001)
            max_dlon = max_leg_km / (111.0 * cos_lat)
        else:
            max_dlon = float("inf")

        for dest_code, dest_info in nodes.items():
            if orig_code == dest_code:
                continue

            # Rechazo rapido por bounding box antes de calcular haversine
            if apply_limit:
                if abs(dest_info.lat - orig_info.lat) > max_dlat:
                    continue
                if abs(dest_info.lon - orig_info.lon) > max_dlon:
                    continue

            dist = haversine_km(orig_info.lat, orig_info.lon,
                                dest_info.lat, dest_info.lon)

            if apply_limit and dist > max_leg_km:
                continue

            brng = bearing_deg(orig_info.lat, orig_info.lon,
                               dest_info.lat, dest_info.lon)

            if mode == "shortest":
                weight = dist

            elif mode == "fastest":
                gs = effective_groundspeed_kt(cruise_kt, wind_dir, wind_spd_kt, brng)
                weight = dist / gs   # tiempo en horas (proporcional a distancia/velocidad)

            else:  # safest
                r_dest = r.get(dest_code, 0.0)
                weight = dist * (1.0 + r_dest)

            edges[orig_code].append(EdgeInfo(
                origin      = orig_code,
                dest        = dest_code,
                distance_km = round(dist, 2),   # presentacion
                bearing_deg = round(brng, 1),   # presentacion
                # El peso NO se redondea: la admisibilidad de la heuristica de A*
                # es una desigualdad entre el peso y la distancia directa, y
                # redondear el peso hacia abajo la puede violar. El error es de
                # ~1e-7 km, irrelevante en magnitud pero suficiente para romper
                # la garantia formal de optimalidad.
                weight      = weight,
            ))

    # Cota superior de velocidad de tierra: crucero mas el viento (caso de cola
    # pura). La heuristica de A* divide por este valor para no sobrestimar nunca
    # el tiempo restante. Sin esto la heuristica deja de ser admisible y A* puede
    # devolver una ruta subóptima.
    max_gs = cruise_kt + (wind_spd_kt or 0.0)

    return RouteGraph(nodes=nodes, edges=edges, r_map=r, mode=mode, max_gs_kt=max_gs)


def get_edge(graph: RouteGraph, origin: str, dest: str) -> Optional[EdgeInfo]:
    """Retorna la arista (origin, dest) del grafo, o None si no existe."""
    for edge in graph.edges.get(origin, []):
        if edge.dest == dest:
            return edge
    return None


def neighbors(graph: RouteGraph, node: str) -> List[EdgeInfo]:
    """Retorna todas las aristas salientes desde node."""
    return graph.edges.get(node, [])


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: route/graph.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    N = len(AIRPORTS)
    edges_expected = N * (N - 1)   # grafo completo, dirigido

    # ── Modo shortest con max_leg_km=0 (grafo completo, para validar conteo) ──
    g_full = build_graph("shortest", max_leg_km=0)
    check(f"Modo shortest: {N} nodos  (got {len(g_full.nodes)})", len(g_full.nodes) == N)
    total_edges_full = sum(len(v) for v in g_full.edges.values())
    check(f"Grafo completo (max_leg_km=0): {edges_expected} aristas  (got {total_edges_full})",
          total_edges_full == edges_expected)

    # ── Modo shortest con max_leg_km default (500 km) ──
    g_short = build_graph("shortest")
    total_edges = sum(len(v) for v in g_short.edges.values())
    check("Modo guardado correctamente", g_short.mode == "shortest")
    check(f"max_leg_km=500 reduce aristas vs grafo completo", total_edges < total_edges_full)
    # Todo aerodromo CONTINENTAL tiene al menos un vecino a <= 500 km.
    # Excepcion real y esperada: la base antartica Marambio (SAWB, lat -64.2)
    # tiene su vecino mas cercano a 1230 km, cruzando el Pasaje de Drake. Ninguna
    # de las aeronaves del sistema opera ese tramo, asi que queda como nodo
    # aislado del grafo: es un dato geografico correcto, no un defecto.
    aislados = [code for code, v in g_short.edges.items() if len(v) == 0]
    antarticos = [c for c in aislados if g_short.nodes[c].lat < -60.0]
    check(f"max_leg_km=500: solo nodos antarticos quedan aislados  (aislados={aislados})",
          set(aislados) == set(antarticos))

    # Sin autolazo
    sacc_dests = [e.dest for e in neighbors(g_short, "SACC")]
    check("Sin autolazo SACC", "SACC" not in sacc_dests)
    check(f"SACC tiene vecinos (al menos 5)", len(sacc_dests) >= 5)

    # Distancia SACC-SAOM (Marcos Juarez, ~320 km — dentro de max_leg_km=500)
    e = get_edge(g_short, "SACC", "SAOM")
    check("Arista SACC-SAOM presente (dentro de 500 km)", e is not None)
    check(f"SACC-SAOM dist razonable  (got {e.distance_km:.1f}km)", 250.0 <= e.distance_km <= 380.0)

    # En modo shortest el peso = distancia
    check("Modo shortest: peso == distancia",
          e is not None and abs(e.weight - e.distance_km) < 0.01)

    # Simetria: SAOM-SACC misma distancia (no igual bearing)
    e_rev = get_edge(g_short, "SAOM", "SACC")
    check("Simetria distancia SAOM-SACC",
          e is not None and e_rev is not None and
          abs(e.distance_km - e_rev.distance_km) < 0.1)

    # ── Modo fastest ──
    g_fast = build_graph("fastest", wind_dir=270, wind_spd_kt=15.0)
    e_fast = get_edge(g_fast, "SACC", "SAOM")
    check("Modo fastest: arista SACC-SAOM presente", e_fast is not None)
    e_fast_noviento = get_edge(build_graph("fastest"), "SACC", "SAOM")
    check("Modo fastest: viento cola reduce tiempo",
          e_fast is not None and e_fast_noviento is not None and
          e_fast.weight <= e_fast_noviento.weight)

    # ── Modo safest ──
    r_test = {"SACC": 0.3, "SAOM": 0.8, "SAOC": 0.0}
    g_safe = build_graph("safest", r_map=r_test)
    e_safe_saom = get_edge(g_safe, "SACC", "SAOM")
    e_safe_saoe = get_edge(g_safe, "SACC", "SAOC")
    check("Modo safest: aristas SAOM y SAOC presentes",
          e_safe_saom is not None and e_safe_saoe is not None)
    d_saom = get_edge(g_short, "SACC", "SAOM").distance_km
    d_saoe = get_edge(g_short, "SACC", "SAOC").distance_km
    expected_saom = d_saom * 1.8   # r=0.8 → factor 1.8
    expected_saoe = d_saoe * 1.0   # r=0.0 → factor 1.0
    check(f"Modo safest: peso SAOM ~{expected_saom:.0f}  (got {e_safe_saom.weight:.1f})",
          e_safe_saom is not None and abs(e_safe_saom.weight - expected_saom) < 1.0)
    check(f"Modo safest: peso SAOC ~{expected_saoe:.0f}  (got {e_safe_saoe.weight:.1f})",
          e_safe_saoe is not None and abs(e_safe_saoe.weight - expected_saoe) < 1.0)

    # ── Modo invalido ──
    try:
        build_graph("turbo")
        check("ValueError para modo invalido", False)
    except ValueError:
        check("ValueError para modo invalido", True)

    # ── get_edge para arista inexistente ──
    check("get_edge nodo inexistente retorna None",
          get_edge(g_short, "XXXX", "SACC") is None)

    print(f"\n  Grafo ({N} aerodromos, {total_edges} aristas, modo shortest, max_leg_km=500):")
    for code in list(AIRPORTS.keys())[:3]:
        nbrs = neighbors(g_short, code)
        nbrs_sorted = sorted(nbrs, key=lambda e: e.distance_km)
        closest = nbrs_sorted[0]
        print(f"    {code}  vecino mas cercano: {closest.dest}  "
              f"{closest.distance_km:.1f}km  bearing={closest.bearing_deg:.0f}")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
