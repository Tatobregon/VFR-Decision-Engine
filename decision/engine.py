"""
engine.py
=========
Motor de decision VFR GO/NO GO. Orquesta el pipeline completo.

Flujo
-----
  1. Fetch : METAR+TAF (aviationweather.gov) o NWP (open-meteo.com)
  2. Parse : RawMetar/RawNWP → ParsedWeather
  3. Hard blockers : condiciones que producen NO GO inmediato
  4. TAF window (solo METAR): analisis de la ventana de vuelo
  5. Soft scoring: R_total = sum(w_i * r_i) + delta_orografico
  6. Decision: GO / CAUTION / NO GO segun thresholds

Fuentes por estacion
--------------------
  SACC            → NWP (Open-Meteo), sin METAR ni TAF
  SACO/SAVY/etc.  → METAR + TAF (aviationweather.gov)

Uso tipico
----------
    from decision.engine import DecisionEngine

    engine = DecisionEngine(mock=True)
    result = engine.evaluate("SACO", runway_heading=180,
                              departure_time=int(time.time()) + 3600,
                              flight_duration_h=1.0)
    print(result.decision, result.r_total)
"""

import logging
from dataclasses import dataclass
from typing import Optional

try:
    from config import NWP_STATIONS, METAR_STATIONS, NWP_HOURS_AHEAD
    from data.fetcher_aviationweather import AviationWeatherFetcher
    from data.fetcher_openmeteo        import OpenMeteoFetcher
    from parsers.metar_parser          import MetarParser, ParsedWeather
    from parsers.taf_parser            import TafParser
    from parsers.openmeteo_adapter     import OpenMeteoAdapter
    from features.taf_window           import TafAnalyzer, TafWindowResult
    from risk.hard_blockers            import (
        check_hard_blockers,
        check_hard_blockers_from_weather,
        HARD_BLOCKER_TOKENS,
    )
    from risk.soft_scoring             import compute_soft_score, SoftScoreResult
    from risk.aircraft_profiles        import AircraftProfile, ALPHA_TRAINER
    from features.density_altitude     import compute_density_altitude, DensityAltitudeResult
    from data.airports                 import AIRPORTS
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import NWP_STATIONS, METAR_STATIONS, NWP_HOURS_AHEAD
    from data.fetcher_aviationweather import AviationWeatherFetcher
    from data.fetcher_openmeteo        import OpenMeteoFetcher
    from parsers.metar_parser          import MetarParser, ParsedWeather
    from parsers.taf_parser            import TafParser
    from parsers.openmeteo_adapter     import OpenMeteoAdapter
    from features.taf_window           import TafAnalyzer, TafWindowResult
    from risk.hard_blockers            import (
        check_hard_blockers,
        check_hard_blockers_from_weather,
        HARD_BLOCKER_TOKENS,
    )
    from risk.soft_scoring             import compute_soft_score, SoftScoreResult
    from risk.aircraft_profiles        import AircraftProfile, ALPHA_TRAINER
    from features.density_altitude     import compute_density_altitude, DensityAltitudeResult
    from data.airports                 import AIRPORTS

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DecisionResult:
    """
    Resultado completo del motor de decision.

    `fetch_ok=False` indica que no hubo datos disponibles; en ese caso
    la decision es NO GO conservador y el resto de los campos son None/vacios.
    """
    station_id      : str
    decision        : str                       # "GO" | "CAUTION" | "NO GO"
    r_total         : float

    hard_blocked    : bool
    blocker_summary : str                       # descripcion si hard_blocked, "" si no

    score_breakdown : Optional[SoftScoreResult]  # None si hard_blocked o sin datos
    taf_result      : Optional[TafWindowResult]  # None si no hay TAF disponible

    weather_source  : str                       # "metar" | "nwp"
    obs_time        : int                       # Unix UTC de la observacion usada

    next_go_from    : Optional[int]             # Unix UTC estimado proxima ventana GO

    weather         : Optional[ParsedWeather]   # observacion usada (para display)
    fetch_ok        : bool                      # False si no hubo datos
    error_message   : str                       # "" si sin error

    density_altitude: Optional[DensityAltitudeResult] = None  # None si sin temperatura


