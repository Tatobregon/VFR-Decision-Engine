"""
density_altitude.py
===================
Calcula la altitud de densidad (DA) para un aeródromo dado temperatura
observada y elevación del campo.

La DA es clave para LSA y aviones de baja potencia: determina la distancia
real de despegue, la tasa de ascenso y la distancia de aterrizaje. A mayor
DA, mayor degradación del rendimiento.

Formulas aplicadas (aviación general estándar)
----------------------------------------------
  T_ISA  = 15 - 2 × (elev_ft / 1000)          [temperatura ISA a la elevación]
  PA     = elev_ft + 27 × (1013.25 - QNH_hPa) [si QNH disponible]
  PA     = elev_ft                              [si QNH no disponible]
  DA     = PA + 120 × (T_OAT - T_ISA)          [altitud de densidad en ft]

El factor 120 ft/°C es la aproximación estándar en aviación general.

Niveles de advertencia (conservadores para LSA)
-----------------------------------------------
  NORMAL   : DA <= 5000 ft
  ELEVATED : 5000 < DA <= 8000 ft  → rendimiento degradado, revisar manual
  HIGH     : DA > 8000 ft           → degradación severa, POH obligatorio
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Temperatura ISA al nivel del mar y lapso estándar
_ISA_SEA_LEVEL_C = 15.0
_ISA_LAPSE_C_PER_1000FT = 2.0

# Thresholds de advertencia
DA_ELEVATED_FT = 5_000
DA_HIGH_FT     = 8_000


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DensityAltitudeResult:
    elevation_ft        : int            # Elevación del campo en ft
    temp_c              : float          # Temperatura observada en °C
    isa_temp_c          : float          # Temperatura ISA a esa elevación
    isa_deviation_c     : float          # T_OAT − T_ISA (>0 = más caliente que ISA)
    pressure_alt_ft     : int            # Altitud de presión en ft
    density_alt_ft      : int            # Altitud de densidad en ft
    qnh_used            : Optional[float] = None  # QNH usado en hPa (None si no disponible)
    elevation_estimated : bool = False   # True si la elevación del campo es estimada


def advisory(result: DensityAltitudeResult) -> str:
    """Nivel de advertencia: 'NORMAL', 'ELEVATED' o 'HIGH'."""
    if result.density_alt_ft > DA_HIGH_FT:
        return "HIGH"
    if result.density_alt_ft > DA_ELEVATED_FT:
        return "ELEVATED"
    return "NORMAL"


# ──────────────────────────────────────────────────────────────────────────────
# Función principal
# ──────────────────────────────────────────────────────────────────────────────

def compute_density_altitude(
    temp_c      : float,
    elevation_ft: int,
    qnh_hpa     : Optional[float] = None,
    elevation_estimated: bool = False,
) -> DensityAltitudeResult:
    """
    Calcula altitud de densidad para las condiciones dadas.

    Parameters
    ----------
    temp_c       : Temperatura del aire en °C (observada o NWP).
    elevation_ft : Elevación del aeródromo en ft sobre el nivel del mar.
    qnh_hpa      : QNH en hPa. Si None, se usa la elevación como altitud de presión.
    elevation_estimated : Indica si la elevación es un estimado.

    Returns
    -------
    DensityAltitudeResult con DA en ft y metadatos de diagnóstico.
    """
    # Temperatura ISA a la elevación del campo
    isa_temp_c = _ISA_SEA_LEVEL_C - _ISA_LAPSE_C_PER_1000FT * (elevation_ft / 1000.0)

    # Altitud de presión
    if qnh_hpa is not None:
        pressure_alt_ft = int(round(elevation_ft + 27.0 * (1013.25 - qnh_hpa)))
    else:
        pressure_alt_ft = elevation_ft

    # Altitud de densidad
    isa_deviation_c = temp_c - isa_temp_c
    density_alt_ft  = int(round(pressure_alt_ft + 120.0 * isa_deviation_c))

    result = DensityAltitudeResult(
        elevation_ft        = elevation_ft,
        temp_c              = temp_c,
        isa_temp_c          = round(isa_temp_c, 1),
        isa_deviation_c     = round(isa_deviation_c, 1),
        pressure_alt_ft     = pressure_alt_ft,
        density_alt_ft      = density_alt_ft,
        qnh_used            = qnh_hpa,
        elevation_estimated = elevation_estimated,
    )

    level = advisory(result)
    if level != "NORMAL":
        logger.warning(
            f"DA={density_alt_ft}ft ({level}) | "
            f"campo={elevation_ft}ft | T={temp_c}°C | ISA={isa_temp_c:.1f}°C"
        )
    else:
        logger.debug(
            f"DA={density_alt_ft}ft | "
            f"campo={elevation_ft}ft | T={temp_c}°C | ISA={isa_temp_c:.1f}°C"
        )

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    print("=" * 65)
    print("  TEST: features/density_altitude.py")
    print("=" * 65)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ISA estándar al nivel del mar: DA = PA = 0 ft
    r = compute_density_altitude(temp_c=15.0, elevation_ft=0, qnh_hpa=1013.25)
    check("ISA std nivel del mar: DA=0ft",  r.density_alt_ft == 0)
    check("ISA std nivel del mar: PA=0ft",  r.pressure_alt_ft == 0)
    check("ISA std nivel del mar: dev=0",   r.isa_deviation_c == 0.0)
    check("ISA std nivel del mar: NORMAL",  advisory(r) == "NORMAL")

    # SACC: elev=1141m=3743ft, T=30°C (día caluroso de verano), QNH=1008
    # T_ISA = 15 - 2*3.743 = 7.5°C → dev=+22.5°C
    # PA = 3743 + 27*(1013.25-1008) = 3743 + 141 = 3884
    # DA = 3884 + 120*22.5 = 3884 + 2700 = 6584 ft → ELEVATED
    r_sacc = compute_density_altitude(temp_c=30.0, elevation_ft=3743, qnh_hpa=1008.0)
    print(f"\n  SACC verano caluroso:")
    print(f"    T={r_sacc.temp_c}°C  ISA={r_sacc.isa_temp_c}°C  dev=ISA+{r_sacc.isa_deviation_c}°C")
    print(f"    PA={r_sacc.pressure_alt_ft}ft  DA={r_sacc.density_alt_ft}ft  [{advisory(r_sacc)}]")
    check("SACC caluroso: DA > campo (degradación)", r_sacc.density_alt_ft > 3743)
    check("SACC caluroso: nivel ELEVATED o HIGH",    advisory(r_sacc) in ("ELEVATED", "HIGH"))

    # SACC invierno frío: T=5°C, QNH=1020
    r_sacc_inv = compute_density_altitude(temp_c=5.0, elevation_ft=3743, qnh_hpa=1020.0)
    print(f"\n  SACC invierno frío:")
    print(f"    T={r_sacc_inv.temp_c}°C  ISA={r_sacc_inv.isa_temp_c}°C  dev=ISA{r_sacc_inv.isa_deviation_c:+.1f}°C")
    print(f"    PA={r_sacc_inv.pressure_alt_ft}ft  DA={r_sacc_inv.density_alt_ft}ft  [{advisory(r_sacc_inv)}]")
    check("SACC invierno: DA < DA verano", r_sacc_inv.density_alt_ft < r_sacc.density_alt_ft)

    # Sin QNH: PA = elevación
    r_nqnh = compute_density_altitude(temp_c=25.0, elevation_ft=2000, qnh_hpa=None)
    check("Sin QNH: PA = elevación", r_nqnh.pressure_alt_ft == 2000)
    check("Sin QNH: qnh_used es None", r_nqnh.qnh_used is None)

    print("\n" + "=" * 65)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 65)
