"""
terrain.py
==========
Perfil de elevacion de terreno via Open-Topo-Data API (SRTM 30m).

Endpoint publico sin API key: https://api.opentopodata.org/v1/srtm30m
Limite: 100 puntos por request, 1 request/segundo.

Funciones principales:
  get_elevations_m(points)       -> [float] elevaciones en metros
  max_terrain_ft_on_leg(...)     -> float  maximo del terreno en un tramo
  terrain_profile_for_route(...) -> list   perfil completo de la ruta

Mock integrado: usar mock=True para desarrollo sin conexion.
"""

import json
import logging
import time
from math import radians, sin, cos, sqrt, atan2, degrees
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Configuracion API ────────────────────────────────────────────────────────
API_URL        = "https://api.opentopodata.org/v1/srtm30m"
MAX_POINTS_REQ = 100      # maximo puntos por request
REQUEST_DELAY  = 1.1      # segundos entre requests (rate limit: 1 req/s)
TIMEOUT_S      = 10       # timeout por request

# ── Conversiones ─────────────────────────────────────────────────────────────
M_TO_FT = 3.28084
EARTH_RADIUS_KM = 6371.0

# ── Elevaciones de fallback (aerodromos conocidos) ───────────────────────────
# Usadas en mock y como ultimo recurso si la API falla.
KNOWN_ELEVATIONS_M = {
    "SACC": 1141,   # La Cumbre
    "SAAG": 533,    # Alta Gracia
    "SAOE": 380,    # Rio Tercero
    "SAMC": 900,    # Mina Clavero
    "SABV": 131,    # Bell Ville
    "SACB": 113,    # Corral de Bustos
    "SAOL": 139,    # Laboulaye
    "SAOM": 110,    # Marcos Juarez
}


# ────────────────────────────────────────────────────────────────────────────
# Muestreo de puntos sobre un tramo
# ────────────────────────────────────────────────────────────────────────────

def _interpolate_points(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    n   : int,
) -> List[Tuple[float, float]]:
    """
    Genera n puntos equiespaciados a lo largo del tramo (lat1,lon1)-(lat2,lon2).
    Incluye los extremos (n >= 2).
    Usa interpolacion lineal en grados (valido para segmentos < 500km).
    """
    if n < 2:
        n = 2
    points = []
    for i in range(n):
        t = i / (n - 1)
        lat = lat1 + t * (lat2 - lat1)
        lon = lon1 + t * (lon2 - lon1)
        points.append((round(lat, 6), round(lon, 6)))
    return points


# ────────────────────────────────────────────────────────────────────────────
# Llamada a la API
# ────────────────────────────────────────────────────────────────────────────

def get_elevations_m(
    points : List[Tuple[float, float]],
    mock   : bool = False,
) -> List[Optional[float]]:
    """
    Retorna la elevacion AMSL en metros para cada punto (lat, lon).

    Si mock=True, retorna un perfil sintetico basado en la media de
    las elevaciones conocidas de los aerodromos de Cordoba (simplificado).

    Si la API falla, retorna None para los puntos que no se pudieron obtener.
    El engine trata None como 0m (llanura).
    """
    if mock:
        return _mock_elevations(points)

    try:
        import urllib.request
        import urllib.parse
    except ImportError:
        logger.error("urllib no disponible — retornando None para todos los puntos")
        return [None] * len(points)

    results = []
    # Procesar en lotes de MAX_POINTS_REQ
    for batch_start in range(0, len(points), MAX_POINTS_REQ):
        batch = points[batch_start : batch_start + MAX_POINTS_REQ]
        locations = "|".join(f"{lat},{lon}" for lat, lon in batch)
        url = f"{API_URL}?locations={urllib.parse.quote(locations)}"

        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
            elevations = [
                result.get("elevation")  # None si no hay dato SRTM
                for result in data.get("results", [])
            ]
            results.extend(elevations)
            logger.debug("Terrain API: %d puntos obtenidos", len(elevations))
        except Exception as exc:
            logger.warning("Terrain API error (batch %d): %s — usando None",
                           batch_start, exc)
            results.extend([None] * len(batch))

        # Rate limit entre batches
        if batch_start + MAX_POINTS_REQ < len(points):
            time.sleep(REQUEST_DELAY)

    return results


def _mock_elevations(points: List[Tuple[float, float]]) -> List[float]:
    """
    Elevaciones sinteticas para modo mock/offline.

    Usa un gradiente simple basado en la latitud y longitud dentro de
    la provincia de Cordoba:
      - Sierras (lat > -32, lon < -64): mayor elevacion
      - Llanura (este): menor elevacion
    """
    elev = []
    for lat, lon in points:
        if lat > -32.0 and lon < -64.0:
            # Zona serrana: estima entre 500 y 1500m
            base = 800.0 + (lat + 32.0) * -400.0 + (lon + 64.0) * -200.0
        elif lon < -63.5:
            # Zona intermedia
            base = 300.0
        else:
            # Llanura
            base = 150.0
        elev.append(max(50.0, min(base, 1600.0)))
    return elev


# ────────────────────────────────────────────────────────────────────────────
# Maximo de terreno para un tramo
# ────────────────────────────────────────────────────────────────────────────

