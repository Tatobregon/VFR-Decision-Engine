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

Cada matriz de comparacion usa la escala de Saaty (1..9 con reciprocos).

DERIVACION DE LOS JUICIOS — operacion explicita
-----------------------------------------------
La escala de Saaty es una escala de RAZON: a_ij debe aproximar w_i / w_j. Por
lo tanto, si se dispone de un indice de riesgo por criterio, el cociente de
indices NO es una interpretacion de la evidencia sino la entrada correcta de
la matriz. La operacion aplicada es:

    a_ij = redondeo_Saaty( I_i / I_j )          I = probabilidad x severidad

donde I es el indice de riesgo del Doc 9859 de OACI (Manual de gestion de la
seguridad operacional): la probabilidad de ocurrencia por la severidad de la
consecuencia. Sobre datos de accidentologia esa definicion se reduce a:

    I = (n_cat / N_total) x (n_fatales_cat / n_cat) = n_fatales_cat / N_total

es decir, la participacion de la categoria en el total de accidentes fatales.
El mismo indice es el que aplica la Junta de Seguridad en el Transporte (JST)
argentina en su Anuario Estadistico para clasificar el riesgo por categoria
de suceso OACI/ADREP.

PROCEDENCIA DE CADA ENTRADA
---------------------------
No todas las comparaciones admiten anclaje empirico. Cada entrada se declara:

    (E) EVIDENCIA  -> derivada del cociente de indices de accidentologia
    (N) NORMA      -> derivada de la regulacion o de la arquitectura del motor
    (D) DERIVADA   -> impuesta por transitividad de las anteriores
    (J) JUICIO     -> juicio experto declarado, sin anclaje cuantitativo

El residuo subjetivo de las entradas (J) queda ACOTADO por el analisis de
sensibilidad (risk/sensitivity.py): si el veredicto es estable ante
perturbaciones de los pesos, la precision de cada juicio individual deja de
ser critica.

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

# ──────────────────────────────────────────────────────────────────────────────
# EVIDENCIA — accidentologia de referencia
# ──────────────────────────────────────────────────────────────────────────────
# Serie decenal 2007-2016, aviacion general NO COMERCIAL de ala fija.
# Fuente: AOPA Air Safety Institute, 28.º Joseph T. Nall Report (2019),
#         figuras 1.1.1 (aterrizaje) y 1.7.1 (meteorologia).
# Se usa una serie de 10 anios y no un anio suelto porque el cociente es muy
# sensible al ruido interanual (en 2016 la letalidad meteorologica cayo a 52%
# frente a un promedio decenal de 73%).
#
# Corroboracion nacional: JST (Argentina), Anuario Estadistico 2020, aviacion
# general 2015-2020. La categoria UIMC (vuelo no intencionado en IMC) presenta
# severidad 100%; las categorias de pista y control en tierra (RE, LOC-G, ARC)
# presentan "elevada probabilidad, baja severidad". La muestra argentina (27
# accidentes en 2020, 4 fatales) es demasiado chica para un cociente estable,
# por lo que se usa para confirmar la DIRECCION y el ORDEN DE MAGNITUD, no
# para calcular el valor. Es una limitacion declarada.

_EVIDENCE = {
    # grupo: (accidentes totales, accidentes fatales, categoria de la fuente)
    "Referencia visual": (473,  345, "Weather (Nall, fig. 1.7.1)"),
    "Viento":            (3410,  51, "Landing (Nall, fig. 1.1.1)"),
}

_SAATY = [1, 2, 3, 4, 5, 6, 7, 8, 9]


def to_saaty(ratio: float) -> float:
    """
    Discretiza un cociente de indices al valor mas proximo de la escala de
    Saaty. El techo de 9 no es arbitrario: mas alla de un orden de magnitud
    los criterios dejan de ser conmensurables y no deben compensarse entre si
    (ver nota sobre la barrera no-compensatoria mas abajo).
    """
    if ratio >= 1.0:
        return float(min(_SAATY, key=lambda s: abs(s - ratio)))
    return 1.0 / min(_SAATY, key=lambda s: abs(s - 1.0 / ratio))


def evidence_ratio(group_a: str, group_b: str) -> float:
    """Cociente de indices de riesgo I_a / I_b = fatales_a / fatales_b."""
    return _EVIDENCE[group_a][1] / _EVIDENCE[group_b][1]


