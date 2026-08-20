"""
calibration.py
==============
Calibracion de los umbrales de decision (t_go, t_caution) por ANCLAJE NORMATIVO.

Que hace
--------
Sobre la bateria de escenarios de referencia (risk/scenarios.py), cuyo veredicto
"correcto" se deriva de la normativa ANAC/OACI + criterio aeronautico
(normative_label), busca el par de umbrales que hace que el sistema reproduzca
mejor esos veredictos. La busqueda usa una funcion de COSTO ASIMETRICO: quedarse
corto en el aviso (sub-aviso: el sistema dice menos que la norma) es un error
peligroso y se penaliza mucho mas que pasarse de conservador.

Que NO es
---------
No es calibracion empirica: no hay casos reales etiquetados por pilotos. Es
validez de constructo — los umbrales se fijan para que el score agregado
reproduzca la estructura de categorias de la regulacion. Se reporta como tal.

Importante
----------
La busqueda opera sobre el sistema COMPLETO (hard blockers + score compensatorio
+ barrera no-compensatoria). Por eso los umbrales solo mueven el veredicto en la
zona GRADUAL (visibilidad/techo), que es donde tiene sentido calibrarlos; los
showstoppers ya los resuelve la barrera conjuntiva.
"""

from typing import Dict, List, Tuple

try:
    from risk.scenarios import (
        REFERENCE_SCENARIOS, Scenario, default_weights, evaluate_battery,
        recombine, verdict, is_hard_blocked, _RANK, _worst,
    )
    from risk.weights import THRESHOLD_GO, THRESHOLD_CAUTION
except ImportError:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from risk.scenarios import (
        REFERENCE_SCENARIOS, Scenario, default_weights, evaluate_battery,
        recombine, verdict, is_hard_blocked, _RANK, _worst,
    )
    from risk.weights import THRESHOLD_GO, THRESHOLD_CAUTION


# ──────────────────────────────────────────────────────────────────────────────
# Costo asimetrico (predicho vs correcto). El sub-aviso pesa mas que el sobre-aviso.
# ──────────────────────────────────────────────────────────────────────────────
# Filas = veredicto del sistema, columnas = veredicto normativo.
#            GO    CAUTION  NO GO
#   GO       0      4        12     <- sub-avisos (peligrosos)
#   CAUTION  1      0         4
#   NO GO    3      1         0
_COST = {
    ("GO",      "GO"):      0, ("GO",      "CAUTION"): 4,  ("GO",      "NO GO"): 12,
    ("CAUTION", "GO"):      1, ("CAUTION", "CAUTION"): 0,  ("CAUTION", "NO GO"): 4,
    ("NO GO",   "GO"):      3, ("NO GO",   "CAUTION"): 1,  ("NO GO",   "NO GO"): 0,
}


# ──────────────────────────────────────────────────────────────────────────────
# Pre-computo de la bateria (una sola vez)
# ──────────────────────────────────────────────────────────────────────────────

def _precompute() -> List[dict]:
    """
    Devuelve, por escenario: R (pesos actuales), piso conjuntivo, hard-block y
    etiqueta normativa. Con esto el veredicto a cualquier umbral es inmediato.
    """
    w = default_weights()
    rows = []
    for sc, comp, label in evaluate_battery():
        rows.append({
            "sid":    sc.sid,
            "group":  sc.group,
            "desc":   sc.desc,
            "r":      recombine(comp, w),
            "floor":  comp.guardrail_floor,
            "hard":   is_hard_blocked(sc),
            "label":  label,
        })
    return rows


def _verdict_at(row: dict, t_go: float, t_caution: float) -> str:
    """Veredicto del sistema completo para un escenario a un par de umbrales."""
    if row["hard"]:
        return "NO GO"
    return _worst(verdict(row["r"], t_go, t_caution), row["floor"])


def _score_thresholds(rows: List[dict], t_go: float, t_caution: float) -> dict:
    """Metricas de un par de umbrales sobre toda la bateria."""
    cost = agree = dangerous = over = 0
    for row in rows:
        pred = _verdict_at(row, t_go, t_caution)
        cost += _COST[(pred, row["label"])]
        if pred == row["label"]:
            agree += 1
        elif _RANK[pred] < _RANK[row["label"]]:
            dangerous += 1
        else:
            over += 1
    return {"t_go": t_go, "t_caution": t_caution, "cost": cost,
            "agree": agree, "dangerous": dangerous, "over": over}


# ──────────────────────────────────────────────────────────────────────────────
# Grid search
# ──────────────────────────────────────────────────────────────────────────────

def grid_search(rows: List[dict]) -> Tuple[dict, List[dict]]:
    """
    Busca (t_go, t_caution) que minimiza el costo asimetrico.

    Desempate, en orden: menor costo -> menos sub-avisos peligrosos ->
    umbrales mas cercanos a los actuales (parsimonia/estabilidad).
    """
    candidates = []
    t = 5
    while t <= 45:                      # t_go de 0.05 a 0.45
        c = t + 2
        while c <= 75:                  # t_caution de t_go+0.02 a 0.75
            candidates.append(_score_thresholds(rows, t / 100.0, c / 100.0))
            c += 1
        t += 1

    def _key(m):
        return (m["cost"], m["dangerous"],
                abs(m["t_go"] - THRESHOLD_GO) + abs(m["t_caution"] - THRESHOLD_CAUTION))

    best = min(candidates, key=_key)
    return best, candidates


