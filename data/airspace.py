"""
airspace.py
===========
Zonas de espacio aereo de Argentina para el sistema VFR GO/NO GO.

Fuente primaria  : data/ar-airspace.json  (cache descargado desde OpenAIP)
Fuente de respaldo: zonas hardcodeadas de la provincia de Cordoba (15 zonas)

Para actualizar la base de datos nacional, ejecutar:
    python data/fetcher_openaip.py --api-key TU_KEY

Tipos de zona:
  CTR  - Control Zone (zona de control de aeropuerto)
  TMA  - Terminal Maneuvering Area
  R    - Restricted Area (zona restringida)
  P    - Prohibited Area (zona prohibida)
  D    - Danger Area (zona peligrosa)

La funcion route_intersects_zone() soporta tanto geometria circular (radio_km)
como geometria poligonal real (campo polygon). OpenAIP provee poligonos precisos.
"""

import json
import logging
import os
from dataclasses import dataclass
from math import radians, cos, sqrt
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from route.performance import haversine_km
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from route.performance import haversine_km

_DATA_DIR   = os.path.dirname(os.path.abspath(__file__))
_CACHE_PATH = os.path.join(_DATA_DIR, "ar-airspace.json")


# ────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AirspaceZone:
    name          : str    # Nombre oficial de la zona
    zone_type     : str    # "CTR", "TMA", "R", "P", "D"
    is_restricted : bool   # True = zona restringida (no controlada)
    is_controlled : bool   # True = espacio aereo controlado (CTR/TMA)
    center_lat    : float  # Latitud del centro (o centroide del poligono)
    center_lon    : float  # Longitud del centro
    radius_km     : float  # Radio del circulo, o radio del circulo circunscrito si hay poligono
    floor_ft      : int    # Piso de la zona, ft MSL (0 = superficie)
    ceiling_ft    : int    # Techo de la zona, ft MSL
    notes         : str                                        = ""
    polygon       : Optional[Tuple[Tuple[float, float], ...]] = None
    # polygon: tupla de pares (lat, lon) que define el poligono real de la zona.
    # Si es None, se usa geometria circular (center_lat/center_lon + radius_km).


# ────────────────────────────────────────────────────────────────────────────
# Datos de respaldo — provincia de Cordoba (15 zonas)
# Se usan cuando no existe el cache de OpenAIP.
# ────────────────────────────────────────────────────────────────────────────