# Nivel 1 — entre grupos. Orden: [Ref.Visual, Viento, Fenomenos/Tendencia]
#
#   a(RV, V)  = 7   (E) EVIDENCIA
#       I_RV / I_V = 345 / 51 = 6.76  ->  Saaty 7.
#       Severidades subyacentes: meteorologia 72.9%, aterrizaje 1.5%.
#
#   a(V, FT)  = 3   (N) NORMA / ARQUITECTURA
#       Los fenomenos peligrosos (TS, GR, FZRA, FC, VA...) NO llegan al score
#       blando: los extrae la capa categorica (hard_blockers.py). Lo que queda
#       en G3 es el residuo no bloqueante (llovizna, bruma) mas la tendencia
#       pronosticada, que por construccion es secundario frente al viento.
#       No existe categoria de accidentologia para ese residuo: se declara
#       como juicio fundado en la arquitectura, no en datos.
#
#   a(RV, FT) = 9   (D) DERIVADA + TOPE DE ESCALA
#       Por transitividad seria 7 x 3 = 21, fuera del rango de la escala.
#       Se fija en el tope 9. El CR resultante (0.069) mide exactamente esa
#       truncacion y se mantiene dentro del limite admisible de Saaty.
#
# NOTA METODOLOGICA — por que el cruzado no pesa mas.
# Si el indice se calcula con severidad pura (72.9% vs 1.5%), el cociente da
# ~49: casi seis veces el tope de la escala. Que la evidencia exceda el rango
# representable NO es un defecto del dato sino la senial formal de que los dos
# criterios no son conmensurables y no deben compensarse dentro de la misma
# suma ponderada. Esa es la justificacion cuantitativa de que el viento cruzado
# se trate en la BARRERA NO-COMPENSATORIA (soft_scoring.conjunctive_floor) y no
# solo por su peso: el peso AHP carga unicamente su contribucion residual
# dentro de la banda admisible; el resto lo carga la barrera.
_GROUP_LABELS = ["Referencia visual", "Viento", "Fenomenos/tendencia"]

_A_RV_V = to_saaty(evidence_ratio("Referencia visual", "Viento"))   # (E) -> 7
_A_V_FT = 3.0                                                       # (N)
_A_RV_FT = min(_A_RV_V * _A_V_FT, 9.0)                              # (D) + tope

_M_GROUPS = [
    [1,             _A_RV_V,  _A_RV_FT],
    [1 / _A_RV_V,   1,        _A_V_FT ],
    [1 / _A_RV_FT,  1 / _A_V_FT,     1],
]

# Nivel 2a — dentro de "Referencia visual". Orden: [visibilidad, techo, niebla]
#   vis vs techo  = 1  (N) NORMA: ambos son el minimo legal VFR, co-iguales.
#                      La RAAC 91 los exige conjuntamente, sin jerarquia.
#   vis vs niebla = 5  (J) JUICIO: la visibilidad es una magnitud MEDIDA; la
#                      niebla entra al score como PROXY derivado del spread
#                      T/Td, con incertidumbre propia. Se le asigna un peso
#                      claramente menor por ser una estimacion indirecta.
_RV_LABELS = ["visibility", "ceiling", "fog"]
_M_RV = [
    [1,     1,     5],
    [1,     1,     5],
    [1 / 5, 1 / 5, 1],
]

# Nivel 2b — dentro de "Viento". Orden: [cruzado, rafaga]
#   cruzado vs rafaga = 2  (J) JUICIO: el cruzado es el desafio de control
#                          primario y es el que tiene limite publicado por
#                          aeronave (maximo demostrado); la rafaga agrava pero
#                          no define por si sola la maniobra. La accidentologia
#                          disponible no separa ambas causas, por lo que la
#                          entrada no admite anclaje empirico.
_V_LABELS = ["crosswind", "gust"]
_M_V = [
    [1,     2],
    [1 / 2, 1],
]

# Nivel 2c — dentro de "Fenomenos/tendencia". Orden: [wx presentes, TAF]
#   wx vs TAF = 2  (J) JUICIO EPISTEMICO: la condicion observada es mas cierta
#                  que la pronosticada. No es una comparacion de peligrosidad
#                  sino de confiabilidad del dato.
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


def _print_evidence_report() -> None:
    """Muestra la traduccion evidencia -> escala de Saaty, paso por paso."""
    print("\n  EVIDENCIA -> ESCALA DE SAATY")
    print("  " + "-" * 62)
    print(f"    {'grupo':<20s} {'acc.':>6s} {'fatales':>8s} {'severidad':>10s}   fuente")
    for g, (tot, fat, src) in _EVIDENCE.items():
        print(f"    {g:<20s} {tot:6d} {fat:8d} {fat / tot:9.1%}   {src}")
    ratio = evidence_ratio("Referencia visual", "Viento")
    print(f"\n    I = probabilidad x severidad = n_fatales / N_total")
    print(f"    I(Ref.visual) / I(Viento) = {_EVIDENCE['Referencia visual'][1]}"
          f" / {_EVIDENCE['Viento'][1]} = {ratio:.2f}")
    print(f"    a(RV,V) = redondeo_Saaty({ratio:.2f}) = {_A_RV_V:.0f}   [E] evidencia")
    print(f"    a(V,FT) = {_A_V_FT:.0f}   [N] norma/arquitectura (fenomenos duros ya bloqueados)")
    print(f"    a(RV,FT) = min({_A_RV_V:.0f} x {_A_V_FT:.0f}, 9) = {_A_RV_FT:.0f}"
          f"   [D] transitiva, truncada al tope de la escala")


if __name__ == "__main__":
    print("=" * 64)
    print("  DERIVACION AHP DE LOS PESOS DEL SOFT SCORING")
    print("=" * 64)

    _print_evidence_report()
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
