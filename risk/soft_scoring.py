"""
soft_scoring.py
===============
Calcula el score de riesgo total R_total para el motor de decision VFR.

Formula:
    R_total = sum(w_i * r_i)
    R_total = clamp(R_total, 0.0, 1.0)

Decision (compensatoria, umbrales calibrados en risk/calibration.py):
    R < 0.22           -> GO
    0.22 <= R < 0.59   -> CAUTION
    R >= 0.59          -> NO GO

La decision final combina esta decision compensatoria con una BARRERA
NO-COMPENSATORIA (piso conjuntivo, ver conjunctive_floor): un factor showstopper
individual (cruzado sobre el limite del avion, rafaga excesiva, niebla probable,
deterioro TAF) impone un veredicto minimo, de modo que no quede diluido por el
promedio ponderado. decision = worst(umbral(R), piso_conjuntivo).

Este modulo NO evalua hard blockers. El llamador debe verificar
hard_blockers.py primero y solo invocar el soft scoring si is_blocked=False.

Uso tipico
----------
    from risk.hard_blockers import check_hard_blockers_from_weather
    from risk.soft_scoring  import compute_soft_score
    from risk.aircraft_profiles import ALPHA_TRAINER

    blocker = check_hard_blockers_from_weather(weather)
    if blocker.is_blocked:
        decision = "NO GO"
    else:
        result   = compute_soft_score(weather, runway_heading=150, aircraft=ALPHA_TRAINER)
        decision = result.decision
"""

import logging
from dataclasses import dataclass

