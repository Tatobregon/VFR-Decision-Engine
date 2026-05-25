"""
fetcher_openmeteo.py
====================
Obtiene pronostico NWP (Numerical Weather Prediction) desde la API de Open-Meteo.

Fuente   : https://open-meteo.com/
Costo    : Gratuito, sin API key
Cobertura: Global, punto a punto por coordenadas geograficas

Uso tipico
----------
    from data.fetcher_openmeteo import OpenMeteoFetcher

    fetcher = OpenMeteoFetcher()
    nwp = fetcher.get_forecast(lat=-31.00, lon=-64.52, elevation_m=1141)

Para desarrollo sin conexion, activar modo mock:
    fetcher = OpenMeteoFetcher(mock=True)

Nota
----
Los datos de Open-Meteo son estimaciones de modelos numericos (NWP), NO observaciones
directas. El campo nwp_estimated=True en RawNWP indica esta condicion al adaptador y
al motor de riesgo, que aplicara la penalizacion orografica correspondiente (+0.05
al R_total) para zonas de sierras como SACC.
"""

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constantes de la API
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL     = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 2   # solicitar 2 dias para cubrir cualquier ventana de hours_ahead

# Variables horarias a solicitar en cada llamada (especificacion del proyecto)
HOURLY_VARIABLES = ",".join([
    "windspeed_10m",
    "winddirection_10m",
    "windgusts_10m",
    "visibility",
    "cloudcover_low",
    "cloudcover_mid",
    "cloudcover_high",
    "precipitation",
    "temperature_2m",
    "dewpoint_2m",
    "weathercode",
])

DEFAULT_HEADERS = {
    "User-Agent" : "VFR-GONOGO/1.0 (aviation decision support tool)",
    "Accept"     : "application/json",
}

REQUEST_TIMEOUT = 15   # segundos (Open-Meteo puede ser mas lento que AWC)
MAX_RETRIES     = 3
RETRY_DELAY     = 2    # segundos entre reintentos

# Argentina es UTC-3 sin cambio de horario estacional
ARGENTINA_UTC_OFFSET_HOURS = -3


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses de respuesta cruda
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RawNWPHour:
    """
    Un slot horario del pronostico NWP tal como lo devuelve Open-Meteo.
    Cada instancia representa una hora especifica del pronostico.
    El adaptador (openmeteo_adapter.py) convierte esto al formato interno unificado.
    """
    valid_time_iso      : str            # "2024-01-27T15:00" — hora local Argentina (UTC-3)
    valid_time_utc      : int            # Unix timestamp UTC equivalente
    windspeed_10m_kt    : Optional[float]   # velocidad del viento a 10m en nudos
    winddirection_10m   : Optional[int]     # direccion del viento en grados (0-360)
    windgusts_10m_kt    : Optional[float]   # rafagas a 10m en nudos
    visibility_m        : Optional[float]   # visibilidad en metros
    cloudcover_low_pct  : Optional[int]     # cobertura de nubes bajas 0-100 %
    cloudcover_mid_pct  : Optional[int]     # cobertura de nubes medias 0-100 %
    cloudcover_high_pct : Optional[int]     # cobertura de nubes altas 0-100 %
    precipitation_mm    : Optional[float]   # precipitacion acumulada en mm
    temperature_2m_c    : Optional[float]   # temperatura a 2m en Celsius
    dewpoint_2m_c       : Optional[float]   # punto de rocio a 2m en Celsius
    weathercode         : Optional[int]     # codigo WMO (0=despejado, 95=tormenta, etc.)


@dataclass
class RawNWP:
    """
    Pronostico NWP completo para un punto geografico.

    nwp_estimated=True indica que estos datos son estimaciones de modelo numerico,
    NO observaciones directas. El motor de riesgo aplica penalizacion orografica
    adicional (+0.05 al R_total) cuando esta flag esta activa y el punto es SACC
    o similar zona de sierras.
    """
    lat           : float
    lon           : float
    elevation_m   : float
    fetch_time    : str               # ISO 8601 UTC — momento en que se realizo la consulta
    hours         : list[RawNWPHour]  # horas del pronostico, ordenadas cronologicamente
    source        : str  = "open-meteo-nwp"
    nwp_estimated : bool = True


