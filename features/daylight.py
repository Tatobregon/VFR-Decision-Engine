"""
daylight.py
===========
Calculo de orto/ocaso (salida y puesta del sol) para decidir si un vuelo VFR
ocurre en luz diurna.

El VFR de aviacion general es, salvo habilitacion nocturna y equipamiento
especifico, una actividad DIURNA. Este modulo permite bloquear (NO GO) un vuelo
cuyo despegue, ruta o aterrizaje caiga de noche o ya en el anochecer.

Criterio (conservador, segun pedido):
  - Ventana diurna valida = [orto, ocaso] (salida y puesta del sol).
  - Despues del OCASO (ya en anochecer) o antes del ORTO  -> NO GO.
  - Aterrizaje a < CAUTION_MARGIN_MIN del ocaso            -> CAUTION
    (la luz de dia esta ajustada).

Implementacion: algoritmo estandar "Sunrise/Sunset Algorithm" (Almanac for
Computers, 1990), sin dependencias externas. Devuelve tiempos en UTC.
Precision tipica: ~1 minuto, suficiente para esta aplicacion.
"""

import math
from datetime import datetime, timezone
from typing import Optional, Tuple

# Zenith del sol para orto/ocaso "oficial" (incluye refraccion atmosferica)
_ZENITH_OFFICIAL = 90.833

# Margen para el aviso de "luz ajustada": aterrizar a menos de esto del ocaso.
CAUTION_MARGIN_MIN = 45


def _sun_event_hour_utc(
    year: int, month: int, day: int,
    lat: float, lon: float,
    rising: bool,
    zenith: float = _ZENITH_OFFICIAL,
) -> Optional[float]:
    """
    Hora UTC (en horas decimales 0-24) del orto (rising=True) u ocaso
    (rising=False) para una fecha y posicion. None si el sol no sale/pone ese
    dia (noche o dia polar).
    """
    # Dia del anio
    N = (datetime(year, month, day) - datetime(year, 1, 1)).days + 1

    lng_hour = lon / 15.0
    t = N + ((6 - lng_hour) / 24.0 if rising else (18 - lng_hour) / 24.0)

    # Anomalia media del sol
    M = (0.9856 * t) - 3.289
    # Longitud verdadera del sol
    L = M + (1.916 * math.sin(math.radians(M))) \
          + (0.020 * math.sin(math.radians(2 * M))) + 282.634
    L %= 360.0

    # Ascension recta del sol, en el mismo cuadrante que L
    RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360.0
    L_quad  = (math.floor(L / 90.0)) * 90.0
    RA_quad = (math.floor(RA / 90.0)) * 90.0
    RA = (RA + (L_quad - RA_quad)) / 15.0

    # Declinacion del sol
    sin_dec = 0.39782 * math.sin(math.radians(L))
    cos_dec = math.cos(math.asin(sin_dec))

    cos_H = (math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(lat)))) \
            / (cos_dec * math.cos(math.radians(lat)))
    if cos_H > 1.0:
        return None   # el sol no sale ese dia (noche polar)
    if cos_H < -1.0:
        return None   # el sol no se pone ese dia (dia polar)

    H = (360.0 - math.degrees(math.acos(cos_H))) if rising else math.degrees(math.acos(cos_H))
    H /= 15.0

    T = H + RA - (0.06571 * t) - 6.622
    UT = (T - lng_hour) % 24.0
    return UT


def sun_times_unix(lat: float, lon: float, ref_unix: int) -> Tuple[Optional[int], Optional[int]]:
    """
    Orto y ocaso (Unix UTC) para la fecha (UTC) de `ref_unix` en la posicion dada.
    Devuelve (sunrise_unix, sunset_unix). Cualquiera puede ser None en latitudes
    polares (sin orto/ocaso ese dia).
    """
    dt = datetime.fromtimestamp(ref_unix, tz=timezone.utc)
    day_start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)

    sr_h = _sun_event_hour_utc(dt.year, dt.month, dt.day, lat, lon, rising=True)
    ss_h = _sun_event_hour_utc(dt.year, dt.month, dt.day, lat, lon, rising=False)

    sr = int(day_start.timestamp() + sr_h * 3600) if sr_h is not None else None
    ss = int(day_start.timestamp() + ss_h * 3600) if ss_h is not None else None
    return sr, ss


def is_daylight(lat: float, lon: float, when_unix: int) -> bool:
    """
    True si `when_unix` cae entre el orto y el ocaso en esa posicion.

    Latitudes polares: si no hay ocaso (dia polar) -> True; si no hay orto
    (noche polar) -> False.
    """
    sr, ss = sun_times_unix(lat, lon, when_unix)
    if sr is None and ss is None:
        # Sin eventos: decidir por la elevacion solar aproximada via cos_H signo.
        # Fallback conservador: usar mediodia solar. Para territorio argentino
        # esto no ocurre; en bases antarticas, asumimos lo que indique el computo.
        return _polar_is_day(lat, lon, when_unix)
    if sr is None:   # no hubo orto -> dia polar continuo (no se pone)
        return True
    if ss is None:   # no hubo ocaso -> el sol no se pone -> dia
        return True
    return sr <= when_unix <= ss


