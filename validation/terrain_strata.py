"""
terrain_strata.py
=================
Estratificacion de los aerodromos argentinos con archivo METAR segun la
RUGOSIDAD DEL TERRENO circundante.

Por que existe
--------------
El motor usa modelo numerico de pronostico (NWP) en los aerodromos sin estacion
meteorologica, que son la mayoria del pais. Los modelos de grilla suavizan la
orografia: en terreno complejo la celda de grilla no representa el valle ni la
ladera concretos, lo que sesga temperatura y punto de rocio y, por lo tanto, el
spread T/Td del que se deriva la base de nubes (regla de Espy). El techo es el
criterio de mayor peso del modelo de riesgo, asi que ese sesgo importa.

Para medir ese error hace falta comparar NWP contra observacion real, y eso solo
se puede hacer donde HAY observacion real. Este modulo construye la muestra.

Regla de alcance
----------------
La estratificacion es por TERRENO, no por aerodromo. No se estudia "el error en
SACC" (que ademas no tiene estacion y por lo tanto no es verificable): se estudia
la RELACION entre rugosidad del terreno y error del NWP a escala nacional, que
despues puede extrapolarse a cualquier aerodromo sin estacion segun su terreno.

Metodo
------
1. Se toma el listado de estaciones argentinas con archivo METAR del Iowa
   Environmental Mesonet (red AR__ASOS).
2. Para cada estacion se muestrea una grilla de NxN puntos SRTM en un cuadrado
   de +/- RADIUS_KM alrededor, via data/terrain.py (Open-Topo-Data).
3. La rugosidad es el DESVIO ESTANDAR de esas elevaciones. Se prefiere al rango
   (max-min) porque el rango lo fija un unico pixel atipico, mientras que el
   desvio describe cuanto varia el terreno en conjunto — que es exactamente lo
   que una celda de grilla promedia y pierde.
4. Se clasifica en cuatro estratos y se asigna region geografica.

El resultado se cachea en data/terrain_strata.json para no repetir ~30 llamadas
a la API en cada corrida.

Uso
---
    .venv/Scripts/python.exe validation/terrain_strata.py            # usa cache
    .venv/Scripts/python.exe validation/terrain_strata.py --refresh  # recalcula
"""

import json
import logging
import math
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.terrain import get_elevations_m

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuracion
# ──────────────────────────────────────────────────────────────────────────────

IEM_NETWORK_URL = "https://mesonet.agron.iastate.edu/geojson/network/AR__ASOS.geojson"

RADIUS_KM  = 10.0   # semilado del cuadrado de muestreo alrededor de la estacion
API_DELAY_S = 1.2   # pausa entre estaciones (Open-Topo-Data: 1 req/s)
GRID_N     = 7      # 7x7 = 49 puntos por estacion
TIMEOUT_S  = 30

CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "terrain_strata.json",
)

# Umbrales de rugosidad (desvio estandar de elevacion, en metros).
# Calibrados para que los cuatro estratos queden poblados en el pais: la Pampa
# y el Litoral caen en LLANO, las sierras de Cordoba y el pedemonte del NOA en
# ONDULADO/SERRANO, y la cordillera y los Andes patagonicos en MONTANOSO.
STRATA = [
    ("LLANO",     0.0,   25.0),
    ("ONDULADO",  25.0,  100.0),
    ("SERRANO",   100.0, 300.0),
    ("MONTANOSO", 300.0, float("inf")),
]

# Regiones geograficas argentinas.
# Sirven para que la muestra no quede concentrada en una sola zona del pais.
# Los limites son aproximados y siguen el agrupamiento habitual de provincias,
# no una division politica exacta: alcanza para garantizar dispersion espacial.
def _region(lat: float, lon: float) -> str:
    """Asigna region geografica argentina a una coordenada."""
    # Patagonia: al sur del rio Colorado (~ -39) y Neuquen/Rio Negro cordilleranos
    if lat <= -39.0:
        return "Patagonia"
    # NOA: Jujuy, Salta, Tucuman, Catamarca, Santiago del Estero, La Rioja
    if lat > -31.0 and lon < -62.0:
        return "NOA"
    # Litoral: Misiones, Corrientes, Chaco, Formosa, Entre Rios, Santa Fe este
    if lon >= -62.0 and lat > -34.0:
        return "Litoral"
    # Cuyo: Mendoza, San Juan, San Luis
    if lon < -65.0 and -37.0 < lat <= -28.5:
        return "Cuyo"
    # Resto: llanura pampeana (Buenos Aires, La Pampa, sur de Cordoba y Santa Fe)
    return "Pampa"


