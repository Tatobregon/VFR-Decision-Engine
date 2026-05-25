"""
main.py
=======
Entry point CLI del motor de decision VFR GO/NO GO.

Uso
---
    python main.py SACC 150
    python main.py SACO 180 --time 19:00 --duration 1.5
    python main.py SACC 150 --mock
    python main.py SACO 180 --time 19:00 --short

Argumentos
----------
    station_id      Codigo ICAO del aeropuerto (ej: SACC, SACO)
    runway_heading  Rumbo magnetico de la pista en grados (ej: 150)

Opciones
--------
    --time HH:MM    Hora de despegue en UTC (default: ahora + 30 min)
    --date DD/MM    Fecha de despegue en UTC (default: hoy; si HH:MM < hora actual, manana)
    --duration N    Duracion del vuelo en horas (default: 1.0)
    --mock          Usar datos simulados (sin conexion a internet)
    --short         Mostrar solo una linea de resumen
    --verbose       Activar logging DEBUG
"""

import argparse
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

# Asegura que la raiz del proyecto este en el path para imports relativos
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config          import NWP_STATIONS, METAR_STATIONS
from decision.engine import DecisionEngine
from output.formatter import format_decision, format_short


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_DURATION_H      = 1.0
DEFAULT_ADVANCE_MINUTES = 30    # si no se especifica --time, usar ahora + 30 min


# ──────────────────────────────────────────────────────────────────────────────
# Parsing de argumentos
# ──────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog        = "vfr-gonogo",
        description = "Motor de decision meteorologica VFR GO/NO GO.",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = (
            "Ejemplos:\n"
            "  python main.py SACC 150\n"
            "  python main.py SACO 180 --time 19:00 --duration 1.5\n"
            "  python main.py SACC 150 --mock\n"
            "  python main.py SACO 180 --time 19:00 --short\n"
        ),
    )
    p.add_argument("station_id",
                   help="Codigo ICAO del aeropuerto (ej: SACC, SACO)")
    p.add_argument("runway_heading",
                   type=int,
                   help="Rumbo magnetico de la pista en grados (ej: 150)")
    p.add_argument("--time", "-t",
                   metavar="HH:MM",
                   help="Hora de despegue UTC (default: ahora + 30 min)")
    p.add_argument("--date", "-d",
                   metavar="DD/MM",
                   help="Fecha de despegue UTC (default: hoy o manana si HH:MM ya paso)")
    p.add_argument("--duration", "-n",
                   type=float,
                   default=DEFAULT_DURATION_H,
                   metavar="HORAS",
                   help=f"Duracion del vuelo en horas (default: {DEFAULT_DURATION_H})")
    p.add_argument("--mock", "-m",
                   action="store_true",
                   help="Usar datos simulados (sin conexion)")
    p.add_argument("--short", "-s",
                   action="store_true",
                   help="Mostrar solo una linea de resumen")
    p.add_argument("--verbose", "-v",
                   action="store_true",
                   help="Activar logging DEBUG")
    return p


def _parse_departure_time(time_str: str, date_str: str) -> int:
    """
    Convierte HH:MM + DD/MM (UTC) a Unix timestamp.

    Si la hora resultante ya paso y no se especifico fecha, usa el dia siguiente.
    """
    now_utc = datetime.now(tz=timezone.utc)

    hh, mm = map(int, time_str.split(":"))

    if date_str:
        dd, mo = map(int, date_str.split("/"))
        year = now_utc.year
        dep_dt = datetime(year, mo, dd, hh, mm, tzinfo=timezone.utc)
    else:
        dep_dt = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dep_dt <= now_utc:
            dep_dt += timedelta(days=1)

    return int(dep_dt.timestamp())


# ──────────────────────────────────────────────────────────────────────────────
# Logica principal
# ──────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """
    Entry point. Devuelve 0 si la decision es GO, 1 si CAUTION o NO GO, 2 si error.
    """
    parser = _build_parser()
    args   = parser.parse_args(argv)

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level  = log_level,
        format = "%(levelname)s | %(name)s | %(message)s",
        stream = sys.stderr,
    )

    # ── Validar estacion ──────────────────────────────────────────────────────
    sid = args.station_id.upper().strip()
    if sid not in NWP_STATIONS and sid not in METAR_STATIONS:
        print(f"ERROR: estacion '{sid}' no reconocida.", file=sys.stderr)
        print(f"  Estaciones NWP  : {sorted(NWP_STATIONS.keys())}", file=sys.stderr)
        print(f"  Estaciones METAR: {sorted(METAR_STATIONS)}", file=sys.stderr)
        return 2

    # ── Validar runway heading ────────────────────────────────────────────────
    if not (0 <= args.runway_heading <= 360):
        print(f"ERROR: runway_heading debe estar entre 0 y 360.", file=sys.stderr)
        return 2

    # ── Calcular hora de despegue ─────────────────────────────────────────────
    if args.time:
        try:
            departure_time = _parse_departure_time(args.time, args.date or "")
        except (ValueError, AttributeError):
            print(
                f"ERROR: formato de tiempo invalido '{args.time}'. "
                f"Use HH:MM (ej: 19:00).",
                file=sys.stderr,
            )
            return 2
    else:
        now_utc = datetime.now(tz=timezone.utc)
        departure_time = int((now_utc + timedelta(minutes=DEFAULT_ADVANCE_MINUTES)).timestamp())

    dep_dt  = datetime.fromtimestamp(departure_time, tz=timezone.utc)
    dep_str = dep_dt.strftime("%d/%m/%Y %H:%M UTC")

    if not args.short:
        source_lbl = "NWP (Open-Meteo)" if sid in NWP_STATIONS else "METAR+TAF (aviationweather.gov)"
        print(f"  Estacion   : {sid}")
        print(f"  Pista      : {args.runway_heading:03d} grados")
        print(f"  Despegue   : {dep_str}")
        print(f"  Duracion   : {args.duration} h")
        print(f"  Fuente     : {source_lbl}")
        if args.mock:
            print(f"  [MOCK — datos simulados]")
        print()

    # ── Evaluar ───────────────────────────────────────────────────────────────
    engine = DecisionEngine(mock=args.mock)

    try:
        result = engine.evaluate(
            station_id        = sid,
            runway_heading    = args.runway_heading,
            departure_time    = departure_time,
            flight_duration_h = args.duration,
        )
    except Exception as exc:
        print(f"ERROR inesperado durante la evaluacion: {exc}", file=sys.stderr)
        logging.exception("Error en engine.evaluate()")
        return 2

    # ── Mostrar resultado ─────────────────────────────────────────────────────
    if args.short:
        print(format_short(result))
    else:
        print(format_decision(result))

    # ── Exit code segun decision ──────────────────────────────────────────────
    if result.decision == "GO":
        return 0
    return 1


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(main())
