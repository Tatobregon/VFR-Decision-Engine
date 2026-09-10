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
  5. Soft scoring: R_total = sum(w_i * r_i)
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
import math
import time
from dataclasses import dataclass, replace
from typing import Optional

try:
    from config import NWP_STATIONS, NWP_HOURS_AHEAD
    from data.fetcher_aviationweather import AviationWeatherFetcher
    from data.fetcher_openmeteo        import OpenMeteoFetcher
    from parsers.metar_parser          import (
        MetarParser, ParsedWeather, _compute_flight_category,
    )
    from parsers.taf_parser            import TafParser, ParsedTaf
    from parsers.openmeteo_adapter     import OpenMeteoAdapter
    from features.taf_window           import TafAnalyzer, TafWindowResult, nwp_trend_r_taf
    from risk.hard_blockers            import (
        check_hard_blockers,
        check_hard_blockers_from_weather,
    )
    from risk.soft_scoring             import compute_soft_score, SoftScoreResult
    from risk.aircraft_profiles        import AircraftProfile, ALPHA_TRAINER
    from features.density_altitude     import compute_density_altitude, DensityAltitudeResult
    from features.crosswind            import favored_runway
    from data.airports                 import AIRPORTS
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import NWP_STATIONS, NWP_HOURS_AHEAD
    from data.fetcher_aviationweather import AviationWeatherFetcher
    from data.fetcher_openmeteo        import OpenMeteoFetcher
    from parsers.metar_parser          import (
        MetarParser, ParsedWeather, _compute_flight_category,
    )
    from parsers.taf_parser            import TafParser, ParsedTaf
    from parsers.openmeteo_adapter     import OpenMeteoAdapter
    from features.taf_window           import TafAnalyzer, TafWindowResult, nwp_trend_r_taf
    from risk.hard_blockers            import (
        check_hard_blockers,
        check_hard_blockers_from_weather,
    )
    from risk.soft_scoring             import compute_soft_score, SoftScoreResult
    from risk.aircraft_profiles        import AircraftProfile, ALPHA_TRAINER
    from features.density_altitude     import compute_density_altitude, DensityAltitudeResult
    from features.crosswind            import favored_runway
    from data.airports                 import AIRPORTS

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Horizonte de pronostico necesario
# ──────────────────────────────────────────────────────────────────────────────

# Tope de horas que tiene sentido pedir. La fuente entrega dos dias desde la
# medianoche local, asi que desde "ahora" quedan entre 24 y 48 h disponibles.
MAX_FORECAST_HOURS = 48

# Cuanto vale una observacion METAR como descripcion de un momento.
#
# Un METAR se emite cada hora (o cada media hora) y describe el instante en que
# se tomo. Dentro de esa vigencia es el MEJOR dato disponible para ese momento:
# una observacion real le gana a cualquier pronostico del mismo momento. Pasada
# esa hora, el pronostico del TAF describe mejor lo que va a haber que una
# observacion vieja.
VIGENCIA_OBSERVACION_H = 1.0

# Desvio maximo tolerable entre la hora pedida y la muestra evaluada, sin que se
# considere que la salida quedo fuera de alcance. El pronostico es horario, asi
# que redondear al slot mas cercano nunca deberia costar mas de media hora; se
# toma el doble como margen.
DESVIO_MAX_ACEPTABLE_H = 1.0


