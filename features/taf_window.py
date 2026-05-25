"""
taf_window.py
=============
Analiza el pronostico TAF en la ventana temporal del vuelo planificado.

Tres responsabilidades principales:

  1. Interseccion de periodos: detecta que periodos TAF son activos durante
     la ventana [despegue, despegue + duracion].

  2. Herencia de campos (TAF inheritance): los periodos TEMPO/BECMG parciales
     tienen campos None que se heredan del BASE. Este modulo resuelve esa
     herencia para obtener las condiciones efectivas de cada periodo.

  3. Analisis de riesgo: calcula el peor caso dentro de la ventana,
     el score r_taf para el soft scoring, y la proxima ventana GO sostenida.

Reglas de herencia TAF
----------------------
- BECMG: transicion permanente. Una vez que empieza (time_from <= t), sus
  campos no-None reemplazan al BASE y se convierten en el nuevo estado base.
- TEMPO / PROB: condiciones temporales superpuestas. Sus campos no-None
  reemplazan al BASE efectivo solo durante su ventana temporal.
- Campos None en cualquier periodo no-BASE se heredan del BASE efectivo
  en ese momento.

Uso tipico
----------
    from parsers.taf_parser import TafParser
    from features.taf_window import TafAnalyzer

    taf    = TafParser().parse(raw_taf)
    result = TafAnalyzer().analyze(taf, departure_ts, flight_duration_h=2.0)

    print(result.worst_case.flight_category)  # peor categoria en la ventana
    print(result.r_taf)                        # score para el risk engine
    if result.next_go_from:
        print("Proxima ventana GO:", result.next_go_from)
"""

import logging
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

try:
    from parsers.taf_parser import ParsedTaf, ParsedTafPeriod
    from parsers.metar_parser import _compute_flight_category
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from parsers.taf_parser import ParsedTaf, ParsedTafPeriod
    from parsers.metar_parser import _compute_flight_category

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

# Menor rank = condicion mas restrictiva
CATEGORY_RANK = {"LIFR": 0, "IFR": 1, "MVFR": 2, "VFR": 3}
_RANK_DEFAULT = 3   # None o cualquier desconocido = sin restriccion

# Tokens que producen r_taf = 1.0 si aparecen en un periodo transitorio
TAF_HARD_BLOCKERS = {"TS", "TSRA", "TSGR", "GR", "FC", "VA", "FZRA", "FZDZ"}

# Duracion minima (segundos) de una franja para ser considerada "ventana GO"
_DEFAULT_MIN_GO_S = int(1.5 * 3600)   # 1 h 30 min


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TafWindowResult:
    """
    Resultado del analisis TAF para una ventana de vuelo especifica.

    Campos clave para el risk engine:
      worst_case   : ParsedTafPeriod con las condiciones efectivas mas restrictivas.
      r_taf        : score [0, 1] para el soft scoring (peso 0.05).
      next_go_from : Unix UTC del inicio de la proxima franja GO sostenida.
    """
    # Ventana analizada
    window_from          : int
    window_to            : int
    taf_covers_window    : bool            # True si valid_to >= window_to

    # Estado base efectivo al inicio del vuelo (BASE + BECMG aplicados)
    effective_base       : ParsedTafPeriod

    # Periodos no-BASE que intersectan la ventana (sin herencia resuelta)
    active_periods       : list            # list[ParsedTafPeriod]

    # TEMPO / PROB con herencia resuelta (condiciones efectivas completas)
    effective_transients : list            # list[ParsedTafPeriod]

    # Peor caso (base efectivo o algun transitorio efectivo)
    worst_case           : ParsedTafPeriod
    worst_flight_category: Optional[str]

    # Score TAF para el risk engine
    r_taf                : float           # [0, 1]

    # Flags de contenido
    has_tempo            : bool
    has_prob             : bool
    has_becmg            : bool

    # Proxima franja donde las condiciones son GO sostenido (sin TEMPO/PROB activos)
    next_go_from         : Optional[int] = None


# ──────────────────────────────────────────────────────────────────────────────
# TafAnalyzer
# ──────────────────────────────────────────────────────────────────────────────

