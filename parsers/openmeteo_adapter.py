"""
openmeteo_adapter.py
====================
Convierte RawNWPHour / RawNWP (datos crudos de Open-Meteo) en ParsedWeather,
el mismo modelo interno que produce MetarParser, de modo que el risk engine
opera de forma uniforme sin importar la fuente de datos.

Diferencias clave entre METAR y NWP que este modulo resuelve:
    - Visibilidad   : Open-Meteo devuelve metros (float), METAR es string
    - Nubosidad     : NWP da porcentajes por capa, no base de nubes directa
    - Fenomenos     : NWP usa codigos WMO (int), METAR usa tokens de texto
    - Ceiling       : estimado a partir de cobertura de nubes bajas
    - QNH           : no disponible en NWP (se deja None)

Limitacion conocida de ceiling en NWP
--------------------------------------
Open-Meteo no provee la altura de la base de las nubes. El adapter estima el
ceiling usando cloudcover_low_pct: si la cobertura de nubes bajas alcanza umbral
BKN (>= 63 %), se asume el techo en low_cloud_base_ft (default: 2000 ft AGL).

Este valor es configurable al instanciar el adapter: 2000 ft AGL es una estimacion
conservadora de base para nubes bajas.

LIMITACION: el techo estimado por NWP es intrinsecamente menos preciso que el
observado en un METAR, sobre todo en terreno complejo (cordillera, precordillera,
sierras), donde los modelos de grilla no resuelven bien los efectos locales.
El sistema NO aplica hoy ninguna correccion por terreno: la estimacion se usa tal
cual y la limitacion se declara.

Uso tipico
----------
    from data.fetcher_openmeteo import OpenMeteoFetcher
    from parsers.openmeteo_adapter import OpenMeteoAdapter

    fetcher = OpenMeteoFetcher()
    adapter = OpenMeteoAdapter()

    nwp    = fetcher.get_forecast(lat=-31.00, lon=-64.52, elevation_m=1141)
    parsed = adapter.adapt_all(nwp, station_id="SACC")
    # parsed es list[ParsedWeather], un elemento por hora
"""

import logging
from typing import Optional

try:
    from parsers.metar_parser import ParsedWeather, _compute_flight_category, _compute_spread
except ImportError:
    # Ruta directa cuando el archivo se ejecuta como script desde parsers/
    from metar_parser import ParsedWeather, _compute_flight_category, _compute_spread

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

# Umbral de cobertura (%) a partir del cual una capa constituye ceiling (BKN = 5/8 = 62.5%)
CEILING_COVER_THRESHOLD_PCT = 63

# ── Altura de la base de nubes bajas: REGLA DE ESPY ───────────────────────────
# La base de las nubes se estima con la regla de Espy (tambien llamada
# "regla de los 400 pies"), estandar en aviacion general:
#
#     base_nubes (ft AGL) ~= 400 x (T - Td)      [T y Td en grados C]
#
# El aire ascendente se enfria ~3 C por cada 1000 ft (adiabatica seca) mientras
# el punto de rocio baja ~0.5 C, de modo que la saturacion (y por lo tanto la
# base de la nube) se alcanza a unos 400 ft por cada grado de spread.
#
# Por que reemplaza a una constante: antes se asumia una base FIJA de 2000 ft AGL
# para toda nubosidad baja del pais. Eso es falso justamente en los casos que mas
# importan: con spread cercano a 0 (niebla, stratus) las nubes estan practicamente
# a nivel del suelo, y la constante las ubicaba 2000 ft mas arriba, subestimando
# el riesgo. La regla de Espy usa datos que el NWP ya provee (T y Td) y responde a
# la condicion real de cada punto y cada hora.
CLOUD_BASE_FT_PER_DEG_C = 400

# Acotado: por debajo, una base menor a 100 ft es indistinguible de niebla en
# superficie; por encima, si la formula da mas que esto la capa ya no se comporta
# como nube baja y se usa el techo del rango.
MIN_CLOUD_BASE_FT     = 100
MAX_LOW_CLOUD_BASE_FT = 6000

# Fallback cuando el NWP no trae temperatura o punto de rocio (sin spread no hay
# forma de aplicar Espy). Valor conservador de nube baja.
DEFAULT_LOW_CLOUD_BASE_FT  = 2000
DEFAULT_MID_CLOUD_BASE_FT  = 8000
DEFAULT_HIGH_CLOUD_BASE_FT = 20000