@dataclass
class StationTerrain:
    """Una estacion con archivo METAR, caracterizada por su terreno."""
    icao          : str
    name          : str
    lat           : float
    lon           : float
    elevation_m   : float
    archive_begin : Optional[str]
    ruggedness_m  : Optional[float]   # desvio estandar de elevacion en el entorno
    relief_m      : Optional[float]   # max - min en el entorno (informativo)
    stratum       : Optional[str]
    region        : str
    obs_probe     : int = -1   # observaciones en la semana sondeada; <=0 = no reporta


# ──────────────────────────────────────────────────────────────────────────────
# Adquisicion
# ──────────────────────────────────────────────────────────────────────────────

def fetch_iem_stations() -> List[dict]:
    """
    Descarga el listado de estaciones argentinas con archivo METAR del IEM.
    Devuelve [] si el servicio no responde (el modulo es offline: no rompe nada).
    """
    try:
        with urllib.request.urlopen(IEM_NETWORK_URL, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.error("IEM no respondio: %s", exc)
        return []

    out = []
    for feat in data.get("features", []):
        p = feat.get("properties", {})
        geom = feat.get("geometry", {}) or {}
        coords = geom.get("coordinates") or []
        if len(coords) != 2:
            continue
        lon, lat = coords[0], coords[1]
        out.append({
            "icao": feat.get("id", ""),
            "name": p.get("sname", ""),
            "lat": lat,
            "lon": lon,
            "elevation_m": p.get("elevation"),
            "archive_begin": p.get("archive_begin"),
        })
    logger.info("IEM: %d estaciones argentinas con archivo METAR", len(out))
    return out


def _grid_points(lat: float, lon: float) -> List[Tuple[float, float]]:
    """
    Grilla de GRID_N x GRID_N puntos en un cuadrado de +/- RADIUS_KM.

    La conversion km->grados usa 111.32 km/grado de latitud y la correccion por
    coseno de la latitud en longitud. A estas escalas (10 km) el error de la
    aproximacion plana es despreciable.
    """
    dlat = RADIUS_KM / 111.32
    dlon = RADIUS_KM / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    pts = []
    for i in range(GRID_N):
        for j in range(GRID_N):
            fi = -1.0 + 2.0 * i / (GRID_N - 1)
            fj = -1.0 + 2.0 * j / (GRID_N - 1)
            pts.append((round(lat + fi * dlat, 6), round(lon + fj * dlon, 6)))
    return pts


def _stddev(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def classify(ruggedness_m: Optional[float]) -> Optional[str]:
    """Estrato de terreno segun la rugosidad."""
    if ruggedness_m is None:
        return None
    for name, lo, hi in STRATA:
        if lo <= ruggedness_m < hi:
            return name
    return STRATA[-1][0]


# ──────────────────────────────────────────────────────────────────────────────
# Construccion de la estratificacion
# ──────────────────────────────────────────────────────────────────────────────

def build_strata(stations: Optional[List[dict]] = None) -> List[StationTerrain]:
    """
    Calcula la rugosidad de cada estacion. Consume la API de terreno:
    ~GRID_N^2 puntos por estacion, en lotes de 100 con 1.1 s entre lotes.
    """
    if stations is None:
        stations = fetch_iem_stations()

    out: List[StationTerrain] = []
    for k, st in enumerate(stations, 1):
        pts = _grid_points(st["lat"], st["lon"])

        # Open-Topo-Data admite 1 request/segundo. get_elevations_m solo espera
        # ENTRE lotes de una misma llamada, no entre llamadas sucesivas, asi que
        # la pausa entre estaciones va aca. Sin esto la API devuelve 429 y ~60%
        # de las estaciones quedan sin dato.
        elevs: List[float] = []
        for attempt in range(3):
            if k > 1 or attempt > 0:
                time.sleep(API_DELAY_S)
            elevs = [e for e in get_elevations_m(pts) if e is not None]
            if len(elevs) >= len(pts) // 2:
                break
            logger.warning("%s: intento %d devolvio %d/%d puntos — reintentando",
                           st["icao"], attempt + 1, len(elevs), len(pts))

        if len(elevs) < len(pts) // 2:
            rug = relief = None
            logger.error("%s: solo %d/%d puntos SRTM tras 3 intentos — sin clasificar",
                         st["icao"], len(elevs), len(pts))
        else:
            rug = round(_stddev(elevs), 1)
            relief = round(max(elevs) - min(elevs), 1)

        out.append(StationTerrain(
            icao          = st["icao"],
            name          = st["name"],
            lat           = st["lat"],
            lon           = st["lon"],
            elevation_m   = st["elevation_m"],
            archive_begin = st["archive_begin"],
            ruggedness_m  = rug,
            relief_m      = relief,
            stratum       = classify(rug),
            region        = _region(st["lat"], st["lon"]),
        ))
        print(f"    [{k:2d}/{len(stations)}] {st['icao']:5s} "
              f"rugosidad={rug if rug is not None else '--':>7} m  "
              f"{classify(rug) or '?'}")
    return out


def ruggedness_for_point(lat: float, lon: float) -> Optional[float]:
    """
    Rugosidad del terreno en una coordenada CUALQUIERA, con el mismo metodo que
    se aplica a las estaciones.

    Es la pieza que hace utilizable el estudio: permite ubicar en un estrato a un
    aerodromo SIN estacion meteorologica —la mayoria del pais, incluido el del
    comitente— y aplicarle el error medido en las estaciones de ese mismo estrato.
    Sin esta funcion el estudio describiria 61 aerodromos; con ella, los 561.
    """
    pts = _grid_points(lat, lon)
    elevs = [e for e in get_elevations_m(pts) if e is not None]
    if len(elevs) < len(pts) // 2:
        return None
    return round(_stddev(elevs), 1)


def reclassify(strata: List[StationTerrain]) -> List[StationTerrain]:
    """
    Recalcula estrato y region a partir de la rugosidad ya cacheada, sin volver
    a consultar la API de terreno. Se usa cuando cambian los umbrales de STRATA
    o los limites de las regiones.
    """
    for s in strata:
        s.stratum = classify(s.ruggedness_m)
        s.region  = _region(s.lat, s.lon)
    return strata


def save(strata: List[StationTerrain], path: str = CACHE_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in strata], f, ensure_ascii=False, indent=1)
    logger.info("Estratificacion guardada en %s", path)


def load(path: str = CACHE_PATH) -> List[StationTerrain]:
    """Carga la estratificacion cacheada. Lista vacia si no existe."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [StationTerrain(**d) for d in json.load(f)]


# ──────────────────────────────────────────────────────────────────────────────
# Seleccion de la muestra
# ──────────────────────────────────────────────────────────────────────────────

def _has_usable_archive(s: StationTerrain) -> bool:
    """
    True si la estacion EFECTIVAMENTE reporta.

    El criterio es la disponibilidad medida (`obs_probe`), no la metadata del
    IEM. Hubo que cambiarlo: `archive_begin` y `archive_end` indicaban que La
    Rioja (SANL) y Catamarca (SANC) estaban activas desde 1939 y sin fecha de
    cierre, pero al pedir los datos devuelven cero observaciones. De las 61
    estaciones argentinas del IEM, solo 25 emiten datos hoy.

    Esto importa mas de lo que parece: SANL era la mejor gemela de terreno del
    aerodromo del comitente (247 m de rugosidad contra 234 m) y no sirve.
    """
    return s.obs_probe > 0


def select_sample(strata: List[StationTerrain],
                  per_stratum: int = 3) -> List[StationTerrain]:
    """
    Elige hasta `per_stratum` estaciones por estrato, maximizando la diversidad
    REGIONAL dentro de cada uno: primero una estacion por region distinta, y
    recien despues se repite region.

    Esto responde a la regla de alcance del proyecto: la muestra tiene que cubrir
    distintas zonas del pais, no concentrarse donde el terreno es interesante.
    """
    chosen: List[StationTerrain] = []
    for name, _, _ in STRATA:
        pool = [s for s in strata if s.stratum == name and _has_usable_archive(s)]
        # mas rugoso primero dentro del estrato: son los casos exigentes
        pool.sort(key=lambda s: -(s.ruggedness_m or 0.0))
        seen_regions: set = set()
        picked: List[StationTerrain] = []
        for s in pool:                       # primera pasada: una por region
            if len(picked) >= per_stratum:
                break
            if s.region not in seen_regions:
                picked.append(s)
                seen_regions.add(s.region)
        for s in pool:                       # segunda pasada: completar
            if len(picked) >= per_stratum:
                break
            if s not in picked:
                picked.append(s)
        chosen.extend(picked)
    return chosen


# ──────────────────────────────────────────────────────────────────────────────
# Reporte standalone
# ──────────────────────────────────────────────────────────────────────────────

def _print_report(strata: List[StationTerrain]) -> None:
    print("\n" + "=" * 78)
    print("  ESTRATIFICACION POR RUGOSIDAD DEL TERRENO")
    print("  Rugosidad = desvio estandar de elevacion SRTM en +/- "
          f"{RADIUS_KM:.0f} km ({GRID_N}x{GRID_N} puntos)")
    print("=" * 78)

    print(f"\n  {'ICAO':5s} {'estacion':<26s} {'elev':>6s} {'rugos.':>8s} "
          f"{'relieve':>8s}  {'estrato':<10s} region")
    print("  " + "-" * 74)
    for s in sorted(strata, key=lambda x: -(x.ruggedness_m or -1)):
        print(f"  {s.icao:5s} {s.name[:26]:<26s} "
              f"{(s.elevation_m if s.elevation_m is not None else 0):6.0f} "
              f"{(s.ruggedness_m if s.ruggedness_m is not None else 0):8.1f} "
              f"{(s.relief_m if s.relief_m is not None else 0):8.1f}  "
              f"{(s.stratum or '?'):<10s} {s.region}")

    print("\n  RESUMEN POR ESTRATO")
    print("  " + "-" * 74)
    for name, lo, hi in STRATA:
        grp = [s for s in strata if s.stratum == name]
        hi_s = "inf" if hi == float("inf") else f"{hi:.0f}"
        act = [s for s in grp if _has_usable_archive(s)]
        regs = sorted({s.region for s in act})
        print(f"    {name:<10s} [{lo:>5.0f}, {hi_s:>5s}) m   "
              f"{len(grp):3d} estaciones, {len(act):2d} activas   "
              f"regiones activas: {', '.join(regs) if regs else '-'}")
    sin = [s for s in strata if s.stratum is None]
    if sin:
        print(f"    {'SIN DATO':<10s} {'':>15s}   {len(sin):3d} estaciones")

    print("\n  MUESTRA SELECCIONADA (3 por estrato, diversidad regional)")
    print("  " + "-" * 74)
    for s in select_sample(strata):
        print(f"    {s.icao:5s} {s.name[:24]:<24s} {(s.stratum or '?'):<10s} "
              f"{s.region:<10s} rugosidad {s.ruggedness_m:.1f} m  "
              f"(archivo desde {s.archive_begin})")
    _print_target_airports(strata)
    print("=" * 78)


def _print_target_airports(strata: List[StationTerrain]) -> None:
    """
    Ubica en un estrato a aerodromos SIN estacion, para los que el estudio se
    extrapola. Se incluye el del comitente porque es el caso que motiva la
    objecion: opera en las Sierras Chicas y no tiene METAR propio.
    """
    try:
        from data.airports import AIRPORTS
    except Exception:
        return

    targets = ["SACC"]          # LA CUMBRE — base del comitente
    print("\n  EXTRAPOLACION A AERODROMOS SIN ESTACION")
    print("  " + "-" * 74)
    for code in targets:
        ap = AIRPORTS.get(code)
        if ap is None:
            continue
        rug = ruggedness_for_point(ap.lat, ap.lon)
        stratum = classify(rug)
        print(f"    {code:5s} {ap.name[:24]:<24s} rugosidad "
              f"{rug if rug is not None else '--'} m  -> estrato {stratum or '?'}")
        peers = [s for s in strata
                 if s.stratum == stratum and _has_usable_archive(s)]
        if peers:
            peers.sort(key=lambda s: abs((s.ruggedness_m or 0) - (rug or 0)))
            lista = ", ".join(f"{p.icao}({p.ruggedness_m:.0f}m)" for p in peers[:5])
            print(f"          estaciones del mismo estrato para medir el error: {lista}")
        else:
            print("          ATENCION: no hay estaciones con archivo en este estrato")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  %(levelname)s %(message)s")
    refresh = "--refresh" in sys.argv

    data = [] if refresh else load()
    if data and "--reclassify" in sys.argv:
        data = reclassify(data)
        save(data)
        print("  Estratos y regiones recalculados desde el cache (sin tocar la API).")
    if not data:
        print("  Calculando rugosidad (consume la API de terreno, ~1 min)...\n")
        stations = fetch_iem_stations()
        if not stations:
            print("  ERROR: no se pudo obtener el listado de estaciones del IEM.")
            sys.exit(1)
        data = build_strata(stations)
        save(data)
    else:
        print(f"  Estratificacion cargada de {CACHE_PATH} "
              f"({len(data)} estaciones). Usar --refresh para recalcular.")

    _print_report(data)
