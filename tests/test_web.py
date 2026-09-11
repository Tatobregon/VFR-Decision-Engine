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

from route.optimizer import ViaPoint, eta_por_aerodromo
from risk.aircraft_profiles import get_profile
from web.app import (
    MAX_VIA_POINTS,
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
