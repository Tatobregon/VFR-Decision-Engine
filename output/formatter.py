"""
formatter.py
============
Formatea el DecisionResult del motor en texto legible para el piloto.

La funcion principal es `format_decision(result)`. Produce un bloque de
texto multi-linea con:
  - Cabecera: estacion, hora, fuente (METAR / NWP)
  - Decision: GO / CAUTION / NO GO
  - Motivo si es hard blocker
  - Proxima ventana GO si esta disponible
  - Resumen de condiciones actuales
  - Resumen TAF si cubre la ventana de vuelo
  - Desglose del score de riesgo por componente

Uso tipico
----------
    from output.formatter import format_decision

    result = engine.evaluate("SACC", 150, dep_time, 1.0)
    print(format_decision(result))
"""

from datetime import datetime, timezone
from typing import Optional

try:
    from risk.weights import W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF
    from features.density_altitude import advisory as da_advisory
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from risk.weights import W_VIS, W_CEIL, W_XWIND, W_GUST, W_WX, W_FOG, W_TAF
    from features.density_altitude import advisory as da_advisory


# ──────────────────────────────────────────────────────────────────────────────
# Constantes de formato
# ──────────────────────────────────────────────────────────────────────────────

_W   = 56                       # ancho de linea
LINE = "=" * _W
SEP  = "-" * _W

_WEIGHTS = {
    "visibility"  : W_VIS,
    "ceiling"     : W_CEIL,
    "crosswind"   : W_XWIND,
    "gusts"       : W_GUST,
    "wx_phenomena": W_WX,
    "fog"         : W_FOG,
    "taf_risk"    : W_TAF,
}
_LABELS = {
    "visibility"  : "Visibilidad ",
    "ceiling"     : "Ceiling     ",
    "crosswind"   : "Crosswind   ",
    "gusts"       : "Rafagas     ",
    "wx_phenomena": "Fenomenos wx",
    "fog"         : "Niebla/spread",
    "taf_risk"    : "Riesgo TAF  ",
}


# ──────────────────────────────────────────────────────────────────────────────
# Funcion publica principal
# ──────────────────────────────────────────────────────────────────────────────

def format_decision(result) -> str:
    """
    Formatea un DecisionResult como texto legible para el piloto.

    Parameters
    ----------
    result : DecisionResult del motor de decision.

    Returns
    -------
    String multi-linea listo para print().
    """
    parts = []

    # ── Cabecera ──────────────────────────────────────────────────────────────
    obs_str = _fmt_time(result.obs_time) if result.obs_time else "sin hora"
    src_lbl = "NWP" if result.weather_source == "nwp" else "METAR"
    parts.append(LINE)
    parts.append(f"  {result.station_id:<8}  {obs_str} UTC   [{src_lbl}]")
    parts.append(LINE)
    parts.append(f"  {result.decision}")
    parts.append(LINE)

    # ── Sin datos ─────────────────────────────────────────────────────────────
    if not result.fetch_ok:
        parts.append(f"\n  ERROR: {result.error_message}\n")
        return "\n".join(parts)

    # ── Motivo hard blocker ───────────────────────────────────────────────────
    if result.hard_blocked and result.blocker_summary:
        parts.append(f"\n  Motivo: {result.blocker_summary}")

    # ── Proxima ventana GO ────────────────────────────────────────────────────
    if result.next_go_from:
        parts.append(f"  Proxima ventana GO: {_fmt_time(result.next_go_from)} UTC")

    # ── Condiciones actuales ──────────────────────────────────────────────────
    if result.weather is not None:
        parts.append("")
        parts.append(SEP)
        parts.append(f"  Condiciones actuales  ({src_lbl})")
        parts.append(SEP)
        parts += _fmt_weather(result.weather)

    # ── Altitud de densidad ───────────────────────────────────────────────────
    da = getattr(result, "density_altitude", None)
    if da is not None:
        parts.append("")
        parts.append(SEP)
        level = da_advisory(da)
        warn  = {"NORMAL": "", "ELEVATED": "  (!rendimiento degradado)", "HIGH": "  (!!degradacion severa)"}[level]
        est   = "  [elev. estimada]" if da.elevation_estimated else ""
        parts.append(
            f"  Altitud de densidad  :  {da.density_alt_ft:,} ft{warn}{est}"
        )
        parts.append(
            f"  Elev. campo / PA     :  {da.elevation_ft:,} ft  /  {da.pressure_alt_ft:,} ft"
        )
        parts.append(
            f"  T = {da.temp_c:.0f}°C   T_ISA = {da.isa_temp_c:.1f}°C   "
            f"ISA{da.isa_deviation_c:+.1f}°C"
        )

    # ── Resumen TAF ───────────────────────────────────────────────────────────
    if result.taf_result is not None and result.taf_result.taf_covers_window:
        parts.append("")
        parts.append(SEP)
        parts.append("  Pronostico TAF — ventana de vuelo")
        parts.append(SEP)
        parts += _fmt_taf(result.taf_result)

    # ── Desglose del score ────────────────────────────────────────────────────
    if result.score_breakdown is not None:
        parts.append("")
        parts.append(SEP)
        parts.append(
            f"  Score de riesgo   R = {result.r_total:.3f}   ->  {result.decision}"
        )
        parts.append(SEP)
        parts += _fmt_score(result.score_breakdown)
    elif result.hard_blocked:
        parts.append(f"\n  R = 1.000  (hard blocker — score no calculado)")

    parts.append("")
    return "\n".join(parts)


