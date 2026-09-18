"""
test_web.py
===========
Capa web: lo que agrega `web/app.py` por encima del motor.

Solo se prueba lo que ES de esta capa —validacion de la entrada, derivacion de
las horas de llegada y como se marcan los puntos que el piloto pidio—, no la
ruta ni el veredicto, que tienen sus propias suites. Corre SIN RED: los
waypoints se generan con `mock=True` sobre tramos cortos, que no insertan
checkpoints meteorologicos.
"""

import time

import pytest
from fastapi import HTTPException

from data.airports import AIRPORTS
from route.optimizer import ViaPoint, eta_por_aerodromo
from risk.aircraft_profiles import get_profile
from web.app import (
    MAX_VIA_POINTS,
    RouteWaypoint,
    ViaPointIn,
    _generate_route_waypoints,
    _parse_via,
)


# ── Validacion de los puntos de paso ──────────────────────────────────────────
#
# Un codigo mal escrito tiene que fallar rapido y CON NOMBRE. Resolverlo por
# aproximacion mandaria al piloto a otro aerodromo, que es el mismo modo de
# falla que la regla R4 del copiloto existe para evitar.

def test_sin_puntos_de_paso_la_ruta_es_directa():
    assert _parse_via([], "SACO", "SAEZ") == []


def test_los_puntos_conservan_el_orden_y_la_intencion():
    puntos = _parse_via(
        [ViaPointIn(code="SAAR", is_stop=False),
         ViaPointIn(code="SAAP", is_stop=True)],
        "SACO", "SAEZ",
    )
    assert [p.code for p in puntos] == ["SAAR", "SAAP"]
    assert [p.is_stop for p in puntos] == [False, True]


def test_el_codigo_se_normaliza_a_mayusculas():
    puntos = _parse_via([ViaPointIn(code="  saar ")], "SACO", "SAEZ")
    assert puntos[0].code == "SAAR"


@pytest.mark.parametrize("via,fragmento", [
    ([ViaPointIn(code="ZZZZ")],                            "ZZZZ"),
    ([ViaPointIn(code="SACO")],                            "SACO"),
    ([ViaPointIn(code="SAEZ")],                            "SAEZ"),
    ([ViaPointIn(code="SAAR"), ViaPointIn(code="SAAR")],   "SAAR"),
    ([ViaPointIn(code="")],                                "sin codigo"),
])
def test_una_entrada_invalida_falla_nombrando_el_punto(via, fragmento):
    """El mensaje tiene que decir CUAL punto esta mal, no solo que algo lo esta."""
    with pytest.raises(HTTPException) as exc:
        _parse_via(via, "SACO", "SAEZ")
    assert exc.value.status_code == 400
    assert fragmento in exc.value.detail


def test_hay_un_tope_de_puntos_de_paso():
    """Cada punto agrega un segmento y, si es escala, una evaluacion con fetch."""
    demasiados = [ViaPointIn(code=c) for c in
                  ("SAAR", "SANT", "SAZY", "SANU", "SAZB", "SAZR")]
    assert len(demasiados) > MAX_VIA_POINTS
    with pytest.raises(HTTPException) as exc:
        _parse_via(demasiados, "SACO", "SAEZ")
    assert exc.value.status_code == 400
    assert str(MAX_VIA_POINTS) in exc.value.detail


# ── Hora de llegada a cada aerodromo ──────────────────────────────────────────
#
# Una escala se evalua a la hora en que se ATERRIZA ahi. Evaluarla a la hora de
# despegue seria mirar otro momento, que es el error que ya costo caro en el
# motor de decision.

class _Leg:
    """Tramo minimo: lo unico que mira `eta_por_aerodromo` es dest y duracion."""
    def __init__(self, origin, dest, time_hours):
        self.origin, self.dest, self.time_hours = origin, dest, time_hours


def test_la_hora_de_llegada_acumula_los_tramos():
    dep = 1_789_000_000
    legs = [_Leg("SACO", "SAAR", 1.0), _Leg("SAAR", "SAEZ", 2.0)]

    etas = eta_por_aerodromo(legs, dep)

    assert etas["SAAR"] == dep + 3600
    assert etas["SAEZ"] == dep + 3 * 3600


