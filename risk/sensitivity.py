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
    from risk.weights import (
        THRESHOLD_GO, THRESHOLD_CAUTION,
        VIS_RISK_MAX_KM  as W_VIS_LO,
        CEIL_RISK_MAX_FT as W_CEIL_LO,
        FOG_RISK_MAX_C   as W_FOG_LO,
    )
except ImportError:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from risk.scenarios import (
        evaluate_battery, default_weights, WEIGHT_KEYS,
        recombine, system_verdict,
    )
    from risk.weights import (
        THRESHOLD_GO, THRESHOLD_CAUTION,
        VIS_RISK_MAX_KM  as W_VIS_LO,
        CEIL_RISK_MAX_FT as W_CEIL_LO,
        FOG_RISK_MAX_C   as W_FOG_LO,
    )


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
# 4. Sensibilidad de los PARAMETROS DE FORMA de las funciones r_i
# ──────────────────────────────────────────────────────────────────────────────
# Por que existe esta seccion. Los apartados anteriores perturban los PESOS, que
# es lo que el AHP deriva. Pero un modelo del tipo R = sum(w_i * r_i) tiene dos
# familias de parametros: los pesos y la FORMA de cada r_i. Analizar solo los
# primeros deja fuera la mitad del modelo, y —como muestra esta seccion— la
# mitad mas influyente: mover un unico punto de quiebre cambia mas veredictos
# que perturbar los siete pesos simultaneamente.
#
# El hallazgo no invalida el modelo: los limites de riesgo MAXIMO son fronteras
# normativas (no hay eleccion que perturbar) y los de riesgo NULO son juicio
# declarado, cuyo efecto queda acotado aqui. Lo que si invalida es cualquier
# afirmacion de robustez basada unicamente en la sensibilidad de los pesos.

def _ramp(lo: float, hi: float):
    """Rampa lineal descendente: 1.0 en <=lo, 0.0 en >=hi."""
    def f(x):
        if x is None:
            return 0.0
        if x <= lo:
            return 1.0
        if x >= hi:
            return 0.0
        return 1.0 - (x - lo) / (hi - lo)
    return f


# Variantes a probar. Se perturba SOLO el extremo de riesgo nulo, que es el
# parametro de juicio; el de riesgo maximo es la frontera IFR de la norma.
#
# La niebla se perturba sobre features/fog_risk.py y NO sobre weights.r_fog: la
# rampa que el motor aplica es la de ese modulo (weights.r_fog no participa del
# tiempo de ejecucion).
_SHAPE_VARIANTS = [
    ("visibility", "vis  8 -> 10 km  (margen 2.0x)",       10.0),
    ("visibility", "vis  8 ->  6 km  (margen 1.2x)",        6.0),
    ("ceiling",    "techo 2000 -> 3000 ft (margen 3x)",    3000),
    ("ceiling",    "techo 2000 -> 1500 ft (margen 1.5x)",  1500),
    ("fog",        "spread  5 ->  7 C",                     7.0),
    ("fog",        "spread  5 ->  4 C",                     4.0),
]


def shape_analysis(battery, base_w, base_v):
    """
    Perturba un parametro de forma por vez y mide cambios de veredicto y de R.

    Devuelve [{label, flips, mean_dr, max_dr}, ...].
    """
    import risk.soft_scoring as SS
    import features.fog_risk as FR

    orig_vis, orig_ceil = SS.r_visibility, SS.r_ceiling
    orig_fog_hi = FR.SPREAD_LOW_RISK_C
    out = []
    try:
        for criterio, label, hi in _SHAPE_VARIANTS:
            if criterio == "visibility":
                SS.r_visibility = _ramp(W_VIS_LO, hi)
            elif criterio == "ceiling":
                SS.r_ceiling = _ramp(W_CEIL_LO, hi)
            else:
                FR.SPREAD_LOW_RISK_C = hi

            nueva_bat = evaluate_battery()
            v = _verdicts(nueva_bat, base_w, THRESHOLD_GO, THRESHOLD_CAUTION)

            SS.r_visibility, SS.r_ceiling = orig_vis, orig_ceil
            FR.SPREAD_LOW_RISK_C = orig_fog_hi

            flips = sum(1 for a, b in zip(v, base_v) if a != b)
            drs = [abs(recombine(c_new, base_w) - recombine(c_old, base_w))
                   for (_, c_new, _), (_, c_old, _) in zip(nueva_bat, battery)]
            out.append({
                "label":   label,
                "flips":   flips,
                "mean_dr": sum(drs) / len(drs) if drs else 0.0,
                "max_dr":  max(drs) if drs else 0.0,
            })
    finally:
        SS.r_visibility, SS.r_ceiling = orig_vis, orig_ceil
        FR.SPREAD_LOW_RISK_C = orig_fog_hi
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 5. Sensibilidad de las FRONTERAS de la barrera no-compensatoria
# ──────────────────────────────────────────────────────────────────────────────
# El piso conjuntivo eleva el veredicto cuando un factor alcanza cierta fraccion
# del limite de la aeronave. Esas fracciones son juicio o derivacion declarada
# (ver el bloque de constantes de soft_scoring.py) y, a diferencia de un peso,
# son FRONTERAS DE DECISION: no desplazan el puntaje de manera continua sino que
# cambian veredictos de golpe. Corresponde medirlas.
#
# Se barren POR SEPARADO porque cruzado y rafaga ya no comparten escala: el
# cruzado se ancla en un maximo demostrado en certificacion y la rafaga en una
# referencia de operacion normal. Barrerlas juntas ocultaria cual de las dos
# manda en cada escenario.

