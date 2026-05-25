"""
hard_blockers.py
================
Evalua los hard blockers: condiciones que producen NO GO inmediato sin
necesidad de calcular el soft scoring.

Hard blockers definidos (CLAUDE.md):
  - Tokens wx: TS, TSRA, TSGR, GR, FC, VA, FZRA, FZDZ
  - Visibilidad < 1.5 km en la observacion actual
  - Techo < 500 ft AGL en la observacion actual

Si cualquier hard blocker esta activo → NO GO. El risk engine no calcula
R_total en ese caso.

Uso tipico
----------
    from risk.hard_blockers import check_hard_blockers

    result = check_hard_blockers(weather)
    if result.is_blocked:
        print("NO GO:", result.summary)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

HARD_BLOCKER_TOKENS = frozenset({
    "TS",    # tormenta electrica
    "TSRA",  # tormenta con lluvia
    "TSGR",  # tormenta con granizo
    "GR",    # granizo
    "FC",    # funnel cloud / tornado
    "VA",    # ceniza volcanica
    "FZRA",  # lluvia engelante
    "FZDZ",  # llovizna engelante
})

VIS_HARD_LIMIT_KM  = 1.5    # km: visibilidad minima absoluta
CEIL_HARD_LIMIT_FT = 500    # ft: techo minimo absoluto


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class HardBlockerResult:
    """
    Resultado de la evaluacion de hard blockers.

    `is_blocked = True` → NO GO inmediato. No calcular R_total.
    `triggers`  : lista de (tipo, valor, descripcion) de cada blocker activo.
    `summary`   : texto legible para el piloto / logs.
    """
    is_blocked : bool
    triggers   : list = field(default_factory=list)  # list[(tipo, valor, desc)]
    summary    : str  = ""


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def check_hard_blockers(
    visibility_km : Optional[float],
    ceiling_ft    : Optional[int],
    wx_codes      : list,
) -> HardBlockerResult:
    """
    Evalua si alguna condicion activa un hard blocker.

    Parameters
    ----------
    visibility_km : Visibilidad actual en km. None = sin dato (no bloquea).
    ceiling_ft    : Techo actual en ft AGL. None = CLR/FEW (no bloquea).
    wx_codes      : Lista de codigos wx actuales (ej. ["TSRA", "-RA"]).

    Returns
    -------
    HardBlockerResult con is_blocked=True si hay al menos un blocker activo.
    """
    triggers = []

    # ── Tokens wx ─────────────────────────────────────────────────────────────
    for code in wx_codes:
        token = code.strip().upper()
        if token in HARD_BLOCKER_TOKENS:
            triggers.append(("wx_token", token, f"Fenomeno peligroso: {token}"))

    # ── Visibilidad ───────────────────────────────────────────────────────────
    if visibility_km is not None and visibility_km < VIS_HARD_LIMIT_KM:
        triggers.append((
            "visibility",
            visibility_km,
            f"Visibilidad {visibility_km:.1f} km < minimo {VIS_HARD_LIMIT_KM} km",
        ))

    # ── Techo ─────────────────────────────────────────────────────────────────
    if ceiling_ft is not None and ceiling_ft < CEIL_HARD_LIMIT_FT:
        triggers.append((
            "ceiling",
            ceiling_ft,
            f"Techo {ceiling_ft} ft < minimo {CEIL_HARD_LIMIT_FT} ft",
        ))

    is_blocked = len(triggers) > 0

    if is_blocked:
        desc_list = [t[2] for t in triggers]
        summary   = " | ".join(desc_list)
        logger.warning(f"Hard blocker activado: {summary}")
    else:
        summary = "Sin hard blockers"
        logger.debug("Hard blocker check: OK")

    return HardBlockerResult(
        is_blocked = is_blocked,
        triggers   = triggers,
        summary    = summary,
    )


def check_hard_blockers_from_weather(weather) -> HardBlockerResult:
    """Wrapper que extrae los campos directamente de un ParsedWeather."""
    return check_hard_blockers(
        visibility_km = weather.visibility_km,
        ceiling_ft    = weather.ceiling_ft,
        wx_codes      = weather.wx_codes,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 62)
    print("  TEST: hard_blockers.py")
    print("=" * 62)

    test_cases = [
        # (descripcion, vis_km, ceil_ft, wx_codes)
        ("Condiciones normales VFR",      10.0, None,   []),
        ("Tormenta (TSRA)",               10.0, 2000,   ["TSRA"]),
        ("Granizo (GR)",                  10.0, 1500,   ["GR"]),
        ("Ceniza volcanica (VA)",         10.0, None,   ["VA", "-RA"]),
        ("Lluvia engelante (FZRA)",       10.0, 1000,   ["FZRA"]),
        ("Llovizna engelante (FZDZ)",     10.0, 800,    ["FZDZ"]),
        ("Funnel cloud (FC)",             10.0, None,   ["FC"]),
        ("Vis < 1.5 km",                   1.2, None,   []),
        ("Vis exactamente en limite 1.5",  1.5, None,   []),
        ("Techo < 500 ft",                10.0, 400,    []),
        ("Techo exactamente en limite",   10.0, 500,    []),
        ("Doble blocker: vis + TSRA",      1.0, None,   ["TSRA"]),
        ("Triple: vis + ceil + GR",        0.5, 300,    ["GR"]),
        ("Solo lluvia normal (no blocker)", 8.0, 2000,  ["-RA"]),
        ("Bruma (BR) no es blocker",      10.0, 1200,   ["BR"]),
    ]

    print(f"\n  {'Descripcion':<40} {'Block?':>6} {'Triggers'}")
    print(f"  {'-'*40} {'-'*6} {'-'*30}")

    for desc, vis, ceil, wx in test_cases:
        r = check_hard_blockers(vis, ceil, wx)
        trig_s = ", ".join(str(t[1]) for t in r.triggers) if r.triggers else "-"
        print(f"  {desc:<40} {'SI' if r.is_blocked else 'no':>6}   {trig_s}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 62)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    check("VFR normal: no bloqueado",
          not check_hard_blockers(10.0, None, []).is_blocked)
    check("TSRA: bloqueado",
          check_hard_blockers(10.0, 2000, ["TSRA"]).is_blocked)
    check("GR: bloqueado",
          check_hard_blockers(10.0, 2000, ["GR"]).is_blocked)
    check("VA: bloqueado",
          check_hard_blockers(10.0, None, ["VA"]).is_blocked)
    check("FZRA: bloqueado",
          check_hard_blockers(10.0, 1000, ["FZRA"]).is_blocked)
    check("FZDZ: bloqueado",
          check_hard_blockers(10.0, 1000, ["FZDZ"]).is_blocked)
    check("FC: bloqueado",
          check_hard_blockers(10.0, None, ["FC"]).is_blocked)
    check("vis=1.2 km: bloqueado",
          check_hard_blockers(1.2, None, []).is_blocked)
    check("vis=1.5 km: NO bloqueado (exactamente en limite)",
          not check_hard_blockers(1.5, None, []).is_blocked)
    check("vis=1.4 km: bloqueado (< 1.5)",
          check_hard_blockers(1.4, None, []).is_blocked)
    check("ceil=400 ft: bloqueado",
          check_hard_blockers(10.0, 400, []).is_blocked)
    check("ceil=500 ft: NO bloqueado (exactamente en limite)",
          not check_hard_blockers(10.0, 500, []).is_blocked)
    check("ceil=499 ft: bloqueado (< 500)",
          check_hard_blockers(10.0, 499, []).is_blocked)
    check("vis=None: no bloquea (sin dato)",
          not check_hard_blockers(None, None, []).is_blocked)
    check("ceil=None: no bloquea (CLR)",
          not check_hard_blockers(10.0, None, []).is_blocked)
    check("-RA (lluvia leve): NO bloqueado",
          not check_hard_blockers(8.0, 2000, ["-RA"]).is_blocked)
    check("BR (bruma): NO bloqueado",
          not check_hard_blockers(10.0, 1200, ["BR"]).is_blocked)

    # Multiples triggers
    r_multi = check_hard_blockers(0.5, 300, ["GR"])
    check("Triple blocker: 3 triggers",       len(r_multi.triggers) == 3)
    check("Triple blocker: tipos correctos",
          {t[0] for t in r_multi.triggers} == {"wx_token", "visibility", "ceiling"})

    # Token en lista mayor
    check("TSRA entre otros wx: bloqueado",
          check_hard_blockers(10.0, 1000, ["-RA", "TSRA", "BR"]).is_blocked)

    print("\n" + "=" * 62)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 62)