def test_cada_escala_se_evalua_a_su_propia_hora():
    """Dos escalas del mismo vuelo NO comparten la hora de llegada."""
    dep = 1_789_000_000
    legs = [_Leg("SACO", "SAAR", 1.5), _Leg("SAAR", "SAAP", 1.0),
            _Leg("SAAP", "SAEZ", 0.5)]

    etas = eta_por_aerodromo(legs, dep)

    assert etas["SAAR"] != etas["SAAP"]
    assert etas["SAAR"] < etas["SAAP"] < etas["SAEZ"]


def test_si_la_ruta_pasa_dos_veces_vale_el_primer_arribo():
    """Para la meteorologia importa cuando se llega, no cuando se vuelve."""
    dep = 1_789_000_000
    legs = [_Leg("SACO", "SAAR", 1.0), _Leg("SAAR", "SAAP", 1.0),
            _Leg("SAAP", "SAAR", 1.0)]

    etas = eta_por_aerodromo(legs, dep)

    assert etas["SAAR"] == dep + 3600


# ── Los puntos pedidos se marcan, y no se degradan ────────────────────────────

def _waypoints(path, via_points, airway_map=None, avion="Cessna 172 Skyhawk"):
    return _generate_route_waypoints(
        path=path,
        aircraft=get_profile(avion),
        dep_time=int(time.time()) + 3600,
        duration_hours=1.5,
        r_map={},
        mock=True,
        airway_map=airway_map,
        via_points=via_points,
    )


def test_el_punto_pedido_queda_marcado_en_los_waypoints():
    wps = {w.code: w for w in _waypoints(
        ["SACO", "SAOC", "SAAR"], [ViaPoint("SAOC", is_stop=True)])}

    assert wps["SAOC"].is_via is True
    assert wps["SAOC"].is_via_stop is True
    assert wps["SACO"].is_via is False


def test_el_sobrevuelo_se_distingue_de_la_escala():
    """Dan la misma geometria; la pantalla tiene que poder decir cual es cual."""
    wps = {w.code: w for w in _waypoints(
        ["SACO", "SAOC", "SAAR"], [ViaPoint("SAOC", is_stop=False)])}

    assert wps["SAOC"].is_via is True
    assert wps["SAOC"].is_via_stop is False


def test_un_punto_pedido_nunca_sale_de_la_linea_de_ruta():
    """
    Un aerodromo intermedio pasa a marcador lateral cuando la aerovia es
    continua a su alrededor. Un punto que el piloto PIDIO no: no esta ahi como
    consecuencia del calculo, esta porque el lo puso.
    """
    path = ["SACO", "SAOC", "SAAR"]
    # Aerovia continua alrededor del intermedio: la condicion que lo degradaria.
    aw = {("SACO", "SAOC"): [], ("SAOC", "SAAR"): []}

    sin_pedir = {w.code: w for w in _waypoints(path, None, airway_map=aw)}
    pedido    = {w.code: w for w in _waypoints(
        path, [ViaPoint("SAOC", is_stop=False)], airway_map=aw)}

    assert sin_pedir["SAOC"].is_emergency_airport is True, \
        "sin pedirlo, el intermedio si sale de la linea"
    assert pedido["SAOC"].is_emergency_airport is False


@pytest.mark.parametrize("avion", [
    "Pipistrel Alpha Trainer", "Cessna 152", "Cessna 172 Skyhawk",
    "Piper PA-28 Cherokee", "Diamond DA40",
])
def test_el_marcado_no_depende_de_la_aeronave(avion):
    """REGLA DE ALCANCE: que el piloto haya pedido un punto no es cosa del avion."""
    wps = {w.code: w for w in _waypoints(
        ["SACO", "SAOC", "SAAR"], [ViaPoint("SAOC", is_stop=True)], avion=avion)}

    assert wps["SAOC"].is_via is True
    assert wps["SAOC"].is_via_stop is True


@pytest.mark.parametrize("path,punto,region", [
    (["SACO", "SAOC", "SAAR"], "SAOC", "Pampa"),
    (["SASA", "SANT", "SANC"], "SANT", "NOA"),
    (["SAZN", "SAZY", "SAZS"], "SAZY", "Patagonia"),
    (["SAME", "SANU", "SACO"], "SANU", "Cuyo"),
])
def test_el_marcado_funciona_en_cualquier_region(path, punto, region):
    wps = {w.code: w for w in _waypoints(path, [ViaPoint(punto, is_stop=True)])}
    assert wps[punto].is_via is True, region


