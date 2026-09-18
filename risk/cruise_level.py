"""
risk/cruise_level.py
====================
Barrera del NIVEL DE CRUCERO para los checkpoints en ruta.

El puntaje de un checkpoint (`compute_soft_score`) describe la SUPERFICIE
debajo del punto: visibilidad, niebla, fenomenos. Sirve para lo que pasa si hay
que descender o aterrizar ahi, pero no describe el aire por el que el avion
efectivamente cruza el punto. Hasta septiembre de 2026 ese aire se mostraba en
pantalla y no se evaluaba: el riesgo de un checkpoint era identico a 5.500, a
7.500 y a 10.000 ft (medido en 12 puntos de las cinco regiones), de modo que un
nivel metido en una capa cerrada, o con hielo, daba GO mientras el suelo
estuviera despejado.

Esta barrera agrega tres pisos NO compensatorios, con el mismo mecanismo que
`conjunctive_floor`: el veredicto del checkpoint es el peor entre el puntaje de
superficie y estos pisos. No toca los pesos AHP ni los umbrales, que se
calibraron sobre la bateria de aerodromos y siguen valiendo tal cual.

    Nube en el nivel     (solo VFR)  BKN -> CAUTION   OVC -> NO GO
    Engelamiento         (VFR e IFR) T <= 0 C con nube >= BKN en el nivel -> NO GO
    Techo bajo el crucero (solo VFR) base de la capa baja por debajo del nivel -> CAUTION

Procedencia de cada corte
-------------------------
- BKN y OVC son los octavos OACI que el sistema ya usa para definir el techo
  (`_pct_to_sky_cover`): una sola definicion para todo el sistema.
- (J) En VFR el vuelo se hace fuera de nubes: una capa BKN en el nivel obliga a
  esquivarla (CAUTION) y una OVC no deja por donde (NO GO). En IFR la nube no
  veta por si sola.
- (J) Humedad visible = nube en el nivel >= BKN. 0 C es el punto de
  congelacion. Ninguno de los perfiles declara certificacion para hielo, asi
  que la regla aplica a todos y en los dos regimenes.
- (J) La base que se compara con el crucero es SOLO la de la capa BAJA, que se
  estima con la regla de Espy a partir de la temperatura y el rocio pronosticados.
  Las capas media y alta del adaptador van a alturas de REFERENCIA fijas (8.000 y
  20.000 ft) que no son pronostico: compararlas con el crucero seria vetar sobre
  un numero inventado. Esas capas entran por la nubosidad EN el nivel, que si es
  dato del modelo.

Un dato que la fuente no trae se DECLARA en `missing`; no se reemplaza por un
valor supuesto ni se lo toma como cielo despejado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

try:
    from parsers.openmeteo_adapter import (
        MAX_LOW_CLOUD_BASE_FT, _pct_to_sky_cover, estimate_cloud_base_ft,
    )
    from risk.soft_scoring import _worst_verdict
except ImportError:  # ejecucion como script
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from parsers.openmeteo_adapter import (
        MAX_LOW_CLOUD_BASE_FT, _pct_to_sky_cover, estimate_cloud_base_ft,
    )
    from risk.soft_scoring import _worst_verdict


# (J) Coberturas que cuentan como capa: las mismas que constituyen techo.
LAYER_COVERS = frozenset({"BKN", "OVC"})

# (J) Punto de congelacion para el engelamiento con humedad visible.
ICING_TEMP_MAX_C = 0.0


@dataclass
class CruiseLevelCheck:
    """Resultado de la barrera del nivel para UN momento (o el peor de varios)."""
    floor   : str = "GO"                                   # piso impuesto por el nivel
    reasons : List[str] = field(default_factory=list)      # por que, en castellano
    missing : List[str] = field(default_factory=list)      # datos que la fuente no trajo


def _fmt(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def cruise_level_floor(
    cruise_alt_ft      : int,
    flight_rules       : str,
    level_cloud_pct    : Optional[int],
    level_temp_c       : Optional[float],
    low_cloud_pct      : Optional[int],
    surface_temp_c     : Optional[float],
    surface_dewpoint_c : Optional[float],
    terrain_elev_ft    : Optional[float],
) -> CruiseLevelCheck:
    """
    Evalua el aire del nivel de crucero en un momento y devuelve su piso.

    Parameters
    ----------
    cruise_alt_ft      : altitud a la que se cruza el punto (ft MSL).
    flight_rules       : "VFR" o "IFR".
    level_cloud_pct    : nubosidad EN el nivel de presion (%).
    level_temp_c       : temperatura en el nivel (C).
    low_cloud_pct      : nubosidad de la capa baja (%), la que define el techo.
    surface_temp_c     : temperatura a 2 m, para la base de Espy.
    surface_dewpoint_c : punto de rocio a 2 m, para la base de Espy.
    terrain_elev_ft    : elevacion del terreno que uso el modelo (ft MSL). La
                         base de Espy es AGL respecto de ESE terreno.
    """
    vfr   = (flight_rules or "VFR").upper() == "VFR"
    check = CruiseLevelCheck()

    def piso(nivel: str, razon: str) -> None:
        check.floor = _worst_verdict(check.floor, nivel)
        check.reasons.append(razon)

    # ── Nube y hielo en el nivel ─────────────────────────────────────────────
    cover = _pct_to_sky_cover(level_cloud_pct) if level_cloud_pct is not None else None
    if level_cloud_pct is None:
        check.missing.append("nubosidad en el nivel")
    elif cover in LAYER_COVERS:
        if level_temp_c is None:
            check.missing.append("temperatura en el nivel")
        elif level_temp_c <= ICING_TEMP_MAX_C:
            piso("NO GO", f"engelamiento: {_fmt(level_temp_c)} °C con nube "
                          f"{level_cloud_pct} % ({cover}) en el nivel")
        if vfr:
            if cover == "OVC":
                piso("NO GO", f"nivel dentro de nube: {level_cloud_pct} % (OVC)")
            else:
                piso("CAUTION", f"nube {level_cloud_pct} % (BKN) en el nivel")

    # ── Techo bajo el crucero (solo VFR) ─────────────────────────────────────
    if vfr:
        low_cover = _pct_to_sky_cover(low_cloud_pct) if low_cloud_pct is not None else None
        if low_cloud_pct is None:
            check.missing.append("nubosidad baja")
        elif low_cover in LAYER_COVERS:
            if surface_temp_c is None or surface_dewpoint_c is None:
                check.missing.append("temperatura y rocio de superficie (base de la capa baja)")
            elif terrain_elev_ft is None:
                check.missing.append("elevacion del terreno")
            else:
                base_agl = estimate_cloud_base_ft(surface_temp_c, surface_dewpoint_c)
                base_msl = int(round(terrain_elev_ft + base_agl))
                if cruise_alt_ft > base_msl:
                    if base_agl >= MAX_LOW_CLOUD_BASE_FT:
                        # La estimacion esta en su tope: solo se sabe que la base
                        # esta a MAS de esa altura, no si queda bajo el crucero.
                        check.missing.append(
                            f"base de la capa baja: por encima de "
                            f"{MAX_LOW_CLOUD_BASE_FT} ft AGL, no comparable con el crucero")
                    else:
                        piso("CAUTION", f"capa baja {low_cloud_pct} % ({low_cover}) con base "
                                        f"~{base_msl} ft MSL, por debajo del crucero "
                                        f"({cruise_alt_ft} ft)")
    return check


def worst_of(checks: Iterable[CruiseLevelCheck]) -> CruiseLevelCheck:
    """
    Combina los chequeos de varios momentos de la ventana: manda el peor piso,
    como en el resto del camino NWP (peor caso en el tiempo). Las razones y los
    faltantes se juntan sin repetir.
    """
    total = CruiseLevelCheck()
    for c in checks:
        total.floor = _worst_verdict(total.floor, c.floor)
        total.reasons += [r for r in c.reasons if r not in total.reasons]
        total.missing += [m for m in c.missing if m not in total.missing]
    return total


# ──────────────────────────────────────────────────────────────────────────────
# Prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    casos = [
        # descripcion,                         alt,   reglas, nube, T,    baja, Ts,  Td,  terreno, esperado
        ("nivel limpio",                        5500, "VFR",  0,    15.0, 0,    20., 12., 1600,   "GO"),
        ("BKN en el nivel, VFR",                7500, "VFR",  70,   5.0,  0,    20., 12., 1600,   "CAUTION"),
        ("OVC en el nivel, VFR",                7500, "VFR",  95,   5.0,  0,    20., 12., 1600,   "NO GO"),
        ("OVC en el nivel, IFR",                7500, "IFR",  95,   5.0,  0,    20., 12., 1600,   "GO"),
        ("hielo: BKN a -3 C, IFR",             10000, "IFR",  70,   -3.0, 0,    10.,  5.,  500,   "NO GO"),
        ("-3 C sin nube",                      10000, "VFR",  10,   -3.0, 0,    10.,  5.,  500,   "GO"),
        ("capa baja bajo el crucero, VFR",      7500, "VFR",  0,    8.0,  80,   12., 10.,  500,   "CAUTION"),
        ("capa baja sobre el crucero, VFR",     2500, "VFR",  0,    8.0,  80,   12.,  5.,  500,   "GO"),
        ("capa baja bajo el crucero, IFR",      7500, "IFR",  0,    8.0,  80,   12., 10.,  500,   "GO"),
    ]
    ok = True
    for d, alt, fr, nube, t, baja, ts, td, terr, esperado in casos:
        c = cruise_level_floor(alt, fr, nube, t, baja, ts, td, terr)
        bien = c.floor == esperado
        ok &= bien
        print(f"  [{'OK' if bien else 'FALLO'}] {d:34s} -> {c.floor:8s} {'; '.join(c.reasons)}")
    print("\n  TODO OK" if ok else "\n  HAY FALLOS")
