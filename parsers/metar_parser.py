"""
metar_parser.py
===============
Transforma RawMetar (datos crudos de aviationweather.gov) en ParsedWeather,
el modelo interno unificado que consumen el feature layer y el risk engine.

ParsedWeather es el tipo canonico del sistema: tanto este parser como
openmeteo_adapter.py producen instancias de este tipo, garantizando que
el motor de riesgo opere de forma homogenea sin importar la fuente de datos.

Uso tipico
----------
    from data.fetcher_aviationweather import AviationWeatherFetcher
    from parsers.metar_parser import MetarParser, ParsedWeather

    fetcher = AviationWeatherFetcher()
    parser  = MetarParser()

    raw    = fetcher.get_metar("SACO")
    parsed = parser.parse(raw)
    print(parsed.flight_category, parsed.visibility_km, parsed.ceiling_ft)

Normativa aplicada
------------------
Categorias de vuelo segun ANAC/OACI (NO FAA):
    VFR  : vis >= 5 km  AND  ceil >= 1000 ft AGL
    MVFR : vis >= 3 km  AND  ceil >=  500 ft  (o bien peor que VFR)
    IFR  : vis >= 0.8km AND  ceil >=  200 ft  (o bien peor que MVFR)
    LIFR : vis <  0.8km  OR  ceil <   200 ft
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

# Capas de nubosidad que definen ceiling (BKN >= 5/8, OVC = 8/8, VV = visibilidad vertical)
CEILING_COVERS = {"BKN", "OVC", "VV"}

# Factor de conversion: millas nauticas → km
# Milla TERRESTRE (statute mile), que es la unidad en que aviationweather.gov
# reporta la visibilidad. NO confundir con la nautica (1.852 km), que es la que
# estaba aca y sobreestimaba toda visibilidad en SM un 15 %.
SM_TO_KM = 1.609344

# Por encima de este valor, un numero suelto no puede ser millas: son metros.
# La visibilidad en SM que reporta la API llega hasta "6+"; una en metros
# arranca en centenas. No hay solapamiento posible entre las dos escalas.
_UMBRAL_SM_VS_METROS = 15.0


# ──────────────────────────────────────────────────────────────────────────────
# Modelo interno unificado — ParsedWeather
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ParsedWeather:
    """
    Representacion interna normalizada de las condiciones meteorologicas.

    Este tipo es la interfaz entre la capa de datos y las capas de features
    y riesgo. Tanto MetarParser como openmeteo_adapter producen este tipo,
    de modo que el risk engine nunca necesita saber de que fuente vienen los datos.

    Unidades estandarizadas del sistema:
        velocidad del viento : nudos (kt)
        visibilidad          : kilometros (km)
        altura de nubes      : pies AGL (ft)
        temperatura          : Celsius (C)
        presion              : hPa
    """

    # ── Identificacion ────────────────────────────────────────────────────────
    source        : str               # "metar" | "nwp"
    station_id    : str               # ICAO o identificador del punto
    obs_time      : int               # Unix timestamp UTC de la observacion/slot
    nwp_estimated : bool = False      # True si el dato es NWP (no observacion directa)

    # ── Viento ────────────────────────────────────────────────────────────────
    wind_dir      : Optional[int]   = None    # grados (0-360), None si VRB o no disponible
    wind_spd_kt   : Optional[float] = None    # velocidad media en kt
    wind_gust_kt  : Optional[float] = None    # rafaga en kt (None si no hay)
    wind_variable : bool            = False   # True si la direccion es VRB

    # ── Visibilidad ───────────────────────────────────────────────────────────
    visibility_km : Optional[float] = None    # km; 10.0 = CAVOK/"9999" (>= 10 km)

    # ── Techo y nubosidad ─────────────────────────────────────────────────────
    ceiling_ft    : Optional[int]   = None    # pies AGL de la capa BKN/OVC mas baja
                                              # None = cielo despejado o solo FEW/SCT
    sky_layers    : list            = field(default_factory=list)
    # [{"cover": "BKN", "base_ft": 2500}, ...]  — todas las capas ordenadas por base

    # ── Termodinamica ─────────────────────────────────────────────────────────
    temp_c        : Optional[float] = None
    dewpoint_c    : Optional[float] = None
    spread_c      : Optional[float] = None    # T - Td; proxy de riesgo de niebla

    # ── Presion ───────────────────────────────────────────────────────────────
    altimeter_hpa : Optional[float] = None

    # ── Fenomenos ─────────────────────────────────────────────────────────────
    wx_codes      : list            = field(default_factory=list)
    # tokens individuales del wx_string, ej: ["-RA", "BR"] o ["TSRA"]

    # ── Nubosidad y precipitacion (NWP; None en METAR) ────────────────────────
    cloud_cover_pct : Optional[int]   = None   # cobertura total estimada 0-100 %
    precip_mm       : Optional[float] = None   # precipitacion horaria en mm

    # ── Categoria de vuelo (ANAC/OACI) ────────────────────────────────────────
    flight_category : Optional[str] = None   # "VFR" | "VFR marginal" | "IFR" | "IFR bajo mínimos"

    # ── Posicion geografica ───────────────────────────────────────────────────
    lat           : Optional[float] = None
    lon           : Optional[float] = None
    elevation_m   : Optional[float] = None
    station_name  : Optional[str]   = None

    # ── Referencia al string original ────────────────────────────────────────
    raw_string    : Optional[str]   = None


# ──────────────────────────────────────────────────────────────────────────────
# MetarParser
# ──────────────────────────────────────────────────────────────────────────────

class MetarParser:
    """
    Convierte RawMetar en ParsedWeather.

    No tiene estado: se puede instanciar una vez y reutilizar para multiples METARs.
    """

    def parse(self, raw) -> ParsedWeather:
        """
        Parsea un RawMetar y devuelve un ParsedWeather con todas las variables
        normalizadas y la categoria de vuelo calculada segun ANAC/OACI.

        Parameters
        ----------
        raw : RawMetar
            Objeto crudo devuelto por AviationWeatherFetcher.

        Returns
        -------
        ParsedWeather listo para ser consumido por el feature layer.
        """
        vis_km     = _parse_visibility_km(raw.visibility)
        sky_layers = _parse_sky_layers(raw.sky_condition)
        ceiling_ft = _extract_ceiling_ft(sky_layers)
        wx_codes   = _parse_wx_codes(raw.wx_string)
        spread     = _compute_spread(raw.temp_c, raw.dewp_c)
        wind_var   = _is_variable_wind(raw.raw_string, raw.wind_dir)
        flight_cat = _compute_flight_category(vis_km, ceiling_ft)

        logger.debug(
            f"METAR {raw.icao_id}: vis={vis_km}km ceil={ceiling_ft}ft "
            f"cat={flight_cat} wx={wx_codes}"
        )

        return ParsedWeather(
            source          = "metar",
            station_id      = raw.icao_id,
            obs_time        = raw.obs_time,
            nwp_estimated   = False,
            wind_dir        = raw.wind_dir if not wind_var else None,
            wind_spd_kt     = float(raw.wind_spd_kt) if raw.wind_spd_kt is not None else None,
            wind_gust_kt    = float(raw.wind_gust_kt) if raw.wind_gust_kt is not None else None,
            wind_variable   = wind_var,
            visibility_km   = vis_km,
            ceiling_ft      = ceiling_ft,
            sky_layers      = sky_layers,
            temp_c          = raw.temp_c,
            dewpoint_c      = raw.dewp_c,
            spread_c        = spread,
            altimeter_hpa   = raw.altimeter_hpa,
            wx_codes        = wx_codes,
            flight_category = flight_cat,
            lat             = raw.lat,
            lon             = raw.lon,
            elevation_m     = raw.elevation_m,
            station_name    = raw.station_name,
            raw_string      = raw.raw_string,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos de parseo
# ──────────────────────────────────────────────────────────────────────────────

def _parse_visibility_km(vis_str: Optional[str]) -> Optional[float]:
    """
    Convierte el campo de visibilidad (string) a kilometros (float).

    aviationweather.gov entrega la visibilidad en MILLAS TERRESTRES, como texto
    y SIN sufijo: un TAF que dice "4000" (metros) llega como "2.49", y un CAVOK
    llega como "6+". Interpretar ese numero como metros lo divide por mil y
    produce visibilidades de milimetros, que caen por debajo del bloqueo duro de
    1.5 km y generan NO GO falsos. Medido en un muestreo de 28 periodos TAF de 7
    aerodromos: 8 bloqueos espurios.

    Formatos soportados:
        "6+"        → 10.0 km  (AWC: "mas de 6 SM"; es como reporta el CAVOK)
        "2.49"      → 4.01 km  (statute miles sin sufijo, formato AWC)
        "10SM"      → 16.09 km (statute miles con sufijo)
        "1/4SM"     → 0.40 km  (fraccion de SM)
        "1 1/4SM"   → 2.01 km  (entero + fraccion)
        "M1/4SM"    → 0.40 km  (M = menos de, se trata igual)
        "9999"      → 10.0 km  (OACI: >= 10 km, techo de reporte)
        "6000"      → 6.0 km   (metros numericos, formato ICAO)

    La distincion entre millas y metros para un numero suelto es por MAGNITUD, y
    no hay ambiguedad posible: la API tope la escala en "6+", asi que una
    visibilidad en millas nunca pasa de ~10, y una en metros nunca baja de las
    centenas.
    """
    if vis_str is None:
        return None

    s = vis_str.strip()

    if s == "9999":
        return 10.0

    if "SM" in s:
        return _parse_sm_visibility(s)

    # "6+" = mas de 6 SM (9.66 km). Es como la API codifica el CAVOK, asi que se
    # devuelve el mismo techo de reporte que "9999": por encima de 10 km la
    # distincion no cambia ninguna decision (la rampa de riesgo ya es 0 sobre
    # los 8 km) y mantiene una sola convencion para "sin restriccion".
    if s.endswith("+"):
        try:
            sm = float(s[:-1])
        except ValueError:
            logger.warning(f"Visibilidad con formato no reconocido: '{vis_str}'")
            return None
        return max(10.0, round(sm * SM_TO_KM, 3))

    try:
        valor = float(s)
    except ValueError:
        logger.warning(f"Visibilidad con formato no reconocido: '{vis_str}'")
        return None

    if valor >= _UMBRAL_SM_VS_METROS:
        return round(valor / 1000.0, 3)          # metros (formato OACI)
    return round(valor * SM_TO_KM, 3)            # millas terrestres (formato AWC)


def _parse_sm_visibility(s: str) -> Optional[float]:
    """Convierte un string de visibilidad en statute miles a km."""
    # Orden importa: primero el sufijo compuesto "SM", luego el prefijo "M" (less-than)
    s = s.replace("SM", "").lstrip("M").strip()

    try:
        if "/" in s:
            parts = s.split()
            if len(parts) == 2:
                # Formato "1 1/4": parte entera + fraccion
                whole = float(parts[0])
                num, den = parts[1].split("/")
                sm = whole + float(num) / float(den)
            else:
                # Formato "1/4": solo fraccion
                num, den = s.split("/")
                sm = float(num) / float(den)
        else:
            sm = float(s) if s else 0.0

        return round(sm * SM_TO_KM, 3)

    except (ValueError, ZeroDivisionError):
        logger.warning(f"No se pudo parsear visibilidad SM: '{s}'")
        return None


def _parse_sky_layers(sky_condition: list) -> list:
    """
    Normaliza la lista de capas de nubosidad del METAR.

    Input (formato AWC):  [{"cover": "BKN", "base": 2500}, ...]
    Output (formato interno): [{"cover": "BKN", "base_ft": 2500}, ...]

    Los nombres de campo REALES de la API son `cover` y `base` — verificado
    sobre 48 METAR y 36 TAF argentinos. Se aceptan tambien `skyCover` y
    `cloudBase`, que es como los documentaba esta funcion, para no romper a
    ningun llamador que todavia use esa forma.

    `base` ya viene en pies (no en cientos de pies).
    Las capas se devuelven ordenadas de menor a mayor altura.

    NSC ("No Significant Cloud") aparece solo en TAF y SIEMPRE sin base: es la
    forma en que el pronostico dice que no hay nubes relevantes. Queda como
    capa sin base y por lo tanto no constituye techo, que es el tratamiento
    correcto (ver `_extract_ceiling_ft`, que exige base numerica).
    """
    if not sky_condition:
        return []

    layers = []
    for layer in sky_condition:
        cover = (layer.get("cover") or layer.get("skyCover") or "").upper().strip()
        base  = layer.get("base") if layer.get("base") is not None else layer.get("cloudBase")

        if not cover:
            continue

        normalized = {"cover": cover, "base_ft": int(base) if base is not None else None}

        # Preservar tipo de nube si esta disponible (CB, TCU)
        cloud_type = layer.get("type") or layer.get("cloudType")
        if cloud_type:
            normalized["cloud_type"] = cloud_type.upper()

        layers.append(normalized)

    # Ordenar por altura ascendente (las capas sin base van al final)
    layers.sort(key=lambda l: l["base_ft"] if l["base_ft"] is not None else 99999)
    return layers


def _extract_ceiling_ft(sky_layers: list) -> Optional[int]:
    """
    Extrae el ceiling: la base de la primera capa BKN, OVC o VV (en ft AGL).
    FEW (<= 2/8) y SCT (3-4/8) no constituyen ceiling.
    Devuelve None si el cielo esta despejado o solo hay capas FEW/SCT.
    """
    for layer in sky_layers:
        if layer["cover"] in CEILING_COVERS and layer["base_ft"] is not None:
            return layer["base_ft"]
    return None


def _parse_wx_codes(wx_string: Optional[str]) -> list:
    """
    Divide el wx_string en tokens individuales de fenomenos.

    Ejemplo: "-RA BR" → ["-RA", "BR"]
             "TSRA"   → ["TSRA"]
             None     → []

    El hard_blockers module verifica tokens especificos contra esta lista.
    """
    if not wx_string:
        return []
    return [token.strip() for token in wx_string.split() if token.strip()]


def _compute_spread(temp_c: Optional[float], dewp_c: Optional[float]) -> Optional[float]:
    """
    Calcula el spread temperatura/punto de rocio (T - Td).
    Spread < 2°C → riesgo alto de formacion de niebla.
    Spread < 5°C → riesgo moderado.
    """
    if temp_c is None or dewp_c is None:
        return None
    return round(temp_c - dewp_c, 1)


def _is_variable_wind(raw_string: Optional[str], wind_dir: Optional[int]) -> bool:
    """
    Detecta si el viento es variable (VRB) analizando el string METAR crudo.
    La API de AWC codifica VRB como wdir=0, indistinguible de norte (360).
    El string crudo es la fuente fiable.
    """
    if raw_string and "VRB" in raw_string.upper():
        return True
    return False


# Minimo VFR de la regulacion: por debajo de cualquiera de los dos valores la
# condicion no es VFR. Lo usan la categoria de vuelo y la barrera no-compensatoria
# (risk/soft_scoring.py), para que las dos apliquen exactamente el mismo corte.
VFR_MIN_VIS_KM  = 5.0
VFR_MIN_CEIL_FT = 1000


def _compute_flight_category(
    vis_km     : Optional[float],
    ceiling_ft : Optional[int],
) -> Optional[str]:
    """
    Calcula la categoria de vuelo segun ANAC/OACI (no FAA).

    Si alguno de los dos valores es None, se asume condicion permisiva para
    esa variable (sin restriccion), consistente con "dato no disponible".

    Umbrales:
        VFR  : vis >= 5 km  AND  ceil >= 1000 ft   <- minimo VFR de la regulacion
        MVFR : vis >= 3 km  AND  ceil >=  500 ft  (peor que VFR)
        IFR  : vis >= 0.8km AND  ceil >=  200 ft  (peor que MVFR)
        LIFR : vis <  0.8km  OR  ceil <   200 ft

    Solo el corte de VFR es un minimo de la regulacion. Los inferiores son una
    escala del sistema para graduar cuanto se aleja la condicion de ese minimo:
    los minimos IFR reales dependen de cada procedimiento publicado.
    """
    vis  = vis_km    if vis_km    is not None else 99.0
    ceil = ceiling_ft if ceiling_ft is not None else 99999

    # Categorias OACI/ANAC (no FAA): se evitan los rotulos MVFR/LIFR (NWS/FAA).
    if vis < 0.8 or ceil < 200:
        return "IFR bajo mínimos"
    if vis < 3.0 or ceil < 500:
        return "IFR"
    if vis < VFR_MIN_VIS_KM or ceil < VFR_MIN_CEIL_FT:
        return "VFR marginal"
    return "VFR"


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    from datetime import datetime, timezone

    # Permitir importar desde la raiz del proyecto
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from data.fetcher_aviationweather import AviationWeatherFetcher

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("  TEST: MetarParser")
    print("=" * 60)

    fetcher = AviationWeatherFetcher(mock=True)
    parser  = MetarParser()

    # ── SACO con datos (mock) ─────────────────────────────────────────────────
    print("\n[1] Pipeline completo: fetch + parse (SACO mock)")
    raw    = fetcher.get_metar("SACO")
    parsed = parser.parse(raw)

    obs_dt = datetime.fromtimestamp(parsed.obs_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n  Aeropuerto      : {parsed.station_id} — {parsed.station_name}")
    print(f"  Observacion     : {obs_dt}")
    print(f"  Fuente          : {parsed.source}  (NWP estimado: {parsed.nwp_estimated})")
    print(f"  Raw string      : {parsed.raw_string}")

    print(f"\n  VIENTO")
    print(f"    Direccion     : {parsed.wind_dir}°" + ("  (VARIABLE)" if parsed.wind_variable else ""))
    print(f"    Velocidad     : {parsed.wind_spd_kt} kt")
    print(f"    Rafaga        : {parsed.wind_gust_kt} kt" if parsed.wind_gust_kt else "    Rafaga        : sin rafagas")

    print(f"\n  VISIBILIDAD     : {parsed.visibility_km} km")

    print(f"\n  NUBOSIDAD")
    if parsed.sky_layers:
        for layer in parsed.sky_layers:
            cloud_type_str = f" ({layer['cloud_type']})" if layer.get("cloud_type") else ""
            print(f"    {layer['cover']}{cloud_type_str} base {layer['base_ft']} ft AGL")
    else:
        print("    Sin capas reportadas")
    print(f"    Ceiling       : {parsed.ceiling_ft} ft AGL" if parsed.ceiling_ft else "    Ceiling       : sin ceiling (cielo despejado o FEW/SCT)")

    print(f"\n  TERMODINAMICA")
    print(f"    Temperatura   : {parsed.temp_c} °C")
    print(f"    Dew point     : {parsed.dewpoint_c} °C")
    print(f"    Spread T/Td   : {parsed.spread_c} °C", end="")
    if parsed.spread_c is not None:
        if parsed.spread_c < 2:
            print("  [RIESGO NIEBLA ALTO]")
        elif parsed.spread_c < 5:
            print("  [riesgo niebla moderado]")
        else:
            print("  [niebla improbable]")
    else:
        print()

    print(f"\n  FENOMENOS       : {parsed.wx_codes if parsed.wx_codes else 'ninguno'}")
    print(f"  QNH             : {parsed.altimeter_hpa} hPa")
    print(f"\n  CATEGORIA VUELO : {parsed.flight_category}")

    # ── Casos de borde para testear la logica de categorias ──────────────────
    print("\n" + "-" * 60)
    print("[2] Casos de borde — categoria de vuelo (ANAC/OACI)")

    casos = [
        ("CAVOK (9999, sin ceiling)",        10.0, None,  "VFR"),
        ("VFR justo al limite",               5.0, 1000,  "VFR"),
        ("VFR marginal por visibilidad",      4.0, 2000,  "VFR marginal"),
        ("VFR marginal por ceiling",          8.0,  800,  "VFR marginal"),
        ("IFR por visibilidad",               1.5, 2000,  "IFR"),
        ("IFR por ceiling",                   6.0,  300,  "IFR"),
        ("IFR bajo minimos por visibilidad",  0.5, 2000,  "IFR bajo mínimos"),
        ("IFR bajo minimos por ceiling",      6.0,  150,  "IFR bajo mínimos"),
        ("IFR bajo minimos por ambos",        0.3,   50,  "IFR bajo mínimos"),
        ("Sin datos (None, None)",           None,  None,  "VFR"),
    ]

    all_pass = True
    for descripcion, vis, ceil, esperado in casos:
        resultado = _compute_flight_category(vis, ceil)
        ok = resultado == esperado
        all_pass = all_pass and ok
        estado = "OK" if ok else "FALLO"
        print(f"  [{estado}] {descripcion:<38} => {resultado} (esperado: {esperado})")

    # ── Visibilidades en distintos formatos ───────────────────────────────────
    print("\n" + "-" * 60)
    print("[3] Parsing de formatos de visibilidad")

    vis_casos = [
        ("9999",      10.0),
        ("6000",       6.0),
        ("1000",       1.0),
        ("10SM",      18.52),
        ("5SM",        9.26),
        ("1/4SM",      0.463),
        ("1 1/2SM",    2.778),
        ("M1/4SM",     0.463),
    ]

    for s, esperado in vis_casos:
        resultado = _parse_visibility_km(s)
        ok = resultado is not None and abs(resultado - esperado) < 0.01
        estado = "OK" if ok else "FALLO"
        print(f"  [{estado}] '{s:<12}' => {resultado} km  (esperado ~{esperado})")

    print("\n" + "=" * 60)
    status = "TODOS LOS TESTS PASARON" if all_pass else "ALGUNOS TESTS FALLARON"
    print(f"  {status}")
    print("=" * 60)
