"""
genetic.py
==========
Algoritmo Genetico para el modo "sugerida" del optimizador de rutas VFR.

Cromosoma: secuencia de aerodromos intermedios entre origen y destino.
  Representacion: [orig, inter_1, ..., inter_k, dest]  (lista de codigos ICAO)
  El origen y destino son fijos; solo evolucionan los intermedios.

Fitness (minimizar):
  F = 0.50 * t_norm + 0.30 * r_meteo + 0.20 * d_norm
  donde:
    t_norm  = tiempo_total / t_ref        (normalizado al tiempo de ruta directa)
    r_meteo = promedio R_total de los aerodromos del camino
    d_norm  = distancia_total / d_ref     (normalizado a la distancia directa)

Parametros por defecto:
  Poblacion : 40 cromosomas
  Generaciones: 80
  Crossover : 70%
  Mutacion  : 20% por gen
  Elitismo  : top 2 individuos pasan intactos
"""

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from data.airports import AIRPORTS, AirportInfo
    from route.graph import RouteGraph, build_graph, get_edge
    from route.performance import (
        haversine_km, leg_time_hours, leg_fuel_liters,
        CRUISE_KT, KT_TO_KMH,
    )
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS, AirportInfo
    from route.graph import RouteGraph, build_graph, get_edge
    from route.performance import (
        haversine_km, leg_time_hours, leg_fuel_liters,
        CRUISE_KT, KT_TO_KMH,
    )


# ── Pesos del fitness ────────────────────────────────────────────────────────
W_TIME   = 0.50
W_METEO  = 0.30
W_DIST   = 0.20

# ── Parametros del GA ────────────────────────────────────────────────────────
POP_SIZE        = 40
N_GENERATIONS   = 80
CROSSOVER_RATE  = 0.70
MUTATION_RATE   = 0.20
ELITE_SIZE      = 2


# ────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class GeneticResult:
    """Resultado del algoritmo genetico."""
    found        : bool
    path         : List[str]      # [origen, ..., destino] codigos ICAO
    fitness      : float          # valor fitness del mejor individuo (menor = mejor)
    total_dist_km: float
    total_time_h : float
    avg_r_meteo  : float          # promedio de riesgo meteorologico en la ruta
    generation   : int            # generacion en que se encontro el mejor
    mode         : str = "suggested"


# ────────────────────────────────────────────────────────────────────────────
# Evaluacion de cromosoma
# ────────────────────────────────────────────────────────────────────────────

def _route_metrics(
    path    : List[str],
    r_map   : Dict[str, float],
    airports: Dict[str, AirportInfo],
) -> Tuple[float, float, float]:
    """
    Calcula (distancia_km, tiempo_horas, r_meteo_promedio) de una ruta.

    path   : lista de codigos ICAO del camino completo
    r_map  : {codigo: r_total [0,1]}
    airports: diccionario de aerodromos
    """
    total_dist = 0.0
    total_time = 0.0
    r_values   = []

    for i in range(len(path) - 1):
        a = airports.get(path[i])
        b = airports.get(path[i + 1])
        if a is None or b is None:
            return float("inf"), float("inf"), 1.0

        dist = haversine_km(a.lat, a.lon, b.lat, b.lon)
        time = leg_time_hours(dist, CRUISE_KT)
        total_dist += dist
        total_time += time

    # r_meteo: promedio de todos los nodos del camino (excl. origen)
    for code in path[1:]:
        r_values.append(r_map.get(code, 0.0))

    avg_r = sum(r_values) / len(r_values) if r_values else 0.0
    return total_dist, total_time, avg_r


def _fitness(
    path      : List[str],
    r_map     : Dict[str, float],
    airports  : Dict[str, AirportInfo],
    ref_dist  : float,
    ref_time  : float,
) -> float:
    """
    Fitness del cromosoma (menor = mejor).

    Normaliza distancia y tiempo respecto a la ruta directa origen-destino.
    """
    dist, time, r_avg = _route_metrics(path, r_map, airports)
    if dist == float("inf"):
        return float("inf")

    d_norm = dist / ref_dist if ref_dist > 0 else 1.0
    t_norm = time / ref_time if ref_time > 0 else 1.0

    return W_TIME * t_norm + W_METEO * r_avg + W_DIST * d_norm


# ────────────────────────────────────────────────────────────────────────────
# Operadores geneticos
# ────────────────────────────────────────────────────────────────────────────

def _random_chromosome(
    origin    : str,
    dest      : str,
    all_codes : List[str],
    max_stops : int = 3,
) -> List[str]:
    """
    Genera un cromosoma aleatorio: [origen, intermedios..., destino].
    El numero de intermedios es 0..max_stops.
    """
    others = [c for c in all_codes if c not in (origin, dest)]
    n = random.randint(0, min(max_stops, len(others)))
    intermediates = random.sample(others, n)
    return [origin] + intermediates + [dest]