# ──────────────────────────────────────────────────────────────────────────────
# Datos mock para desarrollo sin conexion
# ──────────────────────────────────────────────────────────────────────────────
# Representan condiciones tipicas de SACC (La Cumbre, Sierras Chicas):
# - Viento del NW moderado por canalización orografica
# - Buena visibilidad, escasa nubosidad baja
# - Spread T/Td de ~10°C (riesgo de niebla bajo)
# - Hora 7 tiene mayor viento para testear scoring de rafagas

def _mock_nwp_sacc(hours_ahead: int) -> dict:
    """
    Respuesta JSON simulada de la API de Open-Meteo para SACC.
    Genera dinamicamente los timestamps basandose en la hora actual.
    """
    now_utc   = datetime.now(tz=timezone.utc)
    # Redondear a la hora en curso (inicio de la hora actual)
    base_utc  = now_utc.replace(minute=0, second=0, microsecond=0)
    base_local = base_utc + timedelta(hours=ARGENTINA_UTC_OFFSET_HOURS)

    times           = []
    windspeed       = []
    winddirection   = []
    windgusts       = []
    visibility      = []
    cloudcover_low  = []
    cloudcover_mid  = []
    cloudcover_high = []
    precipitation   = []
    temperature     = []
    dewpoint        = []
    weathercode     = []

    for i in range(hours_ahead):
        slot_local = base_local + timedelta(hours=i)
        times.append(slot_local.strftime("%Y-%m-%dT%H:%M"))

        # Viento: NW (315°) entre 8 y 14 kt, con pico en hora 7 para test de scoring
        spd  = 14.0 if i == 7 else 10.0
        gust = 22.0 if i == 7 else 16.0
        windspeed.append(spd)
        winddirection.append(315)
        windgusts.append(gust)

        # Visibilidad buena (>9km), visibilidad reducida en hora 3 para testear fog proxy
        vis = 4500.0 if i == 3 else 9500.0
        visibility.append(vis)

        # Nubosidad baja tipica de Sierras Chicas
        cloudcover_low.append(35 if i == 3 else 20)
        cloudcover_mid.append(10)
        cloudcover_high.append(5)

        # Sin precipitacion
        precipitation.append(0.0)

        # Temperatura con ciclo diurno aproximado: base 20°C, +-4°C segun hora
        hour_of_day = slot_local.hour
        temp_offset = 4.0 * (1 - abs(hour_of_day - 14) / 14.0)   # maximo a las 14h locales
        temperature.append(round(18.0 + temp_offset, 1))
        dewpoint.append(8.0)    # spread ~10°C → riesgo de niebla bajo

        # WMO: 1=mainly clear, 2=partly cloudy; hora 3 con 45 (niebla) para test
        weathercode.append(45 if i == 3 else (2 if i % 3 == 0 else 1))

    return {
        "latitude"             : -31.0,
        "longitude"            : -64.52,
        "generationtime_ms"    : 0.12,
        "utc_offset_seconds"   : ARGENTINA_UTC_OFFSET_HOURS * 3600,
        "timezone"             : "America/Argentina/Buenos_Aires",
        "timezone_abbreviation": "-03",
        "elevation"            : 1141.0,
        "hourly_units": {
            "time"            : "iso8601",
            "windspeed_10m"   : "kn",
            "winddirection_10m": "°",
            "windgusts_10m"   : "kn",
            "visibility"      : "m",
            "cloudcover_low"  : "%",
            "cloudcover_mid"  : "%",
            "cloudcover_high" : "%",
            "precipitation"   : "mm",
            "temperature_2m"  : "°C",
            "dewpoint_2m"     : "°C",
            "weathercode"     : "wmo code",
        },
        "hourly": {
            "time"             : times,
            "windspeed_10m"    : windspeed,
            "winddirection_10m": winddirection,
            "windgusts_10m"    : windgusts,
            "visibility"       : visibility,
            "cloudcover_low"   : cloudcover_low,
            "cloudcover_mid"   : cloudcover_mid,
            "cloudcover_high"  : cloudcover_high,
            "precipitation"    : precipitation,
            "temperature_2m"   : temperature,
            "dewpoint_2m"      : dewpoint,
            "weathercode"      : weathercode,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _pressure_level_for_alt(alt_ft: int) -> str:
    """
    Retorna el nivel de presion Open-Meteo mas cercano a una altitud en pies MSL.

    Niveles soportados por Open-Meteo y sus altitudes aproximadas ISA:
      925 hPa ~  2.500 ft
      850 hPa ~  5.000 ft
      800 hPa ~  6.500 ft
      700 hPa ~ 10.000 ft
      600 hPa ~ 14.000 ft
      500 hPa ~ 18.000 ft
      400 hPa ~ 24.000 ft
    """
    if alt_ft <= 3_750:
        return "925"
    elif alt_ft <= 5_750:
        return "850"
    elif alt_ft <= 8_250:
        return "800"
    elif alt_ft <= 12_000:
        return "700"
    elif alt_ft <= 16_000:
        return "600"
    elif alt_ft <= 21_000:
        return "500"
    else:
        return "400"


def _to_float(value) -> Optional[float]:
    """Convierte un valor a float, devuelve None si no es convertible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> Optional[int]:
    """Convierte un valor a int, devuelve None si no es convertible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _argentina_iso_to_utc_timestamp(iso_str: str) -> int:
    """
    Convierte una cadena ISO local Argentina ("2024-01-27T15:00") a Unix timestamp UTC.
    Argentina es siempre UTC-3 (sin DST).
    """
    dt_local = datetime.fromisoformat(iso_str)
    # sumar 3h para pasar de UTC-3 a UTC
    dt_utc = dt_local + timedelta(hours=abs(ARGENTINA_UTC_OFFSET_HOURS))
    return int(dt_utc.replace(tzinfo=timezone.utc).timestamp())


# ──────────────────────────────────────────────────────────────────────────────
# Fetcher principal
# ──────────────────────────────────────────────────────────────────────────────

class OpenMeteoFetcher:
    """
    Cliente para la API de Open-Meteo.

    Obtiene pronosticos NWP punto a punto por coordenadas geograficas.
    Fuente principal para aeropuertos sin METAR como SACC (La Cumbre, Cordoba).

    Parameters
    ----------
    mock : bool
        Si True, devuelve datos simulados en lugar de llamar a la API real.
        Util para desarrollo y tests sin conexion a internet.
    """

    def __init__(self, mock: bool = False):
        self.mock    = mock
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ── API real ──────────────────────────────────────────────────────────────

    def _get(self, params: dict) -> dict:
        """
        Hace un GET a la API de Open-Meteo con reintentos automaticos.
        Devuelve el diccionario JSON de la respuesta.

        Raises
        ------
        ConnectionError : si no se puede conectar despues de MAX_RETRIES intentos.
        ValueError      : si la API devuelve status != 200 o JSON invalido.
        """
        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.debug(f"GET {BASE_URL} | params={params} | intento {attempt}/{MAX_RETRIES}")
                response = self.session.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)

                if response.status_code == 200:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError(
                            f"Respuesta inesperada de Open-Meteo: se esperaba dict, "
                            f"se recibio {type(data)}"
                        )
                    if "error" in data:
                        raise ValueError(f"Open-Meteo reporto error: {data.get('reason', data)}")
                    return data

                elif response.status_code == 429:
                    logger.warning(f"Rate limit (429). Esperando {RETRY_DELAY * attempt}s...")
                    time.sleep(RETRY_DELAY * attempt)
                    continue

                else:
                    raise ValueError(
                        f"Open-Meteo respondio con status {response.status_code}: "
                        f"{response.text[:200]}"
                    )

            except requests.exceptions.Timeout:
                last_error = f"Timeout en intento {attempt}"
                logger.warning(last_error)
                time.sleep(RETRY_DELAY)

            except requests.exceptions.ConnectionError as e:
                last_error = f"Error de conexion: {e}"
                logger.warning(last_error)
                time.sleep(RETRY_DELAY)

            except ValueError:
                raise   # errores de logica no se reintentan

        raise ConnectionError(
            f"No se pudo conectar a Open-Meteo despues de {MAX_RETRIES} intentos. "
            f"Ultimo error: {last_error}"
        )

    # ── Parsing de respuesta cruda ────────────────────────────────────────────

    def _parse_response(
        self,
        data        : dict,
        hours_ahead : int,
        pressure_lvl: Optional[str] = None,
    ) -> RawNWP:
        """
        Convierte la respuesta JSON de Open-Meteo en un RawNWP.

        Filtra los slots horarios para devolver solo los proximos hours_ahead
        a partir del momento actual.

        Si pressure_lvl es un string ("925", "850", "700"), usa las variables
        de viento en ese nivel de presion en lugar del viento a 10 m (superficie).
        Las rafagas solo estan disponibles en superficie; en altitud se dejan None.
        """
        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])

        now_utc = datetime.now(tz=timezone.utc)
        cutoff_utc = now_utc + timedelta(hours=hours_ahead)

        hours: list[RawNWPHour] = []

        for i, iso_str in enumerate(times):
            utc_ts = _argentina_iso_to_utc_timestamp(iso_str)
            slot_utc = datetime.fromtimestamp(utc_ts, tz=timezone.utc)

            # Solo incluir slots desde ahora hasta el limite de hours_ahead
            if slot_utc < now_utc or slot_utc > cutoff_utc:
                continue

            def _val(key, _i=i):
                arr = hourly.get(key, [])
                return arr[_i] if _i < len(arr) else None

            # Viento: preferir nivel de presion si se solicito
            if pressure_lvl:
                spd_key = f"windspeed_{pressure_lvl}hPa"
                dir_key = f"winddirection_{pressure_lvl}hPa"
                alt_spd = _to_float(_val(spd_key))
                alt_dir = _to_int(_val(dir_key))
                wind_spd  = alt_spd  if alt_spd  is not None else _to_float(_val("windspeed_10m"))
                wind_dir  = alt_dir  if alt_dir  is not None else _to_int(_val("winddirection_10m"))
                wind_gust = None  # rafagas no disponibles en niveles de presion
            else:
                wind_spd  = _to_float(_val("windspeed_10m"))
                wind_dir  = _to_int(_val("winddirection_10m"))
                wind_gust = _to_float(_val("windgusts_10m"))

            hours.append(RawNWPHour(
                valid_time_iso      = iso_str,
                valid_time_utc      = utc_ts,
                windspeed_10m_kt    = wind_spd,
                winddirection_10m   = wind_dir,
                windgusts_10m_kt    = wind_gust,
                visibility_m        = _to_float(_val("visibility")),
                cloudcover_low_pct  = _to_int(_val("cloudcover_low")),
                cloudcover_mid_pct  = _to_int(_val("cloudcover_mid")),
                cloudcover_high_pct = _to_int(_val("cloudcover_high")),
                precipitation_mm    = _to_float(_val("precipitation")),
                temperature_2m_c    = _to_float(_val("temperature_2m")),
                dewpoint_2m_c       = _to_float(_val("dewpoint_2m")),
                weathercode         = _to_int(_val("weathercode")),
            ))

        fetch_time = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        return RawNWP(
            lat         = _to_float(data.get("latitude"))  or 0.0,
            lon         = _to_float(data.get("longitude")) or 0.0,
            elevation_m = _to_float(data.get("elevation")) or 0.0,
            fetch_time  = fetch_time,
            hours       = hours,
        )

    # ── Metodo publico ────────────────────────────────────────────────────────

    def get_forecast(
        self,
        lat          : float,
        lon          : float,
        elevation_m  : float,
        hours_ahead  : int = 12,
        cruise_alt_ft: Optional[int] = None,
    ) -> Optional[RawNWP]:
        """
        Obtiene el pronostico NWP para las proximas hours_ahead horas.

        Parameters
        ----------
        lat           : latitud del punto en grados decimales (negativo = sur)
        lon           : longitud del punto en grados decimales (negativo = oeste)
        elevation_m   : elevacion del punto en metros AMSL
        hours_ahead   : cuantas horas de pronostico devolver (default 12)
        cruise_alt_ft : si se especifica, solicita tambien viento en el nivel de
                        presion mas cercano a esa altitud y lo usa como viento
                        principal (en lugar del viento a 10m de superficie).
                        Usar para waypoints intermedios en ruta de crucero.

        Returns
        -------
        RawNWP con los slots horarios del pronostico, o None si falla la conexion.
        """
        pressure_lvl = _pressure_level_for_alt(cruise_alt_ft) if cruise_alt_ft else None

        logger.info(
            f"Obteniendo pronostico NWP para lat={lat}, lon={lon}, "
            f"elev={elevation_m}m, horizonte={hours_ahead}h, "
            f"altitud={'superficie' if not cruise_alt_ft else f'{cruise_alt_ft}ft ({pressure_lvl}hPa)'} "
            f"(mock={self.mock})"
        )

        if self.mock:
            raw_data = _mock_nwp_sacc(hours_ahead)
            return self._parse_response(raw_data, hours_ahead, pressure_lvl=None)

        hourly_vars = HOURLY_VARIABLES
        if pressure_lvl:
            hourly_vars += f",windspeed_{pressure_lvl}hPa,winddirection_{pressure_lvl}hPa"

        try:
            raw_data = self._get(params={
                "latitude"       : lat,
                "longitude"      : lon,
                "elevation"      : elevation_m,
                "hourly"         : hourly_vars,
                "wind_speed_unit": "kn",
                "timezone"       : "America/Argentina/Buenos_Aires",
                "forecast_days"  : FORECAST_DAYS,
            })
            return self._parse_response(raw_data, hours_ahead, pressure_lvl=pressure_lvl)

        except (ConnectionError, ValueError) as e:
            logger.error(f"No se pudo obtener pronostico NWP: {e}")
            return None


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    # Coordenadas reales de SACC — La Cumbre, Cordoba
    SACC_LAT  = -31.00
    SACC_LON  = -64.52
    SACC_ELEV = 1141.0

    print("=" * 60)
    print("  TEST: OpenMeteoFetcher")
    print("=" * 60)

    for use_mock in (True, False):
        label = "MOCK" if use_mock else "API REAL"
        print(f"\n{'-'*60}")
        print(f"  Modo: {label}")
        print(f"{'-'*60}")

        fetcher = OpenMeteoFetcher(mock=use_mock)
        nwp     = fetcher.get_forecast(
            lat         = SACC_LAT,
            lon         = SACC_LON,
            elevation_m = SACC_ELEV,
            hours_ahead = 12,
        )

        if nwp is None:
            print("  [!] No se pudo obtener el pronostico")
            continue

        print(f"\n  Fuente        : {nwp.source}")
        print(f"  NWP estimado  : {nwp.nwp_estimated}  (penalizacion orografica aplica)")
        print(f"  Coordenadas   : {nwp.lat}°, {nwp.lon}°")
        print(f"  Elevacion     : {nwp.elevation_m} m AMSL")
        print(f"  Consulta UTC  : {nwp.fetch_time}")
        print(f"  Slots horarios: {len(nwp.hours)}")

        print(f"\n  {'Hora local (AR)':<18} {'Viento':>12} {'Rafaga':>8} {'Vis(km)':>8} "
              f"{'Nub.B%':>7} {'T°C':>5} {'Td°C':>5} {'WMO':>5}")
        print(f"  {'-'*18} {'-'*12} {'-'*8} {'-'*8} {'-'*7} {'-'*5} {'-'*5} {'-'*5}")

        for h in nwp.hours:
            gust_str = f"{h.windgusts_10m_kt:.0f}" if h.windgusts_10m_kt else " — "
            vis_km   = f"{h.visibility_m/1000:.1f}" if h.visibility_m is not None else " — "
            spread   = (
                round(h.temperature_2m_c - h.dewpoint_2m_c, 1)
                if h.temperature_2m_c is not None and h.dewpoint_2m_c is not None
                else None
            )
            print(
                f"  {h.valid_time_iso:<18} "
                f"{h.winddirection_10m or 0:>3}° / {h.windspeed_10m_kt or 0:>4.0f} kt "
                f"G{gust_str:>4} kt "
                f"{vis_km:>7} km "
                f"{h.cloudcover_low_pct or 0:>6}% "
                f"{h.temperature_2m_c or 0:>5.1f} "
                f"{h.dewpoint_2m_c or 0:>5.1f} "
                f"{h.weathercode or 0:>5}"
            )
            if spread is not None:
                logger.debug(f"  Spread T/Td: {spread}°C")

        # Resumen del slot mas restrictivo (mayor viento)
        if nwp.hours:
            worst = max(nwp.hours, key=lambda h: h.windspeed_10m_kt or 0.0)
            print(f"\n  Peor slot (viento)  : {worst.valid_time_iso}  "
                  f"{worst.winddirection_10m}° / {worst.windspeed_10m_kt} kt "
                  f"G{worst.windgusts_10m_kt} kt")

        if not use_mock:
            # Con API real, no ejecutar el segundo ciclo si el primero ya funciono
            break

    print("\n" + "=" * 60)
    print("  TEST completado")
    print("=" * 60)