_CORDOBA_FALLBACK: List[AirspaceZone] = [

    AirspaceZone(
        name="CTR Cordoba (SACO)",
        zone_type="CTR", is_restricted=False, is_controlled=True,
        center_lat=-31.323611, center_lon=-64.208055,
        radius_km=25.0, floor_ft=0, ceiling_ft=3000,
        notes="CTR del Aeropuerto Internacional Ingeniero Ambrosio Taravella (SACO)",
    ),
    AirspaceZone(
        name="TMA Cordoba",
        zone_type="TMA", is_restricted=False, is_controlled=True,
        center_lat=-31.323611, center_lon=-64.208055,
        radius_km=60.0, floor_ft=3000, ceiling_ft=9500,
        notes="TMA Cordoba — espacio terminal que rodea al CTR de SACO",
    ),
    AirspaceZone(
        name="CTR Villa Maria (SAVM)",
        zone_type="CTR", is_restricted=False, is_controlled=True,
        center_lat=-32.413333, center_lon=-63.238333,
        radius_km=10.0, floor_ft=0, ceiling_ft=2500,
        notes="CTR del Aeropuerto Villa Maria",
    ),
    AirspaceZone(
        name="CTR Rio Cuarto (SAOC)",
        zone_type="CTR", is_restricted=False, is_controlled=True,
        center_lat=-33.085278, center_lon=-64.261111,
        radius_km=15.0, floor_ft=0, ceiling_ft=2500,
        notes="CTR del Aeropuerto Almirante Zar — Rio Cuarto",
    ),
    AirspaceZone(
        name="R 20 — Fabrica Militar Rio Tercero",
        zone_type="R", is_restricted=True, is_controlled=False,
        center_lat=-32.170117, center_lon=-64.089228,
        radius_km=8.0, floor_ft=0, ceiling_ft=5000,
        notes="Zona restringida sobre Fabrica Militar de Rio Tercero",
    ),
    AirspaceZone(
        name="R 21 — Complejo Batan",
        zone_type="R", is_restricted=True, is_controlled=False,
        center_lat=-31.650000, center_lon=-64.750000,
        radius_km=5.0, floor_ft=0, ceiling_ft=5000,
        notes="Zona restringida militar — area de maniobras Sierras Chicas",
    ),
    AirspaceZone(
        name="R 22 — Las Lajas",
        zone_type="R", is_restricted=True, is_controlled=False,
        center_lat=-31.200000, center_lon=-65.100000,
        radius_km=6.0, floor_ft=0, ceiling_ft=8000,
        notes="Zona restringida en sierra alta — actividades militares",
    ),
    AirspaceZone(
        name="P 10 — Area Prohibida Presidencial",
        zone_type="P", is_restricted=True, is_controlled=False,
        center_lat=-31.416667, center_lon=-64.183333,
        radius_km=3.0, floor_ft=0, ceiling_ft=99999,
        notes="Zona prohibida sobre Casa de Gobierno de Cordoba capital",
    ),
    AirspaceZone(
        name="R 30 — Polvorines Cordoba",
        zone_type="R", is_restricted=True, is_controlled=False,
        center_lat=-31.520000, center_lon=-64.400000,
        radius_km=4.0, floor_ft=0, ceiling_ft=3000,
        notes="Zona restringida sobre deposito de explosivos",
    ),
    AirspaceZone(
        name="R 25 — Embalse Nuclear",
        zone_type="R", is_restricted=True, is_controlled=False,
        center_lat=-32.200000, center_lon=-64.420000,
        radius_km=10.0, floor_ft=0, ceiling_ft=5000,
        notes="Zona restringida sobre Central Nuclear Embalse",
    ),
    AirspaceZone(
        name="D 05 — Zona de Lanzamiento Jesus Maria",
        zone_type="D", is_restricted=False, is_controlled=False,
        center_lat=-30.980000, center_lon=-64.100000,
        radius_km=15.0, floor_ft=0, ceiling_ft=18000,
        notes="Zona de peligro — actividad de paracaidismo y lanzamientos",
    ),
    AirspaceZone(
        name="D 06 — Entrenamiento Militar Cruz del Eje",
        zone_type="D", is_restricted=False, is_controlled=False,
        center_lat=-30.730000, center_lon=-64.800000,
        radius_km=20.0, floor_ft=0, ceiling_ft=10000,
        notes="Zona de peligro — maniobras militares activas",
    ),
    AirspaceZone(
        name="D 07 — Area de Vuelo Bajo La Carlota",
        zone_type="D", is_restricted=False, is_controlled=False,
        center_lat=-33.420000, center_lon=-63.300000,
        radius_km=25.0, floor_ft=0, ceiling_ft=5000,
        notes="Zona de peligro — fumigacion aerea y vuelo agricola bajo",
    ),
    AirspaceZone(
        name="D 08 — Poligono de Tiro Villa Allende",
        zone_type="D", is_restricted=False, is_controlled=False,
        center_lat=-31.290000, center_lon=-64.290000,
        radius_km=5.0, floor_ft=0, ceiling_ft=4000,
        notes="Zona de peligro — poligono de tiro de Fuerza Aerea Argentina",
    ),
    AirspaceZone(
        name="D 09 — Zona de Maniobras San Francisco",
        zone_type="D", is_restricted=False, is_controlled=False,
        center_lat=-31.450000, center_lon=-62.080000,
        radius_km=18.0, floor_ft=0, ceiling_ft=8000,
        notes="Zona de peligro — ejercicios militares area este provincia",
    ),
]


