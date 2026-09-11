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


@pytest.mark.parametrize("xw_eff,esperado", [
    (6.0,  "GO"),        # 50% del limite: ya no basta para advertir
    (10.1, "GO"),        # 84%: por debajo del corte
    (10.2, "CAUTION"),   # 85% justo
    (11.9, "CAUTION"),
    (12.0, "NO GO"),     # alcanza el maximo demostrado
])
def test_la_escalera_de_viento_cruzado(xw_eff, esperado):
    """
    El corte de CAUTION esta en el 85% del maximo DEMOSTRADO, no en la mitad.

    La razon es que `xw_eff_kt` ya se calcula con la RAFAGA: es el peor valor
    instantaneo que el avion va a encontrar, no el del viento sostenido. Pedir
    ademas que ese peor valor se quede por debajo de la mitad del maximo
    certificado aplica el margen dos veces, y producia CAUTION en dias de viento
    sostenido de 2 kt.
    """
    piso, _ = conjunctive_floor(
        xw_eff_kt=xw_eff, xw_limit_kt=12.0, gust_kt=None, spd_kt=None,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0,
    )
    assert piso == esperado


def test_condiciones_buenas_no_imponen_piso():
    piso, motivo = conjunctive_floor(
        xw_eff_kt=2.0, xw_limit_kt=12.0, gust_kt=None, spd_kt=None,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0,
    )
    assert piso == "GO" and motivo == ""


@pytest.mark.parametrize("perfil", PROFILE_NAMES)
def test_el_veto_no_se_diluye_en_el_promedio(weather, perfil):
    """
    El caso que motiva la barrera: cruzado por encima del maximo demostrado con
    todo lo demas perfecto. El score compensatorio daria GO; debe salir NO GO.

    El viento se DERIVA del limite de cada aeronave en vez de fijarlo en un
    numero: atarlo a los 12 kt del Alpha hacia que el test dependiera de un
    valor del perfil, y se rompia al corregir ese dato (paso: el maximo del
    Alpha se ajusto a 18 kt y el test empezo a fallar sin que hubiera ningun
    problema en el motor). Ademas asi se verifica en las cinco aeronaves, que
    es lo que pide la regla de alcance del proyecto.
    """
    ac = get_profile(perfil)
    # Cruzado justo por encima del maximo demostrado, viento perpendicular.
    w = weather(visibility_km=10.0, ceiling_ft=None,
                wind_dir=270, wind_spd_kt=ac.crosswind_max_kt + 1.0, spread_c=9.0)
    res = compute_soft_score(w, runway_heading=360, aircraft=ac)

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
# La RAFAGA entra por su componente cruzado, no por su magnitud cruda
# ══════════════════════════════════════════════════════════════════════════════
# Septiembre 2026: la barrera sobre el delta crudo de rafaga se ELIMINO. Medía
# cuanto varia el viento sin mirar hacia donde, y vetaba vuelos con la rafaga
# alineada con la pista, donde el avion no recibe carga lateral.
#
# Caso que lo motivo, encontrado por el piloto: SACC 145/12.5 racheado a 27 kt
# sobre la pista 140. El cruzado con rafaga es de 2.4 kt contra un maximo
# demostrado de 18, y aun asi saltaba "factor limitante" por un delta de +18 kt.
#
# El fundamento es el estatus de cada numero: `crosswind_max_kt` es un maximo
# DEMOSTRADO en certificacion y define un limite operativo; `gust_max_kt` es una
# referencia de operacion normal. Un veto —que no se compensa con nada— se apoya
# en un limite, no en una referencia.

from risk.soft_scoring import (
    GUST_CAUTION_FRACTION,
    GUST_NOGO_FACTOR,
    XWIND_CAUTION_FRACTION,
)


def _piso(xw_eff, xw_limit=18.0, gust=None, spd=None, gust_max=20.0):
    piso, motivo = conjunctive_floor(
        xw_eff_kt=xw_eff, xw_limit_kt=xw_limit,
        gust_kt=gust, spd_kt=spd, gust_max_kt=gust_max,
        r_fog=0.0, r_taf=0.0,
        xw_con_rafaga=gust is not None,
    )
    return piso, motivo


@pytest.mark.parametrize("delta", [10.0, 18.0, 32.0, 60.0])
def test_una_rafaga_alineada_con_la_pista_no_veta(delta):
    """
    Sin componente cruzado no hay nada que vetar, por grande que sea el delta.
    Es exactamente el caso que reporto el piloto.
    """
    piso, motivo = _piso(xw_eff=0.0, gust=12.0 + delta, spd=12.0)
    assert piso == "GO"
    assert "rafaga" not in motivo


def test_el_caso_real_del_piloto_no_marca_factor_limitante():
    """SACC 145/12.5 G27 sobre RWY 140: cruzado con rafaga 2.4 kt de 18."""
    perfil = get_profile("Pipistrel Alpha Trainer")
    piso, motivo = _piso(xw_eff=2.4, xw_limit=perfil.crosswind_max_kt,
                         gust=27.0, spd=12.5, gust_max=perfil.gust_max_kt)
    assert piso == "GO"
    assert motivo == ""