try:
    from risk.weights          import (
        W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF,
        r_visibility, r_ceiling, r_crosswind, r_gust, r_wx_codes,
        apply_decision_threshold, THRESHOLD_CAUTION,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from features.crosswind     import compute_crosswind_from_weather
    from features.fog_risk      import compute_fog_risk_from_weather
    from risk.personal_minima   import NEUTRAL
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from risk.weights          import (
        W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF,
        r_visibility, r_ceiling, r_crosswind, r_gust, r_wx_codes,
        apply_decision_threshold, THRESHOLD_CAUTION,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from features.crosswind     import compute_crosswind_from_weather
    from features.fog_risk      import compute_fog_risk_from_weather
    from risk.personal_minima   import NEUTRAL

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Barrera no-compensatoria (veto conjuntivo)
# ──────────────────────────────────────────────────────────────────────────────
# El soft score es una suma ponderada COMPENSATORIA: un factor bueno puede
# "tapar" a uno malo. Eso es adecuado para el deterioro gradual (visibilidad,
# techo), pero es peligroso para los factores que son showstoppers por si solos
# y que ademas pesan poco (viento cruzado 0.18, rafaga 0.09, niebla 0.06,
# tendencia TAF 0.04): un cruzado que SUPERA el maximo demostrado del avion
# aportaria a lo sumo 0.18 al score y quedaria como GO, diluido por el resto.
#
# Las decisiones GO/NO GO reales son CONJUNTIVAS (lista de chequeo): cualquier
# item critico veta, sin importar lo bueno del resto. Esta barrera implementa ese
# criterio como un PISO: cada factor showstopper impone un veredicto minimo, y el
# veredicto final es el peor entre el score compensatorio y este piso.
#
# Cubre exactamente los factores de bajo peso que el score diluye; la visibilidad
# y el techo (peso alto, deterioro gradual) siguen en el score compensatorio.

_VERDICT_RANK = {"GO": 0, "CAUTION": 1, "NO GO": 2}


def _worst_verdict(a: str, b: str) -> str:
    """Devuelve el veredicto mas restrictivo entre dos."""
    return a if _VERDICT_RANK[a] >= _VERDICT_RANK[b] else b


# ──────────────────────────────────────────────────────────────────────────────
# Parametros de la barrera no-compensatoria
# ──────────────────────────────────────────────────────────────────────────────
# Fracciones del limite de la aeronave a partir de las cuales cada factor impone
# un piso al veredicto. Son FRONTERAS DE DECISION: a diferencia de un peso, no
# desplazan el puntaje de forma continua sino que cambian veredictos de golpe.
# Por eso su efecto se mide explicitamente en risk/sensitivity.py, seccion [5],
# y no se los da por robustos sin medirlos.
#
# CRUZADO Y RAFAGA NO COMPARTEN ESCALA, y la razon esta en el propio
# AircraftProfile: los dos numeros tienen distinto estatus epistemico.
#
#   crosswind_max_kt : "Componente cruzado maximo DEMOSTRADO"
#       Es un valor obtenido en certificacion. Superarlo es salir de lo que el
#       fabricante probo. Justifica una escala exigente.
#
#   gust_max_kt      : "Rafaga maxima de referencia para operacion normal"
#       No es un valor certificado ni un limite legal: es una referencia de
#       operacion rutinaria. Superarlo es salir de lo habitual, no de lo
#       demostrado. Justifica una escala mas permisiva.
#
# Ademas, la rafaga es por definicion TRANSITORIA: mide la variabilidad del
# viento, no su intensidad sostenida. Un delta de rafaga de la mitad de la
# referencia es una condicion ventosa normal, no una anomalia; tratarla como
# tal producia CAUTION en dias operables (caso observado: delta +13 kt contra
# una referencia de 20 kt en un aerodromo con cruzado efectivo de 0.2 kt).

# ── Viento cruzado ────────────────────────────────────────────────────────────
# PROCEDENCIA: (J) JUICIO DECLARADO. La regulacion no fija escalones intermedios
# de cruzado y la accidentologia no distingue el cruzado subumbral. Expresa que
# la mitad del maximo demostrado es donde la maniobra deja de ser rutinaria para
# un piloto de aviacion general.
XWIND_CAUTION_FRACTION = 0.5      # >= esta fraccion del limite -> CAUTION
                                  # >= el limite                -> NO GO

# ── Rafagas ───────────────────────────────────────────────────────────────────
# PROCEDENCIA: (J) JUICIO DECLARADO, revisado por piloto sobre el criterio
# original, con el argumento de estatus del parametro que se expone arriba: una
# rafaga por debajo de la referencia de operacion normal deja al avion dentro de
# lo que puede sostener, de modo que el piso de CAUTION no se justifica a la
# mitad de esa referencia sino cerca de ella. El NO GO exige superarla con
# margen, porque exceder una referencia de rutina no equivale a exceder un valor
# certificado.
#
# TRAZABILIDAD DEL CAMBIO (septiembre 2026): el valor anterior era 0.50, igual
# que el del cruzado. Se separo y se elevo tras detectar que producia CAUTION en
# condiciones operables — caso observado: delta +13 kt contra una referencia de
# 20 kt en un aerodromo con cruzado efectivo de 0.2 kt. Ver la nota sobre
# INDEPENDENCIA DE LA REFERENCIA en risk/scenarios.py: para este factor la
# concordancia con la etiqueta normativa es por construccion y NO constituye
# evidencia independiente.
GUST_CAUTION_FRACTION = 0.85      # >= 0.85 x gust_max   -> CAUTION
GUST_NOGO_FACTOR      = 1.5       # >= 1.50 x gust_max   -> NO GO


def conjunctive_floor(
    xw_eff_kt   : float,           # cruzado efectivo (con rafaga si la hay)
    xw_limit_kt : float,           # limite de cruzado del avion (ya ajustado por minimos)
    gust_kt     : float,           # rafaga sostenida (o None)
    spd_kt      : float,           # viento sostenido (o None)
    gust_max_kt : float,           # rafaga maxima de referencia del avion
    r_fog       : float,           # score de niebla ya calculado [0,1]
    r_taf       : float,           # score de tendencia TAF [0,1]
) -> tuple:
    """
    Piso no-compensatorio. Devuelve (veredicto_piso, motivo).

    Reglas (relativas a los limites de CADA aeronave — escalable):
      - Cruzado efectivo >= limite del avion                     -> NO GO
      - Cruzado efectivo >= XWIND_CAUTION_FRACTION del limite     -> CAUTION
      - Delta de rafaga  >= GUST_NOGO_FACTOR x gust_max           -> NO GO
      - Delta de rafaga  >= GUST_CAUTION_FRACTION x gust_max      -> CAUTION
      - Niebla probable (r_fog >= 0.9, spread bajo)               -> CAUTION
      - Deterioro pronosticado en TAF (r_taf >= 0.6)              -> CAUTION

    El cruzado y la rafaga usan escalas distintas a proposito: ver la
    justificacion en el bloque de constantes de este modulo.
    """
    floor   = "GO"
    reasons = []

    # ── Viento cruzado (control primario en el aterrizaje) ────────────────────
    if xw_limit_kt > 0:
        if xw_eff_kt >= xw_limit_kt:
            floor = _worst_verdict(floor, "NO GO")
            reasons.append(
                f"viento cruzado {xw_eff_kt:.0f} kt supera el limite del avion ({xw_limit_kt:.0f} kt)")
        elif xw_eff_kt >= XWIND_CAUTION_FRACTION * xw_limit_kt:
            floor = _worst_verdict(floor, "CAUTION")
            reasons.append(
                f"viento cruzado {xw_eff_kt:.0f} kt "
                f"(>={XWIND_CAUTION_FRACTION:.0%} del limite de {xw_limit_kt:.0f} kt)")

    # ── Rafagas (variabilidad del viento) ─────────────────────────────────────
    # La rafaga mide VARIABILIDAD, no intensidad sostenida, y gust_max_kt es una
    # referencia de operacion normal y no un valor certificado. De ahi que la
    # escala sea mas permisiva que la del cruzado (ver bloque de constantes).
    if gust_kt is not None and spd_kt is not None and gust_max_kt > 0:
        delta = gust_kt - spd_kt
        if delta >= GUST_NOGO_FACTOR * gust_max_kt:
            floor = _worst_verdict(floor, "NO GO")
            reasons.append(
                f"rafaga +{delta:.0f} kt supera {GUST_NOGO_FACTOR:g}x la referencia "
                f"del avion ({gust_max_kt:.0f} kt)")
        elif delta >= GUST_CAUTION_FRACTION * gust_max_kt:
            floor = _worst_verdict(floor, "CAUTION")
            reasons.append(
                f"rafaga +{delta:.0f} kt "
                f"(>={GUST_CAUTION_FRACTION:.0%} de la referencia de "
                f"{gust_max_kt:.0f} kt del avion)")

    # ── Niebla probable (indicador adelantado: puede pasar a IMC rapido) ──────
    if r_fog >= 0.9:
        floor = _worst_verdict(floor, "CAUTION")
        reasons.append("niebla probable (spread termico bajo)")

    # ── Tendencia TAF a deterioro dentro de la ventana ────────────────────────
    if r_taf >= 0.6:
        floor = _worst_verdict(floor, "CAUTION")
        reasons.append("deterioro pronosticado en el TAF (ventana de vuelo)")

    return floor, "; ".join(reasons)


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SoftScoreResult:
    """
    Score de riesgo total y descomposicion por componente.

    Todos los r_i estan en [0, 1], y R_total es su suma ponderada.
    """
    # ── Score final ───────────────────────────────────────────────────────────
    r_total           : float           # Score total clampeado a [0, 1]
    decision          : str             # "GO" | "CAUTION" | "NO GO"

    # ── Componentes individuales ──────────────────────────────────────────────
    r_vis             : float           # Componente visibilidad     (w=0.25)
    r_ceil            : float           # Componente ceiling          (w=0.25)
    r_xwind           : float           # Componente crosswind        (w=0.20)
    r_gust            : float           # Componente rafagas          (w=0.10)
    r_wx              : float           # Componente fenomenos wx     (w=0.10)
    r_fog             : float           # Componente niebla           (w=0.05)
    r_taf             : float           # Componente riesgo TAF       (w=0.05)

    r_weighted_sum    : float           # Suma ponderada (== r_total antes del clamp)

    # ── Contexto ─────────────────────────────────────────────────────────────
    station_id        : str
    runway_heading    : int
    aircraft_name     : str

    # ── Componente dominante ──────────────────────────────────────────────────
    dominant_factor   : str             # Componente con mayor contribucion ponderada

    # ── Barrera no-compensatoria (veto conjuntivo) ────────────────────────────
    guardrail_floor   : str  = "GO"     # Piso impuesto por un factor showstopper
    guardrail_reason  : str  = ""       # Motivo del piso (para el briefing)


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def compute_soft_score(
    weather        ,                            # ParsedWeather
    runway_heading : int,
    aircraft       : AircraftProfile = None,
    taf_r_taf      : float = 0.0,               # r_taf de TafWindowResult (opcional)
    personal_minima = None,                     # PersonalMinima; None = sin ajuste
) -> SoftScoreResult:
    """
    Calcula R_total a partir de un ParsedWeather y el contexto de vuelo.

    Parameters
    ----------
    weather        : ParsedWeather (METAR o NWP).
    runway_heading : Rumbo magnetico de la pista en grados (para crosswind).
    aircraft       : Perfil de aeronave. Default: ALPHA_TRAINER.
    taf_r_taf      : Score TAF pre-calculado por TafAnalyzer.analyze().
                     Pasar 0.0 si no hay TAF disponible.

    Returns
    -------
    SoftScoreResult con R_total, decision y desglose por componente.
    """
    if aircraft is None:
        aircraft = ALPHA_TRAINER

    # Minimos personales: endurecen los umbrales segun la experiencia del piloto.
    pm = personal_minima if personal_minima is not None else NEUTRAL

    # ── r_i individuales ─────────────────────────────────────────────────────

    # Visibilidad y ceiling: el piloto "percibe" peor segun sus minimos personales
    # (dividir por el multiplicador => exigir mas margen).
    _vis_in  = weather.visibility_km / pm.vis_mult if weather.visibility_km is not None else None
    _ceil_in = weather.ceiling_ft   / pm.ceil_mult if weather.ceiling_ft   is not None else None
    _r_vis  = r_visibility(_vis_in)
    _r_ceil = r_ceiling(_ceil_in)

    # Crosswind y gust: usar CrosswindResult para manejar VRB y rafagas
    xw_result = compute_crosswind_from_weather(weather, runway_heading)
    # Para el score de xwind usamos la rafaga cruzada si esta disponible
    _xw_eff   = (xw_result.crosswind_gust_kt
                 if xw_result.crosswind_gust_kt is not None
                 else xw_result.crosswind_kt)
    # La tolerancia al cruzado escala con los minimos personales (xwind_mult<1 = mas estricto)
    _r_xwind  = r_crosswind(_xw_eff, aircraft.crosswind_max_kt * pm.xwind_mult)

    # Gust: delta entre rafaga y sostenida
    _r_gust   = r_gust(
        weather.wind_gust_kt,
        weather.wind_spd_kt,
        aircraft.gust_max_kt,
    )

    # Fenomenos wx
    _r_wx     = r_wx_codes(weather.wx_codes)

    # Niebla: usar FogRiskResult (combina spread + tokens wx de niebla)
    fog_result = compute_fog_risk_from_weather(weather)
    _r_fog     = fog_result.r_fog

    # TAF: viene como parametro externo
    _r_taf    = float(taf_r_taf)

    # ── Suma ponderada ────────────────────────────────────────────────────────
    weighted_sum = (
        W_VIS   * _r_vis   +
        W_CEIL  * _r_ceil  +
        W_XWIND * _r_xwind +
        W_GUST  * _r_gust  +
        W_WX    * _r_wx    +
        W_FOG   * _r_fog   +
        W_TAF   * _r_taf
    )

    r_total = min(weighted_sum, 1.0)

    # ── Decision compensatoria (umbrales sobre R_total) ───────────────────────
    threshold_decision = apply_decision_threshold(r_total)

    # ── Barrera no-compensatoria (veto conjuntivo) ────────────────────────────
    # El limite de cruzado escala con los minimos personales, igual que el score.
    xw_limit = aircraft.crosswind_max_kt * pm.xwind_mult
    guardrail_floor, guardrail_reason = conjunctive_floor(
        xw_eff_kt   = _xw_eff,
        xw_limit_kt = xw_limit,
        gust_kt     = weather.wind_gust_kt,
        spd_kt      = weather.wind_spd_kt,
        gust_max_kt = aircraft.gust_max_kt,
        r_fog       = _r_fog,
        r_taf       = _r_taf,
    )

    # Decision final = peor entre el score compensatorio y el piso conjuntivo.
    decision = _worst_verdict(threshold_decision, guardrail_floor)

    # ── Factor dominante ──────────────────────────────────────────────────────
    contributions = {
        "visibility" : W_VIS   * _r_vis,
        "ceiling"    : W_CEIL  * _r_ceil,
        "crosswind"  : W_XWIND * _r_xwind,
        "gusts"      : W_GUST  * _r_gust,
        "wx_phenomena": W_WX   * _r_wx,
        "fog"        : W_FOG   * _r_fog,
        "taf_risk"   : W_TAF   * _r_taf,
    }
    dominant = max(contributions, key=contributions.get)

    floor_note = f" | PISO={guardrail_floor} ({guardrail_reason})" if guardrail_floor != "GO" else ""
    logger.info(
        f"SoftScore {weather.station_id} pista={runway_heading} | "
        f"R={r_total:.3f} umbral={threshold_decision} -> [{decision}] | "
        f"vis={_r_vis:.2f} ceil={_r_ceil:.2f} xw={_r_xwind:.2f} "
        f"gust={_r_gust:.2f} wx={_r_wx:.2f} fog={_r_fog:.2f} "
        f"taf={_r_taf:.2f} | "
        f"dominante={dominant}{floor_note}"
    )

    return SoftScoreResult(
        r_total          = r_total,
        decision         = decision,
        r_vis            = _r_vis,
        r_ceil           = _r_ceil,
        r_xwind          = _r_xwind,
        r_gust           = _r_gust,
        r_wx             = _r_wx,
        r_fog            = _r_fog,
        r_taf            = _r_taf,
        r_weighted_sum   = weighted_sum,
        station_id       = weather.station_id,
        runway_heading   = runway_heading,
        aircraft_name    = aircraft.name,
        dominant_factor  = dominant,
        guardrail_floor  = guardrail_floor,
        guardrail_reason = guardrail_reason,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from dataclasses import dataclass as _dc, field as _field
    from typing import Optional as _Opt

    # ── ParsedWeather sintetico para pruebas ──────────────────────────────────
    @_dc
    class _MockWeather:
        station_id    : str
        nwp_estimated : bool   = False
        wind_dir      : _Opt[int]   = None
        wind_spd_kt   : _Opt[float] = None
        wind_gust_kt  : _Opt[float] = None
        wind_variable : bool        = False
        visibility_km : _Opt[float] = None
        ceiling_ft    : _Opt[int]   = None
        wx_codes      : list        = _field(default_factory=list)
        spread_c      : _Opt[float] = None

    RUNWAY = 150   # pista SACC: 15/33

    test_cases = [
        # (descripcion, weather_kwargs, taf_r_taf)
        ("CAVOK, viento de frente",
         dict(station_id="SACO", visibility_km=10.0, ceiling_ft=None,
              wind_dir=150, wind_spd_kt=8.0, spread_c=8.0), 0.0),

        ("MVFR: vis baja, viento cruzado moderado",
         dict(station_id="SACO", visibility_km=4.0, ceiling_ft=1200,
              wind_dir=200, wind_spd_kt=12.0, spread_c=4.0), 0.0),

        ("IFR: vis muy baja, techo bajo",
         dict(station_id="SACO", visibility_km=1.8, ceiling_ft=400,
              wind_dir=100, wind_spd_kt=10.0, spread_c=6.0,
              wx_codes=["-RA"]), 0.0),

        ("Viento cruzado fuerte con rafagas",
         dict(station_id="SACO", visibility_km=8.0, ceiling_ft=None,
              wind_dir=60, wind_spd_kt=10.0, wind_gust_kt=18.0, spread_c=7.0), 0.0),

        ("Niebla: spread bajo + BR",
         dict(station_id="SACO", visibility_km=6.0, ceiling_ft=None,
              wind_dir=150, wind_spd_kt=3.0, spread_c=1.5,
              wx_codes=["BR"]), 0.0),

        ("NWP con TEMPO en ventana (r_taf=0.75)",
         dict(station_id="SAZN", nwp_estimated=True, visibility_km=9.0,
              ceiling_ft=None, wind_dir=150, wind_spd_kt=8.0, spread_c=6.0), 0.75),

        ("VFR perfecto",
         dict(station_id="SACO", visibility_km=10.0, ceiling_ft=None,
              wind_dir=150, wind_spd_kt=5.0, spread_c=10.0), 0.0),
    ]

    print("=" * 72)
    print("  TEST: soft_scoring.py")
    print(f"  Aeronave: {ALPHA_TRAINER.name} | Pista: {RUNWAY} grados")
    print("=" * 72)

    print(f"\n  {'Descripcion':<42} {'R_total':>7} {'Decision':<10} {'Dominante'}")
    print(f"  {'-'*42} {'-'*7} {'-'*10} {'-'*15}")

    results = []
    for desc, kwargs, taf_r in test_cases:
        w = _MockWeather(**kwargs)
        r = compute_soft_score(w, RUNWAY, ALPHA_TRAINER, taf_r_taf=taf_r)
        results.append((desc, r))
        print(f"  {desc:<42} {r.r_total:>7.3f} {r.decision:<10} {r.dominant_factor}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 72)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # VFR perfecto
    r_vfr = compute_soft_score(
        _MockWeather(station_id="SACO", visibility_km=10.0, wind_dir=150,
                     wind_spd_kt=5.0, spread_c=10.0),
        RUNWAY, ALPHA_TRAINER, taf_r_taf=0.0
    )
    check("VFR perfecto: decision GO",         r_vfr.decision == "GO")
    check("VFR perfecto: R_total < 0.25",      r_vfr.r_total < 0.25)
    check("VFR perfecto: r_vis=0.0",           r_vfr.r_vis == 0.0)
    check("VFR perfecto: r_ceil=0.0 (CLR)",    r_vfr.r_ceil == 0.0)

    # CAVOK viento de frente
    r_cavok, _ = results[0][1], results[0]
    check("CAVOK frente: r_xwind~0",           r_cavok.r_xwind < 0.01)

    # IFR
    r_ifr = results[2][1]
    check("IFR vis+ceil: R_total alto",        r_ifr.r_total >= 0.25)

    # Crosswind fuerte
    r_xw = results[3][1]
    check("Xwind fuerte: r_xwind > 0.5",       r_xw.r_xwind > 0.5)

    # La fuente (NWP vs METAR) no altera el score: mismas condiciones, mismo R.
    # Antes habia una penalizacion orografica fija que solo aplicaba a un
    # aerodromo (SACC); se elimino por no ser generalizable al pais.
    r_nwp = compute_soft_score(
        _MockWeather(station_id="SAZN", nwp_estimated=True,
                     visibility_km=9.0, wind_dir=150, wind_spd_kt=8.0, spread_c=6.0),
        RUNWAY, ALPHA_TRAINER
    )
    r_metar = compute_soft_score(
        _MockWeather(station_id="SACO", nwp_estimated=False,
                     visibility_km=9.0, wind_dir=150, wind_spd_kt=8.0, spread_c=6.0),
        RUNWAY, ALPHA_TRAINER
    )
    check("NWP y METAR con identicas condiciones dan el mismo R",
          abs(r_nwp.r_total - r_metar.r_total) < 1e-9)
    check("R_total == suma ponderada (sin deltas externos)",
          abs(r_metar.r_total - r_metar.r_weighted_sum) < 1e-9)

    # r_taf se incorpora al total
    r_notaf = compute_soft_score(
        _MockWeather(station_id="SACO", visibility_km=9.0, wind_dir=150,
                     wind_spd_kt=5.0, spread_c=8.0),
        RUNWAY, ALPHA_TRAINER, taf_r_taf=0.0
    )
    r_withtaf = compute_soft_score(
        _MockWeather(station_id="SACO", visibility_km=9.0, wind_dir=150,
                     wind_spd_kt=5.0, spread_c=8.0),
        RUNWAY, ALPHA_TRAINER, taf_r_taf=1.0
    )
    check("r_taf=1.0 aumenta R_total vs r_taf=0.0",
          r_withtaf.r_total > r_notaf.r_total)
    check("diferencia = W_TAF * 1.0",
          abs(r_withtaf.r_total - r_notaf.r_total - W_TAF) < 0.001)

    # R_total siempre en [0, 1]
    check("R_total siempre en [0, 1]",
          all(0.0 <= r.r_total <= 1.0 for _, r in results))

    # decision coherente con R_total + piso conjuntivo (worst-case)
    for desc2, r2 in results:
        threshold = apply_decision_threshold(r2.r_total)
        expected = _worst_verdict(threshold, r2.guardrail_floor)
        if r2.decision != expected:
            check(f"Decision coherente con R_total+piso para '{desc2}'", False)
            break
    else:
        check("Decision coherente con R_total+piso en todos los casos", True)

    # el piso conjuntivo veta el cruzado sobre el limite del avion
    r_over_xw = compute_soft_score(
        _MockWeather(station_id="SACO", visibility_km=10.0, wind_dir=60,
                     wind_spd_kt=13.0, spread_c=9.0),   # cruzado 13 kt en Alpha (limite 12)
        RUNWAY, ALPHA_TRAINER, taf_r_taf=0.0
    )
    check("Guardrail: cruzado sobre limite -> NO GO aunque R sea bajo",
          r_over_xw.decision == "NO GO" and r_over_xw.r_total < THRESHOLD_CAUTION)

    print("\n" + "=" * 72)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 72)