def estimate_cloud_base_ft(
    temp_c    : Optional[float],
    dewpoint_c: Optional[float],
    fallback_ft: int = DEFAULT_LOW_CLOUD_BASE_FT,
) -> int:
    """
    Altura estimada de la base de nubes bajas (ft AGL) por la regla de Espy.

    Devuelve `fallback_ft` si falta temperatura o punto de rocio.
    El resultado queda acotado a [MIN_CLOUD_BASE_FT, MAX_LOW_CLOUD_BASE_FT].
    """
    if temp_c is None or dewpoint_c is None:
        return fallback_ft
    spread_c = max(temp_c - dewpoint_c, 0.0)
    base_ft  = CLOUD_BASE_FT_PER_DEG_C * spread_c
    return int(round(min(max(base_ft, MIN_CLOUD_BASE_FT), MAX_LOW_CLOUD_BASE_FT)))

# ──────────────────────────────────────────────────────────────────────────────
# Tabla de codigos WMO → tokens wx (compatibles con hard_blockers)
# ──────────────────────────────────────────────────────────────────────────────
# Fuente: WMO Table 4677 (presente weather) / Open-Meteo WMO code docs
# Los tokens siguen la convencion METAR para que hard_blockers.py los procese
# igual que si vinieran de un METAR real.

WMO_TO_WX: dict = {
    0 : [],            # cielo despejado
    1 : [],            # principalmente despejado
    2 : [],            # parcialmente nublado
    3 : [],            # nublado / cubierto
    45: ["FG"],        # niebla
    48: ["FG"],        # niebla con escarcha (rime fog)
    51: ["-DZ"],       # llovizna ligera
    53: ["DZ"],        # llovizna moderada
    55: ["+DZ"],       # llovizna intensa
    56: ["FZDZ"],      # llovizna engelante ligera  → hard blocker
    57: ["FZDZ"],      # llovizna engelante intensa → hard blocker
    61: ["-RA"],       # lluvia ligera
    63: ["RA"],        # lluvia moderada
    65: ["+RA"],       # lluvia intensa
    66: ["FZRA"],      # lluvia engelante ligera    → hard blocker
    67: ["FZRA"],      # lluvia engelante intensa   → hard blocker
    71: ["-SN"],       # nevadas ligeras
    73: ["SN"],        # nevadas moderadas
    75: ["+SN"],       # nevadas intensas
    77: ["SG"],        # granulos de nieve
    80: ["-SHRA"],     # chubascos ligeros
    81: ["SHRA"],      # chubascos moderados
    82: ["+SHRA"],     # chubascos violentos
    85: ["-SHSN"],     # chubascos de nieve ligeros
    86: ["+SHSN"],     # chubascos de nieve intensos
    95: ["TS"],        # tormenta electrica          → hard blocker
    96: ["TSGR"],      # tormenta con granizo leve   → hard blocker
    99: ["TSGR"],      # tormenta con granizo intenso→ hard blocker
}


# ──────────────────────────────────────────────────────────────────────────────
# OpenMeteoAdapter
# ──────────────────────────────────────────────────────────────────────────────

