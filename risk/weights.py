"""
weights.py
==========
Pesos y funciones r_i del soft scoring.

El score total se calcula como:
    R_total = sum(w_i * r_i)  +  delta_orografico

Cada r_i es una funcion de riesgo en [0, 1]:
  - 0.0 = sin riesgo para esa componente
  - 1.0 = riesgo maximo (condicion en o mas alla del limite operacional)

Los pesos suman 1.0. El delta orografico se aplica DESPUES del weighted sum.

Los pesos fueron derivados por AHP (Analytic Hierarchy Process) a partir de
comparaciones de a pares fundamentadas en accidentologia de aviacion general,
no fijados a ojo. La derivacion completa (jerarquia, matrices, autovector y
razon de consistencia CR) es reproducible en risk/ahp_weights.py.

Pesos AHP (CR global = 0.063, aceptable por ser <= 0.10):
  Visibilidad  | vis_km      | 0.279 | Rampa: 1 si vis<3km, 0 si vis>8km
  Ceiling      | ceil_ft     | 0.279 | Rampa: 1 si ceil<500ft, 0 si ceil>2000ft
  Crosswind    | xw_kt       | 0.179 | Lineal: xw / xw_max
  Rafagas      | gust-spd kt | 0.090 | Lineal: delta / gust_max
  Fenomenos    | wx_codes    | 0.078 | Escalonado por severidad
  Niebla proxy | spread_c    | 0.056 | Rampa: 1 si spread<2C, 0 si spread>5C
  Riesgo TAF   | PROB/TEMPO  | 0.039 | Escalonado (calculado en taf_window.py)
"""

from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Pesos del scoring — derivados por AHP (ver risk/ahp_weights.py)
# ──────────────────────────────────────────────────────────────────────────────
# Redondeados a 3 decimales por el metodo del resto mayor (largest remainder)
# para que sumen exactamente 1.000. Valores exactos del autovector AHP:
#   vis .2793  ceil .2793  xwind .1789  gust .0895  wx .0781  fog .0559  taf .0391

W_VIS   = 0.279
W_CEIL  = 0.279
W_XWIND = 0.179
W_GUST  = 0.090
W_WX    = 0.078
W_FOG   = 0.056
W_TAF   = 0.039

assert abs(W_VIS + W_CEIL + W_XWIND + W_GUST + W_WX + W_FOG + W_TAF - 1.0) < 1e-9, \
    "Los pesos deben sumar 1.0"

# Thresholds de decision
THRESHOLD_GO      = 0.25   # R < 0.25       → GO
THRESHOLD_CAUTION = 0.50   # 0.25 <= R < 0.50 → CAUTION
                           # R >= 0.50      → NO GO


# ──────────────────────────────────────────────────────────────────────────────
# Funciones r_i
# ──────────────────────────────────────────────────────────────────────────────

def r_visibility(vis_km: Optional[float]) -> float:
    """
    Score de riesgo por visibilidad.

    Rampa lineal:
      vis <= 3.0 km  → 1.0  (IFR/LIFR, bien por debajo del minimo VFR)
      vis >= 8.0 km  → 0.0  (excelente visibilidad)
      None           → 0.0  (sin dato: tratado como permisivo)
    """
    if vis_km is None:
        return 0.0
    if vis_km <= 3.0:
        return 1.0
    if vis_km >= 8.0:
        return 0.0
    return 1.0 - (vis_km - 3.0) / (8.0 - 3.0)


def r_ceiling(ceil_ft: Optional[int]) -> float:
    """
    Score de riesgo por techo de nubes.

    Rampa lineal:
      ceil <= 500 ft  → 1.0  (muy bajo, IFR)
      ceil >= 2000 ft → 0.0  (holgado para VFR)
      None            → 0.0  (CLR o FEW/SCT: sin techo efectivo)
    """
    if ceil_ft is None:
        return 0.0
    if ceil_ft <= 500:
        return 1.0
    if ceil_ft >= 2000:
        return 0.0
    return 1.0 - (ceil_ft - 500) / (2000 - 500)


def r_crosswind(xw_kt: float, xw_max_kt: float) -> float:
    """
    Score de riesgo por viento cruzado.

    Lineal: xw_kt / xw_max_kt, clampeado a [0, 1].
    xw_max_kt es el crosswind_max_kt del perfil de aeronave (12 kt Alpha Trainer).
    """
    if xw_max_kt <= 0:
        return 1.0
    return min(xw_kt / xw_max_kt, 1.0)


