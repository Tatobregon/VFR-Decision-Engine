"""
personal_minima.py
==================
Minimos personales del piloto.

En aviacion general el go/no-go lo domina la experiencia del piloto, no solo el
minimo legal. Este modulo modela "minimos personales" por nivel de experiencia
que ENDURECEN los umbrales del soft scoring (no los pesos AHP):

  - vis_mult   : el piloto "percibe" la visibilidad dividida por este factor
                 (>1 => exige mas visibilidad para sentirse comodo).
  - ceil_mult  : idem para el techo.
  - xwind_mult : escala la tolerancia al cruzado (la r_xwind satura antes si <1).

Aplicacion (en risk/soft_scoring.compute_soft_score):
    r_visibility(vis / vis_mult)
    r_ceiling(ceil / ceil_mult)
    r_crosswind(xw, xw_max * xwind_mult)

El nivel "Avanzado" es la identidad (1,1,1) = minimos legales sin margen extra.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonalMinima:
    name       : str
    vis_mult   : float   # >1 = mas exigente en visibilidad
    ceil_mult  : float   # >1 = mas exigente en techo
    xwind_mult : float   # <1 = menos tolerante al cruzado


# Niveles (acordados con el usuario)
STUDENT  = PersonalMinima("Alumno",   vis_mult=1.6, ceil_mult=1.5, xwind_mult=0.6)
PPL      = PersonalMinima("PPL",      vis_mult=1.2, ceil_mult=1.2, xwind_mult=1.0)
ADVANCED = PersonalMinima("Avanzado", vis_mult=1.0, ceil_mult=1.0, xwind_mult=1.0)

# Identidad: sin ajuste (para callers internos que no usan minimos personales)
NEUTRAL  = PersonalMinima("Neutral",  vis_mult=1.0, ceil_mult=1.0, xwind_mult=1.0)

LEVELS = {
    "Alumno":   STUDENT,
    "PPL":      PPL,
    "Avanzado": ADVANCED,
}

DEFAULT = PPL
LEVEL_NAMES = ["Alumno", "PPL", "Avanzado"]


def get_minima(name: str) -> PersonalMinima:
    """Devuelve los minimos personales por nombre. Fallback al default (PPL)."""
    return LEVELS.get((name or "").strip(), DEFAULT)


if __name__ == "__main__":
    print("=" * 56)
    print("  TEST: personal_minima.py")
    print("=" * 56)
    ok = True
    def check(d, c):
        global ok; ok = ok and c
        print(f"  [{'OK' if c else 'FALLO'}] {d}")
    check("3 niveles", len(LEVELS) == 3)
    check("default = PPL", DEFAULT is PPL)
    check("Alumno mas exigente en vis que PPL", STUDENT.vis_mult > PPL.vis_mult)
    check("Avanzado es identidad", ADVANCED.vis_mult == 1.0 and ADVANCED.xwind_mult == 1.0)
    check("PPL cruzado = 1.0 (sabe aterrizar en cruzado)", PPL.xwind_mult == 1.0)
    check("Alumno cruzado = 0.6", STUDENT.xwind_mult == 0.6)
    check("get_minima('Alumno')", get_minima("Alumno") is STUDENT)
    check("get_minima(desconocido)->default", get_minima("xxx") is DEFAULT)
    print("=" * 56)
    print("  " + ("TODOS OK" if ok else "FALLO"))