class TafAnalyzer:
    """
    Analiza un ParsedTaf en el contexto de la ventana de vuelo planificada.
    Sin estado: se puede instanciar una vez y reutilizar.
    """

    def analyze(
        self,
        taf               : ParsedTaf,
        departure_time    : int,
        flight_duration_h : float = 1.0,
        min_go_duration_h : float = 1.5,
    ) -> TafWindowResult:
        """
        Analiza el TAF para la ventana [departure_time, departure_time + duracion].

        Parameters
        ----------
        taf               : ParsedTaf parseado.
        departure_time    : Unix timestamp UTC del despegue estimado.
        flight_duration_h : Duracion del vuelo en horas.
        min_go_duration_h : Horas minimas consecutivas sin TEMPO/PROB para
                            considerar una franja como "ventana GO sostenida".

        Returns
        -------
        TafWindowResult con todos los resultados del analisis.
        """
        window_from = departure_time
        window_to   = departure_time + int(flight_duration_h * 3600)
        covers      = taf.valid_to >= window_to

        base_raw = _find_base(taf)
        if base_raw is None:
            logger.warning(
                f"TAF {taf.station_id}: no hay periodo BASE; "
                f"usando primer periodo como fallback"
            )
            base_raw = taf.periods[0] if taf.periods else None

        if base_raw is None:
            logger.error(f"TAF {taf.station_id}: sin periodos, devolviendo resultado vacio")
            return _empty_result(window_from, window_to, covers)

        # BASE efectivo: aplicar BECMG que hayan empezado antes del despegue
        effective_base = self._effective_base_at(taf, base_raw, window_from)

        # Periodos no-BASE que se superponen con la ventana de vuelo
        active = _periods_in_window(taf, window_from, window_to)

        # Periodos transitorios efectivos (herencia resuelta)
        effective_transients = [
            self._apply_overlay(effective_base, p, keep_meta=True)
            for p in active
            if p.is_transient
        ]

        has_tempo = any(p.change_indicator == "TEMPO"  for p in active)
        has_prob  = any(
            p.change_indicator in ("PROB30", "PROB40") for p in active
        )
        has_becmg = any(p.change_indicator == "BECMG"  for p in active)

        # Peor caso entre base efectivo y todos los transitorios efectivos
        candidates = [effective_base] + effective_transients
        worst = _find_worst(candidates)

        r_taf = self._taf_score(effective_transients)

        next_go = self._next_go_window(
            taf, base_raw, window_to, int(min_go_duration_h * 3600)
        )

        logger.info(
            f"TAF {taf.station_id} | ventana {_fmt(window_from)}-{_fmt(window_to)} | "
            f"activos={len(active)} TEMPO={has_tempo} PROB={has_prob} BECMG={has_becmg} | "
            f"worst={worst.flight_category} r_taf={r_taf:.2f}"
        )

        return TafWindowResult(
            window_from          = window_from,
            window_to            = window_to,
            taf_covers_window    = covers,
            effective_base       = effective_base,
            active_periods       = active,
            effective_transients = effective_transients,
            worst_case           = worst,
            worst_flight_category= worst.flight_category,
            r_taf                = r_taf,
            has_tempo            = has_tempo,
            has_prob             = has_prob,
            has_becmg            = has_becmg,
            next_go_from         = next_go,
        )

    # ── Herencia / merge ──────────────────────────────────────────────────────

    def _effective_base_at(
        self,
        taf     : ParsedTaf,
        base_raw: ParsedTafPeriod,
        at_time : int,
    ) -> ParsedTafPeriod:
        """
        Estado permanente efectivo en `at_time`: BASE con todos los BECMG
        que hayan empezado (time_from <= at_time) aplicados en orden.
        """
        effective = copy(base_raw)
        becmg_list = sorted(
            (p for p in taf.periods
             if p.change_indicator == "BECMG" and p.time_from <= at_time),
            key=lambda p: p.time_from,
        )
        for becmg in becmg_list:
            effective = self._apply_overlay(effective, becmg, keep_meta=False)
        return effective

    def _apply_overlay(
        self,
        base    : ParsedTafPeriod,
        overlay : ParsedTafPeriod,
        keep_meta: bool,
    ) -> ParsedTafPeriod:
        """
        Devuelve una copia del base con los campos no-None del overlay aplicados.

        keep_meta=True  : el resultado lleva los timestamps y tipo del overlay
                          (usado para TEMPO/PROB: preservar su identidad).
        keep_meta=False : el resultado hereda timestamps y tipo del base
                          (usado para BECMG: actualizar el estado permanente).
        """
        merged = copy(base)

        if overlay.wind_dir      is not None: merged.wind_dir      = overlay.wind_dir
        if overlay.wind_spd_kt   is not None: merged.wind_spd_kt   = overlay.wind_spd_kt
        if overlay.wind_gust_kt  is not None: merged.wind_gust_kt  = overlay.wind_gust_kt
        if overlay.wind_variable:             merged.wind_variable  = overlay.wind_variable
        if overlay.visibility_km is not None: merged.visibility_km = overlay.visibility_km

        # Nubosidad: si el overlay define sky_layers, reemplaza techo Y capas
        if overlay.sky_layers:
            merged.sky_layers = overlay.sky_layers
            merged.ceiling_ft = overlay.ceiling_ft
        elif overlay.ceiling_ft is not None:
            merged.ceiling_ft = overlay.ceiling_ft

        # Fenomenos: si el overlay define wx, reemplaza completamente
        if overlay.wx_codes:
            merged.wx_codes = overlay.wx_codes

        if keep_meta:
            merged.time_from        = overlay.time_from
            merged.time_to          = overlay.time_to
            merged.change_indicator = overlay.change_indicator
            merged.probability      = overlay.probability
            merged.is_transient     = overlay.is_transient

        # Recalcular con los valores efectivos
        merged.flight_category = _compute_flight_category(
            merged.visibility_km, merged.ceiling_ft
        )
        return merged

    # ── Score TAF ─────────────────────────────────────────────────────────────

    def _taf_score(self, effective_transients: list) -> float:
        """
        Score de riesgo TAF [0, 1] para el soft scoring (peso 0.05).

        Combina la severidad de las condiciones efectivas (flight_category)
        con la certeza del tipo de indicador (TEMPO > PROB40 > PROB30).
        Hard blockers en cualquier transitorio → 1.0 inmediato.
        """
        if not effective_transients:
            return 0.0

        max_score = 0.0

        for p in effective_transients:
            if any(tok in TAF_HARD_BLOCKERS for tok in p.wx_codes):
                return 1.0

            cat = p.flight_category
            ind = p.change_indicator

            # Score por categoria de vuelo efectiva
            if   cat == "LIFR": cat_score = 1.00
            elif cat == "IFR":  cat_score = 0.75
            elif cat == "MVFR": cat_score = 0.45
            else:               cat_score = 0.15  # VFR con alguna degradacion

            # Modificador por certeza del deterioro
            if   ind == "TEMPO":  modifier = 1.00
            elif ind == "PROB40": modifier = 0.85
            elif ind == "PROB30": modifier = 0.65
            else:                 modifier = 1.00

            max_score = max(max_score, cat_score * modifier)

        return min(max_score, 1.0)

    # ── Proxima ventana GO ─────────────────────────────────────────────────────

    def _next_go_window(
        self,
        taf          : ParsedTaf,
        base_raw     : ParsedTafPeriod,
        from_time    : int,
        min_duration_s: int,
    ) -> Optional[int]:
        """
        Encuentra la proxima franja de al menos `min_duration_s` segundos donde:
          - El BASE efectivo es VFR.
          - No hay TEMPO ni PROB activos.
          - No hay hard blockers en wx_codes del BASE efectivo.

        Escanea los puntos de cambio del TAF (inicio y fin de cada periodo)
        a partir de from_time. Devuelve el Unix timestamp de inicio, o None.
        """
        # Puntos de cambio: cada vez que algun periodo empieza o termina
        breakpoints = sorted(
            {from_time}
            | {p.time_from for p in taf.periods if p.time_from >= from_time}
            | {p.time_to   for p in taf.periods if p.time_to   >  from_time}
        )

        go_start = None

        for i, t in enumerate(breakpoints):
            if t >= taf.valid_to:
                break

            eff_base = self._effective_base_at(taf, base_raw, t)

            # Hay algun TEMPO/PROB activo en t?
            transients_active = any(
                p.is_transient and p.time_from <= t < p.time_to
                for p in taf.periods
            )

            is_go = (
                eff_base.flight_category == "VFR"
                and not transients_active
                and not any(tok in TAF_HARD_BLOCKERS for tok in eff_base.wx_codes)
            )

            if is_go:
                if go_start is None:
                    go_start = t
                # Fin de este intervalo = proximo breakpoint o fin de validez
                next_t = (
                    breakpoints[i + 1]
                    if i + 1 < len(breakpoints)
                    else taf.valid_to
                )
                # La franja GO acumulada [go_start, next_t] ya cubre el minimo?
                if (next_t - go_start) >= min_duration_s:
                    return go_start
            else:
                go_start = None  # reiniciar si las condiciones no son GO

        return None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de modulo (sin acceso a self)
