"""
crosswind.py
============
Calcula componentes de viento cruzado y de proa dado el viento observado
y el rumbo magnetico de la pista.

Uso tipico
----------
    from parsers.metar_parser import ParsedWeather
    from features.crosswind import compute_crosswind

    result = compute_crosswind(weather, runway_heading=180)
    print(result.crosswind_kt, result.headwind_kt)

Convenciones
------------
- Angulos en grados magneticos (0-360).
- Velocidades en kt.
- Viento variable (wind_variable=True) o sin datos (wind_dir=None) devuelve
  crosswind = wind_spd_kt (worst-case conservador).
- Componente de costado positiva siempre (valor absoluto; no importa de que lado).
- Headwind positivo = viento de frente; negativo = viento de cola.
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CrosswindResult:
    """
    Resultado del calculo de componentes de viento para una pista dada.

    Todos los valores en kt. crosswind_kt siempre >= 0.
    headwind_kt positivo = viento de frente, negativo = viento de cola.
    """
    runway_heading    : int            # Rumbo magnetico de la pista (grados)
    wind_dir          : Optional[int]  # Direccion del viento (None si VRB)
    wind_spd_kt       : float          # Velocidad del viento
    wind_gust_kt      : Optional[float]

    crosswind_kt      : float          # Componente cruzada |V * sin(angulo)|
    headwind_kt       : float          # Componente de proa  V * cos(angulo)

    crosswind_gust_kt : Optional[float]  # Cruzada con rafaga (None si sin rafaga)
    headwind_gust_kt  : Optional[float]  # Proa con rafaga (None si sin rafaga)

    wind_variable     : bool = False   # True si el viento es variable
    is_worst_case     : bool = False   # True si se uso supuesto conservador (VRB)


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def compute_crosswind(
    wind_dir      : Optional[int],
    wind_spd_kt   : Optional[float],
    runway_heading: int,
    wind_gust_kt  : Optional[float] = None,
    wind_variable : bool = False,
) -> CrosswindResult:
    """
    Calcula crosswind y headwind para un rumbo de pista dado.

    Parameters
    ----------
    wind_dir       : Direccion del viento en grados (0-360). None si VRB.
    wind_spd_kt    : Velocidad media del viento en kt. None = sin dato.
    runway_heading : Rumbo magnetico de la pista en grados (0-359).
    wind_gust_kt   : Velocidad de rafaga en kt. None si no hay rafaga.
    wind_variable  : True si el viento fue reportado como variable (VRB).

    Returns
    -------
    CrosswindResult con componentes calculadas.

    Notas
    -----
    Si wind_variable=True o wind_dir=None, se asume que el viento sopla
    exactamente de costado (angulo=90 grados) → crosswind = wind_spd_kt.
    Es el peor caso conservador: maximiza el componente cruzado.
    """
    spd = wind_spd_kt if wind_spd_kt is not None else 0.0

    # ── Caso viento variable o sin direccion: peor caso conservador ───────────
    if wind_variable or wind_dir is None:
        logger.debug(
            f"Viento variable/sin dir para pista {runway_heading}: "
            f"usando worst-case (xwind={spd:.1f}kt)"
        )
        gust_xw = wind_gust_kt if wind_gust_kt is not None else None
        return CrosswindResult(
            runway_heading    = runway_heading,
            wind_dir          = wind_dir,
            wind_spd_kt       = spd,
            wind_gust_kt      = wind_gust_kt,
            crosswind_kt      = spd,
            headwind_kt       = 0.0,
            crosswind_gust_kt = gust_xw,
            headwind_gust_kt  = 0.0 if gust_xw is not None else None,
            wind_variable     = wind_variable,
            is_worst_case     = True,
        )

    # ── Calculo geometrico normal ─────────────────────────────────────────────
    # angulo = diferencia entre la direccion del viento y el rumbo de la pista
    # El viento "sopla desde" wind_dir, la pista apunta hacia runway_heading.
    # Convencion: angulo entre el vector viento y el eje de la pista.
    angle_deg = (wind_dir - runway_heading) % 360
    # Normalizar a [-180, 180] para que headwind negativo sea viento de cola
    if angle_deg > 180:
        angle_deg -= 360

    angle_rad = math.radians(angle_deg)

    xw = abs(spd * math.sin(angle_rad))
    hw = spd * math.cos(angle_rad)

    xw_gust = None
    hw_gust = None
    if wind_gust_kt is not None:
        xw_gust = abs(wind_gust_kt * math.sin(angle_rad))
        hw_gust = wind_gust_kt * math.cos(angle_rad)

    logger.debug(
        f"Viento {wind_dir}/{spd:.0f}kt pista {runway_heading}: "
        f"angulo={angle_deg:.0f}deg xwind={xw:.1f}kt hwind={hw:.1f}kt"
    )

    return CrosswindResult(
        runway_heading    = runway_heading,
        wind_dir          = wind_dir,
        wind_spd_kt       = spd,
        wind_gust_kt      = wind_gust_kt,
        crosswind_kt      = xw,
        headwind_kt       = hw,
        crosswind_gust_kt = xw_gust,
        headwind_gust_kt  = hw_gust,
        wind_variable     = False,
        is_worst_case     = False,
    )


def compute_crosswind_from_weather(weather, runway_heading: int) -> CrosswindResult:
    """
    Wrapper que extrae los campos de viento directamente de un ParsedWeather.

    Parameters
    ----------
    weather        : ParsedWeather (o ParsedTafPeriod con campos de viento).
    runway_heading : Rumbo magnetico de la pista en grados.
    """
    return compute_crosswind(
        wind_dir       = weather.wind_dir,
        wind_spd_kt    = weather.wind_spd_kt,
        runway_heading = runway_heading,
        wind_gust_kt   = weather.wind_gust_kt,
        wind_variable  = weather.wind_variable,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Seleccion automatica de pista favorable
# ──────────────────────────────────────────────────────────────────────────────

def favored_runway(
    runway_headings : list,
    wind_dir        : Optional[int],
    wind_spd_kt     : Optional[float],
    wind_variable   : bool = False,
    default         : int = 180,
) -> int:
    """
    Devuelve el rumbo de la cabecera mas conveniente para el viento dado.

    Considera AMBAS cabeceras de cada pista. Criterio: menor componente cruzada
    y, ante empate, mayor componente de proa (evita viento de cola). Esto modela
    que el piloto siempre usa la pista mas alineada con el viento.

    Viento variable, sin direccion o calmo -> primera cabecera (no hay preferencia).
    """
    cands = set()
    for h in runway_headings or []:
        if h is None:
            continue
        cands.add(int(h) % 360)
        cands.add((int(h) + 180) % 360)
    if not cands:
        return default
    if wind_variable or wind_dir is None or not wind_spd_kt:
        return sorted(cands)[0]

    best, best_key = default, None
    for h in cands:
        cw = compute_crosswind(wind_dir, wind_spd_kt, h, None, wind_variable)
        # minimizar |crosswind|, luego maximizar headwind (evita tailwind)
        key = (round(cw.crosswind_kt, 1), -round(cw.headwind_kt, 1))
        if best_key is None or key < best_key:
            best_key, best = key, h
    return best


# ──────────────────────────────────────────────────────────────────────────────
# Helper de evaluacion contra limite de aeronave
# ──────────────────────────────────────────────────────────────────────────────

def exceeds_crosswind_limit(result: CrosswindResult, max_crosswind_kt: float) -> bool:
    """
    Devuelve True si el crosswind (o la rafaga cruzada) supera el limite.

    Usa la rafaga si esta disponible, porque es el peor momento del aterrizaje.
    """
    effective_xw = result.crosswind_gust_kt if result.crosswind_gust_kt is not None else result.crosswind_kt
    return effective_xw >= max_crosswind_kt


def crosswind_risk_score(result: CrosswindResult, max_crosswind_kt: float) -> float:
    """
    Componente de riesgo por viento cruzado para el soft scoring.

    r_xwind = xw_efectivo / max_crosswind_kt, clampeado a [0, 1].
    La rafaga es el valor efectivo si esta disponible.

    Parameters
    ----------
    result           : CrosswindResult calculado previamente.
    max_crosswind_kt : Limite de la aeronave (12 kt para Alpha Trainer).

    Returns
    -------
    float en [0, 1]. 1.0 = en o sobre el limite.
    """
    if max_crosswind_kt <= 0:
        return 1.0

    effective_xw = result.crosswind_gust_kt if result.crosswind_gust_kt is not None else result.crosswind_kt
    return min(effective_xw / max_crosswind_kt, 1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    ALPHA_TRAINER_XWIND_MAX = 12.0  # kt

    print("=" * 65)
    print("  TEST: crosswind.py")
    print("=" * 65)

    # ── Casos de prueba ───────────────────────────────────────────────────────
    # Pista SACC: segun carta VAC, pista 15/33 (rumbo aprox 150/330 grados)
    RUNWAY_HDG = 150

    test_cases = [
        # (descripcion, wind_dir, wind_spd, wind_gust, wind_variable)
        ("Viento de frente exacto",      150, 10.0, None,  False),
        ("Viento de cola exacto",        330, 10.0, None,  False),
        ("Viento cruzado exacto 90 deg", 240,  8.0, None,  False),
        ("Viento cruzado exacto 90 deg",  60,  8.0, None,  False),
        ("Viento 45 grados sin rafaga",  195, 10.0, None,  False),
        ("Viento 45 grados con rafaga",  195,  8.0, 14.0,  False),
        ("Viento variable sin rafaga",   None,  6.0, None, True),
        ("Viento variable con rafaga",   None,  6.0, 13.0, True),
        ("Sin viento (calma)",           360,  0.0, None,  False),
        ("Viento fuerte cruzado",         60, 18.0, 22.0,  False),
    ]

    print(f"\n  Pista: {RUNWAY_HDG} grados | Limite Alpha Trainer: {ALPHA_TRAINER_XWIND_MAX} kt\n")
    print(f"  {'Descripcion':<35} {'WD':>4} {'WS':>5} {'WG':>5} "
          f"{'XW':>6} {'XWG':>6} {'HW':>6} {'R':>5} {'LIM?'}")
    print(f"  {'-'*35} {'-'*4} {'-'*5} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*5} {'-'*5}")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    results = []
    for desc, wd, ws, wg, wvar in test_cases:
        r = compute_crosswind(
            wind_dir=wd, wind_spd_kt=ws, runway_heading=RUNWAY_HDG,
            wind_gust_kt=wg, wind_variable=wvar
        )
        results.append((desc, r))
        score = crosswind_risk_score(r, ALPHA_TRAINER_XWIND_MAX)
        over  = "SI" if exceeds_crosswind_limit(r, ALPHA_TRAINER_XWIND_MAX) else "no"
        wd_s  = f"{wd:3}" if wd is not None else "VRB"
        wg_s  = f"{wg:5.1f}" if wg is not None else "  ---"
        xwg_s = f"{r.crosswind_gust_kt:6.1f}" if r.crosswind_gust_kt is not None else "   ---"
        print(f"  {desc:<35} {wd_s:>4} {ws:5.1f} {wg_s:>5} "
              f"{r.crosswind_kt:6.1f} {xwg_s:>6} {r.headwind_kt:6.1f} {score:5.2f} {over:>5}")

    # ── Verificaciones matematicas ────────────────────────────────────────────
    print("\n" + "-" * 65)
    print("  Verificaciones")

    r_front = compute_crosswind(wind_dir=150, wind_spd_kt=10.0, runway_heading=150)
    check("Viento de frente: crosswind~0", r_front.crosswind_kt < 0.01)
    check("Viento de frente: headwind=10", abs(r_front.headwind_kt - 10.0) < 0.01)

    r_tail = compute_crosswind(wind_dir=330, wind_spd_kt=10.0, runway_heading=150)
    check("Viento de cola: crosswind~0", r_tail.crosswind_kt < 0.01)
    check("Viento de cola: headwind=-10 (viento de cola)", abs(r_tail.headwind_kt + 10.0) < 0.01)

    r_90 = compute_crosswind(wind_dir=240, wind_spd_kt=8.0, runway_heading=150)  # 240-150=90 grados
    check("Viento 90 grados: crosswind=8", abs(r_90.crosswind_kt - 8.0) < 0.01)
    check("Viento 90 grados: headwind~0", abs(r_90.headwind_kt) < 0.01)

    r_45 = compute_crosswind(wind_dir=195, wind_spd_kt=10.0, runway_heading=150)
    check("Viento 45 grados: crosswind ~= 7.07", abs(r_45.crosswind_kt - 10 * math.sin(math.radians(45))) < 0.01)
    check("Viento 45 grados: headwind  ~= 7.07", abs(r_45.headwind_kt  - 10 * math.cos(math.radians(45))) < 0.01)

    r_vrb = compute_crosswind(wind_dir=None, wind_spd_kt=6.0, runway_heading=150, wind_variable=True)
    check("VRB: crosswind = wind_spd (worst-case)", abs(r_vrb.crosswind_kt - 6.0) < 0.01)
    check("VRB: is_worst_case=True", r_vrb.is_worst_case)

    r_gust = compute_crosswind(wind_dir=195, wind_spd_kt=8.0, runway_heading=150, wind_gust_kt=14.0)
    check("Rafaga: crosswind_gust_kt calculado", r_gust.crosswind_gust_kt is not None)
    # xwg = 14 * sin(45) ~= 9.9kt < 12kt -> NO excede
    check("Rafaga 14kt a 45 grados: xwg~9.9kt, no excede limite", not exceeds_crosswind_limit(r_gust, ALPHA_TRAINER_XWIND_MAX))

    r_over = compute_crosswind(wind_dir=60, wind_spd_kt=18.0, runway_heading=150, wind_gust_kt=22.0)
    check("Viento fuerte cruzado supera limite", exceeds_crosswind_limit(r_over, ALPHA_TRAINER_XWIND_MAX))

    score_zero = crosswind_risk_score(
        compute_crosswind(wind_dir=150, wind_spd_kt=2.0, runway_heading=150),
        ALPHA_TRAINER_XWIND_MAX
    )
    check("Score viento de frente 2kt ~= 0", score_zero < 0.01)

    score_full = crosswind_risk_score(
        compute_crosswind(wind_dir=60, wind_spd_kt=15.0, runway_heading=150),
        ALPHA_TRAINER_XWIND_MAX
    )
    check("Score viento cruzado 15kt = 1.0 (clampeado)", score_full == 1.0)

    print("\n" + "=" * 65)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 65)
