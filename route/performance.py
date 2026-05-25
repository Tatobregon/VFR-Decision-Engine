"""
performance.py
==============
Calculos de performance de vuelo para el optimizador de rutas VFR.

Todas las funciones son puras: sin I/O, sin efectos secundarios.
Las constantes son valores del Pipistrel Alpha Trainer (defaults para v1.0).
Las funciones que dependen de performance de aeronave aceptan un parametro
opcional AircraftProfile; si no se pasa, usan las constantes globales.
Unidades: km, kt, litros, horas, ft, grados.
"""

from math import radians, sin, cos, sqrt, atan2, degrees, ceil
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from risk.aircraft_profiles import AircraftProfile


# ── Constantes fisicas ──────────────────────────────────────────────────────
EARTH_RADIUS_KM = 6371.0
KT_TO_KMH       = 1.852
KMH_TO_KT       = 1.0 / KT_TO_KMH

# ── Perfil Alpha Trainer (datos POH) ────────────────────────────────────────
CRUISE_KT        = 97.0    # velocidad crucero tipica, kt
FUEL_FLOW_LPH    = 14.0    # consumo en crucero, L/hr
FUEL_CAPACITY_L  = 50.0    # tanque total, L
FUEL_RESERVE_MIN = 30      # reserva reglamentaria minima, minutos
FUEL_RESERVE_L   = FUEL_FLOW_LPH * (FUEL_RESERVE_MIN / 60.0)   # ~7 L
FUEL_USABLE_L    = FUEL_CAPACITY_L - FUEL_RESERVE_L             # ~43 L

# ── Parametros de ruta ──────────────────────────────────────────────────────
FUEL_STOP_THRESHOLD_KM = 500.0   # umbral para recomendar escala de combustible
TERRAIN_BUFFER_FT      = 500     # buffer minimo sobre terreno mas alto del tramo
MIN_GROUNDSPEED_KT     = 10.0    # piso para evitar division por cero


