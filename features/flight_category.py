"""
flight_category.py
==================
Evalua la categoria de vuelo de un ParsedWeather en el contexto de los
minimos operacionales de la aeronave.

La funcion _compute_flight_category de metar_parser aplica los umbrales
ANAC/OACI puros. Este modulo agrega la capa de aeronave: compara la categoria
resultante contra los minimos del perfil (vis_min_km, ceiling_min_ft) y
determina si las condiciones permiten operar la aeronave especifica.

Uso tipico
----------
    from features.flight_category import evaluate_flight_category

    result = evaluate_flight_category(weather, vis_min_km=5.0, ceiling_min_ft=1000)
    if not result.within_aircraft_limits:
        print(f"NO GO por {result.limiting_factor}")
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Prioridad de categorias para comparacion: menor indice = peor condicion
_CAT_ORDER = ["LIFR", "IFR", "MVFR", "VFR"]


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FlightCategoryResult:
    """
    Resultado de la evaluacion de categoria con contexto de aeronave.

    `within_aircraft_limits` combina el resultado ANAC con los minimos
    operacionales del perfil de aeronave. Es el campo que consume el
    risk engine para el hard blocker por categoria.
    """
    flight_category        : Optional[str]  # "VFR" | "MVFR" | "IFR" | "LIFR"
    within_aircraft_limits : bool           # True si la aeronave puede operar

    # Componentes individuales
    vis_ok                 : bool           # vis >= vis_min_km (o None = sin restriccion)
    ceiling_ok             : bool           # ceiling >= ceil_min (o None = sin restriccion)

    # Valores usados
    visibility_km          : Optional[float]
    ceiling_ft             : Optional[int]
    vis_min_km             : float
    ceiling_min_ft         : int

    # Factor limitante principal para el mensaje al piloto
    limiting_factor        : str            # "visibility" | "ceiling" | "both" | "none"


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_flight_category(
    visibility_km  : Optional[float],
    ceiling_ft     : Optional[int],
    vis_min_km     : float = 5.0,
    ceiling_min_ft : int   = 1000,
) -> FlightCategoryResult:
    """
    Evalua la categoria de vuelo y los minimos operacionales de la aeronave.

    Parameters
    ----------
    visibility_km  : Visibilidad en km. None = sin dato (tratado como permisivo).
    ceiling_ft     : Techo en ft AGL. None = CLR o FEW/SCT (sin techo).
    vis_min_km     : Minimo de visibilidad de la aeronave (default: Alpha Trainer 5.0 km).
    ceiling_min_ft : Minimo de techo de la aeronave (default: Alpha Trainer 1000 ft).

    Returns
    -------
    FlightCategoryResult con categoria ANAC y evaluacion contra minimos.
    """
    # Aplicar umbrales ANAC/OACI
    flight_cat = _compute_flight_category_local(visibility_km, ceiling_ft)

    # Evaluar minimos de la aeronave
    # None significa cielo despejado o FEW/SCT: tratado como sin restriccion
    vis_ok     = (visibility_km  is None) or (visibility_km  >= vis_min_km)
    ceiling_ok = (ceiling_ft     is None) or (ceiling_ft     >= ceiling_min_ft)

    within = vis_ok and ceiling_ok

    if   not vis_ok and not ceiling_ok: limiting = "both"
    elif not vis_ok:                    limiting = "visibility"
    elif not ceiling_ok:                limiting = "ceiling"
    else:                               limiting = "none"

    logger.debug(
        f"FlightCat: vis={visibility_km} ceil={ceiling_ft} "
        f"-> {flight_cat} within={within} limit={limiting}"
    )

    return FlightCategoryResult(
        flight_category        = flight_cat,
        within_aircraft_limits = within,
        vis_ok                 = vis_ok,
        ceiling_ok             = ceiling_ok,
        visibility_km          = visibility_km,
        ceiling_ft             = ceiling_ft,
        vis_min_km             = vis_min_km,
        ceiling_min_ft         = ceiling_min_ft,
        limiting_factor        = limiting,
    )


def evaluate_from_weather(weather, vis_min_km: float = 5.0, ceiling_min_ft: int = 1000):
    """Wrapper que extrae vis y ceiling directamente de un ParsedWeather."""
    return evaluate_flight_category(
        visibility_km  = weather.visibility_km,
        ceiling_ft     = weather.ceiling_ft,
        vis_min_km     = vis_min_km,
        ceiling_min_ft = ceiling_min_ft,
    )


def is_worse_than(cat_a: Optional[str], cat_b: Optional[str]) -> bool:
    """
    Devuelve True si cat_a es una categoria mas restrictiva que cat_b.
    Util para comparar categorias de diferentes periodos o fuentes.
    """
    rank_a = _CAT_ORDER.index(cat_a) if cat_a in _CAT_ORDER else len(_CAT_ORDER)
    rank_b = _CAT_ORDER.index(cat_b) if cat_b in _CAT_ORDER else len(_CAT_ORDER)
    return rank_a < rank_b


# ──────────────────────────────────────────────────────────────────────────────
# Implementacion local de umbrales ANAC/OACI
# (replica la logica de metar_parser._compute_flight_category sin importarla)
# ──────────────────────────────────────────────────────────────────────────────

def _compute_flight_category_local(
    visibility_km: Optional[float],
    ceiling_ft   : Optional[int],
) -> Optional[str]:
    """
    Umbrales ANAC/OACI:
      VFR  : vis >= 5.0 km  AND ceil >= 1000 ft (o sin techo)
      MVFR : vis >= 3.0 km  AND ceil >=  500 ft (o sin techo)
      IFR  : vis >= 0.8 km  AND ceil >=  200 ft (o sin techo)
      LIFR : vis <  0.8 km   OR ceil <   200 ft

    None en cualquier campo se trata como sin restriccion (permisivo).
    """
    vis  = visibility_km if visibility_km is not None else 99.0
    ceil = ceiling_ft    if ceiling_ft    is not None else 99999

    if vis < 0.8 or ceil < 200:
        return "LIFR"
    if vis < 3.0 or ceil < 500:
        return "IFR"
    if vis < 5.0 or ceil < 1000:
        return "MVFR"
    return "VFR"


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    # Minimos Alpha Trainer
    VIS_MIN   = 5.0
    CEIL_MIN  = 1000

    print("=" * 65)
    print("  TEST: flight_category.py")
    print(f"  Minimos aeronave: VIS >= {VIS_MIN} km | CEIL >= {CEIL_MIN} ft")
    print("=" * 65)

    test_cases = [
        # (descripcion, vis_km, ceil_ft)
        ("CAVOK (vis=10, sin techo)",        10.0,  None),
        ("VFR justo (vis=5.0, ceil=1000)",    5.0,  1000),
        ("MVFR (vis=4.0, ceil=1200)",         4.0,  1200),
        ("MVFR bajo (vis=3.0, ceil=500)",     3.0,   500),
        ("IFR (vis=1.5, ceil=300)",           1.5,   300),
        ("IFR solo vis (vis=0.8, ceil=5000)", 0.8,  5000),
        ("IFR solo techo (vis=8, ceil=200)",  8.0,   200),
        ("LIFR vis (vis=0.5, ceil=300)",      0.5,   300),
        ("LIFR techo (vis=5, ceil=150)",      5.0,   150),
        ("LIFR ambos (vis=0.3, ceil=100)",    0.3,   100),
        ("Sin vis, con techo OK",            None,  2000),
        ("Sin techo, vis baja (MVFR)",        4.0,  None),
    ]

    print(f"\n  {'Descripcion':<40} {'Vis':>6} {'Ceil':>6} "
          f"{'Cat':>5} {'OK?':>5} {'Limite'}")
    print(f"  {'-'*40} {'-'*6} {'-'*6} {'-'*5} {'-'*5} {'-'*12}")

    for desc, vis, ceil in test_cases:
        r = evaluate_flight_category(vis, ceil, VIS_MIN, CEIL_MIN)
        vis_s  = f"{vis:.1f}" if vis  is not None else " None"
        ceil_s = str(ceil)    if ceil is not None else "None"
        ok_s   = "SI" if r.within_aircraft_limits else "NO"
        print(f"  {desc:<40} {vis_s:>6} {ceil_s:>6} "
              f"{r.flight_category:>5} {ok_s:>5} {r.limiting_factor}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 65)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # Categorias ANAC
    check("CAVOK -> VFR",
          evaluate_flight_category(10.0, None).flight_category == "VFR")
    check("vis=5.0, ceil=1000 -> VFR (limite exacto)",
          evaluate_flight_category(5.0, 1000).flight_category == "VFR")
    check("vis=4.9, ceil=1000 -> MVFR",
          evaluate_flight_category(4.9, 1000).flight_category == "MVFR")
    check("vis=3.0, ceil=500 -> MVFR (limite exacto)",
          evaluate_flight_category(3.0, 500).flight_category == "MVFR")
    check("vis=2.9, ceil=500 -> IFR",
          evaluate_flight_category(2.9, 500).flight_category == "IFR")
    check("vis=0.8, ceil=200 -> IFR (limite exacto)",
          evaluate_flight_category(0.8, 200).flight_category == "IFR")
    check("vis=0.7, ceil=200 -> LIFR",
          evaluate_flight_category(0.7, 200).flight_category == "LIFR")
    check("vis=5.0, ceil=199 -> LIFR (techo bajo)",
          evaluate_flight_category(5.0, 199).flight_category == "LIFR")

    # Minimos de aeronave (Alpha Trainer: 5.0 km / 1000 ft)
    check("CAVOK: within_aircraft_limits=True",
          evaluate_flight_category(10.0, None, 5.0, 1000).within_aircraft_limits)
    check("vis=5.0, ceil=1000: within=True (exactamente en limite)",
          evaluate_flight_category(5.0, 1000, 5.0, 1000).within_aircraft_limits)
    check("vis=4.9, ceil=1000: within=False (vis bajo minimo)",
          not evaluate_flight_category(4.9, 1000, 5.0, 1000).within_aircraft_limits)
    check("vis=6.0, ceil=900: within=False (techo bajo minimo)",
          not evaluate_flight_category(6.0, 900, 5.0, 1000).within_aircraft_limits)
    check("vis=4.0, ceil=800: limiting='both'",
          evaluate_flight_category(4.0, 800, 5.0, 1000).limiting_factor == "both")
    check("vis=4.0, ceil=1200: limiting='visibility'",
          evaluate_flight_category(4.0, 1200, 5.0, 1000).limiting_factor == "visibility")
    check("vis=6.0, ceil=900: limiting='ceiling'",
          evaluate_flight_category(6.0, 900, 5.0, 1000).limiting_factor == "ceiling")
    check("CAVOK: limiting='none'",
          evaluate_flight_category(10.0, None, 5.0, 1000).limiting_factor == "none")

    # is_worse_than
    check("is_worse_than('IFR', 'VFR') = True",
          is_worse_than("IFR", "VFR"))
    check("is_worse_than('VFR', 'IFR') = False",
          not is_worse_than("VFR", "IFR"))
    check("is_worse_than('LIFR', 'LIFR') = False",
          not is_worse_than("LIFR", "LIFR"))
    check("is_worse_than('MVFR', 'VFR') = True",
          is_worse_than("MVFR", "VFR"))

    print("\n" + "=" * 65)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 65)
