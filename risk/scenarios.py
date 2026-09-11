"""
scenarios.py
============
Bateria de escenarios de referencia para calibrar los umbrales de decision y
para el analisis de sensibilidad de los pesos.

Es la fuente de verdad compartida entre:
  - risk/calibration.py  (busca los umbrales t_go / t_caution optimos)
  - risk/sensitivity.py  (mide cuanto cambia el veredicto al perturbar los pesos)

Metodologia — ANCLAJE NORMATIVO (validez de constructo, NO validez empirica)
----------------------------------------------------------------------------
No se dispone de un conjunto de casos reales etiquetados por pilotos (METAR +
veredicto correcto). En ausencia de esa verdad de terreno, la referencia se
construye a partir de la NORMATIVA vigente y del criterio aeronautico estandar:

  * Cada escenario recibe una ETIQUETA NORMATIVA (GO / CAUTION / NO GO) derivada
    de una regla explicita y documentada (`normative_label`), NO del score R.
    La etiqueta es la "verdad" contra la cual se calibra; por eso debe ser
    independiente del propio motor de scoring.

  * La componente de visibilidad/techo se etiqueta con la MISMA categoria
    ANAC/OACI que ya calcula el sistema (`_compute_flight_category`), de modo
    que la referencia no introduce un criterio nuevo ni arbitrario:
        VFR            -> GO      (dentro de minimos legales VFR)
        VFR marginal   -> CAUTION (por debajo del minimo VFR, aun operable)
        IFR / bajo min -> NO GO   (claramente fuera de VFR)

  * El viento cruzado, las rafagas, la niebla, los fenomenos y la tendencia TAF
    votan por umbrales relativos al limite de CADA aeronave (regla de
    escalabilidad: nunca atado a un avion unico).

  * El veredicto normativo del escenario es el PEOR de todos los votos
    (filosofia worst-case, la misma del motor de decision).

IMPORTANTE: el score R depende de funciones r_i cuyas rampas (vis 3-8 km,
techo 500-2000 ft) NO coinciden con los cortes normativos (vis 5, techo 1000).
Por eso la etiqueta y el score son independientes y la concordancia entre ambos
es una medida real, no una tautologia.

ALCANCE DE ESA INDEPENDENCIA — leer antes de citar la concordancia
------------------------------------------------------------------
La afirmacion anterior vale PARA VISIBILIDAD Y TECHO, donde la etiqueta usa los
cortes de la categoria ANAC/OACI (5 km, 1000 ft) y el motor usa rampas que no
coinciden con ellos. Vale tambien para fenomenos wx y tendencia TAF.

NO vale para VIENTO CRUZADO ni para RAFAGAS. En esos dos factores la etiqueta
aplica las MISMAS fracciones del limite de la aeronave que la barrera
no-compensatoria de risk/soft_scoring.py. La coincidencia entre etiqueta y
motor en esos factores es POR CONSTRUCCION y no constituye evidencia
independiente: es la misma decision de diseno enunciada dos veces.

Se declara aca porque no hay forma de arreglarlo con mas trabajo: no existe
norma ANAC ni OACI que fije un factor de rafaga admisible para aviacion general,
de modo que no hay arbitro externo contra el cual contrastar. Lo unico que se
puede hacer es no presentarlo como validacion, y eso es lo que hace esta nota.

La concordancia global reportada por risk/calibration.py debe leerse con esta
salvedad: los escenarios cuyo veredicto de referencia esta determinado por
cruzado o rafaga miden consistencia interna, no acuerdo con una fuente externa.
"""

import math
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

try:
    from parsers.metar_parser     import ParsedWeather, _compute_flight_category
    from risk.aircraft_profiles   import get_profile
    from risk.soft_scoring        import compute_soft_score, SoftScoreResult
    from risk.weights             import (
        W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF,
        THRESHOLD_GO, THRESHOLD_CAUTION, _WX_SEVERITY,
    )
    from risk.hard_blockers       import check_hard_blockers_from_weather
    from features.crosswind       import compute_crosswind_from_weather