class OpenMeteoAdapter:
    """
    Convierte datos NWP crudos de Open-Meteo al modelo interno ParsedWeather.

    Parameters
    ----------
    low_cloud_base_ft : int
        Altura estimada de la base de nubes bajas en pies AGL.
        Se usa cuando cloudcover_low_pct >= CEILING_COVER_THRESHOLD_PCT.
        Default: 2000 ft (conservador para Sierras Chicas / SACC).
    mid_cloud_base_ft : int
        Altura estimada de la base de nubes medias en pies AGL. Default: 8000 ft.
    high_cloud_base_ft : int
        Altura estimada de la base de nubes altas en pies AGL. Default: 20000 ft.
    """

    def __init__(
        self,
        low_cloud_base_ft : int = DEFAULT_LOW_CLOUD_BASE_FT,
        mid_cloud_base_ft : int = DEFAULT_MID_CLOUD_BASE_FT,
        high_cloud_base_ft: int = DEFAULT_HIGH_CLOUD_BASE_FT,
    ):
        self.low_cloud_base_ft  = low_cloud_base_ft
        self.mid_cloud_base_ft  = mid_cloud_base_ft
        self.high_cloud_base_ft = high_cloud_base_ft

    # ── Metodos publicos ──────────────────────────────────────────────────────

    def adapt(
        self,
        nwp_hour  ,
        station_id: str,
        lat       : Optional[float] = None,
        lon       : Optional[float] = None,
        elevation_m: Optional[float] = None,
    ) -> ParsedWeather:
        """
        Convierte un RawNWPHour en ParsedWeather.

        Parameters
        ----------
        nwp_hour   : RawNWPHour del fetcher de Open-Meteo.
        station_id : identificador del punto (ej: "SACC").
        lat, lon, elevation_m : coordenadas geograficas del punto.

        Returns
        -------
        ParsedWeather con nwp_estimated=True y todos los campos calculados.
        """
        vis_km     = _visibility_to_km(nwp_hour.visibility_m)
        sky_layers = self._estimate_sky_layers(
            nwp_hour.cloudcover_low_pct,
            nwp_hour.cloudcover_mid_pct,
            nwp_hour.cloudcover_high_pct,
            temp_c     = nwp_hour.temperature_2m_c,
            dewpoint_c = nwp_hour.dewpoint_2m_c,
        )
        ceiling_ft = _extract_ceiling_ft(sky_layers)
        wx_codes   = _wmo_to_wx_codes(nwp_hour.weathercode)
        spread     = _compute_spread(nwp_hour.temperature_2m_c, nwp_hour.dewpoint_2m_c)
        flight_cat = _compute_flight_category(vis_km, ceiling_ft)

        logger.debug(
            f"NWP {station_id} {nwp_hour.valid_time_iso}: "
            f"vis={vis_km}km ceil={ceiling_ft}ft cat={flight_cat} wx={wx_codes}"
        )

        return ParsedWeather(
            source          = "nwp",
            station_id      = station_id,
            obs_time        = nwp_hour.valid_time_utc,
            nwp_estimated   = True,
            wind_dir        = nwp_hour.winddirection_10m,
            wind_spd_kt     = nwp_hour.windspeed_10m_kt,
            wind_gust_kt    = nwp_hour.windgusts_10m_kt,
            wind_variable   = False,   # NWP no reporta VRB
            visibility_km   = vis_km,
            ceiling_ft      = ceiling_ft,
            sky_layers      = sky_layers,
            temp_c          = nwp_hour.temperature_2m_c,
            dewpoint_c      = nwp_hour.dewpoint_2m_c,
            spread_c        = spread,
            altimeter_hpa   = None,    # no disponible en NWP
            wx_codes        = wx_codes,
            cloud_cover_pct = _max_cloud_cover(
                nwp_hour.cloudcover_low_pct,
                nwp_hour.cloudcover_mid_pct,
                nwp_hour.cloudcover_high_pct,
            ),
            precip_mm       = nwp_hour.precipitation_mm,
            flight_category = flight_cat,
            lat             = lat,
            lon             = lon,
            elevation_m     = elevation_m,
            station_name    = None,
            raw_string      = None,
        )

    def adapt_all(self, nwp, station_id: str) -> list:
        """
        Convierte todos los slots de un RawNWP en una lista de ParsedWeather.

        Parameters
        ----------
        nwp        : RawNWP del fetcher de Open-Meteo.
        station_id : identificador del punto (ej: "SACC").

        Returns
        -------
        list[ParsedWeather] ordenado cronologicamente, un elemento por hora.
        """
        if nwp is None:
            logger.warning(f"adapt_all recibio nwp=None para station_id={station_id}")
            return []

        logger.info(
            f"Adaptando {len(nwp.hours)} slots NWP para {station_id} "
            f"(lat={nwp.lat}, lon={nwp.lon}, elev={nwp.elevation_m}m)"
        )

        return [
            self.adapt(
                hour,
                station_id  = station_id,
                lat         = nwp.lat,
                lon         = nwp.lon,
                elevation_m = nwp.elevation_m,
            )
            for hour in nwp.hours
        ]

    # ── Helper privado de estimacion de capas ─────────────────────────────────

    def _estimate_sky_layers(
        self,
        low_pct : Optional[int],
        mid_pct : Optional[int],
        high_pct: Optional[int],
        temp_c    : Optional[float] = None,
        dewpoint_c: Optional[float] = None,
    ) -> list:
        """
        Construye capas de nubosidad estimadas a partir de porcentajes de cobertura.

        La base de la capa BAJA se calcula con la regla de Espy a partir del spread
        T/Td de esa misma hora (ver estimate_cloud_base_ft): con aire saturado la
        capa queda cerca del suelo, y con aire seco, alta. Las capas media y alta
        conservan alturas de referencia, porque no condicionan el techo operativo
        en la practica y el NWP no aporta datos para estimarlas mejor.

        Cada capa resultante incluye "estimated": True para distinguirla de capas
        observadas directamente en un METAR, y "base_reference", que dice si su
        base es una ALTURA DE REFERENCIA fija y no un pronostico: las capas media
        y alta siempre, y la baja cuando falta temperatura o rocio para Espy. La
        pantalla lo necesita para no presentar esos 8.000 ft como un techo
        pronosticado.
        """
        layers = []

        low_base_ft = estimate_cloud_base_ft(
            temp_c, dewpoint_c, fallback_ft=self.low_cloud_base_ft,
        )
        # La capa media nunca puede quedar por debajo de la baja
        mid_base_ft = max(self.mid_cloud_base_ft, low_base_ft + 1000)

        low_is_reference = temp_c is None or dewpoint_c is None
        for cover_pct, base_ft, is_reference in (
            (low_pct,  low_base_ft,             low_is_reference),
            (mid_pct,  mid_base_ft,             True),
            (high_pct, self.high_cloud_base_ft, True),
        ):
            if cover_pct is None:
                continue
            cover_str = _pct_to_sky_cover(cover_pct)
            if cover_str is None:
                continue
            layers.append({
                "cover"         : cover_str,
                "base_ft"       : base_ft,
                "estimated"     : True,
                "base_reference": is_reference,
            })

        # Ordenar por altura ascendente (igual que MetarParser)
        layers.sort(key=lambda l: l["base_ft"])
        return layers


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _visibility_to_km(visibility_m: Optional[float]) -> Optional[float]:
    """Convierte visibilidad de metros a kilometros."""
    if visibility_m is None:
        return None
    return round(visibility_m / 1000.0, 3)


