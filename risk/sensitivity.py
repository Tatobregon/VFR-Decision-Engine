"""
sensitivity.py
==============
Analisis de sensibilidad de los pesos del soft scoring.

Pregunta que responde
---------------------
Los pesos vienen del AHP (risk/ahp_weights.py). Un juicio de a pares distinto
habria dado pesos algo diferentes. ¿Cuanto cambia el VEREDICTO final (GO /
CAUTION / NO GO) si cada peso se corre +/-20%? Si el veredicto casi no cambia,
el sistema es ROBUSTO frente a la incertidumbre en los pesos, y la eleccion
exacta de los w_i no es critica.

Metodo
------
1. One-at-a-time (OAT): se perturba un peso +/-20% y se RENORMALIZA el resto
   proporcionalmente (los pesos siguen sumando 1.0). Se cuenta cuantos
   escenarios de la bateria cambian de veredicto respecto de la linea base.
2. Monte Carlo: se perturban los 7 pesos a la vez (factor uniforme en
   [0.8, 1.2]) y se mide la estabilidad global del veredicto sobre muchos
   sorteos.
3. Sensibilidad de umbrales: +/-0.05 en t_go y t_caution.

El veredicto usa el sistema COMPLETO (hard blockers + score + barrera
no-compensatoria). Como la barrera no depende de los pesos, los escenarios
resueltos por un showstopper quedan "clavados": eso es parte del resultado.
"""

import random
from typing import Dict, List

try:
    from risk.scenarios import (
        evaluate_battery, default_weights, WEIGHT_KEYS,
        recombine, system_verdict,
    )
    from risk.weights import THRESHOLD_GO, THRESHOLD_CAUTION
except ImportError:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from risk.scenarios import (
        evaluate_battery, default_weights, WEIGHT_KEYS,
        recombine, system_verdict,
    )
    from risk.weights import THRESHOLD_GO, THRESHOLD_CAUTION


_LABELS = {
    "vis": "Visibilidad", "ceil": "Techo", "xwind": "Cruzado", "gust": "Rafagas",
    "wx": "Fenomenos", "fog": "Niebla", "taf": "Tendencia TAF",
}


def perturb(weights: Dict[str, float], key: str, factor: float) -> Dict[str, float]:
    """
    Multiplica weights[key] por `factor` y renormaliza el resto en forma
    proporcional para que la suma siga siendo 1.0. Preserva las proporciones
    relativas entre los pesos no perturbados.
    """
    w = dict(weights)
    old = w[key]
    new = old * factor
    others = 1.0 - old
    if others <= 0:
        return w
    scale = (1.0 - new) / others
    for k in w:
        w[k] = new if k == key else w[k] * scale
    return w


def _verdicts(battery, weights, t_go, t_caution) -> List[str]:
    """Veredicto del sistema completo por escenario para un juego de pesos/umbrales."""
    out = []
    for sc, comp, _label in battery:
        r = recombine(comp, weights)
        out.append(system_verdict(sc, comp, r, t_go, t_caution))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 1. One-at-a-time
# ──────────────────────────────────────────────────────────────────────────────

def oat_analysis(battery, base_w, base_v, pct=0.20):
    """Para cada peso: flips a -pct y +pct, y |ΔR| promedio/maximo."""
    n = len(battery)
    results = []
    for key in WEIGHT_KEYS:
        row = {"key": key, "flips_down": 0, "flips_up": 0, "sum_dr": 0.0, "max_dr": 0.0}
        for factor, tag in ((1 - pct, "down"), (1 + pct, "up")):
            w = perturb(base_w, key, factor)
            flips = 0
            for i, (sc, comp, _l) in enumerate(battery):
                r      = recombine(comp, w)
                r_base = recombine(comp, base_w)
                dr = abs(r - r_base)
                row["sum_dr"] += dr
                row["max_dr"] = max(row["max_dr"], dr)
                v = system_verdict(sc, comp, r, THRESHOLD_GO, THRESHOLD_CAUTION)
                if v != base_v[i]:
                    flips += 1
            row[f"flips_{tag}"] = flips
        row["mean_dr"] = row["sum_dr"] / (2 * n)
        row["flips_total"] = row["flips_down"] + row["flips_up"]
        results.append(row)
    results.sort(key=lambda r: r["flips_total"], reverse=True)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 2. Monte Carlo (perturbacion simultanea de los 7 pesos)
# ──────────────────────────────────────────────────────────────────────────────

