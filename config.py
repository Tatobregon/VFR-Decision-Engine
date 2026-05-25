"""
config.py
=========
Constantes globales del sistema VFR GO/NO GO.

NWP_STATIONS se construye automaticamente desde data/airports.py.
Para agregar o modificar un aeropuerto, editar solo ese archivo.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.airports import AIRPORTS


# ──────────────────────────────────────────────────────────────────────────────
# Estaciones NWP (Open-Meteo) — todos los aerodromos usan esta fuente
# ──────────────────────────────────────────────────────────────────────────────

NWP_STATIONS: dict = {
    code: {
        "name"   : info.name,
        "lat"    : info.lat,
        "lon"    : info.lon,
        "elev_m" : int(info.elev_ft * 0.3048),   # Open-Meteo requiere metros
    }
    for code, info in AIRPORTS.items()
}

# Sin estaciones METAR en v1.0: todos los aerodromos son rurales sin METAR propio
METAR_STATIONS: frozenset = frozenset()


# ──────────────────────────────────────────────────────────────────────────────
# Parametros de fetch
# ──────────────────────────────────────────────────────────────────────────────

NWP_HOURS_AHEAD = 12


# ──────────────────────────────────────────────────────────────────────────────
# Aeronave por defecto
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_AIRCRAFT = "ALPHA_TRAINER"


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  CONFIG")
    print("=" * 60)

    print(f"\n  Aerodromos NWP  : {len(NWP_STATIONS)}")
    print(f"  METAR stations  : {len(METAR_STATIONS)}  (no usadas en v1.0)")
    print(f"  NWP horas ahead : {NWP_HOURS_AHEAD}")
    print(f"  Aeronave defecto: {DEFAULT_AIRCRAFT}")

    print("\n  Detalle estaciones NWP:")
    for sid, cfg in NWP_STATIONS.items():
        print(f"    {sid:<6}  {cfg['name']:<20}  "
              f"lat={cfg['lat']:+.6f}  lon={cfg['lon']:+.6f}  {cfg['elev_m']:>4} m")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    print()
    check("SACC en NWP_STATIONS",
          "SACC" in NWP_STATIONS)
    check(f"710 aerodromos registrados  (got {len(NWP_STATIONS)})",
          len(NWP_STATIONS) == 710)
    check("METAR_STATIONS vacio",
          len(METAR_STATIONS) == 0)
    sacc = NWP_STATIONS.get("SACC", {})
    check("SACC lat ~-31.0",
          abs(sacc.get("lat", 0) - (-31.0)) < 0.1)
    check("SACC lon ~-64.5",
          abs(sacc.get("lon", 0) - (-64.5)) < 0.1)
    check("SACC elev_m > 0",
          sacc.get("elev_m", 0) > 0)
    check("NWP_HOURS_AHEAD > 0",
          NWP_HOURS_AHEAD > 0)
    check("Todos tienen lat/lon/elev/name",
          all("lat" in c and "lon" in c and "elev_m" in c and "name" in c
              for c in NWP_STATIONS.values()))

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
