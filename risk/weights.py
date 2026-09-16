"""
weights.py
==========
Pesos y funciones r_i del soft scoring.

El score total se calcula como:
    R_total = sum(w_i * r_i)

Cada r_i es una funcion de riesgo en [0, 1]:
  - 0.0 = sin riesgo para esa componente
  - 1.0 = riesgo maximo (condicion en o mas alla del limite operacional)

Los pesos suman 1.0.

Los pesos fueron derivados por AHP (Analytic Hierarchy Process). Los juicios de
a pares NO se fijaron a ojo: se derivan de un indice de riesgo de accidentologia
(I = probabilidad x severidad, definicion del Doc 9859 de OACI) mediante la
operacion explicita a_ij = redondeo_Saaty(I_i / I_j). La derivacion completa
—evidencia, jerarquia, matrices, autovector, razon de consistencia CR y la
procedencia declarada de cada entrada— es reproducible en risk/ahp_weights.py.

Pesos AHP (CR global = 0.069, aceptable por ser <= 0.10):
  Visibilidad  | vis_km      | 0.357 | Rampa: 1 si vis<3km, 0 si vis>8km
  Ceiling      | ceil_ft     | 0.357 | Rampa: 1 si ceil<500ft, 0 si ceil>2000ft
  Crosswind    | xw_kt       | 0.099 | Lineal: xw / xw_max
  Niebla proxy | spread_c    | 0.071 | Rampa: 1 si spread<2C, 0 si spread>5C
  Rafagas      | gust-spd kt | 0.050 | Lineal: delta / gust_max
  Fenomenos    | wx_codes    | 0.044 | Escalonado por severidad
  Riesgo TAF   | PROB/TEMPO  | 0.022 | Escalonado (calculado en taf_window.py)

NOTA sobre el peso del cruzado. Es bajo A PROPOSITO y no subestima el riesgo:
la evidencia indica que la referencia visual domina al viento por casi un orden
de magnitud en terminos de fatalidad, lo que en la escala de Saaty significa que
ambos criterios dejan de ser conmensurables. Por eso el viento cruzado NO se
gestiona por su peso en la suma compensatoria sino en la BARRERA
NO-COMPENSATORIA (soft_scoring.conjunctive_floor), que impone un piso de
veredicto cuando supera los limites de la aeronave. El peso solo carga su
contribucion residual dentro de la banda admisible.
"""

from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Pesos del scoring — derivados por AHP (ver risk/ahp_weights.py)
# ──────────────────────────────────────────────────────────────────────────────
# Redondeados a 3 decimales. Valores exactos del autovector AHP:
#   vis .35700  ceil .35700  xwind .09921  fog .07140  gust .04960
#   wx .04386  taf .02193

W_VIS   = 0.357
W_CEIL  = 0.357
W_XWIND = 0.099
W_GUST  = 0.050
W_WX    = 0.044
W_FOG   = 0.071
W_TAF   = 0.022

assert abs(W_VIS + W_CEIL + W_XWIND + W_GUST + W_WX + W_FOG + W_TAF - 1.0) < 1e-9, \
    "Los pesos deben sumar 1.0"

# ──────────────────────────────────────────────────────────────────────────────
# Parametros de FORMA de las funciones r_i
# ──────────────────────────────────────────────────────────────────────────────
# Los pesos w_i dicen cuanto pesa cada criterio; estos parametros dicen COMO se
# convierte una magnitud fisica en un riesgo normalizado [0,1]. Son tan
# determinantes como los pesos —medido: mover un solo quiebre cambia mas
# veredictos de la bateria que perturbar los siete pesos a la vez (ver la
# seccion de forma en risk/sensitivity.py)— asi que se declara su procedencia
# con el mismo esquema que las comparaciones del AHP:
#
#   (N) NORMA  -> el valor ES una frontera de la regulacion, no una eleccion
#   (J) JUICIO -> juicio declarado, sin anclaje normativo ni empirico
#   (A) AERONAVE -> se deriva del perfil; no es un parametro libre del modelo
#
# El minimo VFR de la regulacion es 5 km de visibilidad y 1000 ft de techo, y
# cae DENTRO de las rampas (r = 0.60 y 0.67), no en uno de sus extremos.
#
# Los limites de riesgo MAXIMO coinciden con los de rechazo categorico del
# sistema (risk/hard_blockers.py): por debajo de 3 km o de 500 ft el veredicto
# es NO GO sin calcular puntaje. No son una frontera de la norma —los minimos
# IFR dependen de cada procedimiento publicado—, sino una decision de diseno
# declarada. Mover el extremo cambia la pendiente de la rampa por encima de el,
# y ese efecto se mide en el analisis de sensibilidad.
#
# Los limites de riesgo NULO tambien son juicio: expresan "holgadamente por
# encima del minimo VFR", con un margen de 1.6x en visibilidad y 2x en techo.

