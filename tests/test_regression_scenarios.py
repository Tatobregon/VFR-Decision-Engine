"""
test_regression_scenarios.py
============================
Test de regresion sobre la bateria de escenarios de referencia.

Es el test mas importante de la suite: fija el COMPORTAMIENTO DEL VEREDICTO. Si
un cambio futuro en pesos, umbrales, funciones r_i o en la barrera altera lo que
el sistema decide, estos tests fallan y obligan a justificar el cambio en vez de
descubrirlo en produccion.

Los numeros que verifica son los que se reportan en el documento de tesis
(concordancia con la norma y ausencia de sub-avisos), asi que tambien protegen la
consistencia entre lo escrito y lo que el codigo hace.
"""

import pytest

from risk.scenarios import (
    REFERENCE_SCENARIOS, default_weights, evaluate_battery,
    recombine, system_verdict, normative_label, _RANK,
)
from risk.weights import THRESHOLD_GO, THRESHOLD_CAUTION


@pytest.fixture(scope="module")
def bateria():
    """La bateria evaluada una sola vez para todo el modulo (es costosa)."""
    return evaluate_battery()


@pytest.fixture(scope="module")
def veredictos(bateria):
    w = default_weights()
    out = []
    for sc, comp, label in bateria:
        r = recombine(comp, w)
        out.append({
            "sid": sc.sid, "desc": sc.desc, "label": label,
            "r": r,
            "pred": system_verdict(sc, comp, r, THRESHOLD_GO, THRESHOLD_CAUTION),
        })
    return out


# ── Integridad de la bateria ──────────────────────────────────────────────────

def test_la_bateria_tiene_escenarios_y_todos_con_id_unico():
    assert len(REFERENCE_SCENARIOS) >= 30
    ids = [s.sid for s in REFERENCE_SCENARIOS]
    assert len(ids) == len(set(ids))


def test_la_bateria_cubre_varias_aeronaves():
    """La referencia no puede estar atada a un solo avion (regla de alcance)."""
    aviones = {s.aircraft for s in REFERENCE_SCENARIOS}
    assert len(aviones) >= 3


def test_la_etiqueta_normativa_es_independiente_del_score():
    """
    La etiqueta sale de la normativa, no del motor. Si dependiera del score, la
    concordancia seria una tautologia y no mediria nada.
    """
    for sc in REFERENCE_SCENARIOS:
        assert normative_label(sc) in {"GO", "CAUTION", "NO GO"}


# ── Los numeros que van al documento ──────────────────────────────────────────

def test_sin_sub_avisos(veredictos):
    """
    NINGUN escenario puede recibir un veredicto mas permisivo que el normativo.
    Es el criterio de seguridad del sistema: preferimos sobre-avisar.
    """
    sub = [v for v in veredictos if _RANK[v["pred"]] < _RANK[v["label"]]]
    assert sub == [], (
        "Sub-avisos detectados (el sistema avisa MENOS que la norma): "
        + "; ".join(f"{v['sid']} R={v['r']:.3f} {v['pred']}<{v['label']} :: {v['desc']}"
                    for v in sub)
    )


def test_concordancia_minima(veredictos):
    """La concordancia con la norma no debe caer por debajo del 90 %."""
    aciertos = sum(1 for v in veredictos if v["pred"] == v["label"])
    ratio = aciertos / len(veredictos)
    assert ratio >= 0.90, f"concordancia {ratio:.0%} ({aciertos}/{len(veredictos)})"


def test_los_showstoppers_siempre_dan_no_go(veredictos):
    """Todo escenario cuya etiqueta normativa es NO GO debe salir NO GO."""
    fallos = [v for v in veredictos if v["label"] == "NO GO" and v["pred"] != "NO GO"]
    assert fallos == [], (
        "Escenarios NO GO por norma que el sistema deja pasar: "
        + "; ".join(f"{v['sid']} -> {v['pred']}" for v in fallos)
    )


def test_el_score_siempre_esta_en_rango(veredictos):
    assert all(0.0 <= v["r"] <= 1.0 for v in veredictos)


# ── Robustez frente a los pesos ───────────────────────────────────────────────

def test_el_veredicto_es_estable_ante_perturbaciones_de_pesos(bateria):
    """
    Perturbar cada peso +/-20 % no debe cambiar mas del 15 % de los veredictos.
    Sostiene la afirmacion de robustez del analisis de sensibilidad.
    """
    from risk.sensitivity import perturb
    from risk.scenarios import WEIGHT_KEYS

    base_w = default_weights()
    base_v = [system_verdict(sc, comp, recombine(comp, base_w),
                             THRESHOLD_GO, THRESHOLD_CAUTION)
              for sc, comp, _ in bateria]

    total = cambios = 0
    for key in WEIGHT_KEYS:
        for factor in (0.8, 1.2):
            w = perturb(base_w, key, factor)
            for i, (sc, comp, _) in enumerate(bateria):
                v = system_verdict(sc, comp, recombine(comp, w),
                                   THRESHOLD_GO, THRESHOLD_CAUTION)
                total += 1
                if v != base_v[i]:
                    cambios += 1

    assert cambios / total <= 0.15, f"inestabilidad {cambios/total:.1%}"


def test_endurecer_los_umbrales_nunca_relaja_el_veredicto(bateria):
    """Bajar t_go solo puede hacer el sistema mas conservador, nunca menos."""
    w = default_weights()
    for sc, comp, _ in bateria:
        r = recombine(comp, w)
        normal = system_verdict(sc, comp, r, THRESHOLD_GO, THRESHOLD_CAUTION)
        estricto = system_verdict(sc, comp, r, THRESHOLD_GO - 0.05, THRESHOLD_CAUTION - 0.05)
        assert _RANK[estricto] >= _RANK[normal], f"{sc.sid}: {normal} -> {estricto}"