# ── El camino que se vuela, no la recta entre aerodromos ──────────────────────
#
# El optimizador mide la ruta como la recta entre aerodromos, pero un corredor
# visual o una aerovia la doblan. Todo lo que se informa —tabla de tramos,
# distancia, tiempo, combustible y hora de llegada— tiene que describir el
# camino que el avion realmente hace.

from web.app import _nombre_de_punto, _tipo_de_punto, _tramos_volados


def _wp(code, lat, lon, **kw):
    return RouteWaypoint(code=code, name=code, lat=lat, lon=lon,
                         r_total=0.0, decision="GO", **kw)


def test_la_suma_de_los_tramos_es_el_total():
    """Si no cierran, el pie de la tabla contradice a sus propias filas."""
    wps = [_wp("A", -31.0, -64.5), _wp("B", -31.0, -64.0), _wp("C", -30.5, -63.5)]

    tramos, resumen = _tramos_volados(wps, get_profile("Cessna 172 Skyhawk"))

    assert len(tramos) == 2
    assert round(sum(t["distance_km"] for t in tramos), 1) == resumen["total_distance_km"]
    assert abs(sum(t["time_hours"] for t in tramos) - resumen["total_time_hours"]) < 0.01
    assert round(sum(t["fuel_liters"] for t in tramos), 1) == resumen["total_fuel_liters"]


def test_doblar_la_ruta_la_alarga():
    """
    Es la razon de ser de todo esto: el corredor mete un punto fuera de la recta
    y la distancia informada tiene que crecer, no quedarse en la recta.
    """
    ac = get_profile("Cessna 172 Skyhawk")
    recta  = [_wp("A", -31.0, -64.5), _wp("B", -31.0, -63.5)]
    doblada = [_wp("A", -31.0, -64.5),
               _wp("COR", -30.5, -64.0, is_corridor_waypoint=True, point_name="ASCOCHINGA"),
               _wp("B", -31.0, -63.5)]

    _, r_recta   = _tramos_volados(recta, ac)
    _, r_doblada = _tramos_volados(doblada, ac)

    assert r_doblada["total_distance_km"] > r_recta["total_distance_km"]
    assert r_doblada["total_time_hours"]  > r_recta["total_time_hours"]
    assert r_doblada["total_fuel_liters"] > r_recta["total_fuel_liters"]


def test_un_punto_sobre_la_linea_no_alarga_nada():
    """
    Un checkpoint meteorologico se interpola SOBRE el tramo: partir la recta en
    dos no puede cambiar su longitud. Si cambiara, el calculo estaria mal.
    """
    ac = get_profile("Cessna 172 Skyhawk")
    sin_chk = [_wp("A", -31.0, -64.5), _wp("B", -31.0, -63.5)]
    con_chk = [_wp("A", -31.0, -64.5),
               _wp("WP1", -31.0, -64.0, is_checkpoint=True),
               _wp("B", -31.0, -63.5)]

    _, r1 = _tramos_volados(sin_chk, ac)
    _, r2 = _tramos_volados(con_chk, ac)

    assert abs(r1["total_distance_km"] - r2["total_distance_km"]) < 0.2


def test_los_aerodromos_de_emergencia_no_son_parte_del_camino():
    """Estan al costado como referencia: no se pasa por ellos."""
    ac = get_profile("Cessna 172 Skyhawk")
    wps = [_wp("A", -31.0, -64.5),
           _wp("EMERG", -32.5, -64.5, is_emergency_airport=True),
           _wp("B", -31.0, -63.5)]

    tramos, _ = _tramos_volados(wps, ac)

    assert len(tramos) == 1
    assert tramos[0]["origin"] == "A" and tramos[0]["dest"] == "B"


def test_cada_tramo_trae_su_rumbo():
    """Es el dato que faltaba: sin el, el piloto no sabe donde virar."""
    wps = [_wp("A", -31.0, -64.5),
           _wp("COR", -31.0, -64.0, is_corridor_waypoint=True, point_name="ASCOCHINGA"),
           _wp("B", -30.5, -64.0)]

    tramos, _ = _tramos_volados(wps, get_profile("Cessna 172 Skyhawk"))

    rumbos = [t["bearing_deg"] for t in tramos]
    assert all(r is not None for r in rumbos)
    assert rumbos[0] != rumbos[1], "el camino dobla: los rumbos no pueden ser iguales"
    assert 85 <= rumbos[0] <= 95      # hacia el este
    assert rumbos[1] < 10 or rumbos[1] > 350   # hacia el norte


