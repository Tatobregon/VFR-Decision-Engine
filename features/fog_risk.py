"""
fog_risk.py
===========
Evalua el riesgo de niebla / visibilidad reducida para un vuelo VFR.

Combina dos señales independientes:
  1. Spread termico (T - Td): proxy de proximidad al punto de rocio.
  2. Tokens wx observados: FG, BR, MIFG, etc.

El score final r_fog = max(r_spread, r_wx), clampeado a [0, 1].

Uso tipico
----------
    from features.fog_risk import compute_fog_risk

    result = compute_fog_risk(spread_c=1.5, wx_codes=["BR"])
    print(result.r_fog)   # -> valor alto por spread bajo Y bruma presente
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Parametros del modelo de niebla
# ──────────────────────────────────────────────────────────────────────────────

# Umbral de spread por debajo del cual el riesgo es maximo (niebla inminente)
SPREAD_HIGH_RISK_C  = 2.0   # T-Td < 2 grados -> r_spread = 1.0

# Umbral de spread por encima del cual el riesgo es nulo
SPREAD_LOW_RISK_C   = 5.0   # T-Td > 5 grados -> r_spread = 0.0

# Scores para tokens wx relacionados con niebla/visibilidad reducida.
# Los tokens pueden aparecer con modificadores (+/-/MI/BC/PR/FZ).
# Se evalua si el token BASE esta contenido en el codigo wx reportado.
FOG_WX_SCORES = {
    "FZFG" : 1.0,   # niebla engelante -> maximo riesgo
    "FG"   : 0.85,  # niebla densa
    "BCFG" : 0.65,  # niebla en banco
    "MIFG" : 0.55,  # niebla baja superficial
    "PRFG" : 0.55,  # niebla parcial
    "BR"   : 0.30,  # bruma (mist) -> visibilidad reducida pero no critica
    "HZ"   : 0.15,  # neblina seca (haze)
}

# Orden de evaluacion: del mas grave al mas leve (primer match gana)
_FOG_TOKEN_PRIORITY = ["FZFG", "FG", "BCFG", "MIFG", "PRFG", "BR", "HZ"]


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FogRiskResult:
    """
    Resultado de la evaluacion de riesgo de niebla.

    r_fog es el score final para el soft scoring del risk engine.
    r_spread y r_wx son los sub-scores para trazabilidad.
    """
    r_fog        : float                  # Score final [0, 1]
    r_spread     : float                  # Contribucion del spread termico
    r_wx         : float                  # Contribucion de los tokens wx
    fog_tokens   : list = field(default_factory=list)  # Tokens de niebla encontrados
    spread_c     : Optional[float] = None # Spread usado (None si no disponible)
    dominated_by : str = "none"           # "spread" | "wx" | "none"


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def compute_fog_risk(
    spread_c  : Optional[float],
    wx_codes  : list,
) -> FogRiskResult:
    """
    Calcula el score de riesgo de niebla combinando spread y tokens wx.

    Parameters
    ----------
    spread_c  : Diferencia T - Td en grados Celsius. None si no disponible
                (NWP sin datos de dewpoint; se usa solo la señal wx).
    wx_codes  : Lista de codigos wx del ParsedWeather (ej. ["BR", "-RA"]).

    Returns
    -------
    FogRiskResult con r_fog en [0, 1].
    """
    r_spread    = _spread_score(spread_c)
    r_wx, found = _wx_score(wx_codes)

    r_fog = max(r_spread, r_wx)

    if r_spread >= r_wx:
        dominated_by = "spread" if r_spread > 0 else "none"
    else:
        dominated_by = "wx"

    logger.debug(
        f"Fog risk: spread={spread_c}C r_spread={r_spread:.2f} "
        f"tokens={found} r_wx={r_wx:.2f} r_fog={r_fog:.2f}"
    )

    return FogRiskResult(
        r_fog        = r_fog,
        r_spread     = r_spread,
        r_wx         = r_wx,
        fog_tokens   = found,
        spread_c     = spread_c,
        dominated_by = dominated_by,
    )


def compute_fog_risk_from_weather(weather) -> FogRiskResult:
    """
    Wrapper que extrae spread_c y wx_codes directamente de un ParsedWeather.
    """
    return compute_fog_risk(
        spread_c = weather.spread_c,
        wx_codes = weather.wx_codes,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Sub-scores internos
# ──────────────────────────────────────────────────────────────────────────────

def _spread_score(spread_c: Optional[float]) -> float:
    """
    Convierte el spread termico en score [0, 1].

    Interpolacion lineal entre SPREAD_HIGH_RISK_C y SPREAD_LOW_RISK_C.
    Si spread es None (dato no disponible) devuelve 0.0 (sin penalizacion
    por falta de dato; la señal wx sigue activa).
    """
    if spread_c is None:
        return 0.0

    if spread_c <= SPREAD_HIGH_RISK_C:
        return 1.0

    if spread_c >= SPREAD_LOW_RISK_C:
        return 0.0

    # Interpolacion lineal: 1 en HIGH_RISK, 0 en LOW_RISK
    range_c = SPREAD_LOW_RISK_C - SPREAD_HIGH_RISK_C
    return 1.0 - (spread_c - SPREAD_HIGH_RISK_C) / range_c


def _wx_score(wx_codes: list) -> tuple:
    """
    Evalua los tokens wx y devuelve (score_maximo, lista_tokens_encontrados).

    Matching exacto por token: "BCFG" no dispara "FG".
    Cada elemento de wx_codes es un token completo (ej. "BCFG", "BR", "-RA").

    Returns
    -------
    (float, list): score maximo encontrado y lista de tokens que matchearon.
    """
    if not wx_codes:
        return 0.0, []

    found_tokens = []
    max_score    = 0.0

    for code in wx_codes:
        code_upper = code.strip().upper()
        if code_upper in FOG_WX_SCORES:
            score = FOG_WX_SCORES[code_upper]
            if code_upper not in found_tokens:
                found_tokens.append(code_upper)
            max_score = max(max_score, score)

    return max_score, found_tokens


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 65)
    print("  TEST: fog_risk.py")
    print("=" * 65)

    # ── Tabla de casos ────────────────────────────────────────────────────────
    test_cases = [
        # (descripcion, spread_c, wx_codes)
        ("Cielo claro, spread alto",             8.0, []),
        ("Spread limite alto (5.0 C)",           5.0, []),
        ("Spread intermedio (3.5 C)",            3.5, []),
        ("Spread limite bajo (2.0 C)",           2.0, []),
        ("Spread muy bajo (0.5 C)",              0.5, []),
        ("Solo bruma (BR)",                      8.0, ["BR"]),
        ("Niebla densa (FG)",                    8.0, ["FG"]),
        ("Niebla engelante (FZFG)",              8.0, ["FZFG"]),
        ("Niebla en banco (BCFG)",               8.0, ["BCFG"]),
        ("Spread bajo + BR",                     1.5, ["BR"]),
        ("Spread bajo + FG (doble señal)",       1.0, ["FG"]),
        ("Sin spread NWP + BR",                 None, ["BR"]),
        ("Sin spread NWP + sin wx",             None, []),
        ("Lluvia + bruma (BR dominado por FG)", 8.0, ["-RA", "BR"]),
        ("Spread muy bajo + lluvia (no fog)",    1.5, ["-RA"]),
    ]

    print(f"\n  {'Descripcion':<40} {'Sprd':>5} {'Tokens':<12} "
          f"{'r_sprd':>6} {'r_wx':>6} {'r_fog':>6} {'Domina'}")
    print(f"  {'-'*40} {'-'*5} {'-'*12} {'-'*6} {'-'*6} {'-'*6} {'-'*8}")

    results = []
    for desc, spread, wx in test_cases:
        r = compute_fog_risk(spread_c=spread, wx_codes=wx)
        results.append((desc, r))
        sp_s = f"{spread:.1f}" if spread is not None else " ---"
        tok_s = ",".join(r.fog_tokens) if r.fog_tokens else "-"
        print(f"  {desc:<40} {sp_s:>5} {tok_s:<12} "
              f"{r.r_spread:6.2f} {r.r_wx:6.2f} {r.r_fog:6.2f} {r.dominated_by:>8}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 65)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # Spread
    check("spread=8.0 -> r_spread=0.0",
          compute_fog_risk(8.0, []).r_spread == 0.0)
    check("spread=5.0 -> r_spread=0.0",
          compute_fog_risk(5.0, []).r_spread == 0.0)
    check("spread=2.0 -> r_spread=1.0",
          compute_fog_risk(2.0, []).r_spread == 1.0)
    check("spread=0.0 -> r_spread=1.0",
          compute_fog_risk(0.0, []).r_spread == 1.0)
    check("spread=3.5 -> r_spread=0.5 (punto medio)",
          abs(compute_fog_risk(3.5, []).r_spread - 0.5) < 0.001)
    check("spread=None -> r_spread=0.0 (sin penalizacion)",
          compute_fog_risk(None, []).r_spread == 0.0)

    # Tokens wx
    check("FG -> r_wx=0.85",
          abs(compute_fog_risk(8.0, ["FG"]).r_wx - 0.85) < 0.001)
    check("FZFG -> r_wx=1.0",
          compute_fog_risk(8.0, ["FZFG"]).r_wx == 1.0)
    check("BR -> r_wx=0.30",
          abs(compute_fog_risk(8.0, ["BR"]).r_wx - 0.30) < 0.001)
    check("BCFG matchea como token exacto con score 0.65 (no 0.85 de FG)",
          abs(compute_fog_risk(8.0, ["BCFG"]).r_wx - 0.65) < 0.001)
    check("Sin wx -> r_wx=0.0",
          compute_fog_risk(8.0, []).r_wx == 0.0)

    # r_fog = max(r_spread, r_wx)
    check("r_fog = max(r_spread, r_wx) con spread bajo + BR",
          compute_fog_risk(1.5, ["BR"]).r_fog == 1.0)  # spread domina
    check("r_fog = max con sin spread + FG",
          abs(compute_fog_risk(None, ["FG"]).r_fog - 0.85) < 0.001)  # wx domina

    # Rango
    for desc2, sp2, wx2 in test_cases:
        r2 = compute_fog_risk(sp2, wx2)
        if not (0.0 <= r2.r_fog <= 1.0):
            check(f"r_fog en [0,1] para '{desc2}'", False)
            break
    else:
        check("r_fog en [0, 1] para todos los casos", True)

    print("\n" + "=" * 65)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 65)