# ────────────────────────────────────────────────────────────────────────────
# Conversor de cache OpenAIP
# ────────────────────────────────────────────────────────────────────────────

# Mapeo de tipo OpenAIP (int) → (zone_type, is_restricted, is_controlled)
# Basado en la documentacion de OpenAIP v2 API.
# Tipos omitidos (FIR, UIR, airways, clases de espacio aereo): no relevantes para routing VFR.
_OPENAIP_TYPES = {
    1 : ("R",   True,  False),   # RESTRICTED
    2 : ("D",   False, False),   # DANGER
    3 : ("P",   True,  False),   # PROHIBITED
    4 : ("CTR", False, True),    # CTR
    5 : ("CTR", False, True),    # TMZ (Transponder Mandatory Zone)
    6 : ("CTR", False, True),    # RMZ (Radio Mandatory Zone)
    7 : ("TMA", False, True),    # TMA
    8 : ("R",   True,  False),   # TRA (Temporary Reserved Area)
    9 : ("R",   True,  False),   # TSA (Temporary Segregated Area)
    12: ("R",   True,  False),   # ADIZ (Air Defense Identification Zone)
    13: ("CTR", False, True),    # ATZ (Aerodrome Traffic Zone)
    14: ("CTR", False, True),    # MATZ (Military ATZ)
}

_SKIP_OPENAIP_TYPES = {0, 10, 11, 15, 16, 17, 18, 19, 20, 21, 22}
# 0=unclassified, 10=FIR, 11=UIR, 15-22=airspace classes B-G / airways


def _openaip_altitude_ft(limit: dict) -> int:
    """Convierte un objeto de altitud de OpenAIP a pies MSL."""
    if not limit:
        return 0
    value = int(limit.get("value", 0))
    unit  = limit.get("unit", 0)   # 0=ft, 1=m, 6=FL (flight level)
    if unit == 6:
        return value * 100
    elif unit == 1:
        return int(value * 3.28084)
    return value