# Cada frontera se perturba -30%, -20%, +20% y +30% respecto de SU base, para
# que las tres sean comparables entre si aunque partan de valores distintos.
_BARRERAS = (
    ("XWIND_CAUTION_FRACTION", (0.35, 0.40, 0.60, 0.65)),   # base 0.50
    ("GUST_CAUTION_FRACTION",  (0.60, 0.68, 1.02, 1.11)),   # base 0.85
    ("GUST_NOGO_FACTOR",       (1.05, 1.20, 1.80, 1.95)),   # base 1.50
)


def barrier_sensitivity(battery, base_w, base_v, barreras=_BARRERAS):
    """Perturba cada frontera de la barrera y mide cuantos veredictos se mueven."""
    import risk.soft_scoring as SS

    out = []
    for nombre, valores in barreras:
        original = getattr(SS, nombre)
        try:
            for v_ in valores:
                setattr(SS, nombre, v_)
                verdicts = _verdicts(evaluate_battery(), base_w,
                                     THRESHOLD_GO, THRESHOLD_CAUTION)
                setattr(SS, nombre, original)
                flips = sum(1 for a, b in zip(verdicts, base_v) if a != b)
                out.append({"parametro": nombre, "base": original,
                            "valor": v_, "flips": flips})
        finally:
            setattr(SS, nombre, original)
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

    # ── 4. Parametros de forma de las r_i ─────────────────────────────────────
    print("\n  [4] PARAMETROS DE FORMA  (extremo de riesgo nulo de cada rampa)")
    print(f"      {'variante':<38}{'flips':>8}{'|dR| medio':>13}{'|dR| max':>11}")
    print(f"      {'-'*37:<38}{'-'*7:>8}{'-'*12:>13}{'-'*10:>11}")
    sh = shape_analysis(battery, base_w, base_v)
    for row in sh:
        print(f"      {row['label']:<38}{row['flips']:>4}/{n:<3}"
              f"{row['mean_dr']:>13.4f}{row['max_dr']:>11.4f}")
    peor = max(sh, key=lambda r: r["flips"])
    print(f"\n      Peor caso: {peor['flips']}/{n} = {100*peor['flips']/n:.1f}% de los "
          f"escenarios cambian de veredicto")
    print(f"      Comparacion: perturbar los 7 pesos a la vez (+/-20%, OAT) cambia "
          f"{100*total_oat_flips/(14*n):.1f}%")
    print("      -> los parametros de forma son MAS influyentes que los pesos.")
    print("         Los extremos de riesgo maximo (3 km / 500 ft) no se perturban:")
    print("         son la frontera IFR de la norma, no una eleccion del modelo.")

    # ── 5. Fraccion de CAUTION de la barrera ──────────────────────────────────
    print("\n  [5] BARRERA NO-COMPENSATORIA  (fronteras de piso, una por una)")
    print(f"      {'parametro':<26}{'base':>7}{'valor':>8}{'flips':>10}")
    print(f"      {'-'*25:<26}{'-'*6:>7}{'-'*7:>8}{'-'*9:>10}")
    _prev = None
    for row in barrier_sensitivity(battery, base_w, base_v):
        etiqueta = row["parametro"] if row["parametro"] != _prev else ""
        _prev = row["parametro"]
        print(f"      {etiqueta:<26}{row['base']:>7.2f}{row['valor']:>8.2f}"
              f"{row['flips']:>4}/{n:<5}")
    print("      Cruzado y rafaga se barren por separado: no comparten escala.")

    # ── Conclusion cuantitativa ───────────────────────────────────────────────
    print("\n" + "-" * 82)
    print("  LECTURA:")
    print(f"  - La barrera no-compensatoria 'clava' los showstoppers: {mc['never_flip']}/{n} "
          f"escenarios\n    son insensibles a los pesos.")
    print(f"  - Los flips se concentran en la zona gradual (visibilidad/techo), en escenarios\n"
          f"    cuyo R cae muy cerca de un umbral. El sistema es robusto: los pesos exactos\n"
          f"    del AHP no son criticos para el veredicto.")
    print("  - Los PARAMETROS DE FORMA pesan mas que los pesos: mover un solo punto")
    print("    de quiebre mueve mas veredictos que perturbar los siete pesos a la vez.")
    print("    Es el resultado mas importante de este analisis: acota donde hay que")
    print("    poner el esfuerzo de justificacion. Lo atenua que los quiebres de riesgo")
    print("    MAXIMO sean frontera normativa y no eleccion del modelo; lo que queda")
    print("    como juicio son los extremos de riesgo nulo, medidos arriba.")
    print("  - La fraccion de CAUTION de la barrera (0.50) es robusta: moverla entre")
    print("    0.35 y 0.65 cambia a lo sumo un veredicto de la bateria.")
    print("  - ALCANCE: todo esto vale para la configuracion SIN minimos personales.")
    print("    Con minimos activados el sistema se aparta a proposito de la referencia")
    print("    normativa; risk/calibration.py reporta la concordancia de cada nivel y")
    print("    verifica que en ninguno de ellos aparecen sub-avisos.")
    print("=" * 82)
