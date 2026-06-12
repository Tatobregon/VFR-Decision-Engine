"""
vfr_altitude.py
===============
Altitud de crucero VFR segun la regla de los semicirculos (RAAC / OACI Anexo 2).

A diferencia del vuelo IFR --que sigue el nivel asignado a la aerovia (MEA/FL)--
el piloto VFR elige su nivel de crucero aplicando la regla de los semicirculos,
basada en el RUMBO MAGNETICO de la derrota:

    Rumbo magnetico 000-179 (componente Este) -> miles IMPARES + 500 ft
                                                  (3500, 5500, 7500, 9500, ...)
    Rumbo magnetico 180-359 (componente Oeste) -> miles PARES   + 500 ft
                                                  (4500, 6500, 8500, 10500, ...)

La regla rige por encima de 3000 ft AGL. Por debajo de esa altura no hay
restriccion de nivel de crucero.

Conversion rumbo verdadero -> magnetico
---------------------------------------
La regla usa rumbo MAGNETICO. El router calcula rumbos VERDADEROS (great circle).
En Argentina la declinacion magnetica no es despreciable (~ -3 a -13 grados,
Oeste), y como la mayoria de las rutas del pais son N-S (rumbos cercanos a 000 o
180, justo el borde de los semicirculos), ignorarla cambiaria el semicirculo.

Se aplica una APROXIMACION lineal de la declinacion (epoca ~2025) en funcion de
lat/lon, valida para territorio argentino. Es una simplificacion documentada: en
produccion deberia reemplazarse por el modelo WMM/IGRF oficial. El error tipico
es de 1-2 grados, suficiente para esta aplicacion salvo en derrotas casi
exactamente sobre el limite del semicirculo.

    rumbo_magnetico = rumbo_verdadero - declinacion   (declinacion Oeste < 0)
"""



# ──────────────────────────────────────────────────────────────────────────────
# Declinacion magnetica aproximada para Argentina (epoca ~2025)
# ──────────────────────────────────────────────────────────────────────────────

def magnetic_declination_ar(lat: float, lon: float) -> float:
    """
    Declinacion magnetica aproximada (grados; Oeste = negativo) para Argentina.

    Aproximacion lineal anclada en valores WMM 2025 de referencia:
      ~ -4 grados en el norte (lat -24), creciente hacia el sur (~ -12 a -13 en
      Tierra del Fuego), con leve atenuacion hacia el oeste.

    SIMPLIFICACION documentada: error tipico 1-2 grados. Para precision
    aeronautica certificada usar el modelo WMM/IGRF oficial.
    """
    alat = abs(lat)
    alon = abs(lon)
    decl = -4.0 - 0.30 * (alat - 24.0) + 0.15 * (alon - 58.0)
    # Acotar a un rango fisicamente razonable para el pais
    return max(-16.0, min(2.0, decl))


def true_to_magnetic(true_track_deg: float, lat: float, lon: float) -> float:
    """
    Convierte rumbo verdadero a rumbo magnetico aplicando la declinacion local.
    Devuelve el resultado normalizado a [0, 360).
    """
    mag = true_track_deg - magnetic_declination_ar(lat, lon)
    return mag % 360.0


# ──────────────────────────────────────────────────────────────────────────────
# Regla de los semicirculos
# ──────────────────────────────────────────────────────────────────────────────

def is_eastbound(magnetic_track_deg: float) -> bool:
    """True si el rumbo magnetico esta en el semicirculo Este (000-179)."""
    return (magnetic_track_deg % 360.0) < 180.0


def _valid_vfr_levels(eastbound: bool, max_ft: int) -> list:
    """
    Niveles VFR validos (en ft) para un semicirculo, hasta max_ft.

    Este : miles impares + 500 (3500, 5500, 7500, ...)
    Oeste: miles pares   + 500 (4500, 6500, 8500, ...)
    """
    levels = []
    base_thousand = 3 if eastbound else 4   # primer mil impar / par
    thousand = base_thousand
    while thousand * 1000 + 500 <= max_ft + 1000:
        levels.append(thousand * 1000 + 500)
        thousand += 2
    return levels


