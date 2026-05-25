"""
taf_parser.py
=============
Transforma RawTaf / RawTafPeriod (datos crudos de aviationweather.gov) en
ParsedTaf / ParsedTafPeriod, los tipos que consume taf_window.py para el
analisis temporal del pronostico en la ventana de vuelo.

Modelo de datos TAF
-------------------
Un TAF tiene entre 2 y 10+ periodos. El primer periodo es la condicion BASE;
los siguientes son cambios superpuestos:

    TEMPO   : deterioro temporal (< 60 min de cada ocurrencia, < 50% del periodo)
    BECMG   : transicion permanente a nuevas condiciones
    PROB30/40: condiciones con probabilidad 30% o 40%

Los campos no especificados en un periodo TEMPO/BECMG/PROB se heredan del
periodo BASE. Este modulo parsea cada periodo de forma independiente con sus
propios campos (None = no especificado en ese periodo). La herencia la resuelve
taf_window.py al construir las condiciones efectivas de cada slot temporal.

Uso tipico
----------
    from data.fetcher_aviationweather import AviationWeatherFetcher
    from parsers.taf_parser import TafParser

    fetcher = AviationWeatherFetcher()
    parser  = TafParser()

    raw_taf = fetcher.get_taf("SACO")
    taf     = parser.parse(raw_taf)

    for periodo in taf.periods:
        print(periodo.change_indicator, periodo.flight_category)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

try:
    from parsers.metar_parser import (
        ParsedWeather,
        _parse_visibility_km,
        _parse_sky_layers,
        _extract_ceiling_ft,
        _parse_wx_codes,
        _compute_flight_category,
    )
except ImportError:
    from metar_parser import (
        ParsedWeather,
        _parse_visibility_km,
        _parse_sky_layers,
        _extract_ceiling_ft,
        _parse_wx_codes,
        _compute_flight_category,
    )

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

# Indicadores de cambio que representan deterioro transitorio o probabilistico.
# Estos son los que elevan el score de riesgo TAF en el risk engine.
TRANSIENT_CHANGES = {"TEMPO", "PROB30", "PROB40", "PROB"}


# ──────────────────────────────────────────────────────────────────────────────
# Modelos de datos TAF
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ParsedTafPeriod:
    """
    Un periodo individual del TAF con todas las variables normalizadas.

    Los campos que valen None no fueron especificados en ese periodo del TAF;
    taf_window.py los resuelve heredando del periodo BASE cuando lo necesita.
    """

    # ── Ventana temporal ──────────────────────────────────────────────────────
    time_from        : int             # Unix timestamp UTC inicio del periodo
    time_to          : int             # Unix timestamp UTC fin del periodo

    # ── Tipo de cambio ────────────────────────────────────────────────────────
    change_indicator : Optional[str]   # None=BASE, "TEMPO", "BECMG", "PROB30", "PROB40"
    probability      : Optional[int]   # 30 o 40 para periodos PROB; None si no aplica
    is_transient     : bool            # True si es TEMPO o PROB (deterioro transitorio)

    # ── Viento ────────────────────────────────────────────────────────────────
    wind_dir         : Optional[int]   = None
    wind_spd_kt      : Optional[float] = None
    wind_gust_kt     : Optional[float] = None
    wind_variable    : bool            = False

    # ── Visibilidad ───────────────────────────────────────────────────────────
    visibility_km    : Optional[float] = None

    # ── Techo y nubosidad ─────────────────────────────────────────────────────
    ceiling_ft       : Optional[int]   = None
    sky_layers       : list            = field(default_factory=list)

    # ── Fenomenos ─────────────────────────────────────────────────────────────
    wx_codes         : list            = field(default_factory=list)

    # ── Categoria de vuelo (ANAC/OACI) ────────────────────────────────────────
    flight_category  : Optional[str]   = None


@dataclass
class ParsedTaf:
    """
    TAF completo con todos sus periodos parseados y normalizados.

    La lista de periodos esta ordenada cronologicamente por time_from.
    El primer elemento (change_indicator=None) siempre es el periodo BASE.
    """
    station_id  : str
    raw_string  : str
    issue_time  : str                    # ISO string de emision
    valid_from  : int                    # Unix UTC inicio de validez
    valid_to    : int                    # Unix UTC fin de validez
    periods     : list                   # list[ParsedTafPeriod]
    source      : str = "aviationweather.gov"


# ──────────────────────────────────────────────────────────────────────────────
# TafParser
# ──────────────────────────────────────────────────────────────────────────────

class TafParser:
    """
    Convierte RawTaf en ParsedTaf.

    No tiene estado: se puede instanciar una vez y reutilizar para multiples TAFs.
    """

    def parse(self, raw) -> ParsedTaf:
        """
        Parsea un RawTaf y devuelve un ParsedTaf con todos sus periodos normalizados.

        Parameters
        ----------
        raw : RawTaf
            Objeto crudo devuelto por AviationWeatherFetcher.

        Returns
        -------
        ParsedTaf con periodos ordenados cronologicamente.
        """
        periods = [self._parse_period(p) for p in (raw.periods or [])]

        # Garantizar orden cronologico independientemente de como lleguen de la API
        periods.sort(key=lambda p: p.time_from)

        logger.debug(
            f"TAF {raw.icao_id}: {len(periods)} periodos, "
            f"validez {raw.valid_from} -> {raw.valid_to}"
        )

        return ParsedTaf(
            station_id = raw.icao_id,
            raw_string = raw.raw_string,
            issue_time = raw.issue_time,
            valid_from = raw.valid_from,
            valid_to   = raw.valid_to,
            periods    = periods,
            source     = raw.source,
        )

    def _parse_period(self, raw_p) -> ParsedTafPeriod:
        """Convierte un RawTafPeriod en ParsedTafPeriod."""
        change_ind, probability = _normalize_change_indicator(
            raw_p.change_indicator,
            raw_p.probability,
        )
        is_transient = change_ind in TRANSIENT_CHANGES if change_ind else False

        vis_km     = _parse_visibility_km(raw_p.visibility)
        sky_layers = _parse_sky_layers(raw_p.sky_condition)
        ceiling_ft = _extract_ceiling_ft(sky_layers)
        wx_codes   = _parse_wx_codes(raw_p.wx_string)
        wind_var   = _is_vrb_wind(raw_p.wind_dir, raw_p.wind_spd_kt)

        # Calcular flight_category solo cuando hay datos suficientes en el periodo.
        # Periodos TEMPO/BECMG parciales (sin vis ni ceiling) se dejan como None;
        # taf_window.py los resuelve con herencia del BASE.
        if vis_km is not None or ceiling_ft is not None:
            flight_cat = _compute_flight_category(vis_km, ceiling_ft)
        else:
            flight_cat = None

        return ParsedTafPeriod(
            time_from        = raw_p.time_from,
            time_to          = raw_p.time_to,
            change_indicator = change_ind,
            probability      = probability,
            is_transient     = is_transient,
            wind_dir         = raw_p.wind_dir if not wind_var else None,
            wind_spd_kt      = float(raw_p.wind_spd_kt) if raw_p.wind_spd_kt is not None else None,
            wind_gust_kt     = float(raw_p.wind_gust_kt) if raw_p.wind_gust_kt is not None else None,
            wind_variable    = wind_var,
            visibility_km    = vis_km,
            ceiling_ft       = ceiling_ft,
            sky_layers       = sky_layers,
            wx_codes         = wx_codes,
            flight_category  = flight_cat,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_change_indicator(
    indicator   : Optional[str],
    probability : Optional[int],
) -> tuple:
    """
    Normaliza el indicador de cambio y la probabilidad.

    La API de AWC a veces codifica la probabilidad dentro del indicador
    ("PROB30", "PROB40") y otras veces como campo separado ("PROB" + probability=30).
    Este helper unifica ambas formas.

    Returns
    -------
    (change_indicator_normalizado, probability_int_o_None)
    """
    if indicator is None:
        return None, None

    ind = indicator.strip().upper()

    # Caso: "PROB30" o "PROB40" embebido en el indicador
    if ind.startswith("PROB") and len(ind) > 4:
        try:
            prob_val = int(ind[4:])
            return ind, prob_val
        except ValueError:
            pass

    # Caso: "PROB" separado con probabilidad en campo aparte
    if ind == "PROB" and probability is not None:
        return f"PROB{probability}", probability

    return ind, probability


def _is_vrb_wind(wind_dir: Optional[int], wind_spd_kt: Optional[int]) -> bool:
    """
    Detecta viento variable en periodos TAF.
    En TAF, los vientos VRB se codifican como wdir=0 con speed muy bajo,
    o directamente como 0/None. Sin el raw string, usamos speed < 3 kt como proxy.
    """
    if wind_dir == 0 and wind_spd_kt is not None and wind_spd_kt < 3:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    from datetime import datetime, timezone

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from data.fetcher_aviationweather import AviationWeatherFetcher

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("  TEST: TafParser")
    print("=" * 60)

    fetcher = AviationWeatherFetcher(mock=True)
    parser  = TafParser()

    raw_taf = fetcher.get_taf("SACO")
    taf     = parser.parse(raw_taf)

    # ── Cabecera del TAF ──────────────────────────────────────────────────────
    v_from = datetime.fromtimestamp(taf.valid_from, tz=timezone.utc).strftime("%d/%m %H:%M")
    v_to   = datetime.fromtimestamp(taf.valid_to,   tz=timezone.utc).strftime("%d/%m %H:%M")

    print(f"\n  Aeropuerto  : {taf.station_id}")
    print(f"  Emitido     : {taf.issue_time}")
    print(f"  Validez     : {v_from} -> {v_to} UTC")
    print(f"  Raw TAF     : {taf.raw_string}")
    print(f"  Periodos    : {len(taf.periods)}")

    # ── Tabla de periodos ─────────────────────────────────────────────────────
    print(f"\n  {'#':<3} {'Desde':>8} {'Hasta':>8} {'Tipo':<10} "
          f"{'Cat':>5} {'Vis km':>7} {'Ceil ft':>8} {'Viento':>12} {'WX'}")
    print(f"  {'-'*3} {'-'*8} {'-'*8} {'-'*10} {'-'*5} {'-'*7} {'-'*8} {'-'*12} {'-'*15}")

    for i, p in enumerate(taf.periods):
        t_from = datetime.fromtimestamp(p.time_from, tz=timezone.utc).strftime("%d/%m %H:%M")
        t_to   = datetime.fromtimestamp(p.time_to,   tz=timezone.utc).strftime("%H:%M")

        tipo   = p.change_indicator or "BASE"
        if p.probability:
            tipo = f"PROB{p.probability}"

        cat_str  = p.flight_category or "---"
        vis_str  = f"{p.visibility_km:.1f}" if p.visibility_km is not None else "---"
        ceil_str = str(p.ceiling_ft) if p.ceiling_ft is not None else "CLR"
        wind_str = (
            f"{p.wind_dir or 0:>3}/{p.wind_spd_kt or 0:.0f}kt"
            + (f"G{p.wind_gust_kt:.0f}" if p.wind_gust_kt else "")
        )
        wx_str   = " ".join(p.wx_codes) if p.wx_codes else "-"
        flag     = " [TRANSITORIO]" if p.is_transient else ""

        print(f"  {i+1:<3} {t_from:>8} {t_to:>8} {tipo:<10} "
              f"{cat_str:>5} {vis_str:>7} {ceil_str:>8} {wind_str:>12} {wx_str}{flag}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("  Verificaciones")

    all_pass = True

    def check(descripcion, condicion):
        global all_pass
        all_pass = all_pass and condicion
        print(f"  [{'OK' if condicion else 'FALLO'}] {descripcion}")

    # Periodo BASE
    base = next((p for p in taf.periods if p.change_indicator is None), None)
    check("Existe periodo BASE (change_indicator=None)", base is not None)
    check("BASE es VFR", base is not None and base.flight_category == "VFR")
    check("BASE no es transitorio", base is not None and not base.is_transient)

    # Periodo TEMPO con TSRA
    tempo = next((p for p in taf.periods if p.change_indicator == "TEMPO"), None)
    check("Existe periodo TEMPO", tempo is not None)
    check("TEMPO contiene 'TSRA' en wx_codes", tempo is not None and "TSRA" in tempo.wx_codes)
    check("TEMPO es transitorio (is_transient=True)", tempo is not None and tempo.is_transient)
    check("TEMPO degrada la categoria (no VFR)", tempo is not None and tempo.flight_category not in (None, "VFR"))

    # Periodo BECMG
    becmg = next((p for p in taf.periods if p.change_indicator == "BECMG"), None)
    check("Existe periodo BECMG", becmg is not None)
    check("BECMG NO es transitorio", becmg is not None and not becmg.is_transient)

    # Normalizacion de indicadores PROB
    check("PROB30 normalizado correctamente",
          _normalize_change_indicator("PROB30", None) == ("PROB30", 30))
    check("PROB separado normalizado correctamente",
          _normalize_change_indicator("PROB", 40) == ("PROB40", 40))
    check("BASE (None) normalizado correctamente",
          _normalize_change_indicator(None, None) == (None, None))

    # Todos los periodos tienen timestamps validos
    check("Todos los periodos tienen time_from < time_to",
          all(p.time_from < p.time_to for p in taf.periods))

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
