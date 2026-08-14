"""
briefing.py
===========
Generador de briefing meteorologico en lenguaje natural (espanol).

Genera un analisis integral de texto para el piloto, basado en los
resultados del motor de decision y el optimizador de rutas.

La generacion es 100% determinista por reglas (sin IA ni LLM).
El tono es tecnico-operacional: preciso, conciso, sin ambigedades.

Funcion principal:
  generate_briefing(origin_result, dest_result, route_result, aircraft) -> str

Retorna un string multi-linea listo para mostrar en la GUI o imprimir.
"""

from datetime import datetime, timezone
from typing import Optional, List

try:
    from decision.engine import DecisionResult
    from route.optimizer import OptimizeResult
    from parsers.metar_parser import ParsedWeather
    from risk.soft_scoring import SoftScoreResult
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from decision.engine import DecisionResult
    from route.optimizer import OptimizeResult
    from parsers.metar_parser import ParsedWeather
    from risk.soft_scoring import SoftScoreResult


# ────────────────────────────────────────────────────────────────────────────
# Helpers de formato
# ────────────────────────────────────────────────────────────────────────────

def _fmt_time_hm(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60))
    return f"{h}h {m:02d}min"


def _fmt_utc(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%d/%m %H:%MZ")


def _decision_label(decision: str) -> str:
    return {"GO": "GO (apto)", "CAUTION": "PRECAUCION", "NO GO": "NO GO (condiciones no aptas)"}.get(
        decision, decision
    )


def _r_label(r: float) -> str:
    if r < 0.15:
        return "muy bajo"
    if r < 0.25:
        return "bajo"
    if r < 0.40:
        return "moderado"
    if r < 0.55:
        return "elevado"
    return "critico"


def _wind_text(wx: "Optional[ParsedWeather]") -> str:
    if wx is None:
        return "viento no disponible"
    if wx.wind_variable:
        spd = wx.wind_spd_kt or 0
        return f"viento variable {spd:.0f} kt"
    if wx.wind_dir is None or wx.wind_spd_kt is None:
        return "viento no disponible"
    gust_str = f" (rafagas {wx.wind_gust_kt:.0f} kt)" if wx.wind_gust_kt else ""
    return f"viento {wx.wind_dir:03d}/{wx.wind_spd_kt:.0f} kt{gust_str}"


def _vis_text(wx: "Optional[ParsedWeather]") -> str:
    if wx is None or wx.visibility_km is None:
        return "visibilidad no disponible"
    if wx.visibility_km >= 9.9:
        return "visibilidad >10 km (CAVOK/ilimitada)"
    return f"visibilidad {wx.visibility_km:.1f} km"


def _ceil_text(wx: "Optional[ParsedWeather]") -> str:
    if wx is None:
        return "techo no disponible"
    if wx.ceiling_ft is None:
        return "sin techo significativo (CLR/FEW)"
    return f"techo {wx.ceiling_ft} ft"


def _wx_codes_text(wx: "Optional[ParsedWeather]") -> str:
    if wx is None or not wx.wx_codes:
        return ""
    codes = ", ".join(wx.wx_codes)
    return f"fenomenos: {codes}"


def _cat_text(wx: "Optional[ParsedWeather]") -> str:
    if wx is None or wx.flight_category is None:
        return ""
    return f"categoria {wx.flight_category}"


def _temp_spread_text(wx: "Optional[ParsedWeather]") -> str:
    if wx is None:
        return ""
    parts = []
    if wx.temp_c is not None:
        parts.append(f"T={wx.temp_c:.0f} C")
    if wx.dewpoint_c is not None:
        parts.append(f"Td={wx.dewpoint_c:.0f} C")
    if wx.spread_c is not None:
        risk_fog = wx.spread_c < 3.0
        fog_warn = " [riesgo niebla]" if risk_fog else ""
        parts.append(f"spread={wx.spread_c:.0f} C{fog_warn}")
    return ", ".join(parts)


def _score_detail(score: "Optional[SoftScoreResult]") -> str:
    if score is None:
        return ""
    lines = []
    if score.r_vis is not None and score.r_vis > 0.05:
        lines.append(f"  visibilidad: {score.r_vis:.2f}")
    if score.r_ceil is not None and score.r_ceil > 0.05:
        lines.append(f"  techo: {score.r_ceil:.2f}")
    if score.r_xwind is not None and score.r_xwind > 0.05:
        lines.append(f"  viento cruzado: {score.r_xwind:.2f}")
    if score.r_gust is not None and score.r_gust > 0.05:
        lines.append(f"  rafagas: {score.r_gust:.2f}")
    if score.r_wx is not None and score.r_wx > 0.05:
        lines.append(f"  fenomenos: {score.r_wx:.2f}")
    if score.r_fog is not None and score.r_fog > 0.05:
        lines.append(f"  niebla proxy: {score.r_fog:.2f}")
    if score.r_taf is not None and score.r_taf > 0.05:
        lines.append(f"  riesgo TAF: {score.r_taf:.2f}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Secciones del briefing
# ────────────────────────────────────────────────────────────────────────────

def _section_header(title: str) -> str:
    return f"\n{'-' * 60}\n{title.upper()}\n{'-' * 60}"


def _section_resumen_ejecutivo(
    origin_r : DecisionResult,
    dest_r   : DecisionResult,
    route_r  : Optional[OptimizeResult],
) -> str:
    lines = [_section_header("Resumen ejecutivo")]

    # Decision global
    decisions = [origin_r.decision, dest_r.decision]
    if route_r and route_r.found:
        for ir in route_r.intermediate_results:
            decisions.append(ir.decision)

    if "NO GO" in decisions:
        global_dec = "NO GO"
    elif "CAUTION" in decisions:
        global_dec = "CAUTION"
    else:
        global_dec = "GO"

    lines.append(f"\nDecision global: {_decision_label(global_dec)}")

    # Descripcion de situacion
    orig_name = origin_r.station_id
    dest_name = dest_r.station_id
    if route_r and route_r.found:
        orig_name = f"{origin_r.station_id}"
        dest_name = f"{dest_r.station_id}"
        dist = route_r.total_dist_km
        tiempo = _fmt_time_hm(route_r.total_time_h)
        ac_name = route_r.aircraft.name if route_r.aircraft else "aeronave"
        lines.append(
            f"Ruta {orig_name} - {dest_name} en {ac_name}: "
            f"{dist:.0f} km, tiempo estimado {tiempo}."
        )

    # Factor limitante
    if global_dec == "NO GO":
        if origin_r.decision == "NO GO":
            lines.append(f"Factor limitante: condiciones en origen ({origin_r.station_id}) no aptas para vuelo VFR.")
        elif dest_r.decision == "NO GO":
            lines.append(f"Factor limitante: condiciones en destino ({dest_r.station_id}) no aptas.")
        else:
            lines.append("Factor limitante: condiciones NO GO en uno o mas aerodromos intermedios.")
    elif global_dec == "CAUTION":
        lines.append("Proceder con precaucion. Verificar condiciones antes del despegue y monitorear en ruta.")

    return "\n".join(lines)


def _section_aerodromo(
    result   : DecisionResult,
    role     : str,   # "origen" o "destino"
) -> str:
    lines = [_section_header(f"Condiciones en {role}: {result.station_id}")]

    lines.append(f"\nDecision: {_decision_label(result.decision)}  (R={result.r_total:.2f}, riesgo {_r_label(result.r_total)})")

    # Factor limitante no-compensatorio: explica un veredicto mas restrictivo
    # que el que sugeriria R (un showstopper individual que no se promedia).
    sb = result.score_breakdown
    if sb is not None and getattr(sb, "guardrail_reason", ""):
        lines.append(f"Factor limitante (veto): {sb.guardrail_reason}")

    if result.obs_time:
        lines.append(f"Observacion: {_fmt_utc(result.obs_time)} UTC  (fuente: {result.weather_source})")

    if result.hard_blocked:
        lines.append(f"\n[!] BLOQUEADOR CRITICO: {result.blocker_summary}")
        return "\n".join(lines)

    wx = result.weather
    if wx is not None:
        wind = _wind_text(wx)
        vis  = _vis_text(wx)
        ceil = _ceil_text(wx)
        lines.append(f"\n{wind}")
        lines.append(vis)
        lines.append(ceil)
        cat = _cat_text(wx)
        if cat:
            lines.append(cat)
        wx_str = _wx_codes_text(wx)
        if wx_str:
            lines.append(wx_str)
        temp_str = _temp_spread_text(wx)
        if temp_str:
            lines.append(temp_str)

    score_str = _score_detail(result.score_breakdown)
    if score_str:
        lines.append("\nFactores de riesgo destacados:")
        lines.append(score_str)

    if result.next_go_from:
        lines.append(f"\nProxima ventana GO estimada: {_fmt_utc(result.next_go_from)} UTC")

    return "\n".join(lines)


def _section_ruta(route_r: OptimizeResult) -> str:
    lines = [_section_header("Ruta y espacio aereo")]

    path_str = " -> ".join(route_r.path)
    mode_names = {
        "shortest" : "mas corta (distancia minima)",
        "fastest"  : "mas rapida (tiempo minimo)",
        "safest"   : "mas segura (riesgo minimo)",
        "suggested": "sugerida (multi-objetivo GA)",
    }
    mode_label = mode_names.get(route_r.mode, route_r.mode)
    lines.append(f"\nRuta {mode_label}: {path_str}")

    lines.append(f"\nTramos:")
    for leg in route_r.legs:
        t_str = _fmt_time_hm(leg.time_hours)
        lines.append(
            f"  {leg.origin} -> {leg.dest}: "
            f"{leg.distance_km:.0f} km, rumbo {leg.bearing_deg:.0f} deg, "
            f"{t_str}, {leg.fuel_liters:.1f} L"
        )

    if route_r.airspace_conflicts:
        lines.append("\nIntersecciones de espacio aereo detectadas:")
        for z in route_r.airspace_conflicts:
            tipo = "RESTRINGIDA" if z.is_restricted else "CONTROLADA" if z.is_controlled else "PELIGROSA"
            lines.append(f"  [{tipo}] {z.name}  (piso: {z.floor_ft} ft, techo: {z.ceiling_ft} ft)")
            if z.notes:
                lines.append(f"    Nota: {z.notes}")
    else:
        lines.append("\nSin conflictos de espacio aereo en la ruta calculada.")

    return "\n".join(lines)


def _section_meteo_en_ruta(route_r: OptimizeResult) -> str:
    if not route_r.intermediate_results:
        return ""

    lines = [_section_header("Meteo en aerodromos intermedios")]
    lines.append("\nAerodromos en ruta (potenciales alternativas de emergencia):")

    for ir in route_r.intermediate_results:
        dec_label = _decision_label(ir.decision)
        lines.append(
            f"\n  {ir.code} - {ir.name}"
            f"\n    Decision: {dec_label}  (R={ir.r_total:.2f})"
        )

    return "\n".join(lines)


def _section_alternativo(route_r: OptimizeResult) -> str:
    alt = route_r.alternate
    if alt is None:
        return ""

    lines = [_section_header("Aerodromo alternativo")]
    dec_label = _decision_label(alt.decision)
    t_str = _fmt_time_hm(alt.time_from_dest_h)

    lines.append(f"\nAlternativo sugerido: {alt.code} - {alt.name}")
    lines.append(f"Condiciones: {dec_label}  (R={alt.r_total:.2f}, riesgo {_r_label(alt.r_total)})")
    lines.append(f"Desde destino: {alt.dist_from_dest_km:.0f} km ({t_str})")

    if alt.decision == "NO GO":
        lines.append("\n[ATENCION] El alternativo sugerido esta en condiciones NO GO.")
        lines.append("Seleccionar manualmente un alternativo apto antes del vuelo.")
    elif alt.decision == "CAUTION":
        lines.append("\nEl alternativo presenta condiciones marginales. Verificar antes del desvio.")

    return "\n".join(lines)


def _section_performance(route_r: OptimizeResult) -> str:
    ac = route_r.aircraft
    lines = [_section_header("Performance")]

    if ac:
        lines.append(f"\nAeronave: {ac.name}")
        lines.append(f"  Velocidad crucero  : {ac.cruise_kt:.0f} kt")
        lines.append(f"  Consumo crucero    : {ac.fuel_flow_lph:.1f} L/hr")
        lines.append(f"  Combustible usable : {ac.fuel_usable_l:.0f} L (reserva {ac.fuel_reserve_min} min incluida)")
        lines.append(f"  Alcance maximo     : {ac.range_km:.0f} km")
        lines.append(f"  Viento cruzado max : {ac.crosswind_max_kt:.0f} kt")

    lines.append(f"\nResumen de vuelo:")
    lines.append(f"  Distancia total    : {route_r.total_dist_km:.0f} km")
    lines.append(f"  Tiempo estimado    : {_fmt_time_hm(route_r.total_time_h)}")
    lines.append(f"  Combustible total  : {route_r.total_fuel_l:.1f} L")
    lines.append(f"  Combustible OK     : {'SI' if route_r.fuel_ok else 'NO - REQUIERE ESCALA'}")
    lines.append(f"  Escala recomendada : {'SI' if route_r.needs_fuel_stop else 'NO'}")

    if not route_r.fuel_ok:
        lines.append("\n[!] El combustible disponible NO alcanza para completar la ruta sin escala.")
        lines.append("    Planificar reaprovisionamiento en ruta.")

    return "\n".join(lines)


def _section_recomendacion(
    origin_r : DecisionResult,
    dest_r   : DecisionResult,
    route_r  : Optional[OptimizeResult],
) -> str:
    lines = [_section_header("Recomendacion final")]

    decisions = [origin_r.decision, dest_r.decision]
    if route_r and route_r.found:
        for ir in route_r.intermediate_results:
            decisions.append(ir.decision)

    if "NO GO" in decisions:
        lines.append("\nNO REALIZAR EL VUELO en las condiciones actuales.")
        blockers = []
        if origin_r.decision == "NO GO":
            s = f"origen ({origin_r.station_id}): "
            if origin_r.hard_blocked:
                s += origin_r.blocker_summary
            else:
                s += f"R={origin_r.r_total:.2f}"
            blockers.append(s)
        if dest_r.decision == "NO GO":
            s = f"destino ({dest_r.station_id}): "
            if dest_r.hard_blocked:
                s += dest_r.blocker_summary
            else:
                s += f"R={dest_r.r_total:.2f}"
            blockers.append(s)
        if blockers:
            lines.append("Condiciones limitantes:")
            for b in blockers:
                lines.append(f"  - {b}")

        # Proxima ventana GO
        next_go_times = []
        if origin_r.next_go_from:
            next_go_times.append(origin_r.next_go_from)
        if dest_r.next_go_from:
            next_go_times.append(dest_r.next_go_from)
        if next_go_times:
            earliest = max(next_go_times)
            lines.append(f"\nProxima ventana GO estimada: {_fmt_utc(earliest)} UTC")

    elif "CAUTION" in decisions:
        lines.append("\nProceder con PRECAUCION.")
        lines.append("Condiciones son marginales pero dentro de limites VFR.")
        lines.append("Acciones recomendadas:")
        if origin_r.r_total >= 0.25:
            lines.append(f"  - Verificar condiciones actualizadas en {origin_r.station_id} antes del despegue.")
        if dest_r.r_total >= 0.25:
            lines.append(f"  - Confirmar condiciones en {dest_r.station_id} antes del inicio del descenso.")
        if route_r and route_r.alternate:
            lines.append(f"  - Verificar alternativo {route_r.alternate.code} antes de partir.")
        lines.append("  - Monitorear condiciones en vuelo y estar preparado para desviar.")

    else:
        lines.append("\nVuelo apto segun condiciones actuales.")
        r_orig = origin_r.r_total
        r_dest = dest_r.r_total
        lines.append(
            f"Riesgo meteorologico: origen {r_orig:.2f} ({_r_label(r_orig)}), "
            f"destino {r_dest:.2f} ({_r_label(r_dest)})."
        )
        if route_r and not route_r.fuel_ok:
            lines.append("[ATENCION] Planificar escala de combustible.")

    lines.append(
        f"\nBriefing generado: {datetime.now(tz=timezone.utc).strftime('%d/%m/%Y %H:%MZ UTC')}"
    )

    return "\n".join(lines)


def _section_notams(
    notams_orig : list,
    notams_dest : list,
    name_orig   : str,
    name_dest   : str,
) -> str:
    """Sección de NOTAMs activos para origen y destino."""
    if not notams_orig and not notams_dest:
        return ""

    lines = [_section_header("NOTAMs activos")]

    def _fmt_notam(n) -> str:
        end = n.end_date if n.end_date else "N/A"
        end_short = end[:10] if len(end) >= 10 else end
        start_short = n.start_date[:10] if len(n.start_date) >= 10 else n.start_date
        msg_first = n.message.split("\n")[-1].strip() if "\n" in n.message else n.message
        return f"  [{n.notam_id}]  {start_short} → {end_short}\n    {msg_first}"

    if notams_orig:
        lines.append(f"\n{name_orig}:")
        for n in notams_orig[:5]:
            lines.append(_fmt_notam(n))
    else:
        lines.append(f"\n{name_orig}: sin NOTAMs activos")

    if notams_dest:
        lines.append(f"\n{name_dest}:")
        for n in notams_dest[:5]:
            lines.append(_fmt_notam(n))
    else:
        lines.append(f"\n{name_dest}: sin NOTAMs activos")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ────────────────────────────────────────────────────────────────────────────

def generate_briefing(
    origin_result : DecisionResult,
    dest_result   : DecisionResult,
    route_result  : Optional[OptimizeResult] = None,
    notams_orig   : Optional[List] = None,
    notams_dest   : Optional[List] = None,
) -> str:
    """
    Genera el briefing meteorologico completo en lenguaje natural (espanol).

    Parametros
    ----------
    origin_result : DecisionResult del aerodromo de origen
    dest_result   : DecisionResult del aerodromo de destino
    route_result  : OptimizeResult de la ruta (opcional; si None, omite
                    secciones de ruta, performance y alternativo)

    Retorna
    -------
    String multi-linea listo para mostrar.
    """
    sections = []

    # 1. Resumen ejecutivo
    sections.append(_section_resumen_ejecutivo(origin_result, dest_result, route_result))

    # 2. Condiciones en origen
    sections.append(_section_aerodromo(origin_result, "origen"))

    # 3. Ruta y espacio aereo
    if route_result and route_result.found:
        sections.append(_section_ruta(route_result))

    # 4. Meteo en aerodromos intermedios
    if route_result and route_result.found and route_result.intermediate_results:
        sections.append(_section_meteo_en_ruta(route_result))

    # 5. Condiciones en destino
    sections.append(_section_aerodromo(dest_result, "destino"))

    # 6. Alternativo
    if route_result and route_result.alternate:
        sections.append(_section_alternativo(route_result))

    # 7. Performance
    if route_result and route_result.found:
        sections.append(_section_performance(route_result))

    # 8. NOTAMs
    try:
        from data.airports import AIRPORTS
        orig_ap = AIRPORTS.get(origin_result.station_id)
        dest_ap = AIRPORTS.get(dest_result.station_id)
        name_orig = orig_ap.name if orig_ap else origin_result.station_id
        name_dest = dest_ap.name if dest_ap else dest_result.station_id
    except Exception:
        name_orig = origin_result.station_id
        name_dest = dest_result.station_id

    notam_sec = _section_notams(
        notams_orig or [], notams_dest or [], name_orig, name_dest
    )
    if notam_sec:
        sections.append(notam_sec)

    # 9. Recomendacion final
    sections.append(_section_recomendacion(origin_result, dest_result, route_result))

    return "\n".join(s for s in sections if s)


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: output/briefing.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ── Construir datos de prueba con mock ──
    from decision.engine import DecisionEngine
    from route.optimizer import optimize
    from risk.aircraft_profiles import ALPHA_TRAINER

    engine = DecisionEngine(mock=True, aircraft=ALPHA_TRAINER)
    import time
    now = int(time.time())

    origin_r = engine.evaluate("SACC", runway_heading=140, departure_time=now, flight_duration_h=1.5)
    dest_r   = engine.evaluate("SAOM", runway_heading=180, departure_time=now + 5400, flight_duration_h=0.5)

    route_r = optimize(
        "SACC", "SAOM",
        mode="suggested",
        aircraft=ALPHA_TRAINER,
        suggest_alternate=True,
        evaluate_intermediate=True,
        mock=True,
    )

    # ── Tests basicos ──
    check("origin_result es DecisionResult", hasattr(origin_r, "decision"))
    check("dest_result es DecisionResult",   hasattr(dest_r, "decision"))
    check("route_result found",              route_r.found)

    # ── Generar briefing GO/CAUTION ──
    briefing = generate_briefing(origin_r, dest_r, route_r)
    check("Briefing generado (no vacio)",    len(briefing) > 100)
    check("Contiene resumen ejecutivo",      "RESUMEN EJECUTIVO" in briefing.upper())
    check("Contiene seccion origen",         "ORIGEN" in briefing.upper())
    check("Contiene seccion destino",        "DESTINO" in briefing.upper())
    check("Contiene seccion ruta",           "RUTA" in briefing.upper())
    check("Contiene seccion performance",    "PERFORMANCE" in briefing.upper())
    check("Contiene recomendacion",          "RECOMENDACION" in briefing.upper())
    check("Contiene nombre aeronave",        ALPHA_TRAINER.name in briefing)
    check("Contiene SACC en briefing",       "SACC" in briefing)
    check("Contiene SAOM en briefing",       "SAOM" in briefing)

    print("\n" + "=" * 60)
    print("  BRIEFING GENERADO:")
    print("=" * 60)
    print(briefing)
    print("=" * 60)

    # ── Sin ruta (solo evaluacion) ──
    briefing_solo = generate_briefing(origin_r, dest_r, route_result=None)
    check("Briefing sin ruta: no vacio",     len(briefing_solo) > 50)
    check("Briefing sin ruta: no tiene Performance",
          "PERFORMANCE" not in briefing_solo.upper())

    # ── Caso NO GO (forzar con un resultado manipulado) ──
    from dataclasses import replace
    ng_result = replace(origin_r, decision="NO GO", r_total=0.85,
                        hard_blocked=True, blocker_summary="TS activo en el aerodromo")
    briefing_ng = generate_briefing(ng_result, dest_r)
    check("NO GO en briefing",          "NO REALIZAR" in briefing_ng)
    check("NO GO menciona bloqueador",  "TS activo" in briefing_ng)

    # ── Caso CAUTION ──
    ca_result = replace(origin_r, decision="CAUTION", r_total=0.35)
    briefing_ca = generate_briefing(ca_result, dest_r)
    check("CAUTION en briefing", "PRECAUCION" in briefing_ca)

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