# ────────────────────────────────────────────────────────────────────────────
# Geometria
# ────────────────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia ortodromica entre dos puntos WGS84, en km."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi       = radians(lat2 - lat1)
    dlambda    = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2.0 * EARTH_RADIUS_KM * atan2(sqrt(a), sqrt(1.0 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Rumbo verdadero inicial de punto 1 hacia punto 2, en grados 0-360.
    Convencion: 0=Norte, 90=Este, 180=Sur, 270=Oeste.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    dlambda    = radians(lon2 - lon1)
    x = sin(dlambda) * cos(phi2)
    y = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlambda)
    return (degrees(atan2(x, y)) + 360.0) % 360.0


# ────────────────────────────────────────────────────────────────────────────
# Performance de vuelo
# ────────────────────────────────────────────────────────────────────────────

def effective_groundspeed_kt(
    cruise_kt   : float,
    wind_dir    : Optional[int],
    wind_spd_kt : Optional[float],
    track_deg   : float,
) -> float:
    """
    Velocidad sobre el suelo considerando componente de viento en ruta.

    wind_dir   : direccion DE DONDE sopla el viento (convencion meteorologica).
                 None si viento variable o desconocido.
    wind_spd_kt: velocidad del viento, kt. None si desconocido.
    track_deg  : rumbo de vuelo verdadero, grados.

    Positivo headwind_component = viento de frente = reduce groundspeed.
    """
    if wind_dir is None or wind_spd_kt is None or wind_spd_kt == 0.0:
        return cruise_kt
    headwind = wind_spd_kt * cos(radians(wind_dir - track_deg))
    return max(cruise_kt - headwind, MIN_GROUNDSPEED_KT)


def leg_time_hours(distance_km: float, groundspeed_kt: float) -> float:
    """Tiempo de vuelo de un tramo, en horas."""
    gs_kmh = max(groundspeed_kt, MIN_GROUNDSPEED_KT) * KT_TO_KMH
    return distance_km / gs_kmh


def leg_fuel_liters(
    time_hours    : float,
    fuel_flow_lph : float = FUEL_FLOW_LPH,
) -> float:
    """Combustible consumido en un tramo, en litros."""
    return time_hours * fuel_flow_lph


def safe_altitude_ft(
    terrain_max_ft : float,
    buffer_ft      : int = TERRAIN_BUFFER_FT,
) -> int:
    """
    Altitud minima segura para un tramo dado el terreno mas alto.
    Redondea al siguiente multiplo de 500 ft por encima del buffer.
    """
    minimum = terrain_max_ft + buffer_ft
    return int(ceil(minimum / 500) * 500)


def needs_fuel_stop(
    total_distance_km : float,
    threshold_km      : float = FUEL_STOP_THRESHOLD_KM,
) -> bool:
    """True si la ruta supera el umbral de combustible y requiere escala."""
    return total_distance_km > threshold_km


# ────────────────────────────────────────────────────────────────────────────
# Resumen de ruta
# ────────────────────────────────────────────────────────────────────────────

def route_summary(legs: List[Dict], aircraft: "Optional[AircraftProfile]" = None) -> Dict:
    """
    Calcula el resumen de una ruta completa a partir de sus tramos.

    Cada leg debe tener las claves: distance_km, time_hours, fuel_liters.
    Si se pasa aircraft, se usan sus parametros de combustible; si no,
    se usan los defaults del Alpha Trainer.

    Retorna:
        total_distance_km  : float
        total_time_hours   : float
        total_fuel_liters  : float
        needs_fuel_stop    : bool
        fuel_ok            : bool  — True si el combustible utilizable alcanza
    """
    total_dist = sum(leg["distance_km"] for leg in legs)
    total_time = sum(leg["time_hours"]  for leg in legs)
    total_fuel = sum(leg["fuel_liters"] for leg in legs)

    if aircraft is not None:
        usable_l     = aircraft.fuel_usable_l
        threshold_km = aircraft.range_km * 0.90
    else:
        usable_l     = FUEL_USABLE_L
        threshold_km = FUEL_STOP_THRESHOLD_KM

    return {
        "total_distance_km" : round(total_dist, 1),
        "total_time_hours"  : round(total_time, 3),
        "total_fuel_liters" : round(total_fuel, 1),
        "needs_fuel_stop"   : total_dist > threshold_km,
        "fuel_ok"           : total_fuel <= usable_l,
    }


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: route/performance.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # -- haversine_km --
    # La Cumbre (SACC) - Alta Gracia (SAAG): ~73 km
    d = haversine_km(-31.010853, -64.526899, -31.657347, -64.397470)
    check(f"SACC-SAAG distancia ~73 km  (got {d:.1f})", 65.0 <= d <= 80.0)

    # Punto identico = 0
    check("Punto identico = 0 km", haversine_km(0, 0, 0, 0) == 0.0)

    # -- bearing_deg --
    # Norte puro
    b_norte = bearing_deg(0.0, 0.0, 1.0, 0.0)
    check(f"Rumbo Norte puro ~0  (got {b_norte:.1f})", abs(b_norte) < 1.0 or abs(b_norte - 360) < 1.0)

    # Este puro
    b_este = bearing_deg(0.0, 0.0, 0.0, 1.0)
    check(f"Rumbo Este puro ~90  (got {b_este:.1f})", abs(b_este - 90.0) < 1.0)

    # SACC - SAAG (al sur-este)
    b = bearing_deg(-31.010853, -64.526899, -31.657347, -64.397470)
    check(f"SACC-SAAG rumbo SE ~160  (got {b:.1f})", 140.0 <= b <= 180.0)

    # -- effective_groundspeed_kt --
    # Sin viento = crucero puro
    gs = effective_groundspeed_kt(97.0, None, None, 90.0)
    check(f"Sin viento = GS crucero  (got {gs:.1f})", gs == 97.0)

    # Viento en cola exacto (viento FROM 270, track 090 = tailwind 20kt)
    gs_tail = effective_groundspeed_kt(97.0, 270, 20.0, 90.0)
    check(f"Viento cola 20kt = GS ~117kt  (got {gs_tail:.1f})", abs(gs_tail - 117.0) < 0.5)

    # Viento en cara exacto (viento FROM 090, track 090 = headwind 20kt)
    gs_head = effective_groundspeed_kt(97.0, 90, 20.0, 90.0)
    check(f"Viento frente 20kt = GS ~77kt  (got {gs_head:.1f})", abs(gs_head - 77.0) < 0.5)

    # Viento cruzado (viento FROM 000, track 090 = componente 0)
    gs_cross = effective_groundspeed_kt(97.0, 0, 20.0, 90.0)
    check(f"Viento cruzado puro = GS crucero  (got {gs_cross:.1f})", abs(gs_cross - 97.0) < 0.5)

    # -- leg_time_hours --
    # 97kt = 179.7 km/h; 180 km / 179.7 km/h ~1.0016 hr
    t = leg_time_hours(180.0, 97.0)
    check(f"180km a 97kt ~1h  (got {t:.3f})", 0.99 <= t <= 1.02)

    # -- leg_fuel_liters --
    f = leg_fuel_liters(1.0)
    check(f"1h vuelo = 14L  (got {f:.1f})", f == 14.0)

    # -- safe_altitude_ft --
    # Terreno 3000 ft + 500 buffer = 3500, ceil a 500 = 3500
    sa = safe_altitude_ft(3000)
    check(f"Terreno 3000ft = altitud segura 3500ft  (got {sa})", sa == 3500)

    # Terreno 2500 ft + 500 = 3000, multiplo exacto = 3000
    sa2 = safe_altitude_ft(2500)
    check(f"Terreno 2500ft = altitud segura 3000ft  (got {sa2})", sa2 == 3000)

    # Terreno 1800 ft + 500 = 2300, ceil a 500 = 2500
    sa3 = safe_altitude_ft(1800)
    check(f"Terreno 1800ft = altitud segura 2500ft  (got {sa3})", sa3 == 2500)

    # -- needs_fuel_stop --
    check("450km no necesita escala",  not needs_fuel_stop(450.0))
    check("550km necesita escala",         needs_fuel_stop(550.0))
    check("500km exacto no necesita",  not needs_fuel_stop(500.0))

    # -- route_summary --
    legs = [
        {"distance_km": 100.0, "time_hours": 0.57, "fuel_liters": 8.0},
        {"distance_km": 120.0, "time_hours": 0.68, "fuel_liters": 9.5},
    ]
    s = route_summary(legs)
    check(f"Resumen distancia 220km  (got {s['total_distance_km']})", s["total_distance_km"] == 220.0)
    check(f"Resumen fuel 17.5L  (got {s['total_fuel_liters']})", s["total_fuel_liters"] == 17.5)
    check("Resumen fuel_ok True (17.5 < 43L)", s["fuel_ok"] is True)
    check("Resumen no necesita escala (220km)", s["needs_fuel_stop"] is False)

    # Ruta larga: 2 tramos de 300km = 600km total
    legs_long = [
        {"distance_km": 300.0, "time_hours": 1.71, "fuel_liters": 24.0},
        {"distance_km": 300.0, "time_hours": 1.71, "fuel_liters": 24.0},
    ]
    s2 = route_summary(legs_long)
    check("Ruta 600km necesita escala", s2["needs_fuel_stop"] is True)
    check("Ruta 600km fuel_ok False (48L > 43L)", s2["fuel_ok"] is False)

    # Constantes de reserva
    check(f"Reserva ~7L  (got {FUEL_RESERVE_L:.1f})", abs(FUEL_RESERVE_L - 7.0) < 0.1)
    check(f"Usable ~43L  (got {FUEL_USABLE_L:.1f})", abs(FUEL_USABLE_L - 43.0) < 0.1)

    print("\n  Constantes Alpha Trainer:")
    print(f"    Crucero     : {CRUISE_KT} kt")
    print(f"    Consumo     : {FUEL_FLOW_LPH} L/hr")
    print(f"    Tanque      : {FUEL_CAPACITY_L} L  (usable: {FUEL_USABLE_L:.1f} L)")
    print(f"    Reserva     : {FUEL_RESERVE_MIN} min  ({FUEL_RESERVE_L:.1f} L)")
    print(f"    Buffer terr.: {TERRAIN_BUFFER_FT} ft")
    print(f"    Umbral comb.: {FUEL_STOP_THRESHOLD_KM} km")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