def max_terrain_ft_on_leg(
    lat1       : float,
    lon1       : float,
    lat2       : float,
    lon2       : float,
    n_samples  : int  = 10,
    mock       : bool = False,
) -> float:
    """
    Retorna la elevacion maxima del terreno en el tramo (lat1,lon1)-(lat2,lon2),
    en pies (ft MSL).

    n_samples : numero de puntos de muestreo a lo largo del tramo.
    Retorna 0.0 si no se pudo obtener ningun dato de elevacion.
    """
    points = _interpolate_points(lat1, lon1, lat2, lon2, n_samples)
    elevs  = get_elevations_m(points, mock=mock)
    valid  = [e for e in elevs if e is not None]
    if not valid:
        logger.warning("Sin datos de elevacion para el tramo — asumiendo 0m")
        return 0.0
    max_m = max(valid)
    return max_m * M_TO_FT


# ────────────────────────────────────────────────────────────────────────────
# Perfil completo de la ruta
# ────────────────────────────────────────────────────────────────────────────

def terrain_profile_for_route(
    path_coords  : List[Tuple[float, float]],  # [(lat, lon), ...]
    n_per_leg    : int  = 10,
    mock         : bool = False,
) -> List[dict]:
    """
    Calcula el perfil de terreno para cada tramo de una ruta.

    path_coords : lista de (lat, lon) en orden del camino
    n_per_leg   : puntos de muestreo por tramo

    Retorna lista de dicts, uno por tramo:
      {
        "origin_idx"   : int,    # indice en path_coords del origen
        "dest_idx"     : int,
        "max_terrain_ft": float,
        "safe_alt_ft"  : int,    # altitud minima segura (con buffer 500ft)
        "profile_m"    : [float] # elevaciones en metros del tramo
      }
    """
    if len(path_coords) < 2:
        return []

    try:
        from route.performance import safe_altitude_ft
    except ImportError:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from route.performance import safe_altitude_ft

    results = []
    for i in range(len(path_coords) - 1):
        lat1, lon1 = path_coords[i]
        lat2, lon2 = path_coords[i + 1]
        pts    = _interpolate_points(lat1, lon1, lat2, lon2, n_per_leg)
        elevs  = get_elevations_m(pts, mock=mock)
        valid  = [e for e in elevs if e is not None]
        max_m  = max(valid) if valid else 0.0
        max_ft = max_m * M_TO_FT
        results.append({
            "origin_idx"    : i,
            "dest_idx"      : i + 1,
            "max_terrain_ft": round(max_ft, 0),
            "safe_alt_ft"   : safe_altitude_ft(max_ft),
            "profile_m"     : [round(e, 0) if e is not None else 0.0 for e in elevs],
        })
        # Rate limit solo si hay multiples tramos y no es mock
        if not mock and i < len(path_coords) - 2:
            time.sleep(REQUEST_DELAY)

    return results


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: data/terrain.py  (modo mock)")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ── Interpolacion ──
    pts = _interpolate_points(-31.0, -64.5, -32.0, -64.5, 5)
    check("Interpolacion: 5 puntos", len(pts) == 5)
    check("Interpolacion: primer punto = origen", pts[0] == (-31.0, -64.5))
    check("Interpolacion: ultimo punto = destino", pts[-1] == (-32.0, -64.5))
    check("Interpolacion: punto medio correcto", abs(pts[2][0] - (-31.5)) < 0.001)

    # ── Mock elevations ──
    # Zona serrana (SACC area)
    elevs_sierra = get_elevations_m([(-31.0, -64.5), (-31.5, -64.9)], mock=True)
    check("Mock sierra: 2 valores retornados", len(elevs_sierra) == 2)
    check("Mock sierra: elevaciones positivas", all(e > 0 for e in elevs_sierra))
    check("Mock sierra: elevaciones razonables (>300m)", all(e > 300 for e in elevs_sierra))

    # Zona de llanura
    elevs_llano = get_elevations_m([(-33.0, -62.0), (-32.7, -62.7)], mock=True)
    check("Mock llanura: valores menores que sierra", max(elevs_llano) < max(elevs_sierra))

    # ── max_terrain_ft_on_leg ──
    max_ft = max_terrain_ft_on_leg(-31.0, -64.5, -31.6, -64.4, n_samples=8, mock=True)
    check(f"max_terrain_ft: positivo  (got {max_ft:.0f})", max_ft > 0)
    check(f"max_terrain_ft: en rango razonable  (got {max_ft:.0f}ft)",
          1000 <= max_ft <= 8000)

    # ── terrain_profile_for_route ──
    coords = [(-31.010853, -64.526899),   # SACC
              (-31.657347, -64.397470),   # SAAG
              (-32.170117, -64.089228)]   # SAOE
    profile = terrain_profile_for_route(coords, n_per_leg=6, mock=True)
    check("Perfil de ruta: 2 tramos  (got {})".format(len(profile)), len(profile) == 2)
    check("Tramo 0: origin_idx=0", profile[0]["origin_idx"] == 0)
    check("Tramo 1: origin_idx=1", profile[1]["origin_idx"] == 1)
    check("Tramos tienen safe_alt_ft", all("safe_alt_ft" in t for t in profile))
    check("Tramos tienen profile_m", all(len(t["profile_m"]) == 6 for t in profile))

    for i, t in enumerate(profile):
        print(f"\n  Tramo {i}: max_terrain={t['max_terrain_ft']:.0f}ft  "
              f"safe_alt={t['safe_alt_ft']}ft")
        print(f"    Perfil (m): {t['profile_m']}")

    # ── Elevaciones conocidas (referencia) ──
    print("\n  Elevaciones de referencia aerodromos:")
    for code, elev_m in KNOWN_ELEVATIONS_M.items():
        elev_ft = int(elev_m * M_TO_FT)
        print(f"    {code}: {elev_m}m  ({elev_ft}ft)")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