def test_la_escala_parte_el_combustible_en_etapas():
    """
    En una escala se vuelve a cargar: el tanque no tiene que aguantar el vuelo
    entero, solo la etapa mas larga. Es el mismo criterio que `_optimize_via`.
    """
    ac = get_profile("Pipistrel Alpha Trainer")     # 552 km de alcance
    # Dos tramos de ~400 km: juntos no entran en el tanque, por etapas si.
    wps = [_wp("A", -31.0, -66.0), _wp("ESC", -31.0, -62.0), _wp("B", -31.0, -58.0)]

    _, sin_escala = _tramos_volados(wps, ac)
    _, con_escala = _tramos_volados(wps, ac, escalas={"ESC"})

    assert sin_escala["total_distance_km"] == con_escala["total_distance_km"]
    assert sin_escala["fuel_ok"] is False, "el vuelo entero no entra en el tanque"
    assert con_escala["fuel_ok"] is True, "por etapas si, porque en la escala se carga"


def test_el_punto_de_corredor_se_nombra_por_el_punto_no_por_el_corredor():
    """
    El `code` de un waypoint de corredor es el id del CORREDOR y se repite en
    sus dos extremos: la tabla diria "VFR-COR-04 -> VFR-COR-04" y el piloto no
    sabria donde virar.
    """
    con_nombre = _wp("VFR-COR-04", -31.0, -64.5,
                     is_corridor_waypoint=True, point_name="ASCOCHINGA",
                     corridor_id="VFR-COR-04")
    sin_nombre = _wp("VFR-COR-09", -31.0, -64.5,
                     is_corridor_waypoint=True, corridor_id="VFR-COR-09")

    assert _nombre_de_punto(con_nombre) == "ASCOCHINGA"
    # Sin nombre publicado no se inventa uno: se usa el id del corredor.
    assert _nombre_de_punto(sin_nombre) == "VFR-COR-09"


@pytest.mark.parametrize("wp,esperado", [
    (_wp("X", -31.0, -64.0, is_corridor_waypoint=True), "corredor"),
    (_wp("X", -31.0, -64.0, is_airway_waypoint=True),   "aerovia"),
    (_wp("X", -31.0, -64.0, is_checkpoint=True),        "checkpoint"),
    (_wp("SACO", -31.0, -64.0),                          "aerodromo"),
])
def test_cada_punto_declara_que_es(wp, esperado):
    """
    No todos los puntos son un viraje: un checkpoint meteorologico esta SOBRE la
    linea. Decir que es cada uno evita leer una fila de muestreo como si fuera
    una instruccion de navegacion.
    """
    assert _tipo_de_punto(wp) == esperado


@pytest.mark.parametrize("avion", [
    "Pipistrel Alpha Trainer", "Cessna 152", "Cessna 172 Skyhawk",
    "Piper PA-28 Cherokee", "Diamond DA40",
])
def test_el_tiempo_y_el_combustible_salen_del_perfil(avion):
    """REGLA DE ALCANCE: la distancia es geometria, el tiempo depende del avion."""
    ac = get_profile(avion)
    wps = [_wp("A", -31.0, -64.5), _wp("B", -31.0, -63.5)]

    tramos, resumen = _tramos_volados(wps, ac)

    esperado_h = tramos[0]["distance_km"] / (ac.cruise_kt * 1.852)
    assert abs(tramos[0]["time_hours"] - esperado_h) < 0.01
    assert resumen["total_fuel_liters"] > 0


def test_una_ruta_de_un_solo_punto_no_rompe():
    tramos, resumen = _tramos_volados([_wp("A", -31.0, -64.5)],
                                      get_profile("Cessna 152"))
    assert tramos == [] and resumen == {}


# ── Los corredores publican el nombre de cada punto ───────────────────────────