def _load_from_openaip_cache(cache_path: str) -> List[AirspaceZone]:
    """
    Convierte el cache raw de OpenAIP en una lista de AirspaceZone.
    Filtra tipos irrelevantes para vuelos VFR (FIR, UIR, airways, etc.).

    GeoJSON usa coordenadas [longitud, latitud]; esta funcion invierte el orden
    a (latitud, longitud) para el resto del sistema.

    Retorna lista vacia si el archivo no existe o esta vacio.
    """
    if not os.path.exists(cache_path):
        return []

    try:
        with open(cache_path, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"Error leyendo cache OpenAIP ({cache_path}): {e}")
        return []

    zones   : List[AirspaceZone] = []
    skipped : int = 0

    for item in raw:
        type_code = item.get("type")

        if type_code in _SKIP_OPENAIP_TYPES:
            skipped += 1
            continue

        type_info = _OPENAIP_TYPES.get(type_code)
        if type_info is None:
            skipped += 1
            continue

        zone_type, is_restricted, is_controlled = type_info
        name = item.get("name", "Sin nombre").strip()

        # ── Geometria ──────────────────────────────────────────────────────
        geom      = item.get("geometry", {})
        geom_type = geom.get("type", "")
        coords    = geom.get("coordinates", [])

        polygon    : Optional[Tuple[Tuple[float, float], ...]] = None
        center_lat  = 0.0
        center_lon  = 0.0
        radius_km   = 5.0  # valor minimo por defecto

        if geom_type == "Polygon" and coords:
            # GeoJSON exterior ring: [[lon, lat], ...]
            ring = coords[0]
            poly_points = tuple((float(pt[1]), float(pt[0])) for pt in ring if len(pt) >= 2)
            if len(poly_points) >= 3:
                polygon    = poly_points
                lats       = [p[0] for p in poly_points]
                lons       = [p[1] for p in poly_points]
                center_lat = sum(lats) / len(lats)
                center_lon = sum(lons) / len(lons)
                # Radio circunscrito = distancia maxima al centroide
                radius_km  = max(
                    haversine_km(center_lat, center_lon, lat, lon)
                    for lat, lon in poly_points
                )

        elif geom_type == "Point" and len(coords) >= 2:
            center_lon, center_lat = float(coords[0]), float(coords[1])
            radius_km = 10.0  # radio generico para zonas puntuales

        elif geom_type == "MultiPolygon" and coords:
            # Usar el primer poligono del multi-poligono
            ring = coords[0][0]
            poly_points = tuple((float(pt[1]), float(pt[0])) for pt in ring if len(pt) >= 2)
            if len(poly_points) >= 3:
                polygon    = poly_points
                lats       = [p[0] for p in poly_points]
                lons       = [p[1] for p in poly_points]
                center_lat = sum(lats) / len(lats)
                center_lon = sum(lons) / len(lons)
                radius_km  = max(
                    haversine_km(center_lat, center_lon, lat, lon)
                    for lat, lon in poly_points
                )

        if center_lat == 0.0 and center_lon == 0.0:
            # Geometria invalida o sin coordenadas utiles
            skipped += 1
            continue

        # ── Altitudes ──────────────────────────────────────────────────────
        floor_ft   = _openaip_altitude_ft(item.get("lowerLimit", {}))
        ceiling_ft = _openaip_altitude_ft(item.get("upperLimit", {}))
        if ceiling_ft == 0:
            ceiling_ft = 99999

        zones.append(AirspaceZone(
            name          = name,
            zone_type     = zone_type,
            is_restricted = is_restricted,
            is_controlled = is_controlled,
            center_lat    = round(center_lat, 6),
            center_lon    = round(center_lon, 6),
            radius_km     = round(radius_km, 2),
            floor_ft      = floor_ft,
            ceiling_ft    = ceiling_ft,
            notes         = f"OpenAIP (type={type_code})",
            polygon       = polygon,
        ))

    logger.info(
        f"OpenAIP: {len(zones)} zonas cargadas, {skipped} omitidas "
        f"(FIR/UIR/airways/clases de espacio aereo)"
    )
    return zones


# ────────────────────────────────────────────────────────────────────────────
# Carga al importar
# ────────────────────────────────────────────────────────────────────────────

def _build_airspace_zones() -> List[AirspaceZone]:
    """
    Intenta cargar desde el cache de OpenAIP.
    Si no existe o esta vacio, usa los datos de respaldo de Cordoba (15 zonas).
    """
    zones = _load_from_openaip_cache(_CACHE_PATH)
    if zones:
        logger.info(f"Espacio aereo cargado desde OpenAIP: {len(zones)} zonas nacionales")
        return zones
    logger.info(
        "Cache OpenAIP no encontrado — usando datos de respaldo de Cordoba (15 zonas). "
        "Para cobertura nacional: python data/fetcher_openaip.py --api-key TU_KEY"
    )
    return list(_CORDOBA_FALLBACK)


AIRSPACE_ZONES: List[AirspaceZone] = _build_airspace_zones()

RESTRICTED_ZONES = [z for z in AIRSPACE_ZONES if z.is_restricted]
CONTROLLED_ZONES = [z for z in AIRSPACE_ZONES if z.is_controlled]
DANGER_ZONES     = [z for z in AIRSPACE_ZONES if z.zone_type == "D"]


# ────────────────────────────────────────────────────────────────────────────
# Geometria de interseccion
# ────────────────────────────────────────────────────────────────────────────

