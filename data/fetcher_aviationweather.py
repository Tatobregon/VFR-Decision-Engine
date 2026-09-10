"""
fetcher_aviationweather.py
==========================
Obtiene METAR y TAF crudos desde la API de Aviation Weather Center (aviationweather.gov).

Fuente   : https://aviationweather.gov/api/data/
Costo    : Gratuito, sin API key
Cobertura: Global, incluyendo aeropuertos argentinos con observacion automatica

Uso tipico
----------
    from data.fetcher_aviationweather import AviationWeatherFetcher

    fetcher = AviationWeatherFetcher()
    metar   = fetcher.get_metar("SACO")
    taf     = fetcher.get_taf("SACO")

Para desarrollo sin conexion, activar modo mock:
    fetcher = AviationWeatherFetcher(mock=True)
"""

import os
import re
import html as _html
import time
import logging
from dataclasses import dataclass
from typing import Optional

import requests

try:
    from data.cache import METAR_CACHE, TAF_CACHE, NOTAM_CACHE
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from data.cache import METAR_CACHE, TAF_CACHE, NOTAM_CACHE

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constantes de la API
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL       = "https://aviationweather.gov/api/data"
METAR_ENDPOINT = f"{BASE_URL}/metar"
TAF_ENDPOINT   = f"{BASE_URL}/taf"

# NOTAMs: aviationweather.gov NO sirve NOTAMs internacionales. Se usa la fuente
# oficial argentina (AIS de ANAC). Endpoint POST que devuelve una tabla HTML
# de NOTAMs por aeródromo. El parámetro 'indicador' es el código local del
# aeródromo (local_id de MADHEL: CBA=Córdoba, AER=Aeroparque, EZE=Ezeiza...).
ANAC_NOTAM_URL = "https://ais.anac.gob.ar/notam/pib"

DEFAULT_HEADERS = {
    "User-Agent" : "VFR-GONOGO/1.0 (aviation decision support tool)",
    "Accept"     : "application/json",
}

REQUEST_TIMEOUT   = 10   # segundos
MAX_RETRIES       = 3
RETRY_DELAY       = 2    # segundos entre reintentos


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses de respuesta cruda
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RawMetar:
    """
    Datos crudos del METAR tal como los devuelve aviationweather.gov.
    El parser (metar_parser.py) transforma esto en variables limpias.
    """
    icao_id       : str
    raw_string    : str
    obs_time      : int
    report_time   : str
    temp_c        : Optional[float]
    dewp_c        : Optional[float]
    wind_dir      : Optional[int]
    wind_spd_kt   : Optional[int]
    wind_gust_kt  : Optional[int]
    visibility    : Optional[str]
    altimeter_hpa : Optional[float]
    wx_string     : Optional[str]
    sky_condition : list[dict]
    flight_cat    : Optional[str]
    lat           : Optional[float]
    lon           : Optional[float]
    elevation_m   : Optional[float]
    station_name  : Optional[str]
    source        : str = "aviationweather.gov"


@dataclass
class RawTafPeriod:
    """Un periodo individual dentro del TAF."""
    time_from       : int
    time_to         : int
    change_indicator: Optional[str]
    probability     : Optional[int]
    wind_dir        : Optional[int]
    wind_spd_kt     : Optional[int]
    wind_gust_kt    : Optional[int]
    visibility      : Optional[str]
    wx_string       : Optional[str]
    sky_condition   : list[dict]


@dataclass
class RawTaf:
    """TAF completo para un aeropuerto."""
    icao_id         : str
    raw_string      : str
    issue_time      : str
    valid_from      : int
    valid_to        : int
    periods         : list[RawTafPeriod]
    source          : str = "aviationweather.gov"