def test_el_nombre_del_corredor_lista_sus_puntos_en_orden():
    """
    Integridad del dato: el `name` de cada corredor lista sus puntos separados
    por " - ", uno por coordenada. De ahi sale el nombre de cada punto, que es
    lo unico que permite decir DONDE se vira. Si un corredor nuevo no cumpliera
    la convencion, el punto quedaria sin nombre —no mal nombrado—, pero conviene
    enterarse acá.
    """
    import json
    import os

    from route import vfr_corridors as vc

    revisados = 0
    for fname in vc._FILES.values():
        ruta = os.path.join(vc._DATA_DIR, fname)
        if not os.path.exists(ruta):
            continue
        with open(ruta, encoding="utf-8") as f:
            fc = json.load(f)
        for ft in fc.get("features", []):
            props = ft.get("properties", {}) or {}
            coords = ft.get("geometry", {}).get("coordinates", [])
            partes = [x.strip() for x in str(props.get("name", "")).split(" - ")]
            revisados += 1
            assert len(partes) == len(coords), (
                f"{props.get('corridor_id')}: {len(coords)} puntos y "
                f"{len(partes)} nombres en {props.get('name')!r}"
            )
    assert revisados >= 20, "no se cargaron los corredores"


def test_los_puntos_de_corredor_llegan_con_nombre():
    """De punta a punta: el ruteo por corredor entrega el nombre de cada punto."""
    from route.vfr_corridors import corridor_path_for_leg

    a, b = AIRPORTS["SACC"], AIRPORTS["JES"]
    wps = corridor_path_for_leg(a.lat, a.lon, b.lat, b.lon)

    assert wps, "el tramo deberia cruzar la TMA Cordoba"
    assert all(w.get("point_name") for w in wps)
    # Puntos distintos, nombres distintos: es lo que faltaba para saber virar.
    assert len({w["point_name"] for w in wps}) == len(wps)


# ── Las escalas de combustible sugeridas se evaluan como escalas ──────────────

from types import SimpleNamespace

from web.app import RouteWaypoint, _paradas_a_evaluar, _veredictos_en_marcadores


def _ap(code, **kw):
    return RouteWaypoint(code=code, name=code, lat=0.0, lon=0.0,
                         r_total=0.0, decision="GO", **kw)


def _ficha(code, dec, r):
    # La funcion solo lee el veredicto y el R de la ficha.
    return SimpleNamespace(station_id=code, decision=dec, r_total=r)


def test_la_escala_de_combustible_sugerida_se_evalua_como_aterrizaje():
    wps = [_ap("SACO"), _ap("SAOM", is_fuel_stop=True), _ap("SAAR"),
           _ap("SADF", is_fuel_stop=True), _ap("SAEZ")]
    via = [ViaPoint(code="SAAR", is_stop=True)]
    path = ["SACO", "SAOM", "SAAR", "SADF", "SAEZ"]
    assert _paradas_a_evaluar(wps, via, path, "SACO", "SAEZ") == [
        ("SAOM", "combustible"), ("SAAR", "escala"), ("SADF", "combustible")]


def test_si_el_piloto_ya_pidio_escala_ahi_manda_lo_que_pidio():
    wps = [_ap("SACO"), _ap("SAAR", is_fuel_stop=True), _ap("SAEZ")]
    via = [ViaPoint(code="SAAR", is_stop=True)]
    assert _paradas_a_evaluar(wps, via, ["SACO", "SAAR", "SAEZ"], "SACO", "SAEZ") == [
        ("SAAR", "escala")]


def test_un_sobrevuelo_no_es_parada():
    wps = [_ap("SACO"), _ap("SAAR"), _ap("SAEZ")]
    via = [ViaPoint(code="SAAR", is_stop=False)]
    assert _paradas_a_evaluar(wps, via, ["SACO", "SAAR", "SAEZ"], "SACO", "SAEZ") == []


def test_cada_marcador_lleva_el_veredicto_de_su_ficha_y_el_sobrevuelo_ninguno():
    chk = RouteWaypoint(code="WP1-1", name="x", lat=0, lon=0, r_total=0.3,
                        decision="CAUTION", is_checkpoint=True, is_enroute_eval=True)
    wps = [_ap("SACO"), _ap("SAOM", is_fuel_stop=True), chk, _ap("SAAR"), _ap("SAEZ")]
    fichas = {"SACO": _ficha("SACO", "GO", 0.1),
              "SAOM": _ficha("SAOM", "NO GO", 0.7),
              "SAEZ": _ficha("SAEZ", "CAUTION", 0.3)}
    _veredictos_en_marcadores(wps, fichas)
    por = {w.code: (w.decision, w.r_total) for w in wps}
    assert por["SAOM"] == ("NO GO", 0.7)          # la escala ya no es GO de relleno
    assert por["SAAR"] == (None, None)            # se sobrevuela: sin veredicto
    assert por["WP1-1"] == ("CAUTION", 0.3)       # los checkpoints no se tocan