# ──────────────────────────────────────────────────────────────────────────────

def _find_base(taf: ParsedTaf) -> Optional[ParsedTafPeriod]:
    return next((p for p in taf.periods if p.change_indicator is None), None)


def _periods_in_window(
    taf    : ParsedTaf,
    w_from : int,
    w_to   : int,
) -> list:
    """Periodos no-BASE cuya ventana temporal se superpone con [w_from, w_to]."""
    return [
        p for p in taf.periods
        if p.change_indicator is not None
        and p.time_from < w_to
        and p.time_to   > w_from
    ]


def _find_worst(periods: list) -> ParsedTafPeriod:
    """
    Devuelve el periodo con la categoria de vuelo mas restrictiva.
    Desempate: el que tenga hard blockers en wx_codes es peor.
    """
    def sort_key(p):
        cat_rank  = CATEGORY_RANK.get(p.flight_category, _RANK_DEFAULT)
        has_block = int(any(t in TAF_HARD_BLOCKERS for t in p.wx_codes))
        return (cat_rank, -has_block)   # menor cat_rank = peor; -has_block: True=peor

    return min(periods, key=sort_key)


def _empty_result(w_from: int, w_to: int, covers: bool) -> TafWindowResult:
    dummy = ParsedTafPeriod(
        time_from=w_from, time_to=w_to,
        change_indicator=None, probability=None, is_transient=False,
    )
    return TafWindowResult(
        window_from=w_from, window_to=w_to, taf_covers_window=covers,
        effective_base=dummy, active_periods=[], effective_transients=[],
        worst_case=dummy, worst_flight_category=None,
        r_taf=0.0, has_tempo=False, has_prob=False, has_becmg=False,
    )


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m %H:%M")


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    from datetime import datetime, timezone, timedelta

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    from parsers.taf_parser import ParsedTaf, ParsedTafPeriod, TafParser

    print("=" * 68)
    print("  TEST: taf_window.py")
    print("=" * 68)

    # ── Construir TAF de prueba sintetico ─────────────────────────────────────
    # Simulamos el TAF de SACO del siguiente escenario:
    #
    #  00-24 UTC  BASE  : VIS 9999, VNT 100/10kt,    VFR
    #  06-08 UTC  BECMG : VIS 6000, VNT 200/08kt,    VFR  (mejora viento)
    #  10-12 UTC  TEMPO : VIS 1500, CEIL 500ft, TSRA  IFR  (tormenta)
    #  14-16 UTC  PROB40: VIS 3000, CEIL 800ft,       IFR  (lluvia probable)
    #  18-24 UTC  BECMG : VIS 9999, sin cambio viento VFR  (mejora definitiva)
    #
    #  Vuelo planificado: despegue 09:00 UTC, duracion 2 horas -> ventana 09-11

    now      = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    t0       = int(now.timestamp())

    def ts(h): return t0 + h * 3600

    periods = [
        ParsedTafPeriod(
            time_from=ts(0), time_to=ts(24),
            change_indicator=None, probability=None, is_transient=False,
            wind_dir=100, wind_spd_kt=10.0, wind_gust_kt=None,
            visibility_km=10.0, ceiling_ft=None, sky_layers=[],
            wx_codes=[], flight_category="VFR",
        ),
        ParsedTafPeriod(
            time_from=ts(6), time_to=ts(8),
            change_indicator="BECMG", probability=None, is_transient=False,
            wind_dir=200, wind_spd_kt=8.0, wind_gust_kt=None,
            visibility_km=6.0, ceiling_ft=None, sky_layers=[],
            wx_codes=[], flight_category="VFR",
        ),
        ParsedTafPeriod(
            time_from=ts(10), time_to=ts(12),
            change_indicator="TEMPO", probability=None, is_transient=True,
            wind_dir=None, wind_spd_kt=None, wind_gust_kt=25.0,
            visibility_km=1.5, ceiling_ft=500,
            sky_layers=[{"cover": "OVC", "base_ft": 500}],
            wx_codes=["TSRA"], flight_category="IFR",
        ),
        ParsedTafPeriod(
            time_from=ts(14), time_to=ts(16),
            change_indicator="PROB40", probability=40, is_transient=True,
            wind_dir=None, wind_spd_kt=None, wind_gust_kt=None,
            visibility_km=3.0, ceiling_ft=800,
            sky_layers=[{"cover": "BKN", "base_ft": 800}],
            wx_codes=["-RA"], flight_category="IFR",
        ),
        ParsedTafPeriod(
            time_from=ts(18), time_to=ts(24),
            change_indicator="BECMG", probability=None, is_transient=False,
            wind_dir=None, wind_spd_kt=None, wind_gust_kt=None,
            visibility_km=10.0, ceiling_ft=None, sky_layers=[],
            wx_codes=[], flight_category="VFR",
        ),
    ]

    taf = ParsedTaf(
        station_id="SACO", raw_string="TAF SACO ...",
        issue_time="2024-01-27T00:00:00Z",
        valid_from=ts(0), valid_to=ts(24),
        periods=periods,
    )

    analyzer = TafAnalyzer()

    # ── Escenario 1: despegue 09:00 UTC, vuelo 2h -> ventana 09-11 UTC ────────
    dep_09 = ts(9)
    r1 = analyzer.analyze(taf, dep_09, flight_duration_h=2.0)

    print(f"\n  Escenario 1: despegue 09:00 UTC, duracion 2h (ventana 09-11)")
    print(f"    Cubre el TAF      : {r1.taf_covers_window}")
    print(f"    BASE efectivo     : VIS={r1.effective_base.visibility_km} "
          f"ceil={r1.effective_base.ceiling_ft} cat={r1.effective_base.flight_category}")
    print(f"    Periodos activos  : {len(r1.active_periods)} "
          f"({[p.change_indicator for p in r1.active_periods]})")
    print(f"    Transitorios ef.  : {len(r1.effective_transients)}")
    print(f"    Peor caso         : cat={r1.worst_case.flight_category} "
          f"wx={r1.worst_case.wx_codes}")
    print(f"    r_taf             : {r1.r_taf:.3f}")
    print(f"    has_tempo         : {r1.has_tempo}")
    print(f"    next_go_from      : {_fmt(r1.next_go_from) if r1.next_go_from else 'N/A'}")

    # ── Escenario 2: despegue 13:00 UTC, vuelo 3h -> ventana 13-16 UTC ────────
    dep_13 = ts(13)
    r2 = analyzer.analyze(taf, dep_13, flight_duration_h=3.0)

    print(f"\n  Escenario 2: despegue 13:00 UTC, duracion 3h (ventana 13-16)")
    print(f"    BASE efectivo     : VIS={r2.effective_base.visibility_km} "
          f"cat={r2.effective_base.flight_category}")
    print(f"    Periodos activos  : {[p.change_indicator for p in r2.active_periods]}")
    print(f"    Peor caso         : cat={r2.worst_case.flight_category}")
    print(f"    r_taf             : {r2.r_taf:.3f}")
    print(f"    next_go_from      : {_fmt(r2.next_go_from) if r2.next_go_from else 'N/A'}")

    # ── Escenario 3: despegue 19:00 UTC, vuelo 1h -> ventana limpia ───────────
    dep_19 = ts(19)
    r3 = analyzer.analyze(taf, dep_19, flight_duration_h=1.0)

    print(f"\n  Escenario 3: despegue 19:00 UTC, duracion 1h (ventana limpia)")
    print(f"    BASE efectivo     : VIS={r3.effective_base.visibility_km} "
          f"cat={r3.effective_base.flight_category}")
    print(f"    Periodos activos  : {[p.change_indicator for p in r3.active_periods]}")
    print(f"    r_taf             : {r3.r_taf:.3f}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 68)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # Escenario 1: ventana 09-11 intersecta TEMPO(10-12)
    check("Esc1: BECMG(06-08) aplica al BASE efectivo (vis=6.0)",
          r1.effective_base.visibility_km == 6.0)
    check("Esc1: BASE efectivo mantiene cat VFR",
          r1.effective_base.flight_category == "VFR")
    check("Esc1: TEMPO(10-12) detectado en ventana",
          r1.has_tempo)
    check("Esc1: TEMPO efectivo hereda rafaga del overlay",
          any(p.wind_gust_kt == 25.0 for p in r1.effective_transients))
    check("Esc1: TEMPO efectivo hereda viento del BASE efectivo (wind_dir=200)",
          any(p.wind_dir == 200 for p in r1.effective_transients))
    check("Esc1: peor caso tiene TSRA",
          "TSRA" in r1.worst_case.wx_codes)
    check("Esc1: r_taf > 0 (hay TEMPO con TSRA hard blocker)",
          r1.r_taf == 1.0)

    # Escenario 2: ventana 13-16 intersecta PROB40(14-16)
    check("Esc2: PROB40 detectado",
          r2.has_prob)
    # vis=3.0 + ceil=800 -> MVFR segun ANAC (vis>=3 AND ceil>=500)
    check("Esc2: peor caso es MVFR (PROB40: vis=3.0, ceil=800 -> MVFR ANAC)",
          r2.worst_case.flight_category == "MVFR")
    check("Esc2: r_taf < 1.0 (PROB40 sin hard blocker)",
          r2.r_taf < 1.0)
    check("Esc2: r_taf > 0 (hay PROB40 con IFR)",
          r2.r_taf > 0.0)

    # Escenario 3: ventana limpia, sin transitorios
    # BECMG(18-24) se superpone con la ventana 19-20: es correcto que aparezca
    check("Esc3: solo BECMG activo en ventana (sin TEMPO/PROB)",
          len(r3.active_periods) == 1 and r3.active_periods[0].change_indicator == "BECMG")
    check("Esc3: r_taf = 0.0 (sin transitorios)",
          r3.r_taf == 0.0)
    check("Esc3: BASE efectivo VIS=10.0 (BECMG18-24 ya aplico)",
          r3.effective_base.visibility_km == 10.0)

    # Herencia TEMPO: visibilidad y techo se heredan del BASE cuando el TEMPO no los define
    esc1_tempo = r1.effective_transients[0] if r1.effective_transients else None
    check("Esc1: TEMPO efectivo tiene vis=1.5 (del overlay, no del BASE)",
          esc1_tempo is not None and esc1_tempo.visibility_km == 1.5)
    check("Esc1: TEMPO efectivo tiene ceil=500 (del overlay)",
          esc1_tempo is not None and esc1_tempo.ceiling_ft == 500)

    # next_go_from: despues del TEMPO(10-12) y PROB40(14-16), el siguiente GO
    # sostenido deberia ser desde las 16:00 UTC (o 18:00 si el BECMG limpia)
    check("Esc1: next_go_from existe (hay ventana GO posterior)",
          r1.next_go_from is not None)
    # TEMPO termina a las 12:00, PROB40 empieza a las 14:00 -> ventana GO es 12-14 (2h >= 1.5h)
    check("Esc1: next_go_from = ts(12) (ventana limpia entre TEMPO y PROB40)",
          r1.next_go_from is not None and r1.next_go_from == ts(12))

    print("\n" + "=" * 68)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 68)