def r_gust(
    gust_kt : Optional[float],
    spd_kt  : Optional[float],
    max_delta_kt: float,
) -> float:
    """
    Score de riesgo por variabilidad de viento (rafagas).

    Variable: delta = gust_kt - spd_kt (la variabilidad, no el valor absoluto).
    Lineal: delta / max_delta_kt, clampeado a [0, 1].

    max_delta_kt = gust_max_kt del perfil de aeronave (20 kt Alpha Trainer).
    Sin rafagas (gust_kt None o <= spd_kt) → 0.0.
    """
    if gust_kt is None or spd_kt is None:
        return 0.0
    delta = gust_kt - spd_kt
    if delta <= 0:
        return 0.0
    if max_delta_kt <= 0:
        return 1.0
    return min(delta / max_delta_kt, 1.0)


def r_wx_codes(wx_codes: list) -> float:
    """
    Score de riesgo por fenomenos meteorologicos.

    Escalonado por severidad. Devuelve el score mas alto de los fenomenos
    presentes. Matching exacto por token (mismo enfoque que fog_risk.py).

    Los hard blockers (TS, TSRA, GR, etc.) obtienen 1.0 aqui tambien,
    pero ya deben haber sido atrapados por hard_blockers.py antes de llegar
    al soft scoring.
    """
    if not wx_codes:
        return 0.0

    max_score = 0.0
    for code in wx_codes:
        score = _WX_SEVERITY.get(code.strip().upper(), 0.0)
        max_score = max(max_score, score)
    return max_score


def r_fog(spread_c: Optional[float]) -> float:
    """
    Score de riesgo por niebla basado en el spread termico (T - Td).

    Rampa lineal:
      spread <= 2 C → 1.0  (niebla inminente)
      spread >= 5 C → 0.0  (sin riesgo de niebla)
      None          → 0.0  (sin dato: sin penalizacion)

    Nota: este score cubre solo la componente de spread.
    Los tokens wx (FG, BR) son evaluados por r_wx_codes y tambien
    por fog_risk.compute_fog_risk que el soft_scoring.py puede usar
    directamente para mayor precision.
    """
    if spread_c is None:
        return 0.0
    if spread_c <= 2.0:
        return 1.0
    if spread_c >= 5.0:
        return 0.0
    return 1.0 - (spread_c - 2.0) / (5.0 - 2.0)


def apply_decision_threshold(r_total: float) -> str:
    """
    Convierte R_total en la decision final GO / CAUTION / NO GO.

    Thresholds (conservadores, pendientes calibracion con datos reales):
      R < 0.25           → GO
      0.25 <= R < 0.50   → CAUTION
      R >= 0.50          → NO GO
    """
    if r_total < THRESHOLD_GO:
        return "GO"
    if r_total < THRESHOLD_CAUTION:
        return "CAUTION"
    return "NO GO"


# ──────────────────────────────────────────────────────────────────────────────
# Tabla de severidad de fenomenos wx
# ──────────────────────────────────────────────────────────────────────────────

