"""
ahp_weights.py
==============
Derivacion de los pesos del soft scoring mediante AHP (Analytic Hierarchy
Process, Saaty 1980).

Este modulo NO se importa en tiempo de ejecucion del motor: los pesos finales
estan fijados en risk/weights.py. Su proposito es DOCUMENTAR y REPRODUCIR de
donde salen esos pesos, de modo que la eleccion no sea arbitraria sino trazable
y auditable (apendice metodologico de la tesis).

Metodologia
-----------
Los 7 criterios del scoring se organizan en una jerarquia de 3 grupos, segun
lo que mide cada factor. Esto evita comparar criterios heterogeneos entre si
(ej. visibilidad vs riesgo TAF) y reduce las comparaciones de 21 a 8, lo que
ademas facilita mantener la consistencia.

    RIESGO VFR
    |
    +-- G1 · Referencia visual    -> {visibilidad, techo, niebla(spread)}
    +-- G2 · Viento               -> {cruzado, rafaga}
    +-- G3 · Fenomenos/tendencia  -> {wx presentes, riesgo TAF}

Cada matriz de comparacion usa la escala de Saaty (1, 3, 5, 7, 9 con
intermedios 2, 4, 6, 8). Los juicios de a pares estan fundamentados en
accidentologia de aviacion general (la perdida de referencia visual --
VFR-into-IMC -- es la causa de mayor letalidad; la perdida de control por
viento es la segunda; los fenomenos peligrosos "duros" ya estan cubiertos por
hard_blockers.py, por lo que en el score blando pesan menos).

El vector de prioridades se calcula por el metodo de la media geometrica de
filas (aproximacion estandar del autovector principal). La consistencia se
valida con el Consistency Ratio (CR); se considera aceptable CR <= 0.10.

Peso global de cada criterio = (peso del grupo) x (peso local dentro del grupo).
"""

from typing import Dict, List, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Indice de consistencia aleatorio (Saaty) segun el orden n de la matriz
# ──────────────────────────────────────────────────────────────────────────────

_RANDOM_INDEX = {
    1: 0.00, 2: 0.00, 3: 0.58, 4: 0.90, 5: 1.12,
    6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49,
}


# ──────────────────────────────────────────────────────────────────────────────
# Calculo del vector de prioridades y la consistencia de una matriz de a pares
# ──────────────────────────────────────────────────────────────────────────────

def _geometric_mean(row: List[float]) -> float:
    """Media geometrica de una fila."""
    prod = 1.0
    for x in row:
        prod *= x
    return prod ** (1.0 / len(row))


def priority_vector(matrix: List[List[float]]) -> Tuple[List[float], float, float, float]:
    """
    Calcula el vector de prioridades (pesos) de una matriz de comparacion
    reciproca por el metodo de la media geometrica.

    Devuelve (weights, lambda_max, CI, CR):
      - weights    : pesos normalizados (suman 1.0)
      - lambda_max : autovalor principal estimado
      - CI         : indice de consistencia = (lambda_max - n) / (n - 1)
      - CR         : razon de consistencia = CI / RI(n). Aceptable si <= 0.10
    """
    n = len(matrix)
    gm = [_geometric_mean(row) for row in matrix]
    total = sum(gm)
    weights = [g / total for g in gm]

    # lambda_max: promedio de (A·w)_i / w_i sobre todas las filas
    lambda_max = 0.0
    for i in range(n):
        weighted_sum = sum(matrix[i][j] * weights[j] for j in range(n))
        lambda_max += weighted_sum / weights[i]
    lambda_max /= n

    ci = (lambda_max - n) / (n - 1) if n > 1 else 0.0
    ri = _RANDOM_INDEX.get(n, 1.49)
    cr = ci / ri if ri > 0 else 0.0
    return weights, lambda_max, ci, cr


# ──────────────────────────────────────────────────────────────────────────────
# Matrices de comparacion de a pares (juicios expertos fundamentados)
# ──────────────────────────────────────────────────────────────────────────────

# Nivel 1 — entre grupos. Orden: [Ref.Visual, Viento, Fenomenos/Tendencia]
#   RV vs V  = 3 : la perdida de referencia visual (VFR-into-IMC) es la causa
#                  de mayor letalidad; pesa moderadamente mas que el viento.
#   RV vs FT = 4 : los fenomenos peligrosos duros ya estan bloqueados; lo que
#                  resta en el score blando es claramente secundario.
#   V  vs FT = 3
_GROUP_LABELS = ["Referencia visual", "Viento", "Fenomenos/tendencia"]
_M_GROUPS = [
    [1,     3,     4    ],
    [1 / 3, 1,     3    ],
    [1 / 4, 1 / 3, 1    ],
]