@dataclass
class RawNotam:
    """NOTAM crudo tal como lo devuelve aviationweather.gov."""
    icao_id   : str
    notam_id  : str            # identificador, p.ej. "A0042/24"
    message   : str            # texto completo del NOTAM
    start_date: str            # ISO-8601 o cadena de fecha
    end_date  : str            # ISO-8601, "PERM", o "N/A"
    q_code    : str = ""       # Q-code para categorizar el tipo
    source    : str = "aviationweather.gov"


# ──────────────────────────────────────────────────────────────────────────────
# Datos mock para desarrollo sin conexion
# ──────────────────────────────────────────────────────────────────────────────

def _mock_notams_saco() -> list:
    return [
        {
            "id"        : "A0042/24",
            "Qcode"     : "QMRLC",
            "message"   : (
                "A0042/24 NOTAMN\n"
                "Q) SAEF/QMRLC/IV/NBO/A/000/999/3119S06418W005\n"
                "A) SACO B) 2401150600 C) 2403310600\n"
                "E) RWY 18/36 CIERRE PARCIAL BANDA LATERAL ESTE "
                "30M X 1200M POR TRABAJOS DE MANTENIMIENTO. "
                "SEÑALIZACION EN LUGAR."
            ),
            "startDate" : "2024-01-15T06:00:00Z",
            "endDate"   : "2024-03-31T06:00:00Z",
        },
        {
            "id"        : "A0078/24",
            "Qcode"     : "QOBCE",
            "message"   : (
                "A0078/24 NOTAMN\n"
                "Q) SAEF/QOBCE/IV/NBO/A/000/004/3119S06418W002\n"
                "A) SACO B) 2401200000 C) PERM\n"
                "E) GRUA ERIGIDA 38M AGL COORD 311952S 0641820W. "
                "BALIZAMIENTO DIURNO Y NOCTURNO INSTALADO."
            ),
            "startDate" : "2024-01-20T00:00:00Z",
            "endDate"   : "PERM",
        },
    ]


def _mock_metar_saco() -> dict:
    return [{
        "metar_id"    : 99001,
        "icaoId"      : "SACO",
        "receiptTime" : "2024-01-27 18:00:00",
        "obsTime"     : 1706385600,
        "reportTime"  : "2024-01-27 18:00:00",
        "temp"        : 28.0,
        "dewp"        : 14.0,
        "wdir"        : 150,
        "wspd"        : 12,
        "wgst"        : 20,
        "visib"       : "9999",
        "altim"       : 1011.2,
        "slp"         : None,
        "wxString"    : None,
        "presentWx"   : None,
        # Formato REAL de la API: la clave es "clouds" y los campos "cover" y
        # "base". El mock usaba "skyCondition"/"skyCover"/"cloudBase", que la
        # API no devuelve nunca: por eso los tests pasaban con el bug puesto
        # —el sistema no veia ningun techo— en vez de atraparlo. Un mock que no
        # imita a la fuente no es una red de seguridad, es una confirmacion.
        "clouds": [
            {"cover": "SCT", "base": 2500, "type": None},
            {"cover": "BKN", "base": 4000, "type": None},
        ],
        "fltcat"      : "VFR",
        "rawOb"       : "SACO 271800Z 15012G20KT 9999 SCT025 BKN040 28/14 Q1011",
        "mostRecent"  : 1,
        "lat"         : -31.313,
        "lon"         : -64.208,
        "elev"        : 474,
        "name"        : "Cordoba/Ambrosio L.V.V. Taravella",
        "country"     : "AR",
    }]


