"""
test_risk.py
============
Motor de riesgo: pesos, funciones r_i, umbrales, bloqueos duros, score blando y
barrera no-compensatoria.

Es la parte del sistema que produce el veredicto, asi que estos tests son la red
que impide que un cambio futuro lo altere en silencio.
"""

import pytest

from risk.weights import (
    W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF,
    THRESHOLD_GO, THRESHOLD_CAUTION,
    r_visibility, r_ceiling, r_crosswind, r_gust, r_wx_codes,
    apply_decision_threshold,
)
from risk.hard_blockers import check_hard_blockers, HARD_BLOCKER_TOKENS
from risk.soft_scoring import compute_soft_score, conjunctive_floor
from risk.aircraft_profiles import PROFILE_NAMES, get_profile
from risk.personal_minima import get_minima, STUDENT, ADVANCED


# ── Pesos ─────────────────────────────────────────────────────────────────────

def test_los_pesos_suman_uno():
    total = W_VIS + W_CEIL + W_XWIND + W_GUST + W_WX + W_FOG + W_TAF
    assert total == pytest.approx(1.0)


def test_jerarquia_de_pesos_ahp():
    """Visibilidad y techo dominan; la tendencia es el factor de menor peso."""
    assert W_VIS == W_CEIL                      # co-iguales en el AHP
    assert W_VIS > W_XWIND > W_GUST > W_WX > W_FOG > W_TAF


# ── Funciones de riesgo ───────────────────────────────────────────────────────

@pytest.mark.parametrize("vis_km,esperado", [
    (None, 0.0), (10.0, 0.0), (8.0, 0.0), (5.5, 0.5), (3.0, 1.0), (0.5, 1.0),
])
def test_r_visibility(vis_km, esperado):
    assert r_visibility(vis_km) == pytest.approx(esperado, abs=1e-3)


@pytest.mark.parametrize("ceil_ft,esperado", [
    (None, 0.0), (5000, 0.0), (2000, 0.0), (1250, 0.5), (500, 1.0), (100, 1.0),
])
def test_r_ceiling(ceil_ft, esperado):
    assert r_ceiling(ceil_ft) == pytest.approx(esperado, abs=1e-3)


def test_r_ceiling_none_es_cielo_despejado():
    """None significa CLR o solo FEW/SCT, nunca 'sin dato peligroso'."""
    assert r_ceiling(None) == 0.0


def test_r_crosswind_satura_en_el_limite_del_avion():
    assert r_crosswind(0.0, 12.0) == 0.0
    assert r_crosswind(6.0, 12.0) == pytest.approx(0.5)
    assert r_crosswind(12.0, 12.0) == 1.0
    assert r_crosswind(30.0, 12.0) == 1.0      # clampeado


def test_r_gust_mide_la_variabilidad_no_el_valor_absoluto():
    assert r_gust(None, 10.0, 20.0) == 0.0     # sin rafaga
    assert r_gust(10.0, 10.0, 20.0) == 0.0     # rafaga == sostenido
    assert r_gust(20.0, 10.0, 20.0) == pytest.approx(0.5)
    assert r_gust(40.0, 10.0, 20.0) == 1.0


def test_r_wx_codes_toma_el_fenomeno_mas_severo():
    assert r_wx_codes([]) == 0.0
    assert r_wx_codes(["TSRA"]) == 1.0
    assert r_wx_codes(["-RA", "SN"]) == r_wx_codes(["SN"])
    assert r_wx_codes(["TOKEN_INEXISTENTE"]) == 0.0


# ── Umbrales ──────────────────────────────────────────────────────────────────

def test_umbrales_calibrados():
    assert THRESHOLD_GO == 0.22
    assert THRESHOLD_CAUTION == 0.50


@pytest.mark.parametrize("r,esperado", [
    (0.0, "GO"), (0.21, "GO"), (0.22, "CAUTION"), (0.49, "CAUTION"),
    (0.50, "NO GO"), (1.0, "NO GO"),
])
def test_apply_decision_threshold(r, esperado):
    assert apply_decision_threshold(r) == esperado


# ── Bloqueos duros ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("token", sorted(HARD_BLOCKER_TOKENS))
def test_todo_fenomeno_peligroso_bloquea(token):
    assert check_hard_blockers(10.0, 2000, [token]).is_blocked


def test_limites_absolutos_de_visibilidad_y_techo():
    assert check_hard_blockers(1.4, None, []).is_blocked
    assert not check_hard_blockers(1.5, None, []).is_blocked      # justo en el limite
    assert check_hard_blockers(10.0, 499, []).is_blocked
    assert not check_hard_blockers(10.0, 500, []).is_blocked