except ImportError:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from parsers.metar_parser     import ParsedWeather, _compute_flight_category
    from risk.aircraft_profiles   import get_profile
    from risk.soft_scoring        import compute_soft_score, SoftScoreResult
    from risk.weights             import (
        W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF,
        THRESHOLD_GO, THRESHOLD_CAUTION, _WX_SEVERITY,
    )
    from risk.hard_blockers       import check_hard_blockers_from_weather
    from features.crosswind       import compute_crosswind_from_weather


# ──────────────────────────────────────────────────────────────────────────────
# Orden canonico de los pesos (usado por calibracion y sensibilidad)
# ──────────────────────────────────────────────────────────────────────────────

WEIGHT_KEYS = ["vis", "ceil", "xwind", "gust", "wx", "fog", "taf"]

def default_weights() -> Dict[str, float]:
    """Pesos AHP actuales (fuente: risk/weights.py)."""
    return {
        "vis":   W_VIS,   "ceil": W_CEIL, "xwind": W_XWIND, "gust": W_GUST,
        "wx":    W_WX,    "fog":  W_FOG,  "taf":   W_TAF,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Definicion de un escenario
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    """Un caso meteorologico de referencia. Los defaults describen aire calmo VFR."""
    sid            : str
    group          : str
    desc           : str
    aircraft       : str            = "Cessna 152"
    runway_heading : int            = 360          # rumbo magnetico de la pista
    vis_km         : Optional[float] = 10.0
    ceiling_ft     : Optional[int]   = None
    wind_dir       : Optional[int]   = None
    wind_spd_kt    : Optional[float] = 0.0
    wind_gust_kt   : Optional[float] = None
    wind_variable  : bool            = False
    spread_c       : Optional[float] = 10.0
    wx_codes       : tuple           = ()
    r_taf          : float           = 0.0
    nwp_estimated  : bool            = False
    station_id     : str             = "TEST"


def build_weather(sc: Scenario) -> ParsedWeather:
    """Construye el ParsedWeather que consume el motor de scoring."""
    return ParsedWeather(
        source        = "nwp" if sc.nwp_estimated else "metar",
        station_id    = sc.station_id,
        obs_time      = 0,
        nwp_estimated = sc.nwp_estimated,
        wind_dir      = sc.wind_dir,
        wind_spd_kt   = sc.wind_spd_kt,
        wind_gust_kt  = sc.wind_gust_kt,
        wind_variable = sc.wind_variable,
        visibility_km = sc.vis_km,
        ceiling_ft    = sc.ceiling_ft,
        spread_c      = sc.spread_c,
        wx_codes      = list(sc.wx_codes),
        flight_category = _compute_flight_category(sc.vis_km, sc.ceiling_ft),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Etiqueta normativa (la "verdad" de referencia)
# ──────────────────────────────────────────────────────────────────────────────

_RANK = {"GO": 0, "CAUTION": 1, "NO GO": 2}
_RANK_INV = {0: "GO", 1: "CAUTION", 2: "NO GO"}


def _worst(*verdicts: str) -> str:
    """Devuelve el veredicto mas restrictivo (worst-case)."""
    return _RANK_INV[max(_RANK[v] for v in verdicts)]


def normative_label(sc: Scenario) -> str:
    """
    Veredicto de referencia derivado de normativa + criterio, NO del score.

    Es el peor voto entre estas componentes:
      1. Visibilidad/techo : categoria ANAC/OACI del sistema.
      2. Viento cruzado    : fraccion del maximo demostrado de la aeronave,
                             calculado sobre la RAFAGA (peor instante).
      3. Niebla            : spread termico <= 2 C -> deterioro probable.
      4. Fenomenos wx      : severidad tabulada (>=1.0 duro, >=0.6 moderado).
      5. Tendencia TAF     : r_taf >= 0.6 -> deterioro pronosticado.

    La RAFAGA no vota por separado: entra por el cruzado de rafaga (voto 2).
    """
    prof = get_profile(sc.aircraft)

    # 1. Referencia visual (categoria ANAC/OACI, misma fuente que el sistema)
    cat = _compute_flight_category(sc.vis_km, sc.ceiling_ft)
    vote_vis = {
        "VFR":              "GO",
        "VFR marginal":     "CAUTION",
        "IFR":              "NO GO",
        "IFR bajo mínimos": "NO GO",
    }.get(cat, "GO")

    # 2. Viento cruzado (efectivo = con rafaga si la hay), relativo al limite
    cw = compute_crosswind_from_weather(build_weather(sc), sc.runway_heading)
    xw_eff = cw.crosswind_gust_kt if cw.crosswind_gust_kt is not None else cw.crosswind_kt
    # ⚠ Igual que el voto de rafaga: estos cortes REPLICAN los del motor
    # (XWIND_CAUTION_FRACTION). No es una referencia independiente; ver la nota
    # de alcance en el encabezado de este modulo.
    xw_ratio = xw_eff / prof.crosswind_max_kt if prof.crosswind_max_kt > 0 else 1.0
    vote_xw = "NO GO" if xw_ratio >= 1.0 else "CAUTION" if xw_ratio >= 0.85 else "GO"

    # 3. Rafagas: SIN voto propio (septiembre 2026)
    #
    # La rafaga entra por el voto 2, que ya se calcula sobre el cruzado DE LA
    # RAFAGA. Un voto adicional sobre el delta crudo medía cuanto varia el
    # viento sin mirar hacia donde, y etiquetaba como no operable un dia con la
    # rafaga alineada con la pista, donde el avion no recibe carga lateral.
    # El motor dejo de vetar por ese criterio y la referencia lo acompaña: si no
    # lo hiciera, la bateria mediria el desacuerdo contra un criterio que el
    # propio proyecto descarto.

    # 3. Niebla por spread termico
    vote_fog = "CAUTION" if (sc.spread_c is not None and sc.spread_c <= 2.0) else "GO"

    # 4. Fenomenos wx
    sev = max((_WX_SEVERITY.get(c.strip().upper(), 0.0) for c in sc.wx_codes), default=0.0)
    vote_wx = "NO GO" if sev >= 1.0 else "CAUTION" if sev >= 0.6 else "GO"

    # 5. Tendencia TAF
    vote_taf = "CAUTION" if sc.r_taf >= 0.6 else "GO"

    return _worst(vote_vis, vote_xw, vote_fog, vote_wx, vote_taf)


# ──────────────────────────────────────────────────────────────────────────────
# Scoring y recombinacion con pesos/umbrales arbitrarios
# ──────────────────────────────────────────────────────────────────────────────

def score_components(sc: Scenario, personal_minima=None) -> SoftScoreResult:
    """
    Corre el motor de scoring real y devuelve el desglose por componente.

    `personal_minima` permite reproducir la bateria bajo los minimos personales
    de cada nivel de experiencia. Por defecto None (sin ajuste), que es la
    configuracion con la que se calibraron los umbrales: la etiqueta normativa
    de referencia es la de la regulacion y los limites de la aeronave, no la de
    un piloto concreto.
    """
    prof = get_profile(sc.aircraft)
    return compute_soft_score(build_weather(sc), sc.runway_heading, prof,
                              taf_r_taf=sc.r_taf, personal_minima=personal_minima)


def recombine(comp: SoftScoreResult, weights: Dict[str, float]) -> float:
    """
    Recalcula R con un vector de pesos arbitrario a partir de los r_i ya
    computados (evita re-correr el pipeline por cada perturbacion).
    """
    r = (
        weights["vis"]   * comp.r_vis   +
        weights["ceil"]  * comp.r_ceil  +
        weights["xwind"] * comp.r_xwind +
        weights["gust"]  * comp.r_gust  +
        weights["wx"]    * comp.r_wx    +
        weights["fog"]   * comp.r_fog   +
        weights["taf"]   * comp.r_taf
    )
    return min(max(r, 0.0), 1.0)


def verdict(r_total: float, t_go: float, t_caution: float) -> str:
    """Aplica un par de umbrales arbitrario a un R (solo soft score)."""
    if r_total < t_go:
        return "GO"
    if r_total < t_caution:
        return "CAUTION"
    return "NO GO"


def is_hard_blocked(sc: Scenario) -> bool:
    """True si el escenario dispara un hard blocker (NO GO sin importar el score)."""
    return check_hard_blockers_from_weather(build_weather(sc)).is_blocked


def system_verdict(sc: Scenario, comp: SoftScoreResult,
                   r_total: float, t_go: float, t_caution: float) -> str:
    """
    Veredicto del sistema COMPLETO tal como lo devuelve decision/engine.py:
      1. Hard blockers    -> NO GO inmediato.
      2. Score compensatorio (umbrales sobre R).
      3. Piso conjuntivo (barrera no-compensatoria del soft scoring).
    El veredicto final es el peor entre (2) y (3).
    """
    if is_hard_blocked(sc):
        return "NO GO"
    return _worst(verdict(r_total, t_go, t_caution), comp.guardrail_floor)


# ──────────────────────────────────────────────────────────────────────────────
# BATERIA DE ESCENARIOS DE REFERENCIA
# ──────────────────────────────────────────────────────────────────────────────
# Convenciones de viento para razonar el cruzado:
#   wind_dir == runway_heading            -> viento de frente (cruzado ~0)
#   wind_dir == runway_heading + 90       -> cruzado puro (cruzado = velocidad)
# Se cubren las 5 aeronaves para respetar la escalabilidad.

REFERENCE_SCENARIOS: List[Scenario] = [

    # ── GRUPO A · VFR pleno (esperado GO) ────────────────────────────────────
    Scenario("A1", "VFR pleno", "CAVOK, viento de frente suave",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, ceiling_ft=None, wind_dir=360, wind_spd_kt=6.0, spread_c=12.0),
    Scenario("A2", "VFR pleno", "Cielo despejado, brisa cruzada leve",
             aircraft="Pipistrel Alpha Trainer", runway_heading=180,
             vis_km=10.0, ceiling_ft=None, wind_dir=210, wind_spd_kt=8.0, spread_c=9.0),
    Scenario("A3", "VFR pleno", "Techo alto BKN 3500, viento de frente",
             aircraft="Cessna 172 Skyhawk", runway_heading=90,
             vis_km=10.0, ceiling_ft=3500, wind_dir=90, wind_spd_kt=10.0, spread_c=7.0),
    Scenario("A4", "VFR pleno", "Vis 8 km justa, aire calmo",
             aircraft="Diamond DA40", runway_heading=270,
             vis_km=8.0, ceiling_ft=None, wind_dir=None, wind_spd_kt=0.0, spread_c=11.0),
    Scenario("A5", "VFR pleno", "Buen dia, cruzado bajo dentro de limites",
             aircraft="Piper PA-28 Cherokee", runway_heading=360,
             vis_km=10.0, ceiling_ft=4000, wind_dir=30, wind_spd_kt=9.0, spread_c=8.0),
    Scenario("A6", "VFR pleno", "Vis 9 km, SCT 4000 (no es techo), viento frente",
             aircraft="Cessna 152", runway_heading=180,
             vis_km=9.0, ceiling_ft=None, wind_dir=180, wind_spd_kt=7.0, spread_c=6.0),

    # ── GRUPO B · VFR marginal (esperado CAUTION) ────────────────────────────
    Scenario("B1", "VFR marginal", "Vis 4 km (bajo minimo VFR), resto ok",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=4.0, ceiling_ft=None, wind_dir=360, wind_spd_kt=6.0, spread_c=6.0),
    Scenario("B2", "VFR marginal", "Vis 4.5 km, techo 1200, calmo",
             aircraft="Cessna 172 Skyhawk", runway_heading=90,
             vis_km=4.5, ceiling_ft=1200, wind_dir=90, wind_spd_kt=5.0, spread_c=7.0),
    Scenario("B3", "VFR marginal", "Techo 800 ft (bajo minimo), vis buena",
             aircraft="Piper PA-28 Cherokee", runway_heading=180,
             vis_km=10.0, ceiling_ft=800, wind_dir=180, wind_spd_kt=6.0, spread_c=6.0),
    Scenario("B4", "VFR marginal", "Vis 3.5 km + techo 900, viento frente",
             aircraft="Diamond DA40", runway_heading=270,
             vis_km=3.5, ceiling_ft=900, wind_dir=270, wind_spd_kt=8.0, spread_c=5.0),
    Scenario("B5", "VFR marginal", "Vis 3 km justa (borde MVFR/IFR)",
             aircraft="Pipistrel Alpha Trainer", runway_heading=360,
             vis_km=3.0, ceiling_ft=None, wind_dir=360, wind_spd_kt=5.0, spread_c=8.0),
    Scenario("B6", "VFR marginal", "Techo 700 + llovizna leve, viento frente",
             aircraft="Cessna 172 Skyhawk", runway_heading=90,
             vis_km=7.0, ceiling_ft=700, wind_dir=90, wind_spd_kt=7.0, spread_c=3.0,
             wx_codes=("-DZ",)),

    # ── GRUPO C · IFR / claramente fuera de VFR (esperado NO GO) ──────────────
    Scenario("C1", "IFR", "Vis 2 km + techo 600, viento frente",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=2.0, ceiling_ft=600, wind_dir=360, wind_spd_kt=5.0, spread_c=4.0,
             wx_codes=("BR",)),
    Scenario("C2", "IFR", "Vis 2.5 km, cielo cubierto 900",
             aircraft="Cessna 172 Skyhawk", runway_heading=90,
             vis_km=2.5, ceiling_ft=900, wind_dir=90, wind_spd_kt=6.0, spread_c=3.0),
    Scenario("C3", "IFR", "Techo 400 ft (bien bajo), vis 5",
             aircraft="Piper PA-28 Cherokee", runway_heading=180,
             vis_km=5.0, ceiling_ft=400, wind_dir=180, wind_spd_kt=6.0, spread_c=4.0),
    Scenario("C4", "IFR", "Niebla: vis 1.6 km, techo 500, spread 0.5",
             aircraft="Diamond DA40", runway_heading=270,
             vis_km=1.6, ceiling_ft=500, wind_dir=270, wind_spd_kt=4.0, spread_c=0.5,
             wx_codes=("BR",)),
    Scenario("C5", "IFR", "Vis 2 km + lluvia moderada + techo 700",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=2.0, ceiling_ft=700, wind_dir=360, wind_spd_kt=8.0, spread_c=2.0,
             wx_codes=("RA", "BR")),

    # ── GRUPO D · Viento cruzado (gradiente por aeronave) ─────────────────────
    Scenario("D1", "Viento", "Cruzado ~4 kt (35% del limite C152) -> GO",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=4.0, spread_c=9.0),
    Scenario("D2", "Viento", "Cruzado ~8 kt (66% del limite C152) -> CAUTION",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=8.0, spread_c=9.0),
    Scenario("D3", "Viento", "Cruzado ~13 kt (>limite C152 12) -> NO GO",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=13.0, spread_c=9.0),
    Scenario("D4", "Viento", "Cruzado 13 kt pero avion DA40 (limite 20) -> CAUTION",
             aircraft="Diamond DA40", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=13.0, spread_c=9.0),
    Scenario("D5", "Viento", "Cruzado 18 kt en PA-28 (limite 17) -> NO GO",
             aircraft="Piper PA-28 Cherokee", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=18.0, spread_c=9.0),
    Scenario("D6", "Viento", "Cruzado 10 kt en Alpha (limite 12) -> CAUTION",
             aircraft="Pipistrel Alpha Trainer", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=10.0, spread_c=9.0),

    # ── GRUPO E · Rafagas ────────────────────────────────────────────────────
    # Los cortes de este grupo son 0.85 x gust_max (CAUTION) y 1.5 x (NO GO).
    # E1 y E2 quedan por debajo del primero: son dias ventosos normales, no
    # anomalias. E4 y E5 existen para que la bateria siga ejercitando los dos
    # pisos de la barrera despues de que el criterio se elevo (antes bastaba E2,
    # que con el corte anterior de 0.50 caia en la banda de CAUTION).
    Scenario("E1", "Rafagas", "Frente 10 kt con rafaga 18 (delta 8, 40% C152) -> GO",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=360, wind_spd_kt=10.0, wind_gust_kt=18.0, spread_c=8.0),
    Scenario("E2", "Rafagas", "Frente 12 kt con rafaga 24 (delta 12, 60% C152) -> GO",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=360, wind_spd_kt=12.0, wind_gust_kt=24.0, spread_c=8.0),
    Scenario("E3", "Rafagas", "Cruzado 8 kt racheado a 16 en Alpha -> CAUTION/NO GO",
             aircraft="Pipistrel Alpha Trainer", runway_heading=360,
             vis_km=10.0, wind_dir=90, wind_spd_kt=8.0, wind_gust_kt=16.0, spread_c=8.0),
    Scenario("E4", "Rafagas", "Frente 12 kt con rafaga 30 (delta 18, 90% C152) -> CAUTION",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=360, wind_spd_kt=12.0, wind_gust_kt=30.0, spread_c=8.0),
    Scenario("E5", "Rafagas", "Frente 15 kt con rafaga 47 (delta 32, 160% C152) -> NO GO",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=10.0, wind_dir=360, wind_spd_kt=15.0, wind_gust_kt=47.0, spread_c=8.0),

    # ── GRUPO F · Niebla / spread bajo ───────────────────────────────────────
    Scenario("F1", "Niebla", "Vis 6, spread 1.5 + BR, viento frente -> CAUTION",
             aircraft="Cessna 172 Skyhawk", runway_heading=360,
             vis_km=6.0, ceiling_ft=None, wind_dir=360, wind_spd_kt=4.0, spread_c=1.5,
             wx_codes=("BR",)),
    Scenario("F2", "Niebla", "Vis 8, spread 2.0 justo, aire casi calmo",
             aircraft="Diamond DA40", runway_heading=360,
             vis_km=8.0, ceiling_ft=None, wind_dir=None, wind_spd_kt=2.0, spread_c=2.0),

    # ── GRUPO G · Fenomenos wx (no hard-blocker) ─────────────────────────────
    Scenario("G1", "Fenomenos", "Lluvia leve -RA, vis 8, viento frente -> GO",
             aircraft="Cessna 172 Skyhawk", runway_heading=360,
             vis_km=8.0, ceiling_ft=2500, wind_dir=360, wind_spd_kt=8.0, spread_c=4.0,
             wx_codes=("-RA",)),
    Scenario("G2", "Fenomenos", "Nieve SN moderada, vis 6, techo 1500 -> CAUTION",
             aircraft="Cessna 172 Skyhawk", runway_heading=360,
             vis_km=6.0, ceiling_ft=1500, wind_dir=360, wind_spd_kt=8.0, spread_c=3.0,
             wx_codes=("SN",)),

    # ── GRUPO H · Tendencia TAF ──────────────────────────────────────────────
    Scenario("H1", "TAF", "Ahora VFR pleno pero TEMPO a IMC en ventana (r_taf 0.75)",
             aircraft="Cessna 172 Skyhawk", runway_heading=360,
             vis_km=9.0, ceiling_ft=None, wind_dir=360, wind_spd_kt=7.0, spread_c=6.0,
             r_taf=0.75),
    Scenario("H2", "TAF", "VFR con leve tendencia TAF (r_taf 0.3) -> GO",
             aircraft="Cessna 172 Skyhawk", runway_heading=360,
             vis_km=9.0, ceiling_ft=None, wind_dir=360, wind_spd_kt=7.0, spread_c=6.0,
             r_taf=0.30),

    # (El GRUPO I probaba la penalizacion orografica fija de un unico aerodromo.
    #  Esa penalizacion se elimino por no ser generalizable a los 561 aerodromos
    #  del pais, y con ella sus dos escenarios, que sin el delta eran duplicados
    #  de casos ya cubiertos por los grupos A y B.)

    # ── GRUPO J · Combinaciones realistas multifactor ────────────────────────
    Scenario("J1", "Combinado", "Vis 5 justa + cruzado 7 + techo 1100 (frontera VFR)",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=5.0, ceiling_ft=1100, wind_dir=60, wind_spd_kt=8.0, spread_c=4.0),
    Scenario("J2", "Combinado", "Vis 3.5 + cruzado 9 + rafaga + techo 900",
             aircraft="Cessna 172 Skyhawk", runway_heading=360,
             vis_km=3.5, ceiling_ft=900, wind_dir=70, wind_spd_kt=10.0, wind_gust_kt=18.0,
             spread_c=3.0, wx_codes=("BR",)),
    Scenario("J3", "Combinado", "Deterioro fuerte: vis 2 + techo 500 + cruzado limite",
             aircraft="Cessna 152", runway_heading=360,
             vis_km=2.0, ceiling_ft=500, wind_dir=90, wind_spd_kt=12.0, spread_c=1.0,
             wx_codes=("RA", "BR")),
    Scenario("J4", "Combinado", "VFR bueno con leve reduccion: vis 7 + techo 1800",
             aircraft="Diamond DA40", runway_heading=360,
             vis_km=7.0, ceiling_ft=1800, wind_dir=30, wind_spd_kt=10.0, spread_c=5.0),
]


# ──────────────────────────────────────────────────────────────────────────────
# Utilidades de evaluacion de la bateria
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_battery(personal_minima=None) -> List[Tuple[Scenario, SoftScoreResult, str]]:
    """Devuelve [(escenario, componentes, etiqueta_normativa), ...] para toda la bateria."""
    out = []
    for sc in REFERENCE_SCENARIOS:
        comp = score_components(sc, personal_minima=personal_minima)
        label = normative_label(sc)
        out.append((sc, comp, label))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Script de inspeccion standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    w = default_weights()
    rows = evaluate_battery()

    print("=" * 92)
    print("  BATERIA DE ESCENARIOS DE REFERENCIA — anclaje normativo ANAC/OACI")
    print(f"  Umbrales actuales: t_go={THRESHOLD_GO}  t_caution={THRESHOLD_CAUTION}")
    print("=" * 92)
    print(f"  {'ID':<4}{'Grupo':<14}{'R':>7}  {'Sistema':<9}{'Norma':<9} {'Descripcion'}")
    print(f"  {'-'*3:<4}{'-'*13:<14}{'-'*6:>7}  {'-'*8:<9}{'-'*8:<9} {'-'*30}")

    agree = 0
    dangerous = 0   # sistema menos restrictivo que la norma (sub-aviso)
    for sc, comp, label in rows:
        r = recombine(comp, w)
        sysv = system_verdict(sc, comp, r, THRESHOLD_GO, THRESHOLD_CAUTION)
        hb = "*" if is_hard_blocked(sc) else " "
        flag = "  OK" if sysv == label else ("  <-- SUB-AVISO" if _RANK[sysv] < _RANK[label] else "  (mas conserv.)")
        if sysv == label:
            agree += 1
        elif _RANK[sysv] < _RANK[label]:
            dangerous += 1
        print(f"  {sc.sid:<4}{sc.group:<14}{r:>7.3f}{hb} {sysv:<9}{label:<9} {sc.desc}{flag}")

    n = len(rows)
    print("-" * 92)
    print(f"  (*) = hard-blocked (NO GO por bloqueo duro, no por umbral)")
    print(f"  Concordancia sistema vs norma: {agree}/{n} ({100*agree/n:.0f}%)   "
          f"Sub-avisos (peligrosos): {dangerous}/{n}")
    print("=" * 92)
