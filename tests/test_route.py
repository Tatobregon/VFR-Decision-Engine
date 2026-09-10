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


# ══════════════════════════════════════════════════════════════════════════════
# Puntos de paso: sobrevuelo y escala NO son lo mismo
# ══════════════════════════════════════════════════════════════════════════════
# La geometria de la ruta es identica en los dos casos, pero la autonomia no:
# sobrevolar es un vuelo continuo y hay que llegar de una; hacer escala parte el
# vuelo en etapas y cada una se mide por separado. Tratarlos igual daria por
# viable una ruta que no lo es, o al reves.

from route.optimizer import ViaPoint, detour_cost, optimize


def _via(codigo, escala=False):
    return [ViaPoint(codigo, is_stop=escala)]


def test_el_punto_de_paso_aparece_en_la_ruta():
    ac = get_profile("Cessna 172 Skyhawk")
    r = optimize("SACO", "SAEZ", aircraft=ac, via=_via("SAAR"))
    assert r.found
    assert "SAAR" in r.path
    assert r.path[0] == "SACO" and r.path[-1] == "SAEZ"


def test_el_desvio_nunca_acorta_la_ruta():
    """Pasar por un punto solo puede agregar distancia, jamas quitarla."""
    ac = get_profile("Cessna 172 Skyhawk")
    directo = optimize("SACO", "SAEZ", aircraft=ac)
    con_via = optimize("SACO", "SAEZ", aircraft=ac, via=_via("SAAR"))
    assert directo.found and con_via.found
    assert con_via.total_dist_km >= directo.total_dist_km


def test_la_escala_parte_la_autonomia_en_etapas():
    """
    El caso que justifica la distincion. Con el Alpha, SACO-SAEZ no entra en el
    tanque de un tirón; con escala en Rosario cada etapa si. La ruta es la misma
    linea en los dos casos: lo que cambia es si se aterriza en el medio.
    """
    alpha = get_profile("Pipistrel Alpha Trainer")
    sobrevuelo = optimize("SACO", "SAEZ", aircraft=alpha, via=_via("SAAR", False))
    escala     = optimize("SACO", "SAEZ", aircraft=alpha, via=_via("SAAR", True))

    assert sobrevuelo.found and escala.found
    assert sobrevuelo.path == escala.path          # misma geometria
    assert sobrevuelo.total_dist_km == escala.total_dist_km
    assert sobrevuelo.fuel_ok is False             # vuelo continuo: no llega
    assert escala.fuel_ok is True                  # por etapas: si


def test_un_punto_repetido_no_arma_ruta():
    """Un tramo de longitud cero rompe el calculo de rumbo."""
    ac = get_profile("Cessna 172 Skyhawk")
    for via in (_via("SACO"), _via("SAEZ"),
                [ViaPoint("SAAR"), ViaPoint("SAAR")]):
        r = optimize("SACO", "SAEZ", aircraft=ac, via=via)
        assert r.found is False
        assert "repetido" in r.error


def test_un_punto_inexistente_falla_con_mensaje_claro():
    ac = get_profile("Cessna 172 Skyhawk")
    r = optimize("SACO", "SAEZ", aircraft=ac, via=_via("XXXX"))
    assert r.found is False
    assert r.error


def test_varios_puntos_de_paso_se_respetan_en_orden():
    ac = get_profile("Cessna 172 Skyhawk")
    r = optimize("SACO", "SAEZ", aircraft=ac,
                 via=[ViaPoint("SAAR"), ViaPoint("SAAJ")])
    assert r.found
    assert r.path.index("SAAR") < r.path.index("SAAJ")


def test_los_tramos_encadenan_sin_huecos():
    """El destino de cada tramo tiene que ser el origen del siguiente."""
    ac = get_profile("Cessna 172 Skyhawk")
    r = optimize("SACO", "SAEZ", aircraft=ac,
                 via=[ViaPoint("SAAR"), ViaPoint("SAAJ")])
    assert r.found
    for a, b in zip(r.legs, r.legs[1:]):
        assert a.dest == b.origin


def test_el_total_es_la_suma_de_los_tramos():
    ac = get_profile("Cessna 172 Skyhawk")
    r = optimize("SACO", "SAEZ", aircraft=ac, via=_via("SAAR"))
    assert r.found
    assert r.total_dist_km == pytest.approx(
        sum(l.distance_km for l in r.legs), abs=0.2)


def test_el_costo_del_desvio_se_informa():
    ac = get_profile("Cessna 172 Skyhawk")
    base = optimize("SACO", "SAEZ", aircraft=ac)
    c = detour_cost(base, optimize("SACO", "SAEZ", aircraft=ac, via=_via("SAAR")))
    assert c.found
    assert c.dist_km_extra >= 0
    assert c.time_min_extra >= 0
    assert c.path_despues != c.path_antes


def test_un_desvio_que_rompe_la_autonomia_se_declara_aparte():
    """
    Que un desvio deje la ruta sin combustible no es un detalle de magnitud:
    es un cambio de viabilidad y se informa por separado de los kilometros.
    """
    from route.optimizer import DetourCost

    c = DetourCost(
        found=True, dist_km_extra=50.0, time_min_extra=30.0, fuel_l_extra=8.0,
        path_antes=["A", "B"], path_despues=["A", "V", "B"],
        fuel_ok_antes=True, fuel_ok_despues=False,
        needs_stop_antes=False, needs_stop_despues=True,
    )
    assert c.rompe_la_autonomia is True


def test_los_puntos_de_paso_funcionan_en_cualquier_region():
    """Regla de alcance: no puede depender de la Pampa."""
    ac = get_profile("Cessna 172 Skyhawk")
    casos = [
        ("SASA", "SANC", "SANT"),   # NOA
        ("SAAR", "SAEZ", "SAAJ"),   # Pampa
        ("SAZN", "SAZS", "SAZY"),   # Patagonia
    ]
    for origen, destino, paso in casos:
        if not all(c in AIRPORTS for c in (origen, destino, paso)):
            continue
        r = optimize(origen, destino, aircraft=ac, via=_via(paso))
        assert r.found, f"{origen}->{paso}->{destino}: {r.error}"
        assert paso in r.path