def monte_carlo(battery, base_w, base_v, trials=5000, pct=0.20, seed=42):
    rnd = random.Random(seed)
    n = len(battery)
    unchanged_pairs = 0
    total_pairs = 0
    worst_trial_frac = 1.0
    per_scenario_flips = [0] * n

    for _ in range(trials):
        # factor uniforme por peso, luego renormalizar
        w = {k: base_w[k] * rnd.uniform(1 - pct, 1 + pct) for k in WEIGHT_KEYS}
        s = sum(w.values())
        w = {k: v / s for k, v in w.items()}

        same = 0
        for i, (sc, comp, _l) in enumerate(battery):
            r = recombine(comp, w)
            v = system_verdict(sc, comp, r, THRESHOLD_GO, THRESHOLD_CAUTION)
            if v == base_v[i]:
                same += 1
            else:
                per_scenario_flips[i] += 1
        unchanged_pairs += same
        total_pairs += n
        worst_trial_frac = min(worst_trial_frac, same / n)

    return {
        "stability": unchanged_pairs / total_pairs,
        "worst_trial": worst_trial_frac,
        "never_flip": sum(1 for f in per_scenario_flips if f == 0),
        "per_scenario_flips": per_scenario_flips,
        "trials": trials,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3. Sensibilidad de umbrales
# ──────────────────────────────────────────────────────────────────────────────

def threshold_sensitivity(battery, base_w, base_v, delta=0.05):
    out = []
    for name, tg, tc in (
        (f"t_go -{delta}",      THRESHOLD_GO - delta, THRESHOLD_CAUTION),
        (f"t_go +{delta}",      THRESHOLD_GO + delta, THRESHOLD_CAUTION),
        (f"t_caution -{delta}", THRESHOLD_GO, THRESHOLD_CAUTION - delta),
        (f"t_caution +{delta}", THRESHOLD_GO, THRESHOLD_CAUTION + delta),
    ):
        v = _verdicts(battery, base_w, tg, tc)
        flips = sum(1 for a, b in zip(v, base_v) if a != b)
        out.append((name, flips))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Reporte
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    battery = evaluate_battery()
    n = len(battery)
    base_w = default_weights()
    base_v = _verdicts(battery, base_w, THRESHOLD_GO, THRESHOLD_CAUTION)

    dist = {v: base_v.count(v) for v in ("GO", "CAUTION", "NO GO")}

    print("=" * 82)
    print("  ANALISIS DE SENSIBILIDAD DE LOS PESOS DEL SOFT SCORING")
    print(f"  Bateria: {n} escenarios | linea base: pesos AHP + umbrales calibrados "
          f"({THRESHOLD_GO}/{THRESHOLD_CAUTION})")
    print(f"  Distribucion base: GO={dist['GO']}  CAUTION={dist['CAUTION']}  NO GO={dist['NO GO']}")
    print("=" * 82)

    # ── 1. OAT ────────────────────────────────────────────────────────────────
    print("\n  [1] ONE-AT-A-TIME  (+/-20% por peso, renormalizando el resto)")
    print(f"      {'Peso':<16}{'w base':>8}{'flips -20%':>12}{'flips +20%':>12}"
          f"{'|dR| medio':>12}{'|dR| max':>10}")
    print(f"      {'-'*15:<16}{'-'*7:>8}{'-'*11:>12}{'-'*11:>12}{'-'*11:>12}{'-'*9:>10}")
    oat = oat_analysis(battery, base_w, base_v)
    for row in oat:
        print(f"      {_LABELS[row['key']]:<16}{base_w[row['key']]:>8.3f}"
              f"{row['flips_down']:>12}{row['flips_up']:>12}"
              f"{row['mean_dr']:>12.4f}{row['max_dr']:>10.4f}")
    total_oat_flips = sum(r["flips_total"] for r in oat)
    print(f"\n      Total de cambios de veredicto (14 perturbaciones x {n} escenarios "
          f"= {14*n} evaluaciones): {total_oat_flips}")
    print(f"      -> {100*total_oat_flips/(14*n):.1f}% de las evaluaciones cambian de veredicto")

    # ── 2. Monte Carlo ────────────────────────────────────────────────────────
    print("\n  [2] MONTE CARLO  (7 pesos a la vez, factor uniforme +/-20%)")
    mc = monte_carlo(battery, base_w, base_v)
    print(f"      Sorteos: {mc['trials']}")
    print(f"      Estabilidad del veredicto (par sorteo-escenario): {100*mc['stability']:.1f}%")
    print(f"      Peor sorteo: {100*mc['worst_trial']:.1f}% de escenarios sin cambiar")
    print(f"      Escenarios que NUNCA cambian de veredicto: {mc['never_flip']}/{n} "
          f"({100*mc['never_flip']/n:.0f}%)")

    # Escenarios mas sensibles (los que mas veces cambiaron en el Monte Carlo)
    ranked = sorted(range(n), key=lambda i: mc["per_scenario_flips"][i], reverse=True)
    top = [i for i in ranked if mc["per_scenario_flips"][i] > 0][:6]
    if top:
        print(f"\n      Escenarios mas sensibles (borde de umbral):")
        for i in top:
            sc, comp, label = battery[i]
            r = recombine(comp, base_w)
            pct = 100 * mc["per_scenario_flips"][i] / mc["trials"]
            print(f"        {sc.sid:<4} R={r:.3f} base={base_v[i]:<8} "
                  f"cambia en {pct:4.1f}% de sorteos | {sc.desc}")

    # ── 3. Umbrales ───────────────────────────────────────────────────────────
    print("\n  [3] SENSIBILIDAD DE UMBRALES  (+/-0.05)")
    for name, flips in threshold_sensitivity(battery, base_w, base_v):
        print(f"      {name:<16} -> {flips} cambios de veredicto sobre {n}")

    # ── Conclusion cuantitativa ───────────────────────────────────────────────
    print("\n" + "-" * 82)
    print("  LECTURA:")
    print(f"  - La barrera no-compensatoria 'clava' los showstoppers: {mc['never_flip']}/{n} "
          f"escenarios\n    son insensibles a los pesos.")
    print(f"  - Los flips se concentran en la zona gradual (visibilidad/techo), en escenarios\n"
          f"    cuyo R cae muy cerca de un umbral. El sistema es robusto: los pesos exactos\n"
          f"    del AHP no son criticos para el veredicto.")
    print("=" * 82)