def format_short(result) -> str:
    """
    Resumen de una linea: decision + R_total + factor dominante.

    Util para logs o interfaces compactas.
    """
    if not result.fetch_ok:
        return f"{result.station_id}: NO GO  (sin datos)"

    if result.hard_blocked:
        brief = result.blocker_summary.split("|")[0].strip()
        return f"{result.station_id}: NO GO  [{brief}]"

    dom = ""
    if result.score_breakdown:
        dom = f"  factor={result.score_breakdown.dominant_factor}"

    return (
        f"{result.station_id}: {result.decision}  "
        f"R={result.r_total:.3f}{dom}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_time(ts: int) -> str:
    """Unix timestamp UTC → 'DD/MM HH:MM'."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%d/%m %H:%M")


def _fmt_weather(w) -> list:
    """Lista de lineas con las condiciones actuales del ParsedWeather."""
    lines = []

    # Visibilidad
    if w.visibility_km is not None:
        vis_s = "10+ km" if w.visibility_km >= 10.0 else f"{w.visibility_km:.1f} km"
    else:
        vis_s = "sin dato"
    lines.append(f"  Visibilidad  : {vis_s}")

    # Techo
    if w.ceiling_ft is not None:
        ceil_s = f"{w.ceiling_ft} ft"
        # Agregar cobertura de la capa que define el techo
        for layer in (getattr(w, "sky_layers", None) or []):
            if layer.get("base_ft") == w.ceiling_ft and layer.get("cover") in ("BKN", "OVC"):
                ceil_s += f"  {layer['cover']}"
                break
    else:
        ceil_s = "—  (sin techo / FEW/SCT)"
    lines.append(f"  Techo        : {ceil_s}")

    # Viento + crosswind
    lines.append(f"  Viento       : {_fmt_wind(w)}")

    # Temperatura y spread
    if w.temp_c is not None and w.dewpoint_c is not None:
        lines.append(f"  Temp/Td      : {w.temp_c:.0f}°C / {w.dewpoint_c:.0f}°C")
    if w.spread_c is not None:
        spread_s = f"{w.spread_c:.1f} °C"
        if w.spread_c <= 2.0:
            spread_s += "  (!niebla inminente)"
        elif w.spread_c <= 5.0:
            spread_s += "  (riesgo niebla)"
        lines.append(f"  Spread T/Td  : {spread_s}")

    # Fenomenos wx
    if getattr(w, "wx_codes", None):
        lines.append(f"  Fenomenos    : {' '.join(w.wx_codes)}")

    # Categoria de vuelo
    if getattr(w, "flight_category", None):
        lines.append(f"  Categoria    : {w.flight_category}")

    return lines


def _fmt_wind(w) -> str:
    """Formatea la informacion de viento de un ParsedWeather."""
    if getattr(w, "wind_variable", False) or w.wind_dir is None:
        dir_s = "VRB"
    else:
        dir_s = f"{w.wind_dir:03d}"

    spd_s = f"{int(w.wind_spd_kt)} kt" if w.wind_spd_kt is not None else "--- kt"
    gust_s = f"  G{int(w.wind_gust_kt)} kt" if w.wind_gust_kt else ""

    return f"{dir_s}° / {spd_s}{gust_s}"


def _fmt_taf(taf) -> list:
    """Lista de lineas con el resumen del analisis TAF."""
    lines = []
    wc = taf.worst_case

    if wc is not None:
        cat    = taf.worst_flight_category or "desconocida"
        flags  = [f for f, v in [("TEMPO", taf.has_tempo),
                                   ("PROB",  taf.has_prob),
                                   ("BECMG", taf.has_becmg)] if v]
        flags_s = "  [" + "  ".join(flags) + "]" if flags else ""
        lines.append(f"  Peor escenario : {cat}{flags_s}")
        lines.append(f"  r_TAF          : {taf.r_taf:.2f}")

        if wc.wx_codes:
            lines.append(f"  Fenomenos      : {' '.join(wc.wx_codes)}")
        if wc.visibility_km is not None:
            vis_s = "10+ km" if wc.visibility_km >= 10.0 else f"{wc.visibility_km:.1f} km"
            lines.append(f"  Visibilidad    : {vis_s}")
        if wc.ceiling_ft is not None:
            lines.append(f"  Techo          : {wc.ceiling_ft} ft")

    if taf.next_go_from:
        lines.append(f"  Proxima GO     : {_fmt_time(taf.next_go_from)} UTC")

    return lines


def _fmt_score(s) -> list:
    """Lista de lineas con el desglose del soft score."""
    r_vals = {
        "visibility"  : s.r_vis,
        "ceiling"     : s.r_ceil,
        "crosswind"   : s.r_xwind,
        "gusts"       : s.r_gust,
        "wx_phenomena": s.r_wx,
        "fog"         : s.r_fog,
        "taf_risk"    : s.r_taf,
    }

    lines = []
    for key in _WEIGHTS:
        r  = r_vals[key]
        w  = _WEIGHTS[key]
        contrib = r * w
        marker  = "  <- dominante" if key == s.dominant_factor else ""
        lines.append(
            f"  {_LABELS[key]}  "
            f"r={r:.2f}  x{w:.2f}  = {contrib:.3f}{marker}"
        )

    lines.append(f"  {'-' * 44}")

    if s.orographic_delta > 0.0:
        lines.append(
            f"  Delta orografico (NWP)          +{s.orographic_delta:.3f}"
        )

    lines.append(
        f"  {'Suma ponderada':<32}  {s.r_weighted_sum:.3f}"
    )
    lines.append(
        f"  {'R_total':<32}  {s.r_total:.3f}"
    )

    return lines


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

    from decision.engine import DecisionEngine
    from datetime import timedelta

    print("=" * 56)
    print("  TEST: output/formatter.py")
    print("=" * 56)

    engine  = DecisionEngine(mock=True)
    now_utc = datetime.now(tz=timezone.utc)
    dep_now = int((now_utc + timedelta(hours=1)).timestamp())

    # ── Caso 1: SACC NWP — condiciones normales ───────────────────────────────
    print("\n[1] SACC — NWP, condiciones normales")
    print()
    r1 = engine.evaluate("SACC", runway_heading=150,
                          departure_time=dep_now, flight_duration_h=1.0)
    print(format_decision(r1))

    # ── Caso 2: SACO METAR — condiciones normales ─────────────────────────────
    print("\n[2] SACO — METAR, condiciones normales (TAF sin cobertura)")
    print()
    r2 = engine.evaluate("SACO", runway_heading=180,
                          departure_time=dep_now, flight_duration_h=1.0)
    print(format_decision(r2))

    # ── Caso 3: SACO — hard blocker TSRA en TEMPO ────────────────────────────
    # El mock TAF tiene TEMPO TSRA en 2024-01-27 19:00-23:00 UTC
    dep_tempo = 1706390000   # 2024-01-27 19:10 UTC
    print("\n[3] SACO — METAR + TEMPO TSRA (hard blocker)")
    print()
    r3 = engine.evaluate("SACO", runway_heading=180,
                          departure_time=dep_tempo, flight_duration_h=1.0)
    print(format_decision(r3))

    # ── Caso 4: SAVY — sin datos ──────────────────────────────────────────────
    print("\n[4] SAVY — sin datos")
    print()
    r4 = engine.evaluate("SAVY", runway_heading=120,
                          departure_time=dep_now, flight_duration_h=1.0)
    print(format_decision(r4))

    # ── format_short ─────────────────────────────────────────────────────────
    print("[Resumen corto de los 4 casos]")
    for r in [r1, r2, r3, r4]:
        print(f"  {format_short(r)}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 56)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # format_decision devuelve un string no vacio para todos los casos
    for i, r in enumerate([r1, r2, r3, r4], 1):
        txt = format_decision(r)
        check(f"Caso {i}: format_decision devuelve string",   isinstance(txt, str))
        check(f"Caso {i}: contiene decision '{r.decision}'",  r.decision in txt)
        check(f"Caso {i}: contiene station_id '{r.station_id}'",
              r.station_id in txt)

    # format_short
    check("format_short SACC: contiene station_id",
          "SACC" in format_short(r1))
    check("format_short SACO blocker: contiene 'NO GO'",
          "NO GO" in format_short(r3))
    check("format_short SAVY sin datos: contiene 'sin datos'",
          "sin datos" in format_short(r4))

    # Score breakdown visible para casos sin hard blocker
    check("SACC: desglose score en formato",
          "Crosswind" in format_decision(r1))
    check("SACO normal: desglose score en formato",
          "Crosswind" in format_decision(r2))

    # Hard blocker: sin desglose de score
    check("SACO TSRA: no muestra score breakdown",
          "r=0." not in format_decision(r3))

    print("\n" + "=" * 56)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 56)
