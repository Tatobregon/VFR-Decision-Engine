"""
flight_plan.py
==============
Arma un plan de vuelo segun el formulario modelo OACI (el que recibe EANA y
regula ANAC, AIP ENR 1.10) a partir de:
  - datos AUTO derivados de la evaluacion (origen, destino, aeronave, reglas,
    hora, velocidad, nivel, ruta, EET, alternativa), y
  - datos del PILOTO ingresados en la web (matricula, equipo, transponder,
    POB, piloto al mando, color/marcas, autonomia, etc.).

Produce:
  - `fields`  : dict con cada casilla ya formateada (para el documento imprimible).
  - `message` : el mensaje FPL OACI (casillas 7-18) listo para copiar/pegar en
                el sistema de radicacion. (La casilla 19 es suplementaria SAR y
                no se transmite; se incluye solo en el documento.)

NO radica el plan: el documento lo presenta el PILOTO por los canales oficiales,
y es el unico responsable de la veracidad de los datos.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class FlightPlanResult:
    fields: Dict[str, str] = field(default_factory=dict)  # casillas formateadas
    message: str = ""                                     # mensaje FPL (7-18)


def _fmt_speed(kt: Optional[float]) -> str:
    """Velocidad de crucero: N seguido de 4 digitos en kt (N0110 = 110 kt)."""
    if not kt:
        return "N0000"
    return f"N{round(kt):04d}"


def _fmt_level(level_ft: Optional[int], flight_level: bool = False) -> str:
    """
    Nivel de crucero (casilla 15b):
      - VFR sin nivel asignado            -> 'VFR'
      - VFR con altitud planificada       -> A + altitud en centenas de pies (A055 = 5500 ft)
      - IFR (flight_level=True)           -> F + nivel de vuelo en centenas de pies (F085, F165)
    """
    if level_ft is None:
        return "VFR"
    if flight_level:
        return f"F{round(level_ft / 100):03d}"
    return f"A{round(level_ft / 100):03d}"


def _clean_reg(reg: str) -> str:
    """
    Matricula sin guion ni espacios para el mensaje (LV-ABC -> LVABC).
    Casilla 7: maximo 7 caracteres alfanumericos, sin guiones ni simbolos.
    """
    return (reg or "").replace("-", "").replace(" ", "").upper()[:7]


def build_flight_plan(
    *,
    registration   : str,
    flight_rules   : str,            # "V" (VFR) | "I" (IFR)
    flight_type    : str = "G",      # G = aviacion general
    num            : int = 1,
    icao_type      : str = "ZZZZ",
    wake           : str = "L",
    equip_radio    : str = "S",      # casilla 10a
    equip_ssr      : str = "C",      # casilla 10b (transponder)
    dep_icao       : str = "",
    dep_name       : str = "",       # nombre/lugar si dep_icao = ZZZZ (casilla 18 DEP/)
    eobt           : str = "",       # HHMM UTC
    speed_kt       : Optional[float] = None,
    level_ft       : Optional[int] = None,
    route          : str = "DCT",
    dest_icao      : str = "",
    dest_name      : str = "",       # nombre/lugar si dest_icao = ZZZZ (casilla 18 DEST/)
    eet            : str = "",       # HHMM
    alternate      : str = "",
    alternate2     : str = "",
    dof            : str = "",       # YYMMDD
    endurance      : str = "",       # HHMM (casilla 19 E/)
    pob            : str = "",       # casilla 19 P/
    pic            : str = "",       # casilla 19 C/
    color_markings : str = "",       # casilla 19 A/
    radio_emerg    : str = "",       # casilla 19 R/  (lo provee el piloto: U=UHF, V=VHF, E=ELT)
    survival       : str = "",       # casilla 19 S/
    jackets        : str = "",       # casilla 19 J/
    dinghies       : str = "",       # casilla 19 D/
    remarks        : str = "",       # casilla 18 RMK/
    aircraft_name  : str = "",       # para RMK cuando icao_type = ZZZZ
) -> FlightPlanResult:
    """Arma el plan de vuelo OACI. Devuelve FlightPlanResult (fields + message)."""
    reg = _clean_reg(registration)
    rules = (flight_rules or "V").upper()[:1]
    speed = _fmt_speed(speed_kt)
    # IFR: nivel de vuelo (Fxxx); VFR: altitud (Axxx).
    level = _fmt_level(level_ft, flight_level=(rules == "I"))
    route = (route or "DCT").strip() or "DCT"
    altns = " ".join(a for a in (alternate, alternate2) if a).strip().upper()
    # Casilla 9: el numero de aeronaves se inserta SOLO si es mas de una.
    num_str = "" if int(num) <= 1 else str(int(num))

    # Casilla 18 — indicadores en el ORDEN oficial: DEP/ DEST/ DOF/ TYP/ RMK/
    item18_parts = []
    if dep_icao == "ZZZZ" and dep_name:
        item18_parts.append(f"DEP/{dep_name.upper()}")
    if dest_icao == "ZZZZ" and dest_name:
        item18_parts.append(f"DEST/{dest_name.upper()}")
    if dof:
        item18_parts.append(f"DOF/{dof}")
    # Tipo no listado en Doc 8643 (ZZZZ): el tipo real se indica precedido de TYP/.
    if icao_type == "ZZZZ" and aircraft_name:
        item18_parts.append(f"TYP/{aircraft_name.upper()}")
    if remarks.strip():
        item18_parts.append(f"RMK/{remarks.strip().upper()}")
    item18 = " ".join(item18_parts) if item18_parts else "0"

    # ── Mensaje FPL OACI (casillas 7-18) ──
    message = (
        f"(FPL-{reg}-{rules}{flight_type}\n"
        f"-{num_str}{icao_type}/{wake}-{equip_radio}/{equip_ssr}\n"
        f"-{dep_icao}{eobt}\n"
        f"-{speed}{level} {route}\n"
        f"-{dest_icao}{eet}{(' ' + altns) if altns else ''}\n"
        f"-{item18})"
    )

    # ── Casillas formateadas para el documento imprimible ──
    fields = {
        "7_identificacion":     reg or "—",
        "8_reglas":             rules,
        "8_tipo_vuelo":         flight_type,
        "9_numero":             str(num),
        "9_tipo_aeronave":      icao_type,
        "9_estela":             wake,
        "10a_equipo":           equip_radio or "—",
        "10b_vigilancia":       equip_ssr or "—",
        "13_salida":            dep_icao or "—",
        "13_eobt":              eobt or "—",
        "15_velocidad":         speed,
        "15_nivel":             level,
        "15_ruta":              route,
        "16_destino":           dest_icao or "—",
        "16_eet":               eet or "—",
        "16_alternativa":       alternate or "—",
        "16_alternativa2":      alternate2 or "",
        "18_otros":             item18,
        "19_autonomia":         endurance or "—",
        "19_pob":               pob or "—",
        "19_radio_emerg":       radio_emerg or "—",
        "19_superviv":          survival or "",
        "19_chalecos":          jackets or "",
        "19_botes":             dinghies or "",
        "19_color_marcas":      color_markings or "—",
        "19_pic":               pic or "—",
    }
    return FlightPlanResult(fields=fields, message=message)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  TEST: flight_plan.py — plan de vuelo OACI")
    print("=" * 64)

    ok = True
    def check(d, c):
        global ok; ok = ok and c
        print(f"  [{'OK' if c else 'FALLO'}] {d}")

    fp = build_flight_plan(
        registration="LV-ABC", flight_rules="V", icao_type="C172", wake="L",
        equip_radio="SDV", equip_ssr="C",
        dep_icao="SACO", eobt="1300", speed_kt=110, level_ft=5500, route="DCT",
        dest_icao="SAAR", eet="0045", alternate="SADF",
        dof="260622", endurance="0330", pob="2", pic="J. PEREZ",
        color_markings="BLANCO/AZUL",
    )
    print("\n" + fp.message + "\n")

    check("velocidad N0110", fp.fields["15_velocidad"] == "N0110")
    check("nivel A055 (5500 ft)", fp.fields["15_nivel"] == "A055")
    check("matricula sin guion", fp.fields["7_identificacion"] == "LVABC")
    check("reglas V", fp.fields["8_reglas"] == "V")
    check("mensaje arranca con (FPL-LVABC-VG", fp.message.startswith("(FPL-LVABC-VG"))
    check("mensaje termina con )", fp.message.endswith(")"))
    check("DOF en casilla 18", "DOF/260622" in fp.message)
    # Casilla 9: 1 aeronave -> SIN numero (C172/L, no 1C172/L)
    check("1 aeronave: sin numero en casilla 9", "-C172/L-" in fp.message and "-1C172/L-" not in fp.message)

    # VFR sin nivel -> 'VFR'
    fp2 = build_flight_plan(registration="LV-X", flight_rules="V", level_ft=None,
                            dep_icao="SACO", dest_icao="SAAR", speed_kt=90)
    check("nivel None -> VFR", fp2.fields["15_nivel"] == "VFR")

    # Tipo no listado (Alpha) -> ZZZZ + TYP/ con el tipo real (NO RMK/TIPO)
    fp3 = build_flight_plan(registration="LV-Y", flight_rules="V", icao_type="ZZZZ",
                            aircraft_name="Pipistrel Alpha Trainer",
                            dep_icao="SACO", dest_icao="SAAR", speed_kt=97, dof="260622")
    check("ZZZZ -> tipo en TYP/", "TYP/PIPISTREL ALPHA TRAINER" in fp3.message)
    check("ZZZZ -> NO usa RMK/TIPO", "RMK/TIPO" not in fp3.message)

    # Aerodromo sin ICAO -> ZZZZ + DEP/ con el nombre, en orden antes de DOF/
    fp4 = build_flight_plan(registration="LV-Z", flight_rules="V",
                            dep_icao="ZZZZ", dep_name="Campo La Esperanza",
                            dest_icao="SAAR", speed_kt=97, dof="260622")
    check("dep ZZZZ -> DEP/ en casilla 18", "DEP/CAMPO LA ESPERANZA" in fp4.message)
    check("orden DEP/ antes de DOF/", fp4.message.index("DEP/") < fp4.message.index("DOF/"))

    print("\n" + "=" * 64)
    print("  " + ("TODOS LOS TESTS PASARON" if ok else "ALGUNOS TESTS FALLARON"))
    print("=" * 64)