def _horizonte_necesario(departure_time: int, flight_duration_h: float) -> int:
    """
    Cuantas horas de pronostico hacen falta para cubrir la ventana pedida.

    Pedir una constante fija hace que toda salida planificada mas alla de ese
    horizonte caiga fuera del filtro, y el motor termine evaluando otra hora sin
    avisarlo. El horizonte tiene que seguir a lo que el piloto pidio.
    """
    fin = departure_time + int(flight_duration_h * 3600)
    faltan = (fin - int(time.time())) / 3600.0
    return max(NWP_HOURS_AHEAD, min(int(math.ceil(faltan)) + 1, MAX_FORECAST_HOURS))


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

    weather_source  : str                       # "metar" | "metar+taf" | "nwp"
    obs_time        : int                       # Unix UTC de la observacion usada

    next_go_from    : Optional[int]             # Unix UTC estimado proxima ventana GO

    weather         : Optional[ParsedWeather]   # observacion usada (para display)
    fetch_ok        : bool                      # False si no hubo datos
    error_message   : str                       # "" si sin error

    density_altitude: Optional[DensityAltitudeResult] = None  # None si sin temperatura
    runway_heading  : int = 0                   # pista usada (favorable si fue auto)

    # Momento de la muestra que PRODUJO el veredicto. En el camino NWP se evalua
    # el peor caso de toda la ventana de vuelo, que rara vez es la hora que se
    # muestra en pantalla (`obs_time`). Sin este dato el piloto ve "Xwind 1.1 kt"
    # junto a un cartel que dice "viento cruzado 10 kt" y no puede reconciliarlos.
    worst_obs_time  : Optional[int] = None

    # True cuando la hora de salida pedida cae FUERA del horizonte del pronostico
    # y hubo que evaluar la hora disponible mas cercana. Sin esto el sistema
    # devolvia condiciones de otro momento como si fueran las pedidas.
    forecast_out_of_range : bool = False

    # De donde salieron las condiciones que se evaluaron, en texto para el
    # piloto. En un aerodromo con estacion hay TRES fuentes en juego —la
    # observacion, el pronostico de aerodromo y el modelo numerico— y cual mando
    # depende de para que momento se pregunte. Sin declararlo, la pantalla
    # muestra una visibilidad pronosticada con el mismo aspecto que una
    # observada, que es exactamente el error que este campo evita.
    conditions_source : str = ""

    # Texto crudo del TAF que se uso, cuando se uso. El METAR crudo ya se
    # muestra para que el piloto pueda verificar la observacion; si el veredicto
    # sale del pronostico, el pronostico tiene que poder verificarse igual.
    raw_taf : Optional[str] = None


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
        personal_minima = None,
    ):
        self.mock     = mock
        self.aircraft = aircraft or ALPHA_TRAINER
        self.personal_minima = personal_minima  # PersonalMinima o None (sin ajuste)

        self._aw          = AviationWeatherFetcher(mock=mock)
        self._nwp_fetch   = OpenMeteoFetcher(mock=mock)
        self._metar_parser = MetarParser()
        self._taf_parser   = TafParser()
        self._nwp_adapter  = OpenMeteoAdapter()
        self._taf_analyzer = TafAnalyzer()

    # ── Entrada publica ───────────────────────────────────────────────────────

    def _resolve_runway(self, sid: str, runway_heading, weather) -> int:
        """
        Resuelve el rumbo de pista a usar. Si `runway_heading` es None ("auto"),
        elige la cabecera favorable segun el viento de `weather` y las pistas del
        aerodromo. Si se especifico una pista, la respeta (override del usuario).
        """
        if runway_heading is not None:
            return runway_heading
        ap = AIRPORTS.get(sid)
        headings = [r.heading for r in ap.runways] if (ap and ap.runways) else []
        if not headings or weather is None:
            return 180
        return favored_runway(headings, weather.wind_dir, weather.wind_spd_kt,
                              getattr(weather, "wind_variable", False))

    def evaluate(
        self,
        station_id        : str,
        runway_heading    : Optional[int],     # None = auto (pista favorable)
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

        # Preferir METAR (observación real) cuando el aeródromo tiene código ICAO
        # —requisito para reportar METAR—. Si el aeródromo no tiene METAR
        # disponible, _evaluate_metar cae automáticamente a NWP (pronóstico).
        # Aeródromos sin ICAO (rurales solo con identificador local) usan NWP
        # directamente, sin intentar METAR.
        ap = AIRPORTS.get(sid)
        if ap is not None and ap.icao_code:
            return self._evaluate_metar(sid, runway_heading, departure_time, flight_duration_h)
        return self._evaluate_nwp(sid, runway_heading, departure_time, flight_duration_h)

    # ── Path NWP (SACC y similares) ───────────────────────────────────────────

    def _evaluate_nwp(
        self,
        sid              : str,
        runway_heading   : int,
        departure_time   : int,
        flight_duration_h: float,
    ) -> DecisionResult:
        cfg = NWP_STATIONS[sid]

        # Muestreo en anillo: el aerodromo mas un anillo de puntos a su alrededor,
        # en UNA sola peticion. El primero es siempre el aerodromo.
        # Motivo: la grilla del modelo global suaviza la orografia, de modo que un
        # unico punto entrega el promedio de la celda y no describe ni el valle ni
        # la ladera. Ver data/fetcher_openmeteo.get_forecast_ring.
        # Horizonte DERIVADO de la ventana pedida, no una constante. Con las 12 h
        # fijas de NWP_HOURS_AHEAD, una salida planificada para dentro de 19 h
        # quedaba fuera del filtro y el motor caia en "la hora disponible mas
        # cercana" SIN DECIRLO: el piloto pedia las 15:00 y recibia las 06:00.
        horizonte = _horizonte_necesario(departure_time, flight_duration_h)
        ring = self._nwp_fetch.get_forecast_ring(
            lat         = cfg["lat"],
            lon         = cfg["lon"],
            elevation_m = cfg["elev_m"],
            hours_ahead = horizonte,
        )
        if not ring:
            return self._no_data(sid, "nwp")

        all_wx = self._nwp_adapter.adapt_all(ring[0], station_id=sid)
        if not all_wx:
            return self._no_data(sid, "nwp")

        # ── Ventana de vuelo ─────────────────────────────────────────────────
        window_end = departure_time + int(flight_duration_h * 3600)
        window_wx  = [w for w in all_wx
                      if departure_time <= w.obs_time <= window_end]
        fuera_de_rango = False
        if not window_wx:
            # La ventana no contiene ninguna hora del pronostico. Hay DOS causas
            # muy distintas y solo una es un problema:
            #
            #   a) La ventana es mas CORTA que una hora y cae entre dos slots.
            #      Un vuelo de 14 min que sale 20:11 no contiene ni las 20:00 ni
            #      las 21:00. Es lo normal en tramos cortos y la hora mas cercana
            #      esta a minutos: no hay nada que advertir.
            #
            #   b) La salida esta FUERA del horizonte del pronostico y la hora
            #      mas cercana esta a horas de distancia. Eso si hay que decirlo.
            #
            # Lo que las distingue es el DESVIO REAL, no que la ventana este
            # vacia. Como el pronostico es horario, el desvio legitimo maximo es
            # de media hora; el umbral se pone al doble para tener margen.
            window_wx = [min(all_wx, key=lambda w: abs(w.obs_time - departure_time))]
            desvio_h = abs(window_wx[0].obs_time - departure_time) / 3600.0
            fuera_de_rango = desvio_h > DESVIO_MAX_ACEPTABLE_H
            if fuera_de_rango:
                logger.warning(
                    f"NWP {sid}: la salida pedida esta fuera del horizonte del "
                    f"pronostico; se evalua la hora mas cercana "
                    f"({desvio_h:.1f} h de desvio)"
                )

        # Pista a usar (favorable si fue auto), segun el viento representativo.
        ref_wx = min(window_wx, key=lambda w: abs(w.obs_time - departure_time))
        rwy    = self._resolve_runway(sid, runway_heading, ref_wx)

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
                    conditions_source = "pronostico NWP",
                    obs_time        = wx.obs_time,
                    next_go_from    = None,
                    weather         = wx,
                    fetch_ok        = True,
                    error_message   = "",
                    runway_heading  = rwy,
                    worst_obs_time  = wx.obs_time,
                    forecast_out_of_range = fuera_de_rango,
                )

        # ── Tendencia: los aerodromos sin METAR tampoco tienen TAF, asi que el
        # componente de tendencia se sintetiza desde la propia serie NWP. ──────
        r_taf = nwp_trend_r_taf(window_wx, ref_wx)

        # ── Soft scoring: peor caso en TIEMPO y en ESPACIO ────────────────────
        # A la ventana horaria del aerodromo se le suman las mismas horas en cada
        # punto del anillo. El peor de todos define el puntaje.
        #
        # Por que los hard blockers NO usan el anillo: son la traduccion de una
        # regla normativa que se refiere AL AERODROMO ("visibilidad en el
        # aerodromo por debajo de X"). Extenderla a un punto a 10 km seria
        # inventar una regla que la regulacion no tiene. El puntaje blando, en
        # cambio, es una ESTIMACION de riesgo, y tomar la peor estimacion
        # plausible del entorno es un tratamiento legitimo de la incertidumbre.
        # De los puntos del anillo se toma el estado de la MASA DE AIRE
        # (visibilidad, techo, humedad, fenomenos) y NO el viento: el viento se
        # conserva el del aerodromo. El motivo es que el criterio de viento
        # cruzado se define contra la PISTA y contra el maximo demostrado del
        # avion; comparar la rafaga de un cordon a 400 m de altura sobre el
        # campo con el limite de cruzado del avion es un error de categoria,
        # porque la aeronave no va a aterrizar alli. Sin esta salvedad el
        # muestreo produce NO GO por viento en dias de calma en el aerodromo
        # (verificado en SACC: rafaga de ladera del 056 contra pista 320).
        # Del anillo se consideran SOLO los puntos a la elevacion del aerodromo o
        # por encima. La razon es fisica y no admite parametro que calibrar: los
        # fenomenos que degradan la visibilidad en un punto MAS BAJO -niebla de
        # radiacion, encharcamiento de aire frio- se forman por drenaje hacia el
        # fondo del valle, son capas estables y NO ascienden por la ladera. Una
        # niebla cuyo tope esta a 900 m no afecta a un aerodromo a 1138 m. En
        # cambio la nubosidad que se apoya en un cordon POR ENCIMA del campo si
        # importa: es el aire que la aeronave atraviesa al despegar.
        #
        # Sin este filtro, la niebla del fondo del valle de Punilla (730 m) daba
        # r_vis=1 y r_ceil=1, o sea 0.714 de R, y producia NO GO en La Cumbre con
        # la pista despejada. Es la falsa alarma que haria que un instructor deje
        # de mirar la herramienta.
        #
        # Si la niebla fuera lo bastante profunda como para alcanzar al campo, el
        # punto del aerodromo -que siempre entra- ya la reporta: no se pierde nada.
        # Si el aerodromo es el punto mas alto del entorno no queda ningun punto
        # del anillo y el comportamiento vuelve a ser el de la consulta simple,
        # que es lo correcto: alli la nubosidad orografica se forma sobre el campo
        # y el propio punto central la captura.
        site_elev = getattr(ring[0], "elevation_m", None)
        upslope   = [p for p in ring[1:]
                     if site_elev is None
                     or getattr(p, "elevation_m", None) is None
                     or p.elevation_m >= site_elev]

        site_wind = {w.obs_time: w for w in window_wx}
        candidates = list(window_wx)
        for extra in upslope:
            pt_wx = self._nwp_adapter.adapt_all(extra, station_id=sid)
            for w in pt_wx:
                if not (departure_time <= w.obs_time <= window_end):
                    continue
                site = site_wind.get(w.obs_time)
                if site is not None:
                    w = replace(w,
                                wind_dir      = site.wind_dir,
                                wind_spd_kt   = site.wind_spd_kt,
                                wind_gust_kt  = site.wind_gust_kt,
                                wind_variable = site.wind_variable)
                candidates.append(w)

        # Se conserva la muestra junto a su puntaje: hace falta saber DE QUE HORA
        # salio el peor caso para poder informarlo.
        evaluadas = [
            (w, compute_soft_score(w, rwy, self.aircraft, taf_r_taf=r_taf,
                                   personal_minima=self.personal_minima))
            for w in candidates
        ]
        peor_wx, worst = max(evaluadas, key=lambda par: par[1].r_total)

        logger.info(f"NWP {sid}: R_total={worst.r_total:.3f} [{worst.decision}] pista={rwy} "
                    f"(peor de {len(candidates)} muestras; {len(upslope)+1} de {len(ring)} "
                    f"puntos del anillo a la elevacion del campo o por encima)")

        return DecisionResult(
            station_id       = sid,
            decision         = worst.decision,
            r_total          = worst.r_total,
            hard_blocked     = False,
            blocker_summary  = "",
            score_breakdown  = worst,
            taf_result       = None,
            weather_source   = "nwp",
            conditions_source = "pronostico NWP",
            obs_time         = ref_wx.obs_time,
            next_go_from     = None,
            weather          = ref_wx,
            fetch_ok         = True,
            error_message    = "",
            density_altitude = self._compute_da(sid, ref_wx),
            runway_heading   = rwy,
            worst_obs_time   = peor_wx.obs_time,
            forecast_out_of_range = fuera_de_rango,
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
            # El aeródromo tiene ICAO pero no reporta METAR (ej. SACC) → NWP.
            logger.info(f"{sid} sin METAR disponible — usando NWP")
            return self._evaluate_nwp(sid, runway_heading, departure_time, flight_duration_h)

        metar_wx = self._metar_parser.parse(raw_metar)

        # ── TAF window ───────────────────────────────────────────────────────
        # Se analiza ANTES de decidir las condiciones, porque el TAF es la
        # fuente de verdad para todo momento que no cubra la observacion.
        taf_result       = None
        taf_hard_blocked = False
        parsed_taf       = None

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

        # ── Condiciones DEL MOMENTO evaluado ─────────────────────────────────
        # El METAR describe el instante en que se observo; el TAF, el momento
        # que se va a volar. Para una salida dentro de tres horas, la
        # observacion de hace un rato NO son las condiciones del vuelo.
        weather, procedencia = self._condiciones_para_el_momento(
            sid, departure_time, metar_wx, taf_result, parsed_taf)
        logger.info(f"{sid}: condiciones del momento tomadas de {procedencia}")

        # Si ninguna de las dos fuentes del aerodromo describe el momento —la
        # observacion ya vencio y el TAF no llega hasta ahi— el modelo numerico
        # SI lo describe, y es la unica fuente que lo hace. Entregar una
        # observacion de veinte horas atras como "las condiciones del vuelo" es
        # el mismo error que el motor ya evita cuando el aerodromo no tiene
        # estacion: la regla es la misma, un nivel mas adentro.
        #
        # El TAF cubre entre 24 y 30 h; el pronostico numerico llega a 48. La
        # franja entre ambos es real para quien planifica con un dia de
        # anticipacion, que es cuando esta decision se toma.
        if weather is metar_wx and                 abs(departure_time - metar_wx.obs_time) / 3600.0 > VIGENCIA_OBSERVACION_H:
            logger.info(
                f"{sid}: {procedencia}; la observacion tiene "
                f"{abs(departure_time - metar_wx.obs_time) / 3600.0:.1f} h — se usa NWP"
            )
            return self._evaluate_nwp(sid, runway_heading, departure_time,
                                      flight_duration_h)

        # Pista a usar (favorable si fue auto), segun el viento del momento.
        rwy = self._resolve_runway(sid, runway_heading, weather)

        # ── Hard blockers: sobre las condiciones del momento ─────────────────
        blocker = check_hard_blockers_from_weather(weather)

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
                weather_source  = weather.source,
                obs_time        = weather.obs_time,
                next_go_from    = next_go,
                weather         = weather,
                fetch_ok        = True,
                error_message   = "",
                runway_heading  = rwy,
                worst_obs_time  = weather.obs_time,
                conditions_source = procedencia,
                raw_taf         = parsed_taf.raw_string if parsed_taf else None,
            )

        # ── Soft scoring ─────────────────────────────────────────────────────
        r_taf = taf_result.r_taf if taf_result else 0.0
        score = compute_soft_score(weather, rwy, self.aircraft, taf_r_taf=r_taf,
                                   personal_minima=self.personal_minima)

        next_go = taf_result.next_go_from if taf_result else None
        logger.info(f"METAR {sid}: R_total={score.r_total:.3f} [{score.decision}] pista={rwy}")

        return DecisionResult(
            station_id       = sid,
            decision         = score.decision,
            r_total          = score.r_total,
            hard_blocked     = False,
            blocker_summary  = "",
            score_breakdown  = score,
            taf_result       = taf_result,
            weather_source   = weather.source,
            obs_time         = weather.obs_time,
            next_go_from     = next_go,
            weather          = weather,
            fetch_ok         = True,
            error_message    = "",
            density_altitude = self._compute_da(sid, weather),
            runway_heading   = rwy,
            # Por este camino se evalua UN estado —la observacion, o el peor
            # caso del TAF para ese momento—, asi que el peor caso y lo mostrado
            # son lo mismo. Se completa igual para que el consumidor no tenga
            # que distinguir de que camino vino el resultado.
            worst_obs_time   = weather.obs_time,
            conditions_source = procedencia,
            raw_taf          = parsed_taf.raw_string if parsed_taf else None,
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

    # ── Condiciones para el momento evaluado ──────────────────────────────────

    def _nwp_para_el_momento(
        self,
        sid    : str,
        momento: int,
    ) -> Optional[ParsedWeather]:
        """
        Pronostico NWP del aerodromo para un momento dado.

        Se usa para completar lo que el TAF NO trae: temperatura y punto de
        rocio, y con ellos el spread termico del que depende el riesgo de
        niebla y la altitud de densidad.
        """
        cfg = NWP_STATIONS.get(sid)
        if cfg is None:
            return None
        try:
            raw = self._nwp_fetch.get_forecast(
                lat         = cfg["lat"],
                lon         = cfg["lon"],
                elevation_m = cfg["elev_m"],
                hours_ahead = _horizonte_necesario(momento, 1.0),
            )
            if raw is None:
                return None
            horas = self._nwp_adapter.adapt_all(raw, station_id=sid)
            if not horas:
                return None
            return min(horas, key=lambda w: abs(w.obs_time - momento))
        except Exception as exc:
            logger.warning(f"No se pudo completar con NWP en {sid}: {exc}")
            return None

    def _condiciones_para_el_momento(
        self,
        sid       : str,
        momento   : int,
        metar_wx  : ParsedWeather,
        taf_result: Optional[TafWindowResult],
        parsed_taf: Optional[ParsedTaf] = None,
    ) -> tuple:
        """
        Arma las condiciones del MOMENTO evaluado combinando las tres fuentes.

        Devuelve (ParsedWeather, procedencia).

        Criterio, en orden:

          1. Si el momento cae dentro de la vigencia de la observacion, gana el
             METAR. Una observacion real del momento es mejor dato que cualquier
             pronostico para ese mismo momento.

          2. Si no, y el TAF esta VIGENTE para ese momento, el TAF es la verdad
             para lo que pronostica —visibilidad, techo, viento, fenomenos— y el
             NWP completa lo que el TAF no trae: temperatura y punto de rocio.
             El QNH se arrastra del METAR porque es la unica fuente que lo tiene
             y cambia despacio.

          3. Si no hay TAF vigente para ese momento, se devuelve el METAR y se
             declara, para que el llamador sepa que mira una observacion vieja.

        Se toma `worst_case` y no `effective_base`: incluye los transitorios
        (TEMPO/PROB) ademas de la base, que es el mismo criterio con el que este
        modulo ya evalua los bloqueos duros del TAF y la misma filosofia de peor
        caso que usa el camino NWP.

        Cada MAGNITUD se toma entera de una sola fuente. Mezclar la velocidad
        pronosticada con la direccion del modelo, o el techo del TAF con las
        capas de la observacion, produce un estado que ninguna fuente predijo:
        seria informacion inventada por el promedio de dos pronosticos.
        """
        edad_h = abs(momento - metar_wx.obs_time) / 3600.0
        if edad_h <= VIGENCIA_OBSERVACION_H:
            return metar_wx, "observacion METAR"

        if taf_result is None or taf_result.worst_case is None:
            return metar_wx, "observacion METAR (sin TAF)"

        # La vigencia del TAF es la del TAF, no la de la ventana de vuelo: la
        # ventana ES el momento pedido, asi que compararlo contra si mismo no
        # verifica nada. Si el TAF no cubre el momento, no hay pronostico.
        if parsed_taf is not None and not (
                parsed_taf.valid_from <= momento <= parsed_taf.valid_to):
            return metar_wx, "observacion METAR (el TAF no cubre ese momento)"

        taf = taf_result.worst_case
        nwp = self._nwp_para_el_momento(sid, momento)

        # ── Viento: bloque completo de una sola fuente ────────────────────────
        # Una direccion sin su velocidad, o al reves, no describe ningun viento.
        # `wind_dir=None` con `wind_variable=True` es como el TAF dice VRB, y es
        # informacion: no es un hueco que haya que rellenar con el modelo.
        if taf.wind_spd_kt is not None or taf.wind_variable:
            w_dir, w_spd  = taf.wind_dir, taf.wind_spd_kt
            w_gust, w_var = taf.wind_gust_kt, taf.wind_variable
        elif nwp is not None and nwp.wind_spd_kt is not None:
            w_dir, w_spd  = nwp.wind_dir, nwp.wind_spd_kt
            w_gust, w_var = nwp.wind_gust_kt, nwp.wind_variable
        else:
            w_dir, w_spd  = metar_wx.wind_dir, metar_wx.wind_spd_kt
            w_gust, w_var = metar_wx.wind_gust_kt, metar_wx.wind_variable

        # ── Visibilidad ──────────────────────────────────────────────────────
        if taf.visibility_km is not None:
            vis = taf.visibility_km
        elif nwp is not None and nwp.visibility_km is not None:
            vis = nwp.visibility_km
        else:
            vis = metar_wx.visibility_km

        # ── Nubosidad: capas y techo son la misma magnitud ────────────────────
        # En el TAF, "sin capa BKN/OVC" significa SIN TECHO, no "dato ausente":
        # NSC y SKC son afirmaciones del pronostico. Por eso, si el TAF describe
        # el cielo, su techo vale aunque sea None.
        if taf.sky_layers:
            capas, ceiling = list(taf.sky_layers), taf.ceiling_ft
        elif nwp is not None and nwp.sky_layers:
            capas, ceiling = list(nwp.sky_layers), nwp.ceiling_ft
        else:
            capas, ceiling = list(metar_wx.sky_layers or []), metar_wx.ceiling_ft

        # ── Fenomenos: el TAF los pronostica; lista vacia es "no se esperan" ──
        wx_codes = list(taf.wx_codes or [])

        # ── Lo que el TAF NO trae: temperatura y punto de rocio ───────────────
        if nwp is not None and nwp.temp_c is not None:
            temp, rocio, spread = nwp.temp_c, nwp.dewpoint_c, nwp.spread_c
            complemento = " + NWP (temperatura y rocio)"
        else:
            temp, rocio, spread = metar_wx.temp_c, metar_wx.dewpoint_c, metar_wx.spread_c
            complemento = " (sin NWP: temperatura de la observacion)"

        combinado = replace(
            metar_wx,
            source          = "metar+taf",
            obs_time        = momento,
            nwp_estimated   = True,          # es un pronostico, no una observacion
            wind_dir        = w_dir,
            wind_spd_kt     = w_spd,
            wind_gust_kt    = w_gust,
            wind_variable   = w_var,
            visibility_km   = vis,
            ceiling_ft      = ceiling,
            sky_layers      = capas,
            wx_codes        = wx_codes,
            temp_c          = temp,
            dewpoint_c      = rocio,
            spread_c        = spread,
            # QNH: la unica fuente que lo publica es el METAR, y cambia despacio.
            altimeter_hpa   = metar_wx.altimeter_hpa,
            flight_category = _compute_flight_category(vis, ceiling),
        )
        return combinado, "pronostico TAF" + complemento

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
              f"| dominante={s.dominant_factor}")

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

    # ── Caso 4: SAVY — tiene ICAO pero no reporta METAR → cae a NWP ───────────
    print("\n" + "-" * 72)
    print("  [4] SAVY — sin METAR, fallback automatico a NWP")
    r_savy = engine.evaluate("SAVY", runway_heading=120,
                              departure_time=dep_time, flight_duration_h=1.0)

    print(f"  Decision     : {r_savy.decision}")
    print(f"  Fuente       : {r_savy.weather_source}")
    print(f"  fetch_ok     : {r_savy.fetch_ok}")

    # ── Caso 5: sin datos reales (fetchers devuelven None) → NO GO conservador ─
    # En mock los fetchers siempre devuelven datos; se fuerza el caso "sin datos"
    # parcheando ambos para verificar el camino conservador de _no_data().
    print("\n" + "-" * 72)
    print("  [5] Sin datos (fetchers forzados a None) → NO GO conservador")
    engine_nd = DecisionEngine(mock=True)
    engine_nd._aw.get_metar_and_taf = lambda sid: (None, None)
    engine_nd._nwp_fetch.get_forecast = lambda **kw: None
    r_nd = engine_nd.evaluate("SAVY", runway_heading=120,
                              departure_time=dep_time, flight_duration_h=1.0)

    print(f"  Decision     : {r_nd.decision}")
    print(f"  fetch_ok     : {r_nd.fetch_ok}")
    print(f"  error        : {r_nd.error_message}")

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

    # SAVY: sin METAR → fallback a NWP (el mock NWP siempre devuelve datos)
    check("SAVY fallback: fetch_ok=True",           r_savy.fetch_ok)
    check("SAVY fallback: weather_source='nwp'",     r_savy.weather_source == "nwp")
    check("SAVY fallback: decision en {GO,CAUTION,NO GO}",
          r_savy.decision in {"GO", "CAUTION", "NO GO"})

    # Sin datos reales (fetchers forzados a None) → NO GO conservador
    check("Sin datos: fetch_ok=False",   not r_nd.fetch_ok)
    check("Sin datos: decision='NO GO'", r_nd.decision == "NO GO")
    check("Sin datos: error_message!=''",r_nd.error_message != "")

    # Normalización de station_id
    r_lower = engine.evaluate("saco", runway_heading=180,
                               departure_time=dep_time, flight_duration_h=1.0)
    check("station_id en minusculas normalizado",
          r_lower.station_id == "SACO")

    print("\n" + "=" * 72)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 72)