def _max_cloud_cover(low, mid, high) -> Optional[int]:
    """Cobertura total estimada como el maximo de las tres capas NWP (0-100 %)."""
    vals = [c for c in (low, mid, high) if c is not None]
    return max(vals) if vals else None


def _pct_to_sky_cover(pct: int) -> Optional[str]:
    """
    Convierte porcentaje de cobertura de nubes al codigo METAR equivalente.

    Umbrales basados en octavos OACI:
        < 12 %  → None  (cielo despejado, sin capa reportable)
        12-37 % → FEW   (1-2/8)
        38-62 % → SCT   (3-4/8)
        63-87 % → BKN   (5-7/8)  ← constituye ceiling
        88-100% → OVC   (8/8)    ← constituye ceiling
    """
    if pct < 12:
        return None
    if pct < 38:
        return "FEW"
    if pct < 63:
        return "SCT"
    if pct < 88:
        return "BKN"
    return "OVC"


def _extract_ceiling_ft(sky_layers: list) -> Optional[int]:
    """
    Extrae el ceiling de las capas estimadas: primera BKN u OVC mas baja.
    Identica a la funcion de metar_parser pero opera sobre capas estimadas.
    """
    for layer in sky_layers:
        if layer["cover"] in {"BKN", "OVC"}:
            return layer["base_ft"]
    return None


