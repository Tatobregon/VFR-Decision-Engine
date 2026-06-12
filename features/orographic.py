"""
orographic.py
=============
Penalizacion de incertidumbre orografica para aeropuertos NWP en terreno
complejo.

SACC (La Cumbre, Cordoba) esta en las Sierras Chicas a 1141 m AMSL. Los
modelos NWP tienen resolucion espacial limitada y no capturan bien los
efectos de valle, foehn ni conveccion orografica local. Para compensar esta
incertidumbre estructural se aplica una penalizacion fija de +0.05 al
R_total cuando la fuente es NWP (nwp_estimated=True).

La penalizacion aplica SOLO cuando:
  1. nwp_estimated = True  (fuente Open-Meteo, no METAR)
  2. station_id esta en la lista de aeropuertos con penalizacion orografica

Uso tipico
----------
    from features.orographic import orographic_penalty

    delta_r = orographic_penalty(weather.nwp_estimated, weather.station_id)
    r_total = base_score + delta_r
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Configuracion
# ──────────────────────────────────────────────────────────────────────────────

# Aeropuertos con penalizacion orografica activa.
# En v1.0 solo SACC; la estructura permite agregar mas en el futuro.
OROGRAPHIC_STATIONS = {
    "SACC": {
        "name"     : "La Cumbre",
        "delta_r"  : 0.05,
        "reason"   : "Sierras Chicas 1141m AMSL: incertidumbre NWP por terreno complejo",
    },
}

# Penalizacion por defecto para estaciones NWP no mapeadas con terreno elevado
_DEFAULT_NWP_PENALTY = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de salida
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class OrographicResult:
    """Resultado de la evaluacion de penalizacion orografica."""
    station_id    : str
    nwp_estimated : bool
    delta_r       : float   # Penalizacion a sumar al R_total [0, 1]
    applied       : bool    # True si se aplico alguna penalizacion
    reason        : str     # Descripcion para trazabilidad / logs


# ──────────────────────────────────────────────────────────────────────────────
# Funcion principal
# ──────────────────────────────────────────────────────────────────────────────

def orographic_penalty(
    nwp_estimated : bool,
    station_id    : str,
) -> OrographicResult:
    """
    Calcula la penalizacion orografica para una fuente NWP.

    Parameters
    ----------
    nwp_estimated : True si los datos provienen de NWP (Open-Meteo),
                    False si son observacion directa (METAR).
    station_id    : Identificador ICAO del aeropuerto (ej. "SACC").

    Returns
    -------
    OrographicResult con delta_r en [0, 0.05] para v1.0.
    """
    sid = station_id.upper().strip() if station_id else ""

    if not nwp_estimated:
        return OrographicResult(
            station_id    = sid,
            nwp_estimated = False,
            delta_r       = 0.0,
            applied       = False,
            reason        = "Dato METAR: sin penalizacion orografica",
        )

    config = OROGRAPHIC_STATIONS.get(sid)
    if config:
        delta = config["delta_r"]
        reason = config["reason"]
        applied = True
    else:
        delta  = _DEFAULT_NWP_PENALTY
        reason = f"Estacion NWP {sid} sin penalizacion orografica configurada"
        applied = False

    logger.debug(
        f"Orografica {sid}: nwp={nwp_estimated} delta_r={delta:.3f} ({reason})"
    )

    return OrographicResult(
        station_id    = sid,
        nwp_estimated = nwp_estimated,
        delta_r       = delta,
        applied       = applied,
        reason        = reason,
    )


def orographic_penalty_from_weather(weather) -> OrographicResult:
    """Wrapper que extrae nwp_estimated y station_id de un ParsedWeather."""
    return orographic_penalty(
        nwp_estimated = weather.nwp_estimated,
        station_id    = weather.station_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("  TEST: orographic.py")
    print("=" * 60)

    test_cases = [
        # (descripcion, nwp_estimated, station_id)
        ("SACC NWP (penalizacion activa)",  True,  "SACC"),
        ("SACC METAR (imposible, solo NWP)", False, "SACC"),
        ("SACO NWP (sin penalizacion)",     True,  "SACO"),
        ("SACO METAR (normal)",             False, "SACO"),
        ("SAVY NWP (sin penalizacion)",     True,  "SAVY"),
        ("Station desconocida NWP",         True,  "XXXX"),
        ("Station vacia",                   True,  ""),
    ]

    print(f"\n  {'Descripcion':<35} {'Estacion':>8} {'NWP':>5} "
          f"{'delta_r':>8} {'Aplica?':>8}")
    print(f"  {'-'*35} {'-'*8} {'-'*5} {'-'*8} {'-'*8}")

    for desc, nwp, sid in test_cases:
        r = orographic_penalty(nwp, sid)
        print(f"  {desc:<35} {r.station_id:>8} {str(nwp):>5} "
              f"{r.delta_r:>8.3f} {'SI' if r.applied else 'no':>8}")

    # ── Verificaciones ────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("  Verificaciones")

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    check("SACC NWP -> delta_r = 0.05",
          orographic_penalty(True, "SACC").delta_r == 0.05)
    check("SACC NWP -> applied = True",
          orographic_penalty(True, "SACC").applied)
    check("SACC METAR -> delta_r = 0.0",
          orographic_penalty(False, "SACC").delta_r == 0.0)
    check("SACC METAR -> applied = False",
          not orographic_penalty(False, "SACC").applied)
    check("SACO NWP -> delta_r = 0.0 (no orografico)",
          orographic_penalty(True, "SACO").delta_r == 0.0)
    check("SACO METAR -> delta_r = 0.0",
          orographic_penalty(False, "SACO").delta_r == 0.0)
    check("Station desconocida NWP -> delta_r = 0.0",
          orographic_penalty(True, "XXXX").delta_r == 0.0)
    check("Station en minusculas normalizada correctamente",
          orographic_penalty(True, "sacc").delta_r == 0.05)
    check("delta_r siempre en [0, 1]",
          all(0.0 <= orographic_penalty(nwp, sid).delta_r <= 1.0
              for _, nwp, sid in test_cases))

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
