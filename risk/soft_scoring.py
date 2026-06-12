"""
soft_scoring.py
===============
Calcula el score de riesgo total R_total para el motor de decision VFR.

Formula:
    R_total = sum(w_i * r_i)  +  delta_orografico
    R_total = clamp(R_total, 0.0, 1.0)

Decision:
    R < 0.25           -> GO
    0.25 <= R < 0.50   -> CAUTION
    R >= 0.50          -> NO GO

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
        apply_decision_threshold,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from features.crosswind     import compute_crosswind_from_weather
    from features.fog_risk      import compute_fog_risk_from_weather
    from features.orographic    import orographic_penalty_from_weather
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from risk.weights          import (
        W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF,
        r_visibility, r_ceiling, r_crosswind, r_gust, r_wx_codes,
        apply_decision_threshold,
    )
    from risk.aircraft_profiles import AircraftProfile, ALPHA_TRAINER
    from features.crosswind     import compute_crosswind_from_weather
    from features.fog_risk      import compute_fog_risk_from_weather
    from features.orographic    import orographic_penalty_from_weather

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SoftScoreResult:
    """
    Score de riesgo total y descomposicion por componente.

    Todos los r_i estan en [0, 1]. R_total puede superar la suma ponderada
    por el delta orografico (que se aplica despues).
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

    # ── Penalizacion orografica ───────────────────────────────────────────────
    orographic_delta  : float           # Penalizacion SACC NWP (+0.05 o 0.0)
    r_weighted_sum    : float           # Suma ponderada antes del delta

    # ── Contexto ─────────────────────────────────────────────────────────────
    station_id        : str
    runway_heading    : int
    aircraft_name     : str

    # ── Componente dominante ──────────────────────────────────────────────────
    dominant_factor   : str             # Componente con mayor contribucion ponderada


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def compute_soft_score(
    weather        ,                            # ParsedWeather
    runway_heading : int,
    aircraft       : AircraftProfile = None,
    taf_r_taf      : float = 0.0,               # r_taf de TafWindowResult (opcional)
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

    # ── r_i individuales ─────────────────────────────────────────────────────

    # Visibilidad y ceiling: directo del weather
    _r_vis  = r_visibility(weather.visibility_km)
    _r_ceil = r_ceiling(weather.ceiling_ft)

    # Crosswind y gust: usar CrosswindResult para manejar VRB y rafagas
    xw_result = compute_crosswind_from_weather(weather, runway_heading)
    # Para el score de xwind usamos la rafaga cruzada si esta disponible
    _xw_eff   = (xw_result.crosswind_gust_kt
                 if xw_result.crosswind_gust_kt is not None
                 else xw_result.crosswind_kt)
    _r_xwind  = r_crosswind(_xw_eff, aircraft.crosswind_max_kt)

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

    # ── Penalizacion orografica (aplica despues del weighted sum) ─────────────
    oro_result       = orographic_penalty_from_weather(weather)
    orographic_delta = oro_result.delta_r

    r_total = min(weighted_sum + orographic_delta, 1.0)

    # ── Decision ──────────────────────────────────────────────────────────────
    decision = apply_decision_threshold(r_total)

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

    logger.info(
        f"SoftScore {weather.station_id} pista={runway_heading} | "
        f"R={r_total:.3f} [{decision}] | "
        f"vis={_r_vis:.2f} ceil={_r_ceil:.2f} xw={_r_xwind:.2f} "
        f"gust={_r_gust:.2f} wx={_r_wx:.2f} fog={_r_fog:.2f} "
        f"taf={_r_taf:.2f} oro={orographic_delta:.2f} | "
        f"dominante={dominant}"
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
        orographic_delta = orographic_delta,
        r_weighted_sum   = weighted_sum,
        station_id       = weather.station_id,
        runway_heading   = runway_heading,
        aircraft_name    = aircraft.name,
        dominant_factor  = dominant,
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

        ("SACC NWP: condiciones OK pero penalizacion orografica",
         dict(station_id="SACC", nwp_estimated=True, visibility_km=9.0,
              ceiling_ft=None, wind_dir=150, wind_spd_kt=8.0, spread_c=6.0), 0.0),

        ("SACC NWP con TEMPO en ventana (r_taf=0.75)",
         dict(station_id="SACC", nwp_estimated=True, visibility_km=9.0,
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

    # SACC orografico
    r_sacc = results[5][1]
    r_saco = compute_soft_score(
        _MockWeather(station_id="SACO", nwp_estimated=False,
                     visibility_km=9.0, wind_dir=150, wind_spd_kt=8.0, spread_c=6.0),
        RUNWAY, ALPHA_TRAINER
    )
    check("SACC NWP: penalizacion orografica aplicada (+0.05)",
          abs(r_sacc.orographic_delta - 0.05) < 0.001)
    check("SACC NWP R_total > SACO equivalente",
          r_sacc.r_total > r_saco.r_total)

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
    check("diferencia = W_TAF * 1.0 = 0.05",
          abs(r_withtaf.r_total - r_notaf.r_total - 0.05) < 0.001)

    # R_total siempre en [0, 1]
    check("R_total siempre en [0, 1]",
          all(0.0 <= r.r_total <= 1.0 for _, r in results))

    # decision coherente con R_total
    for desc2, r2 in results:
        expected = ("GO" if r2.r_total < 0.25
                    else "CAUTION" if r2.r_total < 0.50
                    else "NO GO")
        if r2.decision != expected:
            check(f"Decision coherente con R_total para '{desc2}'", False)
            break
    else:
        check("Decision coherente con R_total en todos los casos", True)

    print("\n" + "=" * 72)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 72)
