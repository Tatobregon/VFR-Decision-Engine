"""
notam_impact.py
===============
Evaluacion del impacto operacional de los NOTAM sobre el go/no-go.

Criterio (deliberadamente acotado, sin histeria): SOLO se eleva a NO GO cuando
el aerodromo es inutilizable, es decir:
  - Aerodromo cerrado (AD CLSD), o
  - TODAS las cabeceras de pista cerradas (RWY CLSD que cubren todas las pistas).

Cualquier otro NOTAM (radioayuda U/S, obstaculo, servicio degradado, etc.) NO
cambia el veredicto: se sigue mostrando como informacion, pero no bloquea.

Se detecta por Q-code (formato OACI Q + 2 letras de tema + 2 de condicion;
condicion "LC" = closed) y, como respaldo, por palabras clave en el texto.
"""

import re
from dataclasses import dataclass
from typing import List, Set


# Condiciones de cierre en Q-code (4a-5a letra) y temas (2a-3a letra)
_QCOND_CLOSED = {"LC"}
_QSUBJ_AERODROME = {"FA"}                 # FA = aerodromo
_QSUBJ_RUNWAY    = {"MR", "MS"}           # MR/MS = pista / superficie de movimiento

_AD_CLOSED_KW  = ("AD CLSD", "AERODROME CLOSED", "AERODROMO CERRADO",
                  "AERODROMO CLAUSURADO", "APT CLSD")
_RWY_CLOSED_KW = ("RWY", "PISTA")
_CLOSED_KW     = ("CLSD", "CLOSED", "CERRAD", "CLAUSURAD")

_RWY_DESIG_RE = re.compile(r"(?:RWY|PISTA)\s*0*([0-3]?\d)(?:\s*/\s*0*([0-3]?\d))?", re.I)


@dataclass
class NotamImpact:
    blocking: bool                 # True -> NO GO
    reason: str                    # descripcion del motivo (vacio si no bloquea)
    closed_runways: Set[str]       # designadores de pista cerrados detectados


def _qcode_parts(q_code: str):
    """Devuelve (tema, condicion) de un Q-code tipo 'QFALC', o (None, None)."""
    q = (q_code or "").strip().upper()
    if len(q) >= 5 and q.startswith("Q"):
        return q[1:3], q[3:5]
    return None, None


def runway_designators(headings: List[int]) -> Set[str]:
    """
    Conjunto de designadores de cabecera (ej. {'16','34'}) a partir de los
    rumbos de pista del aerodromo, incluyendo ambas cabeceras de cada pista.
    """
    desigs: Set[str] = set()
    for h in headings:
        if h is None:
            continue
        for hh in (h % 360, (h + 180) % 360):
            d = round(hh / 10.0) or 36
            if d == 0:
                d = 36
            desigs.add(f"{d:02d}")
    return desigs


def assess_notam_impact(notams: list, rwy_designators: Set[str]) -> NotamImpact:
    """
    Evalua una lista de NOTAM (objetos con .message y .q_code) contra el conjunto
    de cabeceras del aerodromo. Devuelve NotamImpact.

    Bloquea si: aerodromo cerrado, o las cabeceras cerradas detectadas cubren
    TODAS las del aerodromo (rwy_designators no vacio).
    """
    closed_rwys: Set[str] = set()

    for n in notams or []:
        msg = (getattr(n, "message", "") or "").upper()
        subj, cond = _qcode_parts(getattr(n, "q_code", ""))

        is_closed_qcode = cond in _QCOND_CLOSED
        text_has_closed = any(k in msg for k in _CLOSED_KW)

        # Aerodromo cerrado
        if (is_closed_qcode and subj in _QSUBJ_AERODROME) or \
           any(k in msg for k in _AD_CLOSED_KW):
            return NotamImpact(True, "Aerodromo cerrado (NOTAM AD CLSD)", closed_rwys)

        # Pista cerrada -> juntar designadores
        is_rwy = (subj in _QSUBJ_RUNWAY) or any(k in msg for k in _RWY_CLOSED_KW)
        if is_rwy and (is_closed_qcode or text_has_closed):
            found = _RWY_DESIG_RE.findall(msg)
            for a, b in found:
                for d in (a, b):
                    if d:
                        closed_rwys.add(f"{int(d):02d}")

    # Todas las cabeceras del aerodromo cerradas?
    if rwy_designators and closed_rwys and rwy_designators.issubset(closed_rwys):
        return NotamImpact(
            True,
            f"Todas las pistas cerradas (NOTAM RWY CLSD: {', '.join(sorted(closed_rwys))})",
            closed_rwys,
        )

    return NotamImpact(False, "", closed_rwys)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  TEST: notam_impact.py")
    print("=" * 64)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    @dataclass
    class _N:
        message: str = ""
        q_code: str = ""

    # Aerodromo de 1 pista (16/34)
    d_single = runway_designators([160])
    check("designadores de rumbo 160 = {16,34}", d_single == {"16", "34"})

    # AD cerrado por Q-code
    r = assess_notam_impact([_N("AERODROMO NO DISPONIBLE", "QFALC")], d_single)
    check("AD CLSD por Q-code -> bloquea", r.blocking)

    # AD cerrado por texto
    r = assess_notam_impact([_N("AD CLSD POR OBRAS", "")], d_single)
    check("AD CLSD por texto -> bloquea", r.blocking)

    # Pista unica cerrada -> todas cerradas -> bloquea
    r = assess_notam_impact([_N("RWY 16/34 CLSD MANTENIMIENTO", "QMRLC")], d_single)
    check("unica pista 16/34 cerrada -> bloquea", r.blocking)

    # Una sola cabecera cerrada en pista unica -> NO cubre ambas -> no bloquea
    r = assess_notam_impact([_N("RWY 16 CLSD", "QMRLC")], d_single)
    check("solo RWY 16 cerrada (de 16/34) -> NO bloquea", not r.blocking)

    # Aerodromo de 2 pistas (16/34 y 02/20): cerrar solo una pista NO bloquea
    d_double = runway_designators([160, 20])
    r = assess_notam_impact([_N("RWY 16/34 CLSD", "QMRLC")], d_double)
    check("2 pistas, 1 cerrada -> NO bloquea", not r.blocking)
    # Cerrar ambas pistas -> bloquea
    r = assess_notam_impact([
        _N("RWY 16/34 CLSD", "QMRLC"),
        _N("RWY 02/20 CLSD", "QMRLC"),
    ], d_double)
    check("2 pistas, ambas cerradas -> bloquea", r.blocking)

    # NOTAM no critico (radioayuda) -> no bloquea
    r = assess_notam_impact([_N("VOR CBA U/S", "QNVAS")], d_single)
    check("VOR U/S -> NO bloquea (informativo)", not r.blocking)

    # Sin NOTAMs -> no bloquea
    check("sin NOTAMs -> no bloquea", not assess_notam_impact([], d_single).blocking)

    print("\n" + "=" * 64)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 64)