def _wmo_to_wx_codes(weathercode: Optional[int]) -> list:
    """
    Convierte un codigo WMO a la lista de tokens wx equivalentes en convencion METAR.
    Devuelve lista vacia si el codigo es None o no esta en la tabla.
    """
    if weathercode is None:
        return []
    codes = WMO_TO_WX.get(weathercode)
    if codes is None:
        logger.debug(f"Codigo WMO {weathercode} no tiene mapeo definido, se ignora")
        return []
    return list(codes)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    from datetime import datetime, timezone

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from data.fetcher_openmeteo import OpenMeteoFetcher

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("  TEST: OpenMeteoAdapter")
    print("=" * 60)

    fetcher = OpenMeteoFetcher(mock=True)
    adapter = OpenMeteoAdapter()

    nwp    = fetcher.get_forecast(lat=-31.00, lon=-64.52, elevation_m=1141, hours_ahead=12)
    parsed = adapter.adapt_all(nwp, station_id="SACC")

    print(f"\n  Slots adaptados: {len(parsed)}")
    print(f"  Fuente         : {parsed[0].source if parsed else 'N/A'}")
    print(f"  NWP estimado   : {parsed[0].nwp_estimated if parsed else 'N/A'}")

    print(f"\n  {'Hora (AR)':<18} {'Cat':>5} {'Vis km':>7} {'Ceil ft':>8} "
          f"{'Viento':>12} {'Spread':>7} {'WX codes'}")
    print(f"  {'-'*18} {'-'*5} {'-'*7} {'-'*8} {'-'*12} {'-'*7} {'-'*20}")

    for p in parsed:
        slot_ar  = datetime.fromtimestamp(p.obs_time, tz=timezone.utc)
        hora_str = slot_ar.strftime("%Y-%m-%dT%H:%M")
        vis_str  = f"{p.visibility_km:.1f}" if p.visibility_km is not None else "  ---"
        ceil_str = str(p.ceiling_ft) if p.ceiling_ft is not None else "   CLR"
        wind_str = f"{p.wind_dir or 0:>3}°/{p.wind_spd_kt or 0:.0f}kt"
        sprd_str = f"{p.spread_c:.1f}°C" if p.spread_c is not None else "  ---"
        wx_str   = " ".join(p.wx_codes) if p.wx_codes else "-"
        print(f"  {hora_str:<18} {p.flight_category:>5} {vis_str:>7} {ceil_str:>8} "
              f"{wind_str:>12} {sprd_str:>7} {wx_str}")

    # ── Verificar que los slots del mock cubren los casos de prueba ───────────
    print("\n" + "-" * 60)
    print("  Verificacion de casos de prueba del mock SACC")

    all_pass = True

    # Hora 3 del mock: vis 4.5km + WMO 45 (niebla) → VFR marginal + FG
    fog_slots = [p for p in parsed if p.wx_codes == ["FG"]]
    ok_fog = len(fog_slots) == 1 and fog_slots[0].flight_category in ("VFR marginal", "IFR", "IFR bajo mínimos")
    all_pass = all_pass and ok_fog
    print(f"  [{'OK' if ok_fog else 'FALLO'}] Slot niebla (WMO 45): cat={fog_slots[0].flight_category if fog_slots else 'N/A'}, wx={fog_slots[0].wx_codes if fog_slots else 'N/A'}")

    # Hora 7 del mock: viento 14 kt rafaga 22 kt
    gusty = [p for p in parsed if p.wind_gust_kt and p.wind_gust_kt > 20]
    ok_gust = len(gusty) == 1 and gusty[0].wind_spd_kt == 14.0
    all_pass = all_pass and ok_gust
    print(f"  [{'OK' if ok_gust else 'FALLO'}] Slot rafagas fuertes: spd={gusty[0].wind_spd_kt if gusty else 'N/A'} kt, gust={gusty[0].wind_gust_kt if gusty else 'N/A'} kt")

    # Verificar que nwp_estimated=True en todos los slots
    ok_nwp = all(p.nwp_estimated for p in parsed)
    all_pass = all_pass and ok_nwp
    print(f"  [{'OK' if ok_nwp else 'FALLO'}] nwp_estimated=True en todos los slots")

    # Verificar que el spread se calcula correctamente (T=~20, Td=8 → spread~12)
    spreads_ok = all(p.spread_c is not None and p.spread_c > 5 for p in parsed)
    all_pass = all_pass and spreads_ok
    print(f"  [{'OK' if spreads_ok else 'FALLO'}] Spread T/Td calculado y > 5°C (sin riesgo niebla en horas normales)")

    # ── Tabla de codigos WMO criticos ─────────────────────────────────────────
    print("\n" + "-" * 60)
    print("  Verificacion de mapeo WMO -> hard blockers")

    wmo_criticos = [
        (95,  ["TS"],   "Tormenta"),
        (96,  ["TSGR"], "Tormenta + granizo leve"),
        (99,  ["TSGR"], "Tormenta + granizo intenso"),
        (66,  ["FZRA"], "Lluvia engelante ligera"),
        (56,  ["FZDZ"], "Llovizna engelante"),
        (45,  ["FG"],   "Niebla"),
        (0,   [],       "Cielo despejado"),
    ]

    for code, esperado, desc in wmo_criticos:
        resultado = _wmo_to_wx_codes(code)
        ok = resultado == esperado
        all_pass = all_pass and ok
        print(f"  [{'OK' if ok else 'FALLO'}] WMO {code:>3} ({desc:<30}) => {resultado}")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