def _crossover(parent_a: List[str], parent_b: List[str]) -> Tuple[List[str], List[str]]:
    """
    Crossover por intercambio de segmentos intermedios.
    Preserva origen y destino fijos.
    """
    mid_a = parent_a[1:-1]
    mid_b = parent_b[1:-1]

    if not mid_a or not mid_b:
        return parent_a[:], parent_b[:]

    # Punto de corte aleatorio en los intermedios del mas largo
    cut_a = random.randint(0, len(mid_a))
    cut_b = random.randint(0, len(mid_b))

    child_mid_a = mid_a[:cut_a] + mid_b[cut_b:]
    child_mid_b = mid_b[:cut_b] + mid_a[cut_a:]

    # Eliminar duplicados preservando orden (un aerodromo no puede aparecer dos veces)
    def dedupe(lst):
        seen = set()
        return [x for x in lst if not (x in seen or seen.add(x))]

    origin, dest = parent_a[0], parent_a[-1]
    child_mid_a = [c for c in dedupe(child_mid_a) if c not in (origin, dest)]
    child_mid_b = [c for c in dedupe(child_mid_b) if c not in (origin, dest)]

    return [origin] + child_mid_a + [dest], [origin] + child_mid_b + [dest]


def _mutate(
    chromosome : List[str],
    all_codes  : List[str],
    mutation_rate: float = MUTATION_RATE,
) -> List[str]:
    """
    Mutacion sobre los intermedios del cromosoma.
    Tres operaciones posibles con probabilidad mutation_rate cada una:
      - swap     : intercambia dos intermedios de posicion
      - add      : inserta un aerodromo aleatorio no usado
      - remove   : elimina un intermedio aleatorio
    """
    if len(chromosome) < 2:
        return chromosome

    origin, dest = chromosome[0], chromosome[-1]
    mid = chromosome[1:-1]

    # swap
    if len(mid) >= 2 and random.random() < mutation_rate:
        i, j = random.sample(range(len(mid)), 2)
        mid[i], mid[j] = mid[j], mid[i]

    # add
    if random.random() < mutation_rate:
        candidates = [c for c in all_codes if c not in (origin, dest) and c not in mid]
        if candidates:
            pos = random.randint(0, len(mid))
            mid.insert(pos, random.choice(candidates))

    # remove
    if mid and random.random() < mutation_rate:
        mid.pop(random.randint(0, len(mid) - 1))

    return [origin] + mid + [dest]


# ────────────────────────────────────────────────────────────────────────────
# Algoritmo genetico principal
# ────────────────────────────────────────────────────────────────────────────

def genetic_optimize(
    origin        : str,
    dest          : str,
    r_map         : Optional[Dict[str, float]] = None,
    airports      : Optional[Dict[str, AirportInfo]] = None,
    pop_size      : int   = POP_SIZE,
    n_generations : int   = N_GENERATIONS,
    seed          : Optional[int] = None,
) -> GeneticResult:
    """
    Optimiza la ruta origen->destino usando el Algoritmo Genetico.

    Parametros
    ----------
    origin       : codigo ICAO del origen
    dest         : codigo ICAO del destino
    r_map        : {codigo: r_total [0,1]} — riesgo meteorologico por aerodromo
    airports     : diccionario de aerodromos (por defecto: AIRPORTS global)
    pop_size     : tamano de la poblacion
    n_generations: numero de generaciones
    seed         : semilla para reproducibilidad

    Retorna
    -------
    GeneticResult con la mejor ruta encontrada.
    """
    if seed is not None:
        random.seed(seed)

    aps   = airports if airports is not None else AIRPORTS
    r     = r_map if r_map is not None else {}

    if origin not in aps or dest not in aps:
        return GeneticResult(found=False, path=[], fitness=float("inf"),
                             total_dist_km=0.0, total_time_h=0.0, avg_r_meteo=0.0,
                             generation=0)

    all_codes = list(aps.keys())

    # Referencias para normalizacion (ruta directa)
    orig_info = aps[origin]
    dest_info = aps[dest]
    ref_dist = haversine_km(orig_info.lat, orig_info.lon, dest_info.lat, dest_info.lon)
    ref_time = leg_time_hours(ref_dist, CRUISE_KT)

    # Poblacion inicial
    population = [_random_chromosome(origin, dest, all_codes) for _ in range(pop_size)]
    # Asegurar que la ruta directa esta en la poblacion inicial
    population[0] = [origin, dest]

    best_individual = None
    best_fitness    = float("inf")
    best_generation = 0

    for gen in range(n_generations):
        # Evaluar fitness
        scored = [(ch, _fitness(ch, r, aps, ref_dist, ref_time)) for ch in population]
        scored.sort(key=lambda x: x[1])

        if scored[0][1] < best_fitness:
            best_fitness    = scored[0][1]
            best_individual = scored[0][0][:]
            best_generation = gen

        # Elitismo: los mejores pasan directamente
        new_pop = [scored[i][0][:] for i in range(min(ELITE_SIZE, len(scored)))]

        # Seleccion por torneo + crossover + mutacion
        while len(new_pop) < pop_size:
            # Seleccion por torneo (k=3)
            def tournament():
                contestants = random.choices(scored, k=3)
                return min(contestants, key=lambda x: x[1])[0]

            p1 = tournament()
            p2 = tournament()

            if random.random() < CROSSOVER_RATE:
                c1, c2 = _crossover(p1, p2)
            else:
                c1, c2 = p1[:], p2[:]

            c1 = _mutate(c1, all_codes)
            c2 = _mutate(c2, all_codes)

            new_pop.append(c1)
            if len(new_pop) < pop_size:
                new_pop.append(c2)

        population = new_pop

    if best_individual is None:
        return GeneticResult(found=False, path=[], fitness=float("inf"),
                             total_dist_km=0.0, total_time_h=0.0, avg_r_meteo=0.0,
                             generation=0)

    dist, time, r_avg = _route_metrics(best_individual, r, aps)

    return GeneticResult(
        found         = True,
        path          = best_individual,
        fitness       = round(best_fitness, 6),
        total_dist_km = round(dist, 1),
        total_time_h  = round(time, 3),
        avg_r_meteo   = round(r_avg, 3),
        generation    = best_generation,
    )