def _mock_taf_saco() -> dict:
    return [{
        "tafId"        : 88001,
        "icaoId"       : "SACO",
        "dbPopTime"    : "2024-01-27 15:00:00",
        "bulletinTime" : "2024-01-27 15:00:00",
        "issueTime"    : "2024-01-27 15:00:00",
        "validTimeFrom": 1706374800,
        "validTimeTo"  : 1706461200,
        "remarks"      : None,
        "rawTAF"       : (
            "SACO 271500Z 2715/2815 15012KT 9999 BKN025 "
            "TEMPO 2718/2722 15018G28KT 3000 TSRA SCT015CB BKN020 "
            "BECMG 2804/2806 VRB03KT 9999 FEW015"
        ),
        "fcsts": [
            {
                "timeGroup"  : 0,
                "timeFrom"   : 1706374800,
                "timeTo"     : 1706389200,
                "fcstChange" : None,
                "probability": None,
                "wdir"       : 150,
                "wspd"       : 12,
                "wgst"       : None,
                # La API entrega la visibilidad en MILLAS TERRESTRES: "6+" es
                # como codifica el CAVOK / 9999 m.
                "visib"      : "6+",
                "wxString"   : None,
                "clouds"     : [{"cover": "BKN", "base": 2500, "type": None}],
            },
            {
                "timeGroup"  : 1,
                "timeFrom"   : 1706389200,
                "timeTo"     : 1706403600,
                "fcstChange" : "TEMPO",
                "probability": None,
                "wdir"       : 150,
                "wspd"       : 18,
                "wgst"       : 28,
                "visib"      : "1.86",          # 3000 m expresados en millas
                "wxString"   : "TSRA",
                "clouds"     : [
                    {"cover": "SCT", "base": 1500, "type": "CB"},
                    {"cover": "BKN", "base": 2000, "type": None},
                ],
            },
            {
                "timeGroup"  : 2,
                "timeFrom"   : 1706403600,
                "timeTo"     : 1706418000,
                "fcstChange" : "BECMG",
                "probability": None,
                "wdir"       : 0,
                "wspd"       : 3,
                "wgst"       : None,
                "visib"      : "6+",
                "wxString"   : None,
                "clouds"     : [{"cover": "FEW", "base": 1500, "type": None}],
            },
        ],
    }]


# ──────────────────────────────────────────────────────────────────────────────
# Fetcher principal
# ──────────────────────────────────────────────────────────────────────────────

