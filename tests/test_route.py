"""
test_route.py
=============
Capa de ruta: admisibilidad de la heuristica de A* y propiedades del grafo.

El test central es `test_la_heuristica_es_admisible_en_todos_los_perfiles`, que
verifica formalmente la afirmacion que el documento de tesis hace en el § 3.2.6:
que la heuristica nunca sobrestima el costo restante y que, por lo tanto, A*
devuelve el camino optimo. Esa afirmacion fue FALSA para tres de los cinco
perfiles de aeronave hasta que se corrigio la heuristica: dividia por una
constante de modulo (97 kt, el Alpha Trainer) en lugar de por la velocidad de la
aeronave en uso.
"""

import heapq

import pytest

from data.airports import AIRPORTS
from risk.aircraft_profiles import PROFILE_NAMES, get_profile
from route.astar import astar, _heuristic
from route.graph import build_graph


# Subconjunto chico y disperso: cubre distancias largas y varias regiones sin
# construir el grafo de los 561 aerodromos en cada test.
MUESTRA = ["SACO", "SAEZ", "SAAR", "SASA", "SAME", "SAZS", "SAZN", "SARE"]


def _airports_muestra():
    aps = {c: AIRPORTS[c] for c in MUESTRA if c in AIRPORTS}
    assert len(aps) >= 6, "el registro no tiene los aerodromos de la muestra"
    return aps


def _costo_real_hasta(graph, dest):
    """
    Costo optimo desde cada nodo hasta `dest`, por Dijkstra sobre el grafo
    invertido. Es el valor contra el que se compara la heuristica: h es
    admisible si y solo si h(n) <= costo_real(n) para todo n.
    """
    inverso = {n: [] for n in graph.nodes}
    for origen, aristas in graph.edges.items():
        for e in aristas:
            inverso.setdefault(e.dest, []).append((e.origin, e.weight))

    dist = {dest: 0.0}
    cola = [(0.0, dest)]
    while cola:
        d, u = heapq.heappop(cola)
        if d > dist.get(u, float("inf")):
            continue
        for v, w in inverso.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(cola, (nd, v))
    return dist


@pytest.mark.parametrize("avion", PROFILE_NAMES)
@pytest.mark.parametrize("viento_kt", [0.0, 25.0])
def test_la_heuristica_es_admisible_en_todos_los_perfiles(avion, viento_kt):
    """
    h(n) <= costo real de n al destino, para todo nodo alcanzable.

    Se prueba con los cinco perfiles porque el defecto original solo aparecia en
    los mas rapidos que el Alpha Trainer, y con viento porque la velocidad de
    tierra con componente de cola supera al crucero: ambos rompian la cota.
    """
    perfil = get_profile(avion)
    graph = build_graph(
        mode        = "fastest",
        wind_dir    = 270,
        wind_spd_kt = viento_kt,
        airports    = _airports_muestra(),
        aircraft    = perfil,
        max_leg_km  = 0.0,          # grafo completo: sin tramos omitidos
    )
    dest = "SAEZ"
    real = _costo_real_hasta(graph, dest)

    for nodo in graph.nodes:
        if nodo not in real:        # inalcanzable: la heuristica no aplica
            continue
        h = _heuristic(nodo, dest, graph)
        assert h <= real[nodo] + 1e-9, (
            f"{avion} con viento {viento_kt} kt: h({nodo})={h:.6f} supera el "
            f"costo real {real[nodo]:.6f} — la heuristica no es admisible y A* "
            f"deja de garantizar optimalidad"
        )


@pytest.mark.parametrize("avion", PROFILE_NAMES)
def test_astar_devuelve_el_optimo_en_modo_fastest(avion):
    """A* debe coincidir con Dijkstra, que es optimo por construccion."""
    perfil = get_profile(avion)
    graph = build_graph(mode="fastest", wind_dir=270, wind_spd_kt=20.0,
                        airports=_airports_muestra(), aircraft=perfil,
                        max_leg_km=0.0)
    res  = astar(graph, "SASA", "SAEZ")
    real = _costo_real_hasta(graph, "SAEZ")
    assert res.found
    assert res.total_weight == pytest.approx(real["SASA"], abs=1e-9)


def test_la_cota_de_velocidad_depende_de_la_aeronave():
    """
    Guarda contra la regresion concreta que motivo el arreglo: la cota estaba
    fijada a la velocidad del Alpha Trainer para todas las aeronaves, lo que
    ademas ataba la capa de ruta a un perfil particular.
    """
    aps = _airports_muestra()
    alpha = build_graph(mode="fastest", airports=aps,
                        aircraft=get_profile("Pipistrel Alpha Trainer"),
                        max_leg_km=0.0)
    da40  = build_graph(mode="fastest", airports=aps,
                        aircraft=get_profile("Diamond DA40"),
                        max_leg_km=0.0)
    assert da40.max_gs_kt > alpha.max_gs_kt


def test_la_cota_incluye_el_viento():
    """Con viento la velocidad de tierra puede superar al crucero."""
    aps = _airports_muestra()
    perfil = get_profile("Pipistrel Alpha Trainer")
    calma = build_graph(mode="fastest", airports=aps, aircraft=perfil,
                        wind_spd_kt=0.0, max_leg_km=0.0)
    viento = build_graph(mode="fastest", airports=aps, aircraft=perfil,
                         wind_dir=270, wind_spd_kt=30.0, max_leg_km=0.0)
    assert viento.max_gs_kt == pytest.approx(calma.max_gs_kt + 30.0)


@pytest.mark.parametrize("modo", ["shortest", "safest"])
def test_la_heuristica_es_admisible_en_los_otros_modos(modo):
    """
    shortest: el peso ES la distancia, h = distancia -> admisible con igualdad.
    safest  : el peso es distancia * (1 + r) con r >= 0 -> h = distancia <= peso.
    """
    aps = _airports_muestra()
    r_map = {c: 0.5 for c in aps}          # riesgo no nulo para el modo safest
    graph = build_graph(mode=modo, r_map=r_map, airports=aps, max_leg_km=0.0)
    dest = "SAEZ"
    real = _costo_real_hasta(graph, dest)
    for nodo in graph.nodes:
        if nodo in real:
            assert _heuristic(nodo, dest, graph) <= real[nodo] + 1e-9