_WX_SEVERITY = {
    # Hard blockers: siempre 1.0 (deben ser atrapados antes por hard_blockers.py)
    "TS"    : 1.00,
    "TSRA"  : 1.00,
    "TSGR"  : 1.00,
    "GR"    : 1.00,
    "FC"    : 1.00,
    "VA"    : 1.00,
    "FZRA"  : 1.00,
    "FZDZ"  : 1.00,
    # Precipitacion engelante (peligrosa aunque no este en lista de hard blockers)
    "FZFG"  : 0.90,
    "FZSN"  : 0.80,
    # Precipitacion intensa
    "+RA"   : 0.70,
    "+SN"   : 0.70,
    "+DZ"   : 0.60,
    "+RASN" : 0.65,
    # Pellets de hielo y granizo pequeno
    "PL"    : 0.65,
    "GS"    : 0.60,
    # Precipitacion moderada
    "SN"    : 0.55,
    "RASN"  : 0.45,
    "RA"    : 0.45,
    "DZ"    : 0.30,
    # Precipitacion leve
    "-SN"   : 0.35,
    "-RASN" : 0.25,
    "-RA"   : 0.20,
    "-DZ"   : 0.15,
    # Niebla (vis reducida)
    "FG"    : 0.65,
    "BCFG"  : 0.45,
    "MIFG"  : 0.35,
    "PRFG"  : 0.35,
    # Reduccion de visibilidad (no precipitacion)
    "BR"    : 0.20,
    "HZ"    : 0.10,
    "FU"    : 0.15,
    "SA"    : 0.15,
    "DU"    : 0.15,
    "SQ"    : 0.50,   # squall line
    "SS"    : 0.70,   # sandstorm
    "DS"    : 0.70,   # duststorm
}


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: weights.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    print(f"\n  Pesos: VIS={W_VIS} CEIL={W_CEIL} XWIND={W_XWIND} "
          f"GUST={W_GUST} WX={W_WX} FOG={W_FOG} TAF={W_TAF}")
    print(f"  Suma de pesos: {W_VIS+W_CEIL+W_XWIND+W_GUST+W_WX+W_FOG+W_TAF:.2f}")

    print("\n  -- r_visibility --")
    check("vis=None -> 0.0",        r_visibility(None) == 0.0)
    check("vis=10.0 -> 0.0",        r_visibility(10.0) == 0.0)
    check("vis=8.0 -> 0.0 (limite)",r_visibility(8.0)  == 0.0)
    check("vis=3.0 -> 1.0 (limite)",r_visibility(3.0)  == 1.0)
    check("vis=0.5 -> 1.0",         r_visibility(0.5)  == 1.0)
    check("vis=5.5 -> 0.5 (medio)", abs(r_visibility(5.5) - 0.5) < 0.001)
    check("vis en [0,1] para vis=4", 0 <= r_visibility(4.0) <= 1)

    print("\n  -- r_ceiling --")
    check("ceil=None -> 0.0 (CLR)", r_ceiling(None) == 0.0)
    check("ceil=5000 -> 0.0",       r_ceiling(5000) == 0.0)
    check("ceil=2000 -> 0.0",       r_ceiling(2000) == 0.0)
    check("ceil=500  -> 1.0",       r_ceiling(500)  == 1.0)
    check("ceil=100  -> 1.0",       r_ceiling(100)  == 1.0)
    check("ceil=1250 -> 0.5 (medio)",abs(r_ceiling(1250) - 0.5) < 0.001)

    print("\n  -- r_crosswind --")
    check("xw=0   -> 0.0",          r_crosswind(0.0, 12.0) == 0.0)
    check("xw=12  -> 1.0 (limite)", r_crosswind(12.0, 12.0) == 1.0)
    check("xw=6   -> 0.5",          abs(r_crosswind(6.0, 12.0) - 0.5) < 0.001)
    check("xw=15  -> 1.0 (clamped)",r_crosswind(15.0, 12.0) == 1.0)

    print("\n  -- r_gust --")
    check("sin rafaga -> 0.0",      r_gust(None, 10.0, 20.0) == 0.0)
    check("delta=0    -> 0.0",      r_gust(10.0, 10.0, 20.0) == 0.0)
    check("delta=10   -> 0.5",      abs(r_gust(20.0, 10.0, 20.0) - 0.5) < 0.001)
    check("delta=20   -> 1.0",      r_gust(30.0, 10.0, 20.0) == 1.0)
    check("delta=25   -> 1.0 (clamp)", r_gust(35.0, 10.0, 20.0) == 1.0)

    print("\n  -- r_wx_codes --")
    check("sin wx      -> 0.0",     r_wx_codes([]) == 0.0)
    check("TSRA        -> 1.0",     r_wx_codes(["TSRA"]) == 1.0)
    check("-RA         -> 0.20",    abs(r_wx_codes(["-RA"]) - 0.20) < 0.001)
    check("BR          -> 0.20",    abs(r_wx_codes(["BR"]) - 0.20) < 0.001)
    check("SN          -> 0.55",    abs(r_wx_codes(["SN"]) - 0.55) < 0.001)
    check("max de lista: -RA y SN", abs(r_wx_codes(["-RA", "SN"]) - 0.55) < 0.001)
    check("token desconocido -> 0", r_wx_codes(["UNKN"]) == 0.0)

    print("\n  -- r_fog (spread) --")
    check("spread=None -> 0.0",     r_fog(None) == 0.0)
    check("spread=5.0  -> 0.0",     r_fog(5.0)  == 0.0)
    check("spread=2.0  -> 1.0",     r_fog(2.0)  == 1.0)
    check("spread=3.5  -> 0.5",     abs(r_fog(3.5) - 0.5) < 0.001)

    print("\n  -- apply_decision_threshold --")
    check("R=0.10 -> GO",           apply_decision_threshold(0.10) == "GO")
    check("R=0.25 -> CAUTION",      apply_decision_threshold(0.25) == "CAUTION")
    check("R=0.40 -> CAUTION",      apply_decision_threshold(0.40) == "CAUTION")
    check("R=0.50 -> NO GO",        apply_decision_threshold(0.50) == "NO GO")
    check("R=0.80 -> NO GO",        apply_decision_threshold(0.80) == "NO GO")
    check("R=0.00 -> GO",           apply_decision_threshold(0.00) == "GO")
    check("R=1.00 -> NO GO",        apply_decision_threshold(1.00) == "NO GO")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