# ──────────────────────────────────────────────────────────────────────────────
# Motor principal
# ──────────────────────────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Orquesta el pipeline completo de evaluacion meteorologica VFR.

    Parameters
    ----------
    mock     : Si True, usa datos simulados (sin conexion a internet).
    aircraft : Perfil de aeronave. Default: ALPHA_TRAINER.
    """

    def __init__(
        self,
        mock    : bool = False,
        aircraft: Optional[AircraftProfile] = None,
    ):
        self.mock     = mock
        self.aircraft = aircraft or ALPHA_TRAINER

        self._aw          = AviationWeatherFetcher(mock=mock)
        self._nwp_fetch   = OpenMeteoFetcher(mock=mock)
        self._metar_parser = MetarParser()
        self._taf_parser   = TafParser()
        self._nwp_adapter  = OpenMeteoAdapter()
        self._taf_analyzer = TafAnalyzer()

    # ── Entrada publica ───────────────────────────────────────────────────────

    def evaluate(
        self,
        station_id        : str,
        runway_heading    : int,
        departure_time    : int,          # Unix UTC
        flight_duration_h : float = 1.0,
    ) -> DecisionResult:
        """
        Evalua las condiciones meteorologicas para un vuelo VFR planificado.

        Parameters
        ----------
        station_id        : Codigo ICAO del aeropuerto de origen (ej: "SACC", "SACO").
        runway_heading    : Rumbo magnetico de la pista en grados (para calcular crosswind).
        departure_time    : Hora estimada de despegue en Unix timestamp UTC.
        flight_duration_h : Duracion del vuelo en horas (default 1.0).

        Returns
        -------
        DecisionResult con GO / CAUTION / NO GO y el desglose completo.
        """
        sid = station_id.upper().strip()
        logger.info(
            f"Evaluando {sid} | pista={runway_heading} | "
            f"dep={departure_time} | dur={flight_duration_h}h"
        )

        if sid in NWP_STATIONS:
            return self._evaluate_nwp(sid, runway_heading, departure_time, flight_duration_h)
        else:
            return self._evaluate_metar(sid, runway_heading, departure_time, flight_duration_h)

    # ── Path NWP (SACC y similares) ───────────────────────────────────────────

    def _evaluate_nwp(
        self,
        sid              : str,
        runway_heading   : int,
        departure_time   : int,
        flight_duration_h: float,
    ) -> DecisionResult:
        cfg     = NWP_STATIONS[sid]
        raw_nwp = self._nwp_fetch.get_forecast(
            lat         = cfg["lat"],
            lon         = cfg["lon"],
            elevation_m = cfg["elev_m"],
            hours_ahead = NWP_HOURS_AHEAD,
        )
        if raw_nwp is None:
            return self._no_data(sid, "nwp")

        all_wx = self._nwp_adapter.adapt_all(raw_nwp, station_id=sid)
        if not all_wx:
            return self._no_data(sid, "nwp")

        # ── Ventana de vuelo ─────────────────────────────────────────────────
        window_end = departure_time + int(flight_duration_h * 3600)
        window_wx  = [w for w in all_wx
                      if departure_time <= w.obs_time <= window_end]
        if not window_wx:
            # No hay hora exacta en la ventana: usar la mas cercana al despegue
            window_wx = [min(all_wx, key=lambda w: abs(w.obs_time - departure_time))]

        # ── Hard blockers: verificar toda la ventana ──────────────────────────
        for wx in window_wx:
            blocker = check_hard_blockers_from_weather(wx)
            if blocker.is_blocked:
                logger.warning(f"Hard blocker NWP {sid}: {blocker.summary}")
                return DecisionResult(
                    station_id      = sid,
                    decision        = "NO GO",
                    r_total         = 1.0,
                    hard_blocked    = True,
                    blocker_summary = blocker.summary,
                    score_breakdown = None,
                    taf_result      = None,
                    weather_source  = "nwp",
                    obs_time        = wx.obs_time,
                    next_go_from    = None,
                    weather         = wx,
                    fetch_ok        = True,
                    error_message   = "",
                )

        # ── Soft scoring: peor caso dentro de la ventana ─────────────────────
        scores  = [compute_soft_score(w, runway_heading, self.aircraft) for w in window_wx]
        worst   = max(scores, key=lambda s: s.r_total)
        ref_wx  = min(window_wx, key=lambda w: abs(w.obs_time - departure_time))

        logger.info(f"NWP {sid}: R_total={worst.r_total:.3f} [{worst.decision}]")

        return DecisionResult(
            station_id       = sid,
            decision         = worst.decision,
            r_total          = worst.r_total,
            hard_blocked     = False,
            blocker_summary  = "",
            score_breakdown  = worst,
            taf_result       = None,
            weather_source   = "nwp",
            obs_time         = ref_wx.obs_time,
            next_go_from     = None,
            weather          = ref_wx,
            fetch_ok         = True,
            error_message    = "",
            density_altitude = self._compute_da(sid, ref_wx),
        )

    # ── Path METAR (SACO, SAVY, etc.) ─────────────────────────────────────────

    def _evaluate_metar(
        self,
        sid              : str,
        runway_heading   : int,
        departure_time   : int,
        flight_duration_h: float,
    ) -> DecisionResult:
        raw_metar, raw_taf = self._aw.get_metar_and_taf(sid)

        if raw_metar is None:
            return self._no_data(sid, "metar")

        weather = self._metar_parser.parse(raw_metar)

        # ── Hard blockers: observacion actual ────────────────────────────────
        blocker = check_hard_blockers_from_weather(weather)

        # ── TAF window ───────────────────────────────────────────────────────
        taf_result       = None
        taf_hard_blocked = False

        if raw_taf is not None:
            try:
                parsed_taf = self._taf_parser.parse(raw_taf)
                taf_result = self._taf_analyzer.analyze(
                    parsed_taf,
                    departure_time    = departure_time,
                    flight_duration_h = flight_duration_h,
                )
                taf_hard_blocked = self._taf_window_has_hard_blocker(taf_result)
            except Exception as exc:
                logger.warning(f"Error al analizar TAF de {sid}: {exc}")

        # ── Decision con hard blocker ─────────────────────────────────────────
        if blocker.is_blocked or taf_hard_blocked:
            if blocker.is_blocked:
                summary = blocker.summary
            else:
                summary = "Fenomeno peligroso en TAF dentro de la ventana de vuelo"

            next_go = taf_result.next_go_from if taf_result else None
            logger.warning(f"NO GO {sid}: {summary}")

            return DecisionResult(
                station_id      = sid,
                decision        = "NO GO",
                r_total         = 1.0,
                hard_blocked    = True,
                blocker_summary = summary,
                score_breakdown = None,
                taf_result      = taf_result,
                weather_source  = "metar",
                obs_time        = weather.obs_time,
                next_go_from    = next_go,
                weather         = weather,
                fetch_ok        = True,
                error_message   = "",
            )

        # ── Soft scoring ─────────────────────────────────────────────────────
        r_taf = taf_result.r_taf if taf_result else 0.0
        score = compute_soft_score(weather, runway_heading, self.aircraft, taf_r_taf=r_taf)

        next_go = taf_result.next_go_from if taf_result else None
        logger.info(f"METAR {sid}: R_total={score.r_total:.3f} [{score.decision}]")

        return DecisionResult(
            station_id       = sid,
            decision         = score.decision,
            r_total          = score.r_total,
            hard_blocked     = False,
            blocker_summary  = "",
            score_breakdown  = score,
            taf_result       = taf_result,
            weather_source   = "metar",
            obs_time         = weather.obs_time,
            next_go_from     = next_go,
            weather          = weather,
            fetch_ok         = True,
            error_message    = "",
            density_altitude = self._compute_da(sid, weather),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_da(
        self,
        sid    : str,
        weather: ParsedWeather,
    ) -> Optional[DensityAltitudeResult]:
        """
        Calcula DA usando temperatura del weather y elevación del aeródromo.
        Primero busca en AIRPORTS; si no encuentra, usa weather.elevation_m.
        Devuelve None si no hay temperatura disponible.
        """
        if weather.temp_c is None:
            return None

        ap = AIRPORTS.get(sid)
        if ap is not None:
            elev_ft        = ap.elev_ft
            elev_estimated = ap.elev_estimated
        elif weather.elevation_m is not None:
            elev_ft        = int(round(weather.elevation_m * 3.28084))
            elev_estimated = True
        else:
            # NWP station sin entrada en AIRPORTS
            cfg     = NWP_STATIONS.get(sid, {})
            elev_m  = cfg.get("elev_m", 0)
            elev_ft = int(round(elev_m * 3.28084))
            elev_estimated = (elev_m == 0)

        return compute_density_altitude(
            temp_c              = weather.temp_c,
            elevation_ft        = elev_ft,
            qnh_hpa             = weather.altimeter_hpa,
            elevation_estimated = elev_estimated,
        )

    def _taf_window_has_hard_blocker(self, taf_result: TafWindowResult) -> bool:
        """
        Verifica si el periodo base o algun transitorio de la ventana TAF
        contiene un fenomeno hard-blocker (TS, GR, FZRA, etc.).
        """
        periods_to_check = []

        if taf_result.effective_base is not None:
            periods_to_check.append(taf_result.effective_base)
        periods_to_check.extend(taf_result.effective_transients)

        for period in periods_to_check:
            b = check_hard_blockers(
                visibility_km = period.visibility_km,
                ceiling_ft    = period.ceiling_ft,
                wx_codes      = period.wx_codes or [],
            )
            if b.is_blocked:
                return True

        return False

    def _no_data(self, sid: str, source: str) -> DecisionResult:
        """Resultado conservador cuando no hay datos disponibles."""
        msg = f"Sin datos meteorologicos para {sid} (fuente: {source})"
        logger.error(msg)
        return DecisionResult(
            station_id      = sid,
            decision        = "NO GO",
            r_total         = 1.0,
            hard_blocked    = False,
            blocker_summary = "",
            score_breakdown = None,
            taf_result      = None,
            weather_source  = source,
            obs_time        = 0,
            next_go_from    = None,
            weather         = None,
            fetch_ok        = False,
            error_message   = msg,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    import time

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from datetime import datetime, timezone, timedelta

    print("=" * 72)
    print("  TEST: decision/engine.py")
    print("=" * 72)

    engine = DecisionEngine(mock=True)

    # ── Hora de despegue: ahora + 1 hora (para que caiga dentro del mock TAF) ──
    now_utc = datetime.now(tz=timezone.utc)
    dep_time = int((now_utc + timedelta(hours=1)).timestamp())

    print(f"\n  Aeronave : {engine.aircraft.name}")
    print(f"  Mock     : {engine.mock}")
    print(f"  Dep. UTC : {datetime.fromtimestamp(dep_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")

    # ── Caso 1: SACC NWP ──────────────────────────────────────────────────────
    print("\n" + "-" * 72)
    print("  [1] SACC — Fuente NWP (mock)")
    r_sacc = engine.evaluate("SACC", runway_heading=150,
                              departure_time=dep_time, flight_duration_h=1.0)

    print(f"  Decision     : {r_sacc.decision}")
    print(f"  R_total      : {r_sacc.r_total:.3f}")
    print(f"  Fuente       : {r_sacc.weather_source}")
    print(f"  Hard blocked : {r_sacc.hard_blocked}")
    if r_sacc.score_breakdown:
        s = r_sacc.score_breakdown
        print(f"  vis={s.r_vis:.2f} ceil={s.r_ceil:.2f} xw={s.r_xwind:.2f} "
              f"gust={s.r_gust:.2f} wx={s.r_wx:.2f} fog={s.r_fog:.2f} "
              f"oro+={s.orographic_delta:.2f} | dominante={s.dominant_factor}")

    # ── Caso 2: SACO METAR (TAF expirado — sin cobertura de ventana actual) ──
    print("\n" + "-" * 72)
    print("  [2] SACO — Fuente METAR+TAF (mock, TAF de 2024 sin cobertura hoy)")
    r_saco = engine.evaluate("SACO", runway_heading=180,
                              departure_time=dep_time, flight_duration_h=1.0)

    print(f"  Decision     : {r_saco.decision}")
    print(f"  R_total      : {r_saco.r_total:.3f}")
    print(f"  Fuente       : {r_saco.weather_source}")
    print(f"  Hard blocked : {r_saco.hard_blocked}")
    if r_saco.taf_result:
        print(f"  TAF cubre ventana: {r_saco.taf_result.taf_covers_window}")
        print(f"  TAF r_taf        : {r_saco.taf_result.r_taf:.2f}")
    if r_saco.score_breakdown:
        s = r_saco.score_breakdown
        print(f"  vis={s.r_vis:.2f} ceil={s.r_ceil:.2f} xw={s.r_xwind:.2f} "
              f"gust={s.r_gust:.2f} wx={s.r_wx:.2f} fog={s.r_fog:.2f} "
              f"taf={s.r_taf:.2f} | dominante={s.dominant_factor}")

    # ── Caso 3: SACO con dep_time dentro del TEMPO TSRA del mock ──────────────
    # El mock TAF tiene TEMPO TSRA de 2024-01-27 19:00→23:00 UTC
    # Usando dep_time=1706390000 (≈ 2024-01-27 19:10 UTC) se activa el hard blocker
    dep_in_tempo = 1706390000
    print("\n" + "-" * 72)
    print("  [3] SACO — dep_time dentro del TEMPO TSRA (hard blocker esperado)")
    r_saco_ts = engine.evaluate("SACO", runway_heading=180,
                                 departure_time=dep_in_tempo, flight_duration_h=1.0)

    print(f"  Decision     : {r_saco_ts.decision}")
    print(f"  Hard blocked : {r_saco_ts.hard_blocked}")
    if r_saco_ts.blocker_summary:
        print(f"  Blocker      : {r_saco_ts.blocker_summary}")

    # ── Caso 3: Estacion sin datos ────────────────────────────────────────────
    print("\n" + "-" * 72)
    print("  [3] SAVY — Sin datos (mock devuelve None)")
    r_savy = engine.evaluate("SAVY", runway_heading=120,
                              departure_time=dep_time, flight_duration_h=1.0)

    print(f"  Decision     : {r_savy.decision}")
    print(f"  fetch_ok     : {r_savy.fetch_ok}")
    print(f"  error        : {r_savy.error_message}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 72)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # SACC
    check("SACC: weather_source='nwp'",       r_sacc.weather_source == "nwp")
    check("SACC: taf_result es None",         r_sacc.taf_result is None)
    check("SACC: fetch_ok=True",              r_sacc.fetch_ok)
    check("SACC: decision en {GO,CAUTION,NO GO}",
          r_sacc.decision in {"GO", "CAUTION", "NO GO"})
    check("SACC: R_total en [0, 1]",          0.0 <= r_sacc.r_total <= 1.0)

    # SACO mock (dep_time actual, TAF de 2024 sin cobertura)
    check("SACO mock: weather_source='metar'",      r_saco.weather_source == "metar")
    check("SACO mock: hard_blocked=False (TAF sin cobertura)", not r_saco.hard_blocked)
    check("SACO mock: decision en {GO,CAUTION,NO GO}",
          r_saco.decision in {"GO", "CAUTION", "NO GO"})
    check("SACO mock: fetch_ok=True",               r_saco.fetch_ok)

    # SACO con dep_time dentro del TEMPO TSRA → hard blocker activo
    check("SACO TEMPO TSRA: hard_blocked=True",     r_saco_ts.hard_blocked)
    check("SACO TEMPO TSRA: decision='NO GO'",      r_saco_ts.decision == "NO GO")

    # Sin datos
    check("SAVY sin datos: fetch_ok=False",   not r_savy.fetch_ok)
    check("SAVY sin datos: decision='NO GO'", r_savy.decision == "NO GO")
    check("SAVY sin datos: error_message!=''",r_savy.error_message != "")

    # Normalización de station_id
    r_lower = engine.evaluate("saco", runway_heading=180,
                               departure_time=dep_time, flight_duration_h=1.0)
    check("station_id en minusculas normalizado",
          r_lower.station_id == "SACO")

    print("\n" + "=" * 72)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 72)
