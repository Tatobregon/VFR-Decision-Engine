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
    """
    Visibilidad y techo dominan; la tendencia es el factor de menor peso.

    El orden refleja la derivacion por evidencia de ahp_weights.py: el grupo de
    referencia visual concentra ~0.79 del peso porque la accidentologia le
    atribuye una severidad de 72.9% frente al 1.5% del grupo de viento.
    """
    assert W_VIS == W_CEIL                      # co-iguales en el AHP (minimo legal VFR)
    assert W_VIS > W_XWIND                      # referencia visual domina al viento
    assert W_XWIND > W_GUST                     # el cruzado domina a la rafaga
    assert W_WX > W_TAF                         # lo observado domina a lo pronosticado
    assert W_TAF == min(W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF)


def test_el_cruzado_no_alcanza_el_umbral_de_caution_por_si_solo():
    """
    Documenta POR QUE el peso del cruzado es bajo sin que eso subestime el riesgo.

    Un cruzado en el limite de la aeronave (r_xwind = 1.0) aporta solo W_XWIND al
    score: por debajo de t_go. Si el sistema dependiera solo de la suma ponderada
    daria GO. No lo hace porque la barrera no-compensatoria lo intercepta antes
    (ver test_regression_scenarios.py). Este test fija esa dependencia: si alguien
    sube W_XWIND creyendo "arreglar" algo, o baja la barrera, el diseno se rompe.
    """
    assert W_XWIND < THRESHOLD_GO


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
    """Umbrales optimos de risk/calibration.py sobre la bateria de referencia."""
    assert THRESHOLD_GO == 0.22
    assert THRESHOLD_CAUTION == 0.59


@pytest.mark.parametrize("r,esperado", [
    (0.0, "GO"), (0.21, "GO"), (0.22, "CAUTION"), (0.58, "CAUTION"),
    (0.59, "NO GO"), (1.0, "NO GO"),
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


# ══════════════════════════════════════════════════════════════════════════════
# Barrera de rafagas: escala propia, distinta de la del cruzado
# ══════════════════════════════════════════════════════════════════════════════
# La rafaga y el cruzado dejaron de compartir umbral (septiembre 2026). El
# motivo esta en el estatus de cada parametro: crosswind_max_kt es un maximo
# DEMOSTRADO en certificacion, gust_max_kt es una referencia de operacion
# normal. Estos tests fijan esa separacion para que no se revierta sin querer.

from risk.soft_scoring import (
    GUST_CAUTION_FRACTION,
    GUST_NOGO_FACTOR,
    XWIND_CAUTION_FRACTION,
)


def _piso_por_rafaga(delta_kt, gust_max_kt=20.0):
    """Piso conjuntivo con SOLO rafaga activa (cruzado y demas neutralizados)."""
    piso, _ = conjunctive_floor(
        xw_eff_kt=0.0, xw_limit_kt=12.0,
        gust_kt=10.0 + delta_kt, spd_kt=10.0,
        gust_max_kt=gust_max_kt, r_fog=0.0, r_taf=0.0,
    )
    return piso


def test_la_rafaga_usa_una_escala_mas_permisiva_que_el_cruzado():
    assert GUST_CAUTION_FRACTION > XWIND_CAUTION_FRACTION


@pytest.mark.parametrize("fraccion,esperado", [
    (0.50, "GO"),        # dia ventoso normal: ya no dispara CAUTION
    (0.70, "GO"),
    (0.84, "GO"),
    (0.85, "CAUTION"),   # justo en la referencia de operacion normal
    (1.00, "CAUTION"),
    (1.49, "CAUTION"),
    (1.50, "NO GO"),     # la supera con margen
    (2.00, "NO GO"),
])
def test_la_escalera_de_rafagas(fraccion, esperado):
    assert _piso_por_rafaga(20.0 * fraccion) == esperado


def test_el_caso_operativo_que_motivo_el_cambio_da_go():
    """
    Caso real observado en SACC: viento 318/6.8 kt con rafaga 20.8 sobre la
    pista 320 (cruzado efectivo 0.2 kt) en un Alpha Trainer. Un delta de
    rafaga de 14 kt sobre una referencia de 20 es un dia ventoso, no una
    condicion que amerite advertencia.
    """
    perfil = get_profile("Pipistrel Alpha Trainer")
    piso, _ = conjunctive_floor(
        xw_eff_kt=0.2, xw_limit_kt=perfil.crosswind_max_kt,
        gust_kt=20.8, spd_kt=6.8, gust_max_kt=perfil.gust_max_kt,
        r_fog=0.0, r_taf=0.0,
    )
    assert piso == "GO"


def test_la_escala_de_rafaga_es_relativa_a_cada_aeronave():
    """
    Regla de alcance: el mismo delta pesa distinto segun el avion.

    Con 20 kt de delta, el Alpha (referencia 20 kt) queda al 100% y el DA40
    (referencia 30 kt) al 67%. El umbral nunca es un valor absoluto en kt.
    """
    delta = 20.0
    alpha = get_profile("Pipistrel Alpha Trainer").gust_max_kt   # 20 kt
    da40  = get_profile("Diamond DA40").gust_max_kt              # 30 kt
    assert _piso_por_rafaga(delta, alpha) == "CAUTION"           # 100%
    assert _piso_por_rafaga(delta, da40)  == "GO"                # 67%


def test_la_referencia_normativa_no_se_desincroniza_del_motor():
    """
    El voto de rafaga de risk/scenarios.py DUPLICA los cortes del motor.
    Esa duplicacion es deliberada y esta declarada en el encabezado de ese
    modulo (la concordancia de este factor es por construccion, no evidencia
    independiente), pero si los dos valores se separan sin querer, la bateria
    empieza a medir contra una regla que el sistema ya no aplica.
    """
    import inspect

    from risk import scenarios

    fuente = inspect.getsource(scenarios.normative_label)
    assert f'>= {GUST_NOGO_FACTOR}' in fuente, (
        "el corte de NO GO por rafaga de scenarios.py no coincide con "
        f"GUST_NOGO_FACTOR={GUST_NOGO_FACTOR}"
    )
    assert f'>= {GUST_CAUTION_FRACTION}' in fuente, (
        "el corte de CAUTION por rafaga de scenarios.py no coincide con "
        f"GUST_CAUTION_FRACTION={GUST_CAUTION_FRACTION}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Techo de servicio
# ══════════════════════════════════════════════════════════════════════════════

def test_todo_perfil_declara_techo_de_servicio_por_encima_de_su_crucero():
    """Acota la altitud que el piloto puede elegir a mano en VFR."""
    for nombre in PROFILE_NAMES:
        p = get_profile(nombre)
        assert p.service_ceiling_ft > 0, nombre
        assert p.service_ceiling_ft >= p.cruise_alt_ft, nombre