class AviationWeatherFetcher:
    """
    Cliente para la API de Aviation Weather Center.

    Parameters
    ----------
    mock : bool
        Si True, devuelve datos simulados en lugar de llamar a la API real.
    """

    def __init__(self, mock: bool = False):
        self.mock    = mock
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _get(self, endpoint: str, params: dict) -> list[dict]:
        """
        GET con reintentos. NUNCA lanza: ante cualquier fallo devuelve lista vacia.

        Convencion del proyecto: la capa de datos reporta AUSENCIA de datos, no
        excepciones (el engine maneja la ausencia). Una lista vacia se traduce en
        `RawMetar = None`, y el engine degrada automaticamente a NWP, que cubre
        todo el pais.

        Motivo: aviationweather.gov es un proveedor externo y tiene caidas
        transitorias (se observo un 502 del gateway). Antes ese 502 se propagaba
        como excepcion hasta el endpoint y el piloto recibia un HTTP 500, en vez
        del pronostico NWP que el sistema ya sabe calcular.

        Los fallos transitorios (429 y 5xx) se reintentan; los 4xx no, porque
        reintentar una peticion mal formada no cambia el resultado.
        """
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.debug(f"GET {endpoint} | params={params} | intento {attempt}/{MAX_RETRIES}")
                response = self.session.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)

                if response.status_code == 200:
                    data = response.json()
                    if not isinstance(data, list):
                        last_error = f"Respuesta inesperada: se esperaba lista, se recibio {type(data)}"
                        logger.warning(last_error)
                        return []
                    return data

                if response.status_code == 204:
                    logger.info(f"Sin datos para los parametros: {params}")
                    return []

                if response.status_code == 429 or response.status_code >= 500:
                    # Transitorio (rate limit o fallo del servidor): reintentar
                    last_error = f"API respondio {response.status_code}"
                    logger.warning(
                        f"{last_error}. Reintento {attempt}/{MAX_RETRIES} "
                        f"en {RETRY_DELAY * attempt}s..."
                    )
                    time.sleep(RETRY_DELAY * attempt)
                    continue

                # 4xx distinto de 429: error de la peticion, no se reintenta
                last_error = f"API respondio {response.status_code}: {response.text[:200]}"
                logger.warning(last_error)
                return []

            except requests.exceptions.Timeout:
                last_error = f"Timeout en intento {attempt}"
                logger.warning(last_error)
                time.sleep(RETRY_DELAY)
            except requests.exceptions.ConnectionError as e:
                last_error = f"Error de conexion: {e}"
                logger.warning(last_error)
                time.sleep(RETRY_DELAY)
            except ValueError as e:
                # JSONDecodeError hereda de ValueError: respuesta no parseable
                last_error = f"Respuesta no es JSON valido: {e}"
                logger.warning(last_error)
                return []

        logger.error(
            f"Sin datos de {endpoint} despues de {MAX_RETRIES} intentos "
            f"(ultimo error: {last_error}). El engine degrada a NWP."
        )
        return []

    def _parse_metar_response(self, data: list[dict], icao: str) -> Optional[RawMetar]:
        if not data:
            logger.warning(f"Sin datos METAR para {icao}")
            return None
        d   = data[0]
        # La API devuelve la nubosidad bajo la clave "clouds", NO "skyCondition".
        # Leer la clave equivocada devolvia SIEMPRE una lista vacia, de modo que
        # el sistema quedaba ciego al TECHO —el factor de mayor peso del modelo
        # junto con la visibilidad— en todo aerodromo con METAR. Medido: 11 de
        # 48 METAR argentinos tenian techo real y no se veia ninguno, incluido
        # un OVC003 (300 ft) que esta por debajo del bloqueo duro de 500.
        # Se acepta la clave vieja como respaldo por si la API la reintroduce.
        sky = d.get("clouds") or d.get("skyCondition") or []
        return RawMetar(
            icao_id       = d.get("icaoId", icao).upper(),
            raw_string    = d.get("rawOb", ""),
            obs_time      = d.get("obsTime", 0),
            report_time   = d.get("reportTime", ""),
            temp_c        = _to_float(d.get("temp")),
            dewp_c        = _to_float(d.get("dewp")),
            wind_dir      = _to_int(d.get("wdir")),
            wind_spd_kt   = _to_int(d.get("wspd")),
            wind_gust_kt  = _to_int(d.get("wgst")),
            visibility    = str(d["visib"]) if d.get("visib") is not None else None,
            altimeter_hpa = _to_float(d.get("altim")),
            wx_string     = d.get("wxString") or d.get("presentWx"),
            sky_condition = sky,
            flight_cat    = d.get("fltcat"),
            lat           = _to_float(d.get("lat")),
            lon           = _to_float(d.get("lon")),
            elevation_m   = _to_float(d.get("elev")),
            station_name  = d.get("name"),
        )

    def _parse_taf_response(self, data: list[dict], icao: str) -> Optional[RawTaf]:
        if not data:
            logger.warning(f"Sin datos TAF para {icao}")
            return None
        d       = data[0]
        periods = []
        for fcst in (d.get("fcsts") or []):
            sky = fcst.get("clouds") or fcst.get("skyCondition") or []
            periods.append(RawTafPeriod(
                time_from        = fcst.get("timeFrom", 0),
                time_to          = fcst.get("timeTo", 0),
                change_indicator = fcst.get("fcstChange"),
                probability      = _to_int(fcst.get("probability")),
                wind_dir         = _to_int(fcst.get("wdir")),
                wind_spd_kt      = _to_int(fcst.get("wspd")),
                wind_gust_kt     = _to_int(fcst.get("wgst")),
                visibility       = str(fcst["visib"]) if fcst.get("visib") is not None else None,
                wx_string        = fcst.get("wxString"),
                sky_condition    = sky,
            ))
        return RawTaf(
            icao_id    = d.get("icaoId", icao).upper(),
            raw_string = d.get("rawTAF", ""),
            issue_time = d.get("issueTime", ""),
            valid_from = d.get("validTimeFrom", 0),
            valid_to   = d.get("validTimeTo", 0),
            periods    = periods,
        )

    def get_metar(self, icao: str, hours: int = 2) -> Optional[RawMetar]:
        icao = icao.upper().strip()
        logger.info(f"Obteniendo METAR para {icao} (mock={self.mock})")
        if self.mock:
            raw_data = _mock_metar_saco() if icao == "SACO" else []
        else:
            # Cacheado: un METAR se emite cada 30-60 min, no tiene sentido volver
            # a pedirlo en cada evaluacion (ver data/cache.py).
            raw_data = METAR_CACHE.get_or_call(
                (icao, hours),
                lambda: self._get(METAR_ENDPOINT,
                                  params={"ids": icao, "format": "json", "hours": hours}),
            )
        return self._parse_metar_response(raw_data, icao)

    def get_taf(self, icao: str) -> Optional[RawTaf]:
        icao = icao.upper().strip()
        logger.info(f"Obteniendo TAF para {icao} (mock={self.mock})")
        if self.mock:
            raw_data = _mock_taf_saco() if icao == "SACO" else []
        else:
            raw_data = TAF_CACHE.get_or_call(
                icao,
                lambda: self._get(TAF_ENDPOINT, params={"ids": icao, "format": "json"}),
            )
        return self._parse_taf_response(raw_data, icao)

    def get_metar_and_taf(self, icao: str) -> tuple[Optional[RawMetar], Optional[RawTaf]]:
        return self.get_metar(icao), self.get_taf(icao)

    def get_notams(self, code: str) -> list:
        """
        Devuelve lista de RawNotam activos para el aeródromo, desde el AIS de
        ANAC (fuente oficial argentina).

        `code` puede ser código ICAO o local_id; se resuelve al código local
        (indicador) que usa ANAC. Retorna lista vacía ante cualquier fallo.
        """
        code = code.upper().strip()
        indicador = _resolve_anac_indicador(code)
        logger.info(f"Obteniendo NOTAMs para {code} (indicador={indicador}, mock={self.mock})")

        if self.mock:
            raw_items = _mock_notams_saco() if code in ("SACO", "CBA") else []
            return [_parse_mock_notam(code, it) for it in raw_items]

        if not indicador:
            return []

        def _fetch_notams() -> list:
            try:
                resp = requests.post(
                    ANAC_NOTAM_URL,
                    data={"indicador": indicador},
                    headers={
                        "User-Agent":       DEFAULT_HEADERS["User-Agent"],
                        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer":          "https://ais.anac.gob.ar/notam",
                        "Accept":           "*/*",
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.warning(f"Error obteniendo NOTAMs ANAC para {code}: {exc}")
                return []
            return _parse_anac_notams(code, resp.text)

        # Cacheado por indicador: los NOTAM cambian con baja frecuencia y la
        # misma consulta se repite en cada evaluacion del mismo aerodromo.
        return NOTAM_CACHE.get_or_call(indicador, _fetch_notams)


def _resolve_anac_indicador(code: str) -> str:
    """
    Resuelve el código local (indicador ANAC) de un aeródromo a partir de su
    código ICAO o local_id. ANAC usa el local_id de MADHEL (CBA, AER, EZE...).
    """
    try:
        from data.airports import AIRPORTS
        ap = AIRPORTS.get(code)
        if ap and ap.local_id:
            return ap.local_id.upper()
    except Exception:
        pass
    return code


def _clean_html(s: str) -> str:
    """Quita tags HTML, desescapa entidades y normaliza espacios."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return " ".join(s.split())


def _parse_anac_notams(code: str, html_text: str) -> list:
    """
    Parsea la tabla HTML del AIS de ANAC. Cada fila tiene:
      <td id="place"><p>ID</p><p>nombre</p><p>(cod)</p></td>
      <td id="info"><p>Desde: ...</p><p>Hasta: ...</p><p>texto EN <span>Versión en Español:</span> texto ES</p></td>
    """
    notams: list = []
    rows = re.findall(r"<tr>(.*?)</tr>", html_text, re.DOTALL | re.IGNORECASE)
    for row in rows:
        place_m = re.search(r'id="place".*?>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        info_m  = re.search(r'id="info".*?>(.*?)</td>',  row, re.DOTALL | re.IGNORECASE)
        if not place_m or not info_m:
            continue

        place_ps = re.findall(r"<p[^>]*>(.*?)</p>", place_m.group(1), re.DOTALL | re.IGNORECASE)
        notam_id = _clean_html(place_ps[0]) if place_ps else "?"

        start = end = ""
        text_parts: list = []
        for p in re.findall(r"<p[^>]*>(.*?)</p>", info_m.group(1), re.DOTALL | re.IGNORECASE):
            # El NOTAM original (formato ICAO) está antes del span
            # "Versión en Español:". Descartar la traducción duplicada.
            p_main = re.split(r"<span[^>]*>", p, maxsplit=1, flags=re.IGNORECASE)[0]
            flat = _clean_html(p_main)
            if flat.startswith("Desde:"):
                start = flat.replace("Desde:", "").strip()
            elif flat.startswith("Hasta:"):
                end = flat.replace("Hasta:", "").strip()
            elif flat:
                text_parts.append(flat)

        message = "\n".join(text_parts).strip()
        if not notam_id or notam_id == "?":
            continue
        notams.append(RawNotam(
            icao_id   = code,
            notam_id  = notam_id,
            message   = message,
            start_date= start,
            end_date  = end,
            q_code    = "",
            source    = "ais.anac.gob.ar",
        ))
    return notams


def _parse_mock_notam(icao: str, item: dict) -> RawNotam:
    """Convierte un NOTAM mock (formato legacy) en RawNotam."""
    return RawNotam(
        icao_id   = icao,
        notam_id  = str(item.get("id", "?")),
        message   = str(item.get("message", "")).strip(),
        start_date= str(item.get("startDate", "")),
        end_date  = str(item.get("endDate", "N/A")),
        q_code    = str(item.get("Qcode", "")),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    from datetime import datetime, timezone

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("  TEST: AviationWeatherFetcher")
    print("=" * 60)

    fetcher = AviationWeatherFetcher(mock=True)

    print("\n[1] METAR SACO...")
    metar = fetcher.get_metar("SACO")
    if metar:
        obs_dt = datetime.fromtimestamp(metar.obs_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"  {metar.icao_id} | {obs_dt} | {metar.raw_string}")
        print(f"  T={metar.temp_c}°C  Td={metar.dewp_c}°C  viento={metar.wind_dir}°/{metar.wind_spd_kt}kt  vis={metar.visibility}m")
    else:
        print("  Sin datos METAR")

    print("\n[2] TAF SACO...")
    taf = fetcher.get_taf("SACO")
    if taf:
        print(f"  {taf.icao_id} | emitido={taf.issue_time} | {len(taf.periods)} periodos")
        for p in taf.periods:
            change = f"[{p.change_indicator}]" if p.change_indicator else "[BASE]"
            print(f"    {change}  viento={p.wind_dir}°/{p.wind_spd_kt}kt  vis={p.visibility}  wx={p.wx_string or '-'}")
    else:
        print("  Sin datos TAF")

    print("\n[3] SACC (sin METAR esperado)...")
    print(f"  Resultado: {'Sin datos (correcto)' if fetcher.get_metar('SACC') is None else 'INESPERADO'}")

    print("\n" + "=" * 60)
    print("  TEST completado")
    print("=" * 60)