def _polar_is_day(lat: float, lon: float, when_unix: int) -> bool:
    """Fallback para dia/noche polar (sin orto ni ocaso)."""
    # Reusa el computo de cos_H: si el sol esta siempre arriba, cos_H<-1 (dia).
    dt = datetime.fromtimestamp(when_unix, tz=timezone.utc)
    # Probar como 'rising': cos_H<-1 => dia polar; >1 => noche polar
    N = (datetime(dt.year, dt.month, dt.day) - datetime(dt.year, 1, 1)).days + 1
    lng_hour = lon / 15.0
    t = N + ((6 - lng_hour) / 24.0)
    M = (0.9856 * t) - 3.289
    L = (M + 1.916 * math.sin(math.radians(M)) + 0.020 * math.sin(math.radians(2 * M)) + 282.634) % 360.0
    sin_dec = 0.39782 * math.sin(math.radians(L))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_H = (math.cos(math.radians(_ZENITH_OFFICIAL)) - sin_dec * math.sin(math.radians(lat))) \
            / (cos_dec * math.cos(math.radians(lat)))
    return cos_H < -1.0


def daylight_status(lat: float, lon: float, when_unix: int) -> dict:
    """
    Estado de luz para un punto y momento. Devuelve:
      is_day           : bool
      sunrise_unix     : Optional[int]
      sunset_unix      : Optional[int]
      min_to_sunset    : Optional[int]  minutos hasta el ocaso (si es de dia)
      tight            : bool           True si es de dia pero a < CAUTION_MARGIN_MIN del ocaso
    """
    sr, ss = sun_times_unix(lat, lon, when_unix)
    is_day = is_daylight(lat, lon, when_unix)
    min_to_sunset = None
    tight = False
    if is_day and ss is not None and when_unix <= ss:
        min_to_sunset = int((ss - when_unix) / 60)
        tight = min_to_sunset < CAUTION_MARGIN_MIN
    return {
        "is_day":        is_day,
        "sunrise_unix":  sr,
        "sunset_unix":   ss,
        "min_to_sunset": min_to_sunset,
        "tight":         tight,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  TEST: daylight.py — orto/ocaso")
    print("=" * 64)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    def fmt(u):
        return datetime.fromtimestamp(u, tz=timezone.utc).strftime("%H:%M UTC") if u else "—"

    # Buenos Aires (-34.6, -58.4) — 21 jun 2026 (solsticio de invierno austral, dia corto)
    jun21 = int(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc).timestamp())
    sr, ss = sun_times_unix(-34.6, -58.4, jun21)
    print(f"\n  BA 21-jun-2026: orto {fmt(sr)}  ocaso {fmt(ss)}")
    # En invierno, BA: orto ~10:50 UTC (07:50 local), ocaso ~20:50 UTC (17:50 local)
    check("BA invierno: orto entre 10:30-11:10 UTC", sr and 10.5 <= (sr % 86400) / 3600 <= 11.2)
    check("BA invierno: ocaso entre 20:30-21:10 UTC", ss and 20.5 <= (ss % 86400) / 3600 <= 21.2)

    # Mediodia UTC en BA en invierno -> de dia
    check("BA 12:00 UTC invierno = de dia", is_daylight(-34.6, -58.4, jun21))
    # 23:00 UTC (20:00 local) en invierno -> de noche
    noche = int(datetime(2026, 6, 21, 23, 0, tzinfo=timezone.utc).timestamp())
    check("BA 23:00 UTC invierno = de noche", not is_daylight(-34.6, -58.4, noche))

    # Verano austral: dia mas largo
    dic21 = int(datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc).timestamp())
    sr2, ss2 = sun_times_unix(-34.6, -58.4, dic21)
    print(f"  BA 21-dic-2026: orto {fmt(sr2)}  ocaso {fmt(ss2)}")
    check("verano: ocaso mas tarde que invierno", (ss2 % 86400) > (ss % 86400))

    # Status con luz ajustada (30 min antes del ocaso de invierno)
    casi = ss - 30 * 60
    st = daylight_status(-34.6, -58.4, casi)
    print(f"  30 min antes del ocaso: is_day={st['is_day']} min_to_sunset={st['min_to_sunset']} tight={st['tight']}")
    check("30 min antes del ocaso: de dia pero ajustado (tight)", st["is_day"] and st["tight"])
    check("min_to_sunset ~30", st["min_to_sunset"] is not None and 28 <= st["min_to_sunset"] <= 32)

    # Ushuaia (-54.8, -68.3) invierno: dia muy corto
    sr3, ss3 = sun_times_unix(-54.8, -68.3, jun21)
    print(f"  Ushuaia 21-jun-2026: orto {fmt(sr3)}  ocaso {fmt(ss3)}")
    if sr3 and ss3:
        horas_luz = (ss3 - sr3) / 3600
        check("Ushuaia invierno: < 8.5 h de luz", horas_luz < 8.5)

    print("\n" + "=" * 64)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 64)