VIS_RISK_MAX_KM   = 3.0    # (J) igual al rechazo categorico de visibilidad
VIS_RISK_ZERO_KM  = 8.0    # (J) minimo VFR (5 km) con margen de 1.6x

CEIL_RISK_MAX_FT  = 500    # (J) igual al rechazo categorico de techo
CEIL_RISK_ZERO_FT = 2000   # (J) minimo VFR (1000 ft) con margen de 2x

FOG_RISK_MAX_C    = 2.0    # (J) spread al que la condensacion se considera inminente
FOG_RISK_ZERO_C   = 5.0    # (J) spread por encima del cual no se computa riesgo

# El viento cruzado y la rafaga NO tienen parametros de forma propios: su rampa
# va de 0 al limite publicado de la aeronave (crosswind_max_kt, gust_max_kt), de
# modo que la escala la fija el avion y no el modelo. (A)

# ──────────────────────────────────────────────────────────────────────────────
# Thresholds de decision — CALIBRADOS por anclaje normativo (ver risk/calibration.py)
# ──────────────────────────────────────────────────────────────────────────────
# Los cortes se ajustaron sobre una bateria de escenarios de referencia cuyo
# veredicto correcto se deriva de la normativa ANAC/OACI + criterio aeronautico
# (risk/scenarios.py). La busqueda minimiza un costo asimetrico donde el sub-aviso
# (el sistema avisa menos que la norma) pesa mucho mas que el sobre-aviso.
# Resultado: t_go = 0.22, t_caution = 0.59. El optimo es un RANGO
# (t_go in [0.15, 0.28], t_caution in [0.59, 0.66]), lo que indica robustez.
# Es validez de CONSTRUCTO (reproduce la regulacion), no validez empirica.
#
# HISTORIA DE LA CALIBRACION (relevante para la trazabilidad de la tesis):
#   1. Pesos por juicio experto  -> optimo t_go 0.22 / t_caution 0.50 -> 97%, 0 sub-avisos
#   2. Pesos derivados de evidencia (ver ahp_weights.py) -> optimo t_go 0.22 /
#      t_caution 0.59 -> 97%, 0 sub-avisos
# Dos derivaciones independientes de los pesos, recalibradas cada una, producen
# la MISMA concordancia y los mismos veredictos: el comportamiento decisional
# del sistema no depende de la ponderacion exacta. t_go ni siquiera se movio.
THRESHOLD_GO      = 0.22   # R < 0.22         → GO
THRESHOLD_CAUTION = 0.59   # 0.22 <= R < 0.59 → CAUTION
                           # R >= 0.59        → NO GO


# ──────────────────────────────────────────────────────────────────────────────
# Funciones r_i
# ──────────────────────────────────────────────────────────────────────────────

def r_visibility(vis_km: Optional[float]) -> float:
    """
    Score de riesgo por visibilidad.

    Rampa lineal:
      vis <= 3.0 km  → 1.0  (por debajo, NO GO por rechazo categorico)
      vis >= 8.0 km  → 0.0  (excelente visibilidad)
      None           → 0.0  (sin dato: tratado como permisivo)
    """
    if vis_km is None:
        return 0.0
    if vis_km <= VIS_RISK_MAX_KM:
        return 1.0
    if vis_km >= VIS_RISK_ZERO_KM:
        return 0.0
    return 1.0 - (vis_km - VIS_RISK_MAX_KM) / (VIS_RISK_ZERO_KM - VIS_RISK_MAX_KM)