# ──────────────────────────────────────────────────────────────────────────────
# Reportes
# ──────────────────────────────────────────────────────────────────────────────

def _confusion(rows: List[dict], t_go: float, t_caution: float) -> Dict[Tuple[str, str], int]:
    order = ["GO", "CAUTION", "NO GO"]
    m = {(p, a): 0 for p in order for a in order}
    for row in rows:
        m[(_verdict_at(row, t_go, t_caution), row["label"])] += 1
    return m


def _print_confusion(title: str, rows: List[dict], t_go: float, t_caution: float) -> None:
    order = ["GO", "CAUTION", "NO GO"]
    m = _confusion(rows, t_go, t_caution)
    print(f"\n  {title}  (t_go={t_go:.2f}, t_caution={t_caution:.2f})")
    print(f"    {'sistema \\ norma':<16}" + "".join(f"{a:>9}" for a in order))
    for p in order:
        print(f"    {p:<16}" + "".join(f"{m[(p, a)]:>9}" for a in order))


if __name__ == "__main__":
    rows = _precompute()
    n = len(rows)

    print("=" * 84)
    print("  CALIBRACION DE UMBRALES POR ANCLAJE NORMATIVO ANAC/OACI")
    print(f"  Bateria: {n} escenarios de referencia | costo asimetrico (sub-aviso >> sobre-aviso)")
    print("=" * 84)

    # ── Estado actual ─────────────────────────────────────────────────────────
    cur = _score_thresholds(rows, THRESHOLD_GO, THRESHOLD_CAUTION)
    print(f"\n  UMBRALES ACTUALES  t_go={THRESHOLD_GO:.2f}  t_caution={THRESHOLD_CAUTION:.2f}")
    print(f"    concordancia={cur['agree']}/{n} ({100*cur['agree']/n:.0f}%)  "
          f"sub-avisos={cur['dangerous']}  sobre-avisos={cur['over']}  costo={cur['cost']}")

    # ── Busqueda ──────────────────────────────────────────────────────────────
    best, candidates = grid_search(rows)
    print(f"\n  UMBRALES OPTIMOS   t_go={best['t_go']:.2f}  t_caution={best['t_caution']:.2f}")
    print(f"    concordancia={best['agree']}/{n} ({100*best['agree']/n:.0f}%)  "
          f"sub-avisos={best['dangerous']}  sobre-avisos={best['over']}  costo={best['cost']}")

    # Rango de optimos equivalentes (mismo costo minimo) -> muestra robustez
    min_cost = best["cost"]
    equi = [m for m in candidates if m["cost"] == min_cost]
    tgo_lo = min(m["t_go"] for m in equi); tgo_hi = max(m["t_go"] for m in equi)
    tca_lo = min(m["t_caution"] for m in equi); tca_hi = max(m["t_caution"] for m in equi)
    print(f"    (optimo no es un punto sino un rango: t_go in [{tgo_lo:.2f},{tgo_hi:.2f}], "
          f"t_caution in [{tca_lo:.2f},{tca_hi:.2f}] dan el mismo costo minimo)")

    # ── Matrices de confusion ─────────────────────────────────────────────────
    _print_confusion("ANTES", rows, THRESHOLD_GO, THRESHOLD_CAUTION)
    _print_confusion("DESPUES", rows, best["t_go"], best["t_caution"])

    # ── Curva de operacion: barrido de t_go (t_caution en el optimo) ──────────
    print(f"\n  CURVA DE OPERACION — barrido de t_go (t_caution={best['t_caution']:.2f} fijo)")
    print(f"    {'t_go':>6}{'concord.':>10}{'sub-aviso':>11}{'sobre':>7}{'costo':>7}")
    for tg in [round(x * 0.01, 2) for x in range(10, 31, 2)]:
        if tg >= best["t_caution"]:
            continue
        m = _score_thresholds(rows, tg, best["t_caution"])
        mark = "  <-- optimo" if abs(tg - best["t_go"]) < 1e-9 else ""
        print(f"    {tg:>6.2f}{m['agree']:>7}/{n}{m['dangerous']:>11}{m['over']:>7}{m['cost']:>7}{mark}")

    # ── Escenarios que siguen en desacuerdo con el optimo ─────────────────────
    print(f"\n  DESACUERDOS RESTANTES con umbrales optimos:")
    any_left = False
    for row in rows:
        pred = _verdict_at(row, best["t_go"], best["t_caution"])
        if pred != row["label"]:
            any_left = True
            kind = "SUB-AVISO" if _RANK[pred] < _RANK[row["label"]] else "sobre-aviso"
            print(f"    {row['sid']:<4} R={row['r']:.3f}  sistema={pred:<8} norma={row['label']:<8} "
                  f"[{kind}] {row['desc']}")
    if not any_left:
        print("    (ninguno — concordancia total)")

    print("\n" + "=" * 84)
    print("  Nota: los umbrales son validez de CONSTRUCTO (reproducen la normativa),")
    print("  no validez empirica. La calibracion con juicio de pilotos sobre casos")
    print("  reales queda como trabajo futuro (ver documento).")
    print("=" * 84)