def hemispheric_vfr_altitude(
    true_track_deg : float,
    lat            : float,
    lon            : float,
    preferred_alt_ft : int,
) -> int:
    """
    Altitud de crucero VFR para un tramo, segun la regla de los semicirculos.

    Parameters
    ----------
    true_track_deg   : rumbo verdadero de la derrota (great circle).
    lat, lon         : posicion representativa del tramo (para la declinacion).
    preferred_alt_ft : altitud de crucero deseada (tipicamente cruise_alt_ft del
                       perfil de la aeronave).

    Returns
    -------
    El nivel VFR valido (terminado en 500) del semicirculo correspondiente, mas
    cercano a la altitud preferida. Nunca supera preferred_alt_ft en mas de un
    nivel (no asumimos que el avion suba por encima de su crucero planificado).

    Por debajo de 3000 ft no aplica la regla: se devuelve la altitud preferida
    tal cual.
    """
    if preferred_alt_ft < 3000:
        return preferred_alt_ft

    mag_track = true_to_magnetic(true_track_deg, lat, lon)
    eastbound = is_eastbound(mag_track)

    levels = _valid_vfr_levels(eastbound, max_ft=preferred_alt_ft + 1000)
    if not levels:
        return preferred_alt_ft

    # Nivel valido mas cercano al crucero preferido. Ante empate, el mas bajo
    # (criterio conservador: menos exposicion a hipoxia / espacio controlado).
    return min(levels, key=lambda lv: (abs(lv - preferred_alt_ft), lv))


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  TEST: vfr_altitude.py — regla de los semicirculos")
    print("=" * 64)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ── Declinacion: signo y magnitud razonables para Argentina ──
    d_ba  = magnetic_declination_ar(-34.6, -58.4)   # Buenos Aires
    d_usu = magnetic_declination_ar(-54.8, -68.3)   # Ushuaia
    d_juj = magnetic_declination_ar(-24.2, -65.3)   # Jujuy
    print(f"\n  Declinacion: BA={d_ba:.1f}  Ushuaia={d_usu:.1f}  Jujuy={d_juj:.1f}")
    check("declinacion BA en rango [-10,-5]",   -10.0 <= d_ba <= -5.0)
    check("declinacion mas negativa al sur",    d_usu < d_ba < d_juj)

    # ── Niveles validos por semicirculo ──
    print("\n  -- niveles validos --")
    east = _valid_vfr_levels(True, 12000)
    west = _valid_vfr_levels(False, 12000)
    check("Este  empieza en 3500",  east[0] == 3500)
    check("Este  incluye 7500",     7500 in east)
    check("Oeste empieza en 4500",  west[0] == 4500)
    check("Oeste incluye 6500",     6500 in west)
    check("Este  no tiene pares+500", 4500 not in east)

    # ── Regla hemisferica: rumbo Este vs Oeste ──
    print("\n  -- altitud hemisferica --")
    # Rumbo verdadero 090 (Este franco) en zona central -> semicirculo Este
    a_e = hemispheric_vfr_altitude(90, -32.0, -64.0, 7500)
    check("rumbo Este, pref 7500 -> 7500 (impar+500)", a_e == 7500)
    # Rumbo verdadero 270 (Oeste franco) -> semicirculo Oeste, 7500 no es valido
    a_w = hemispheric_vfr_altitude(270, -32.0, -64.0, 7500)
    check("rumbo Oeste, pref 7500 -> 6500 u 8500 (par+500)", a_w in (6500, 8500))
    check("rumbo Oeste 7500 NO devuelve impar+500", a_w % 2000 != 1500)

    # ── Crucero bajo: < 3000 ft no aplica la regla ──
    check("pref 2500 (<3000) -> devuelve 2500", hemispheric_vfr_altitude(90, -32, -64, 2500) == 2500)

    # ── Caso N-S donde la declinacion importa (borde de semicirculo) ──
    # Rumbo verdadero 175 (casi Sur, lado Este). Con declinacion ~ -8 en BA,
    # el rumbo magnetico ~ 183 -> cruza al semicirculo Oeste.
    mag = true_to_magnetic(175, -34.6, -58.4)
    print(f"\n  rumbo verdadero 175 en BA -> magnetico {mag:.1f}")
    check("declinacion empuja 175 verdadero al semicirculo Oeste", mag >= 180.0)

    # ── Todos los perfiles: la altitud devuelta termina en 500 ──
    print("\n  -- consistencia para todos los cruceros de los perfiles --")
    for alt in (5500, 6000, 7500, 10000, 16500):
        for trk in (45, 135, 225, 315):
            a = hemispheric_vfr_altitude(trk, -34, -64, alt)
            check(f"alt {alt} rumbo {trk} -> termina en 500 ({a})", a % 1000 == 500)

    print("\n" + "=" * 64)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 64)