def test_la_misma_rafaga_SI_veta_cuando_carga_de_costado():
    """
    Lo que importa es la direccion, no la magnitud: el mismo viento contra una
    pista perpendicular tiene que vetar. Si no, se habria eliminado el veto en
    vez de corregirlo.
    """
    perfil = get_profile("Pipistrel Alpha Trainer")
    piso, motivo = _piso(xw_eff=27.0, xw_limit=perfil.crosswind_max_kt,
                         gust=27.0, spd=12.5, gust_max=perfil.gust_max_kt)
    assert piso == "NO GO"
    assert "cruzado" in motivo and "rafaga" in motivo


@pytest.mark.parametrize("fraccion,esperado", [
    (0.50, "GO"),
    (0.84, "GO"),
    (0.85, "CAUTION"),   # el corte lo pone el CRUZADO, no la rafaga
    (0.99, "CAUTION"),
    (1.00, "NO GO"),     # alcanzar el maximo demostrado veta
    (1.50, "NO GO"),
])
def test_la_escalera_la_marca_el_cruzado_de_la_rafaga(fraccion, esperado):
    """
    La rafaga sigue decidiendo el veredicto, pero a traves de `xw_eff_kt`, que
    es el cruzado calculado SOBRE ella: el peor instante que el avion encuentra.
    """
    limite = 18.0
    piso, _ = _piso(xw_eff=limite * fraccion, xw_limit=limite,
                    gust=30.0, spd=12.0)
    assert piso == esperado


def test_la_escala_sigue_siendo_relativa_a_cada_aeronave():
    """REGLA DE ALCANCE: el mismo cruzado pesa distinto segun el avion."""
    alpha = get_profile("Pipistrel Alpha Trainer").crosswind_max_kt   # 18 kt
    da40  = get_profile("Diamond DA40").crosswind_max_kt              # 20 kt
    xw = 18.0
    assert _piso(xw, alpha)[0] == "NO GO"      # alcanza su maximo
    assert _piso(xw, da40)[0]  == "CAUTION"    # 90% del suyo


def test_las_constantes_de_rafaga_ya_no_gobiernan_ningun_piso():
    """
    Se conservan porque `r_gust` y la bateria de escenarios necesitan una escala
    de referencia, pero cambiarlas no puede mover un veredicto: si lo moviera,
    la barrera habria vuelto a existir sin que nadie lo declare.
    """
    assert GUST_CAUTION_FRACTION > 0 and GUST_NOGO_FACTOR > 0
    for gm in (1.0, 20.0, 500.0):
        piso, _ = _piso(xw_eff=0.0, gust=80.0, spd=10.0, gust_max=gm)
        assert piso == "GO", f"la rafaga volvio a vetar con gust_max={gm}"


def test_la_referencia_normativa_tampoco_vota_por_rafaga():
    """
    La bateria dejo de votar por el delta crudo junto con el motor. Si votara,
    mediria el desacuerdo contra un criterio que el propio proyecto descarto.
    """
    import inspect

    from risk import scenarios

    fuente = inspect.getsource(scenarios.normative_label)
    assert "vote_gust" not in fuente, (
        "scenarios.normative_label volvio a votar por la rafaga cruda"
    )
    assert "crosswind_gust_kt" in fuente, (
        "el voto de viento tiene que seguir calculandose sobre la rafaga"
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


# ── El motivo distingue cruzado sostenido de cruzado con rafaga ───────────────
# Reporte del piloto: la tarjeta mostraba "Xwind 4.6 kt" y el cartel del factor
# limitante decia "viento cruzado 16 kt". Los dos numeros eran correctos —uno es
# el cruzado del viento sostenido y el otro el de la rafaga— pero nada lo decia,
# asi que parecian contradecirse.

def test_el_motivo_aclara_cuando_el_cruzado_es_con_rafaga():
    _, motivo = conjunctive_floor(
        xw_eff_kt=16.0, xw_limit_kt=18.0, gust_kt=16.1, spd_kt=4.7,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0, xw_con_rafaga=True,
    )
    assert "con rafaga" in motivo


def test_sin_rafaga_el_motivo_no_la_menciona():
    _, motivo = conjunctive_floor(
        xw_eff_kt=16.0, xw_limit_kt=18.0, gust_kt=None, spd_kt=None,
        gust_max_kt=20.0, r_fog=0.0, r_taf=0.0,
    )
    assert motivo and "con rafaga" not in motivo


def test_el_cruzado_que_veta_es_el_de_la_rafaga(weather):
    """
    Con viento sostenido flojo y rafaga fuerte casi perpendicular a la pista, el
    cruzado que decide es el de la RAFAGA. Es la razon por la que el umbral de
    CAUTION esta en el 85 % y no en la mitad: el valor ya es el peor caso
    instantaneo.
    """
    ac = get_profile("Pipistrel Alpha Trainer")
    w = weather(visibility_km=10.0, ceiling_ft=None, wind_dir=270,
                wind_spd_kt=4.0, wind_gust_kt=ac.crosswind_max_kt + 2.0,
                spread_c=9.0)
    res = compute_soft_score(w, runway_heading=360, aircraft=ac)
    assert res.decision == "NO GO"
    assert "con rafaga" in res.guardrail_reason