def r_ceiling(ceil_ft: Optional[int]) -> float:
    """
    Score de riesgo por techo de nubes.

    Rampa lineal:
      ceil <= 500 ft  → 1.0  (por debajo, NO GO por rechazo categorico)
      ceil >= 2000 ft → 0.0  (holgado para VFR)
      None            → 0.0  (CLR o FEW/SCT: sin techo efectivo)

    NOTA: el "techo" es un PROXY conservador de la condicion VMC real. El VFR no
    exige un techo minimo per se, sino separacion de nubes y referencia visual
    con la superficie. Techo>=1000 ft aproxima "poder volar VFR por debajo de las
    nubes", pero no es el minimo legal literal.
    """
    if ceil_ft is None:
        return 0.0
    if ceil_ft <= CEIL_RISK_MAX_FT:
        return 1.0
    if ceil_ft >= CEIL_RISK_ZERO_FT:
        return 0.0
    return 1.0 - (ceil_ft - CEIL_RISK_MAX_FT) / (CEIL_RISK_ZERO_FT - CEIL_RISK_MAX_FT)


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
    NO SE USA EN TIEMPO DE EJECUCION. La rampa de niebla que aplica el motor
    esta en features/fog_risk.py (SPREAD_HIGH_RISK_C / SPREAD_LOW_RISK_C), donde
    ademas se combina con los tokens wx: r_fog = max(r_spread, r_wx). Esta
    version cubre solo la componente de spread y se conserva por simetria con el
    resto de las r_i y para el self-test del modulo.

    ATENCION al mantener: modificar los valores de aca NO cambia el
    comportamiento del sistema. Los dos juegos de constantes coinciden hoy
    (2 C y 5 C); si se los toca, hay que tocarlos en features/fog_risk.py.

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
    if spread_c <= FOG_RISK_MAX_C:
        return 1.0
    if spread_c >= FOG_RISK_ZERO_C:
        return 0.0
    return 1.0 - (spread_c - FOG_RISK_MAX_C) / (FOG_RISK_ZERO_C - FOG_RISK_MAX_C)


def apply_decision_threshold(r_total: float) -> str:
    """
    Convierte R_total en la decision final GO / CAUTION / NO GO.

    Thresholds calibrados por anclaje normativo (risk/calibration.py):
      R < 0.22           → GO
      0.22 <= R < 0.59   → CAUTION
      R >= 0.59          → NO GO

    NOTA: esta es la decision COMPENSATORIA. El soft_scoring la combina luego
    con la barrera no-compensatoria (piso conjuntivo) por worst-case, de modo
    que un factor showstopper individual no quede diluido por el resto.
    """
    if r_total < THRESHOLD_GO:
        return "GO"
    if r_total < THRESHOLD_CAUTION:
        return "CAUTION"
    return "NO GO"


# ──────────────────────────────────────────────────────────────────────────────
# Tabla de severidad de fenomenos wx
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Severidad de los fenomenos meteorologicos
# ──────────────────────────────────────────────────────────────────────────────
# PROCEDENCIA, con el mismo esquema que el resto del modelo:
#
#   (N) Los valores 1.00 corresponden UNO A UNO a los tokens de
#       risk/hard_blockers.HARD_BLOCKER_TOKENS. No son una eleccion: son la
#       lista normativa de fenomenos incompatibles con el vuelo visual. En la
#       practica nunca se evaluan aca, porque la capa categorica los intercepta
#       antes; figuran para que la tabla sea consistente si se la usa aislada.
#
#   (N) El ORDEN dentro de cada familia sigue el prefijo de intensidad del
#       codigo METAR ("-" ligera, sin prefijo moderada, "+" fuerte), que es
#       codificacion normativa OACI y no criterio del autor.
#
#   (J) Los VALORES numericos concretos son juicio declarado. No existe
#       accidentologia que asigne un incremento de riesgo a "llovizna moderada".
#       Su efecto esta acotado por dos vias: el peso del criterio es el
#       anteultimo del modelo (0.044, de modo que la tabla entera puede mover
#       como maximo 0.044 el puntaje) y los fenomenos realmente peligrosos no
#       pasan por aca sino por la capa categorica.
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

    print("\n  -- apply_decision_threshold (t_go=0.22, t_caution=0.59) --")
    check("R=0.10 -> GO",           apply_decision_threshold(0.10) == "GO")
    check("R=0.21 -> GO (bajo t_go)",apply_decision_threshold(0.21) == "GO")
    check("R=0.22 -> CAUTION (en t_go)", apply_decision_threshold(0.22) == "CAUTION")
    check("R=0.25 -> CAUTION",      apply_decision_threshold(0.25) == "CAUTION")
    check("R=0.40 -> CAUTION",      apply_decision_threshold(0.40) == "CAUTION")
    check("R=0.58 -> CAUTION",      apply_decision_threshold(0.58) == "CAUTION")
    check("R=0.59 -> NO GO",        apply_decision_threshold(0.59) == "NO GO")
    check("R=0.80 -> NO GO",        apply_decision_threshold(0.80) == "NO GO")
    check("R=0.00 -> GO",           apply_decision_threshold(0.00) == "GO")
    check("R=1.00 -> NO GO",        apply_decision_threshold(1.00) == "NO GO")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