def _point_to_segment_distance_km(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """
    Distancia minima de un punto (px,py) a un segmento (ax,ay)-(bx,by).
    Coordenadas en km proyectados localmente (valido para areas pequenas).
    """
    dx, dy     = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t      = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


def _point_in_polygon(px: float, py: float, polygon_xy: List[Tuple[float, float]]) -> bool:
    """
    Ray-casting: retorna True si el punto (px, py) esta dentro del poligono.
    Coordenadas en proyeccion local (km).
    """
    inside = False
    n      = len(polygon_xy)
    j      = n - 1
    for i in range(n):
        xi, yi = polygon_xy[i]
        xj, yj = polygon_xy[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _cross2d(ox: float, oy: float, ax: float, ay: float, bx: float, by: float) -> float:
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def _segments_intersect(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> bool:
    """
    Retorna True si los segmentos AB y CD se intersectan (propiamente).
    Usa el metodo de producto vectorial.
    """
    d1 = _cross2d(cx, cy, dx, dy, ax, ay)
    d2 = _cross2d(cx, cy, dx, dy, bx, by)
    d3 = _cross2d(ax, ay, bx, by, cx, cy)
    d4 = _cross2d(ax, ay, bx, by, dx, dy)
    return (
        ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and
        ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0))
    )


def _segment_intersects_polygon(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    zone: AirspaceZone,
) -> bool:
    """
    Interseccion segmento-poligono con proyeccion equirectangular local.
    El poligono se proyecta desde el centroide de la zona.
    """
    R_km  = 6371.0
    lat_c = radians(zone.center_lat)

    def to_xy(lat: float, lon: float) -> Tuple[float, float]:
        dlat = radians(lat - zone.center_lat)
        dlon = radians(lon - zone.center_lon)
        return dlon * R_km * cos(lat_c), dlat * R_km

    p1 = to_xy(lat1, lon1)
    p2 = to_xy(lat2, lon2)

    assert zone.polygon is not None
    poly = [to_xy(lat, lon) for lat, lon in zone.polygon]

    # Extremo de la ruta dentro del poligono
    if _point_in_polygon(p1[0], p1[1], poly) or _point_in_polygon(p2[0], p2[1], poly):
        return True

    # Aristas del poligono que cruzan el segmento de ruta
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if _segments_intersect(p1[0], p1[1], p2[0], p2[1], ax, ay, bx, by):
            return True

    return False


def route_intersects_zone(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    zone: AirspaceZone,
) -> bool:
    """
    Determina si el segmento de ruta (lat1,lon1)-(lat2,lon2) intersecta la zona.

    Usa geometria poligonal si zone.polygon esta definido (datos OpenAIP);
    en caso contrario usa geometria circular (datos de respaldo de Cordoba).

    Precision suficiente para segmentos < 400 km en latitudes medias.
    """
    if zone.polygon is not None:
        return _segment_intersects_polygon(lat1, lon1, lat2, lon2, zone)

    # Geometria circular — proyeccion equirectangular local
    R_km  = 6371.0
    lat_c = radians(zone.center_lat)

    def to_xy(lat: float, lon: float) -> Tuple[float, float]:
        dlat = radians(lat - zone.center_lat)
        dlon = radians(lon - zone.center_lon)
        return dlon * R_km * cos(lat_c), dlat * R_km

    x1, y1 = to_xy(lat1, lon1)
    x2, y2 = to_xy(lat2, lon2)
    dist   = _point_to_segment_distance_km(0.0, 0.0, x1, y1, x2, y2)
    return dist <= zone.radius_km


def zones_along_route(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    check_restricted: bool = True,
    check_controlled: bool = True,
) -> List[AirspaceZone]:
    """
    Retorna lista de zonas que intersectan el segmento de ruta.

    check_restricted : incluir zonas R y P
    check_controlled : incluir zonas CTR y TMA
    Las zonas D (peligro) siempre se incluyen.
    """
    result = []
    for zone in AIRSPACE_ZONES:
        if zone.is_restricted and not check_restricted:
            continue
        if zone.is_controlled and not check_controlled:
            continue
        if route_intersects_zone(lat1, lon1, lat2, lon2, zone):
            result.append(zone)
    return result


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: data/airspace.py")
    print("=" * 60)

    using_openaip = os.path.exists(_CACHE_PATH)
    print(f"\n  Fuente: {'OpenAIP cache' if using_openaip else 'Cordoba fallback'}")
    print(f"  Zonas totales     : {len(AIRSPACE_ZONES)}")
    print(f"  Zonas restringidas: {len(RESTRICTED_ZONES)}")
    print(f"  Zonas controladas : {len(CONTROLLED_ZONES)}")
    print(f"  Zonas de peligro  : {len(DANGER_ZONES)}")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    check(f"Al menos 5 zonas cargadas  (got {len(AIRSPACE_ZONES)})", len(AIRSPACE_ZONES) >= 5)
    check(f"Zonas restringidas >= 2  (got {len(RESTRICTED_ZONES)})", len(RESTRICTED_ZONES) >= 2)
    check(f"Zonas controladas >= 2  (got {len(CONTROLLED_ZONES)})", len(CONTROLLED_ZONES) >= 2)

    # Buscar el CTR de Cordoba/SACO (presente en ambas fuentes)
    saco = next((z for z in AIRSPACE_ZONES if "SACO" in z.name or
                 ("CTR" in z.zone_type and
                  abs(z.center_lat - (-31.32)) < 0.2 and
                  abs(z.center_lon - (-64.21)) < 0.2)), None)
    check("CTR Cordoba (SACO) presente", saco is not None)

    if saco:
        check("CTR SACO es zona controlada", saco.is_controlled)
        check("CTR SACO no es restringida",  not saco.is_restricted)
        check("CTR SACO radio > 5 km",       saco.radius_km > 5.0)

    # route_intersects_zone — caso positivo: ruta cruzando el CTR SACO
    if saco:
        intersects = route_intersects_zone(-32.0, -64.2, -30.5, -64.2, saco)
        check("Ruta por centro CTR SACO intersecta", intersects)

        # Caso negativo: ruta lejos al este
        no_inter = route_intersects_zone(-32.7, -62.7, -33.3, -62.2, saco)
        check("Ruta lejana no intersecta CTR SACO", not no_inter)

    # zones_along_route — ruta SACC-SAAG
    zones = zones_along_route(-31.010853, -64.526899, -31.657347, -64.397470)
    print(f"\n  Zonas en ruta SACC-SAAG: {[z.name for z in zones]}")
    check("zones_along_route retorna lista", isinstance(zones, list))

    # Geometria auxiliar
    d = _point_to_segment_distance_km(0, 0, -1, 0, 1, 0)
    check(f"Punto sobre segmento = dist 0  (got {d:.3f})", d == 0.0)
    d2 = _point_to_segment_distance_km(0, 5, -1, 0, 1, 0)
    check(f"Punto perpendicular = dist 5  (got {d2:.3f})", abs(d2 - 5.0) < 0.001)

    # Ray-casting
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    check("Punto dentro del cuadrado", _point_in_polygon(0.5, 0.5, square))
    check("Punto fuera del cuadrado",  not _point_in_polygon(2.0, 2.0, square))

    # Interseccion de segmentos
    check("Segmentos que se cruzan",
          _segments_intersect(0, 0, 2, 2, 0, 2, 2, 0))
    check("Segmentos paralelos no se cruzan",
          not _segments_intersect(0, 0, 2, 0, 0, 1, 2, 1))

    print("\n  Muestra de zonas (primeras 10):")
    for z in AIRSPACE_ZONES[:10]:
        flag  = "[R]" if z.is_restricted else "[C]" if z.is_controlled else "[D]"
        pgon  = "poly" if z.polygon else "circ"
        print(f"    {flag} {pgon} {z.name:<45}  r={z.radius_km:>6.1f}km  "
              f"{z.floor_ft:>5}-{z.ceiling_ft:>6}ft")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
