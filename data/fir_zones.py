"""
fir_zones.py
============
Carga FIRs_Argenina.geojson y determina en que FIR cae un punto geografico.

Las 5 FIRs de Argentina segun la data:
  FIR COMODORO     — Patagonia
  FIR CORDOBA      — Centro-Oeste (Cordoba, Cuyo, NOA)
  FIR EZEIZA       — Pampa / Buenos Aires / Litoral
  FIR MENDOZA      — Cordillera / Cuyo
  FIR RESISTENCIA  — NEA (Chaco, Misiones, Corrientes, Formosa)

La asignacion de ATC al piloto usa el nombre de la FIR + "Control":
  FIR CORDOBA  → "Córdoba Control"
  FIR EZEIZA   → "Buenos Aires Control"
  FIR MENDOZA  → "Mendoza Control"
  FIR RESISTENCIA → "Resistencia Control"
  FIR COMODORO → "Comodoro Control"
"""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent

# Nombres de contacto ATC por FIR
_FIR_CONTACT = {
    "FIR COMODORO":    "Comodoro Control",
    "FIR CORDOBA":     "Córdoba Control",
    "FIR EZEIZA":      "Buenos Aires Control",
    "FIR MENDOZA":     "Mendoza Control",
    "FIR RESISTENCIA": "Resistencia Control",
}


def _ray_casting(lat: float, lon: float, polygon: list) -> bool:
    """
    Algoritmo de ray-casting para punto-en-polígono.
    polygon: lista de [lon, lat] (orden GeoJSON).
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _load() -> list:
    path = _DATA_DIR / "FIRs_Argenina.geojson"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    firs = []
    for feat in data["features"]:
        props = feat.get("properties", feat)
        name = props.get("name") or feat.get("name", "")
        coords = feat["geometry"]["coordinates"][0]
        firs.append({"name": name, "polygon": coords})
    return firs


_FIRS = _load()


def get_fir(lat: float, lon: float) -> str:
    """
    Devuelve el nombre del contacto ATC para la FIR que contiene (lat, lon).
    Si el punto no cae en ninguna FIR (oceano, limite) devuelve el mas cercano
    por bounding-box como fallback.
    """
    for fir in _FIRS:
        if _ray_casting(lat, lon, fir["polygon"]):
            return _FIR_CONTACT.get(fir["name"], fir["name"])

    # Fallback: FIR con centroide mas cercano (simplificado por lat)
    if lat < -42:
        return _FIR_CONTACT["FIR COMODORO"]
    if lon < -68:
        return _FIR_CONTACT["FIR MENDOZA"]
    if lat > -28:
        return _FIR_CONTACT["FIR RESISTENCIA"]
    if lon < -64:
        return _FIR_CONTACT["FIR CORDOBA"]
    return _FIR_CONTACT["FIR EZEIZA"]