# Nivel 2a — dentro de "Referencia visual". Orden: [visibilidad, techo, niebla]
#   vis vs techo = 1 : ambos son el minimo legal VFR, co-iguales.
#   vis vs niebla = 5: la visibilidad medida domina a un proxy por spread.
_RV_LABELS = ["visibility", "ceiling", "fog"]
_M_RV = [
    [1,     1,     5],
    [1,     1,     5],
    [1 / 5, 1 / 5, 1],
]

# Nivel 2b — dentro de "Viento". Orden: [cruzado, rafaga]
#   cruzado vs rafaga = 2 : el cruzado es el desafio de control primario.
_V_LABELS = ["crosswind", "gust"]
_M_V = [
    [1,     2],
    [1 / 2, 1],
]

# Nivel 2c — dentro de "Fenomenos/tendencia". Orden: [wx presentes, TAF]
#   wx vs TAF = 2 : la condicion presente es mas cierta que el pronostico.
_FT_LABELS = ["wx", "taf"]
_M_FT = [
    [1,     2],
    [1 / 2, 1],
]


# ──────────────────────────────────────────────────────────────────────────────
# Composicion de la jerarquia -> pesos globales
# ──────────────────────────────────────────────────────────────────────────────

def compute_global_weights() -> Dict[str, float]:
    """
    Resuelve la jerarquia AHP y devuelve el peso global de cada criterio.

    Clave del dict = nombre del criterio (alineado con las constantes de
    risk/weights.py): visibility, ceiling, crosswind, gust, wx, fog, taf.
    """
    group_w, *_ = priority_vector(_M_GROUPS)        # [RV, V, FT]
    rv_w, *_ = priority_vector(_M_RV)               # [vis, ceil, fog]
    v_w, *_ = priority_vector(_M_V)                 # [xw, gust]
    ft_w, *_ = priority_vector(_M_FT)               # [wx, taf]

    w_rv, w_v, w_ft = group_w
    return {
        "visibility": w_rv * rv_w[0],
        "ceiling":    w_rv * rv_w[1],
        "fog":        w_rv * rv_w[2],
        "crosswind":  w_v * v_w[0],
        "gust":       w_v * v_w[1],
        "wx":         w_ft * ft_w[0],
        "taf":        w_ft * ft_w[1],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Reporte / verificacion standalone
# ──────────────────────────────────────────────────────────────────────────────

def _print_matrix_report(title: str, labels: List[str], matrix: List[List[float]]) -> None:
    weights, lambda_max, ci, cr = priority_vector(matrix)
    print(f"\n  {title}")
    for lab, w in zip(labels, weights):
        print(f"    {lab:<20s} {w:6.4f}")
    status = "OK" if cr <= 0.10 else "INCONSISTENTE"
    print(f"    lambda_max={lambda_max:.4f}  CI={ci:.4f}  CR={cr:.4f}  [{status}]")


if __name__ == "__main__":
    print("=" * 64)
    print("  DERIVACION AHP DE LOS PESOS DEL SOFT SCORING")
    print("=" * 64)

    _print_matrix_report("Nivel 1 — grupos", _GROUP_LABELS, _M_GROUPS)
    _print_matrix_report("Nivel 2a — Referencia visual", _RV_LABELS, _M_RV)
    _print_matrix_report("Nivel 2b — Viento", _V_LABELS, _M_V)
    _print_matrix_report("Nivel 2c — Fenomenos/tendencia", _FT_LABELS, _M_FT)

    glob = compute_global_weights()
    print("\n" + "-" * 64)
    print("  PESOS GLOBALES = peso_grupo x peso_local")
    print("-" * 64)
    order = ["visibility", "ceiling", "crosswind", "gust", "wx", "fog", "taf"]
    for k in order:
        print(f"    W_{k.upper():<10s} {glob[k]:6.4f}   (3 dec: {glob[k]:.3f})")
    print(f"\n    SUMA = {sum(glob.values()):.6f}")

    # Verificacion: todas las matrices consistentes y suma ~1.0
    all_cr_ok = all(
        priority_vector(m)[3] <= 0.10
        for m in (_M_GROUPS, _M_RV, _M_V, _M_FT)
    )
    sum_ok = abs(sum(glob.values()) - 1.0) < 1e-9
    print("\n" + "=" * 64)
    print(f"  Consistencia (CR<=0.10 en todas): {'OK' if all_cr_ok else 'FALLO'}")
    print(f"  Suma de pesos = 1.0:              {'OK' if sum_ok else 'FALLO'}")
    print("=" * 64)
