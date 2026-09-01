"""
sources.py
==========
Clientes de archivo historico para el estudio de validacion NWP vs METAR.

NO forman parte del runtime del motor. El motor consume el pronostico EN VIVO;
estos clientes traen el archivo del pasado para poder comparar, a posteriori, lo
que el modelo dijo contra lo que efectivamente se observo.

Fuentes
-------
1. Iowa Environmental Mesonet (IEM) — archivo de METAR crudos de las estaciones
   argentinas. Se pide el METAR TEXTUAL, no los campos ya decodificados, para
   poder pasarlo por el MISMO parser que usa el motor (parsers/metar_parser.py).
   Asi la comparacion mide el error del pronostico, no diferencias de decodificacion.

2. Open-Meteo Historical Forecast API — archivo de los pronosticos emitidos.
   Se eligio esta y no la Previous Runs API por una razon concreta y verificada:
   Previous Runs NO devuelve `cloud_cover_low/mid/high` ni `visibility` (vienen
   nulas), y sin nubosidad por niveles no se puede reconstruir el techo, que es
   el criterio de mayor peso del modelo. La Historical Forecast API entrega el
   juego completo de variables que consume RawNWPHour.

   Consecuencia a declarar: la Historical Forecast archiva el pronostico mas
   reciente disponible para cada hora, es decir, un horizonte de prediccion CORTO.
   Es el caso favorable al modelo. Se adopta a proposito porque reproduce el uso
   real del sistema —el piloto consulta poco antes de volar, y el motor pide el
   pronostico del dia— y porque es la lectura conservadora frente a la objecion:
   si el error aparece incluso a horizonte corto, la objecion queda confirmada.
   El crecimiento del error con el horizonte se puede medir aparte con Previous
   Runs sobre las variables que si expone (T, Td, viento), que alcanzan para el
   spread y por lo tanto para la base de nubes.

Cortesia con los servicios
--------------------------
Ambos son gratuitos y sin clave. Las peticiones van SECUENCIALES con pausa entre
ellas y reintentos con espera incremental. Paralelizar hace que el IEM devuelva
error en la mayoria de las llamadas (verificado). Todo lo descargado se cachea en
disco: el estudio se corre muchas veces mientras se ajustan las metricas y no
corresponde volver a pedir los mismos datos.
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.fetcher_openmeteo import RawNWPHour

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuracion
# ──────────────────────────────────────────────────────────────────────────────

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
OM_HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

REQUEST_DELAY_S = 1.5     # pausa entre peticiones sucesivas
MAX_RETRIES     = 3
RETRY_BACKOFF_S = 5.0     # espera incremental: 5 s, 10 s, 15 s
TIMEOUT_S       = 180     # un mes de METAR puede tardar

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

# Variables NWP que necesita RawNWPHour, en el orden en que se piden
NWP_VARS = [
    "temperature_2m", "dew_point_2m",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
    "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "visibility", "precipitation", "weather_code",
]


def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _get(url: str, label: str) -> Optional[bytes]:
    """
    GET con reintentos y espera incremental. Devuelve None si no se pudo.
    Nunca lanza: el estudio continua con las estaciones que si respondieron.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):          # peticion invalida: no reintentar
                logger.error("%s: HTTP %d — no se reintenta", label, exc.code)
                return None
            logger.warning("%s: HTTP %d (intento %d/%d)", label, exc.code,
                           attempt, MAX_RETRIES)
        except Exception as exc:
            logger.warning("%s: %s (intento %d/%d)", label, type(exc).__name__,
                           attempt, MAX_RETRIES)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_S * attempt)
    logger.error("%s: sin respuesta tras %d intentos", label, MAX_RETRIES)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# 1. Archivo de METAR (IEM)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_metar_archive(
    icao       : str,
    start_date : str,          # "YYYY-MM-DD"
    end_date   : str,          # "YYYY-MM-DD" (exclusivo)
    use_cache  : bool = True,
) -> List[Tuple[int, str]]:
    """
    METAR crudos de una estacion en un rango de fechas.

    Devuelve [(unix_utc, raw_metar), ...] ordenado por tiempo. Lista vacia si la
    estacion no reporta en ese rango — que es lo habitual: varias estaciones
    figuran como activas en la metadata del IEM pero no emiten datos (verificado
    en SANL y SANC). Por eso hay que comprobar disponibilidad real, no confiar en
    el campo `archive_end`.
    """
    _ensure_cache_dir()
    cache_path = os.path.join(CACHE_DIR, f"metar_{icao}_{start_date}_{end_date}.csv")

    if use_cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = f.read()
    else:
        y1, m1, d1 = start_date.split("-")
        y2, m2, d2 = end_date.split("-")
        params = {
            "station": icao, "data": "metar",
            "year1": y1, "month1": m1, "day1": d1,
            "year2": y2, "month2": m2, "day2": d2,
            "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
            "missing": "M", "trace": "T", "direct": "no", "report_type": "3",
        }
        url = f"{IEM_URL}?{urllib.parse.urlencode(params)}"
        body = _get(url, f"IEM {icao} {start_date}")
        if body is None:
            return []
        raw = body.decode("utf-8", errors="replace")
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(raw)
        time.sleep(REQUEST_DELAY_S)

    out: List[Tuple[int, str]] = []
    for line in raw.splitlines()[1:]:              # saltear encabezado
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        _, valid, metar = parts
        metar = metar.strip().strip('"')
        if not metar or metar == "M":
            continue
        try:
            dt = datetime.strptime(valid.strip(), "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append((int(dt.timestamp()), metar))
    out.sort(key=lambda x: x[0])
    return out


def probe_availability(icao: str, start_date: str, end_date: str) -> int:
    """
    Cantidad de observaciones disponibles en el rango. -1 si el servicio fallo.
    Se usa para armar la muestra: solo entran estaciones que efectivamente
    reportan en la ventana del estudio.
    """
    rows = fetch_metar_archive(icao, start_date, end_date)
    return len(rows)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Archivo de pronosticos (Open-Meteo Historical Forecast)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_nwp_archive(
    lat        : float,
    lon        : float,
    start_date : str,
    end_date   : str,
    use_cache  : bool = True,
) -> List[RawNWPHour]:
    """
    Serie horaria del pronostico archivado para una coordenada.

    Devuelve RawNWPHour —el mismo tipo que produce el fetcher de produccion—
    para que la conversion a ParsedWeather use el adaptador real del sistema.
    """
    _ensure_cache_dir()
    key = f"nwp_{lat:.4f}_{lon:.4f}_{start_date}_{end_date}.json"
    cache_path = os.path.join(CACHE_DIR, key)

    if use_cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        params = {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "hourly": ",".join(NWP_VARS),
            "start_date": start_date, "end_date": end_date,
            "timezone": "UTC", "wind_speed_unit": "kn",
        }
        url = f"{OM_HISTORICAL_URL}?{urllib.parse.urlencode(params)}"
        body = _get(url, f"Open-Meteo {lat:.3f},{lon:.3f} {start_date}")
        if body is None:
            return []
        data = json.loads(body.decode())
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        time.sleep(REQUEST_DELAY_S)

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return []

    def col(name: str) -> list:
        return hourly.get(name) or [None] * len(times)

    t   = col("temperature_2m");     td  = col("dew_point_2m")
    ws  = col("wind_speed_10m");     wd  = col("wind_direction_10m")
    wg  = col("wind_gusts_10m")
    ccl = col("cloud_cover_low");    ccm = col("cloud_cover_mid")
    cch = col("cloud_cover_high");   vis = col("visibility")
    pr  = col("precipitation");      wc  = col("weather_code")

    out: List[RawNWPHour] = []
    for i, iso in enumerate(times):
        try:
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append(RawNWPHour(
            valid_time_iso      = iso,
            valid_time_utc      = int(dt.timestamp()),
            windspeed_10m_kt    = ws[i],
            winddirection_10m   = int(wd[i]) if wd[i] is not None else None,
            windgusts_10m_kt    = wg[i],
            visibility_m        = vis[i],
            cloudcover_low_pct  = int(ccl[i]) if ccl[i] is not None else None,
            cloudcover_mid_pct  = int(ccm[i]) if ccm[i] is not None else None,
            cloudcover_high_pct = int(cch[i]) if cch[i] is not None else None,
            precipitation_mm    = pr[i],
            temperature_2m_c    = t[i],
            dewpoint_2m_c       = td[i],
            weathercode         = int(wc[i]) if wc[i] is not None else None,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Verificacion standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  %(levelname)s %(message)s")
    print("=" * 70)
    print("  PRUEBA DE LOS CLIENTES DE ARCHIVO")
    print("=" * 70)

    print("\n  IEM — METAR de SASA (Salta), 1 al 3 de marzo de 2025")
    metars = fetch_metar_archive("SASA", "2025-03-01", "2025-03-03")
    print(f"    observaciones: {len(metars)}")
    for ts, m in metars[:3]:
        print(f"      {datetime.fromtimestamp(ts, timezone.utc):%Y-%m-%d %H:%MZ}  {m}")

    print("\n  Open-Meteo — pronostico archivado para la misma coordenada")
    hours = fetch_nwp_archive(-24.8560, -65.4860, "2025-03-01", "2025-03-03")
    print(f"    horas: {len(hours)}")
    for h in hours[:3]:
        print(f"      {h.valid_time_iso}  T={h.temperature_2m_c}  Td={h.dewpoint_2m_c}  "
              f"nubes_bajas={h.cloudcover_low_pct}%  vis={h.visibility_m} m")

    ok = len(metars) > 0 and len(hours) > 0
    print("\n" + "=" * 70)
    print(f"  {'AMBAS FUENTES OPERATIVAS' if ok else 'FALLO ALGUNA FUENTE'}")
    print("=" * 70)