# ────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: route/genetic.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ── Ruta directa sin riesgo meteo ──
    r1 = genetic_optimize("SACC", "SAAG", seed=42)
    check("SACC-SAAG: found", r1.found)
    check("SACC-SAAG: empieza en SACC", r1.found and r1.path[0] == "SACC")
    check("SACC-SAAG: termina en SAAG", r1.found and r1.path[-1] == "SAAG")
    check("SACC-SAAG: distancia positiva", r1.found and r1.total_dist_km > 0)
    check("SACC-SAAG: tiempo positivo",    r1.found and r1.total_time_h > 0)
    print(f"\n  GA SACC-SAAG (sin riesgo): {' -> '.join(r1.path)}  "
          f"{r1.total_dist_km:.1f}km  fitness={r1.fitness:.4f}  gen={r1.generation}")

    # ── Con riesgo alto en destino directo: GA puede tomar ruta mas larga ──
    r_map_test = {code: 0.0 for code in AIRPORTS}
    r_map_test["SAAG"] = 0.9   # destino riesgoso
    r2 = genetic_optimize("SACC", "SAAG", r_map=r_map_test, seed=42)
    check("SACC-SAAG (SAAG riesgo 0.9): found", r2.found)
    print(f"  GA SACC-SAAG (SAAG riesgo 0.9): {' -> '.join(r2.path)}  "
          f"{r2.total_dist_km:.1f}km  avg_r={r2.avg_r_meteo:.3f}  fitness={r2.fitness:.4f}")

    # ── Nodo inexistente ──
    r_bad = genetic_optimize("XXXX", "SAAG", seed=0)
    check("Nodo inexistente: not found", not r_bad.found)

    # ── Ruta larga: SACC a SACB ──
    r3 = genetic_optimize("SACC", "SACB", seed=7, n_generations=40)
    check("SACC-SACB: found", r3.found)
    check("SACC-SACB: distancia >= directa", r3.found and r3.total_dist_km >= 300.0)
    print(f"  GA SACC-SACB: {' -> '.join(r3.path)}  "
          f"{r3.total_dist_km:.1f}km  fitness={r3.fitness:.4f}")

    # ── Fitness de ruta directa debe ser el menor con meteo 0 ──
    direct_dist = haversine_km(
        AIRPORTS["SACC"].lat, AIRPORTS["SACC"].lon,
        AIRPORTS["SAOM"].lat, AIRPORTS["SAOM"].lon,
    )
    direct_time = leg_time_hours(direct_dist, CRUISE_KT)
    direct_fitness = _fitness(["SACC", "SAOM"], {}, AIRPORTS, direct_dist, direct_time)
    check("Fitness ruta directa sin meteo == W_TIME + W_DIST == 0.70",
          abs(direct_fitness - (W_TIME + W_DIST)) < 0.001)

    # ── Operadores unitarios ──
    ch = ["SACC", "SAOE", "SAAG"]
    c1, c2 = _crossover(ch, ["SACC", "SABV", "SAAG"])
    check("Crossover preserva origen", c1[0] == "SACC" and c2[0] == "SACC")
    check("Crossover preserva destino", c1[-1] == "SAAG" and c2[-1] == "SAAG")

    mutated = _mutate(["SACC", "SAAG"], list(AIRPORTS.keys()))
    check("Mutate preserva origen", mutated[0] == "SACC")
    check("Mutate preserva destino", mutated[-1] == "SAAG")

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