def test_sin_dato_no_bloquea():
    """Ausencia de dato no es lo mismo que condicion peligrosa."""
    assert not check_hard_blockers(None, None, []).is_blocked


# ── Barrera no-compensatoria ──────────────────────────────────────────────────

def test_cruzado_sobre_el_limite_del_avion_veta():
    piso, motivo = conjunctive_floor(
        xw_eff_kt=13.0, xw_limit_kt=12.0, gust_kt=None, spd_kt=None,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0,
    )
    assert piso == "NO GO"
    assert "cruzado" in motivo


def test_cruzado_a_mitad_del_limite_es_precaucion():
    piso, _ = conjunctive_floor(
        xw_eff_kt=6.0, xw_limit_kt=12.0, gust_kt=None, spd_kt=None,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0,
    )
    assert piso == "CAUTION"


def test_condiciones_buenas_no_imponen_piso():
    piso, motivo = conjunctive_floor(
        xw_eff_kt=2.0, xw_limit_kt=12.0, gust_kt=None, spd_kt=None,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0,
    )
    assert piso == "GO" and motivo == ""


def test_el_veto_no_se_diluye_en_el_promedio(weather):
    """
    El caso que motiva la barrera: cruzado por encima del maximo demostrado con
    todo lo demas perfecto. El score compensatorio daria GO; debe salir NO GO.
    """
    alpha = get_profile("Pipistrel Alpha Trainer")
    w = weather(visibility_km=10.0, ceiling_ft=None,
                wind_dir=270, wind_spd_kt=13.0, spread_c=9.0)
    res = compute_soft_score(w, runway_heading=360, aircraft=alpha)

    assert res.r_total < THRESHOLD_CAUTION      # el promedio no lo detecta
    assert res.decision == "NO GO"              # la barrera si
    assert res.guardrail_reason


# ── Escalabilidad entre aeronaves ─────────────────────────────────────────────

def test_el_veredicto_depende_de_la_aeronave(weather):
    """
    El mismo viento cruzado veta al avion mas chico y no al mas grande: los
    limites salen del perfil, nunca de constantes de un avion en particular.
    """
    w = weather(visibility_km=10.0, wind_dir=270, wind_spd_kt=13.0, spread_c=9.0)
    chico = compute_soft_score(w, 360, get_profile("Cessna 152"))       # 12 kt
    grande = compute_soft_score(w, 360, get_profile("Diamond DA40"))    # 20 kt
    assert chico.decision == "NO GO"
    assert grande.decision != "NO GO"


@pytest.mark.parametrize("nombre", PROFILE_NAMES)
def test_todos_los_perfiles_son_utilizables(nombre, weather):
    """Ningun perfil puede romper el motor ni dar valores fuera de rango."""
    perfil = get_profile(nombre)
    res = compute_soft_score(weather(), 360, perfil)
    assert 0.0 <= res.r_total <= 1.0
    assert res.decision in {"GO", "CAUTION", "NO GO"}
    assert perfil.range_km > 0 and perfil.fuel_usable_l > 0


# ── Minimos personales ────────────────────────────────────────────────────────

def test_minimos_personales_endurecen_el_criterio(weather):
    """A igual meteorologia, un alumno debe recibir un veredicto no mas laxo."""
    w = weather(visibility_km=6.0, ceiling_ft=1200, wind_dir=300,
                wind_spd_kt=8.0, spread_c=4.0)
    c172 = get_profile("Cessna 172 Skyhawk")
    r_alumno = compute_soft_score(w, 360, c172, personal_minima=STUDENT)
    r_avanzado = compute_soft_score(w, 360, c172, personal_minima=ADVANCED)
    assert r_alumno.r_total >= r_avanzado.r_total


def test_nivel_desconocido_cae_al_default():
    assert get_minima("no-existe") is get_minima("PPL")


# ── Coherencia general ────────────────────────────────────────────────────────

def test_el_score_es_la_suma_ponderada(weather):
    """No hay deltas externos sumados al score (la penalizacion orografica se removio)."""
    res = compute_soft_score(weather(visibility_km=6.0, ceiling_ft=1500), 360)
    assert res.r_total == pytest.approx(min(res.r_weighted_sum, 1.0))


def test_la_fuente_del_dato_no_cambia_el_score(weather):
    """Mismas condiciones, METAR o NWP, mismo R."""
    metar = compute_soft_score(weather(nwp_estimated=False, source="metar"), 360)
    nwp   = compute_soft_score(weather(nwp_estimated=True, source="nwp"), 360)
    assert metar.r_total == pytest.approx(nwp.r_total)
