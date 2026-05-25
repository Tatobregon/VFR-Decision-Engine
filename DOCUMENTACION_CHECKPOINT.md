# VFR GO/NO GO — Documentación de Checkpoint
**Fecha:** Mayo 2026  
**Versión:** v1.2 (Escalado nacional)  
**Estado:** Operativo y funcional

---

## 1. Descripción General del Sistema

Sistema de decisión meteorológica para vuelos VFR de aviación general en Argentina. Evalúa condiciones meteorológicas reales y pronosticadas para un par origen-destino y devuelve una decisión **GO / CAUTION / NO GO** junto con un briefing completo para el piloto.

### Alcance

| Parámetro | Valor |
|---|---|
| Aeropuertos cubiertos | 710 aeródromos `small_airport` de Argentina (OurAirports) |
| Aeronaves disponibles | Pipistrel Alpha Trainer, Cessna 172, Piper PA-28, Cessna 152, Diamond DA40 |
| Fuente meteorológica principal | Open-Meteo NWP (sin API key, todos los aeródromos) |
| Fuente meteorológica secundaria | aviationweather.gov METAR+TAF (aeródromos con observación) |
| Normativa aplicada | ANAC Argentina / OACI (NO FAA) |
| Interfaz disponible | CLI (`main.py`) + GUI Tkinter (`gui.py`) |

---

## 2. Arquitectura en Capas

```
┌─────────────────────────────────────────────────────────┐
│                     GUI / CLI                           │
│              gui.py (1151 líneas)                       │
│              main.py (174 líneas)                       │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│               INTEGRATION LAYER                         │
│           decision/engine.py (421 líneas)               │
│       output/formatter.py  output/briefing.py           │
└──────┬──────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│                  RISK ENGINE                            │
│   hard_blockers.py  soft_scoring.py  weights.py        │
│         aircraft_profiles.py                           │
└──────┬──────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│                  FEATURE LAYER                          │
│  crosswind.py  fog_risk.py  taf_window.py               │
│  flight_category.py  orographic.py                     │
└──────┬──────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│                  PARSING LAYER                          │
│   metar_parser.py  openmeteo_adapter.py  taf_parser.py │
│           [Contrato central: ParsedWeather]             │
└──────┬──────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│                   DATA LAYER                            │
│  fetcher_openmeteo.py  fetcher_aviationweather.py       │
│  airports.py  airspace.py  terrain.py                  │
│       [CSVs: ar-airports.csv, runways.csv]             │
└─────────────────────────────────────────────────────────┘

                ┌───────────────────────┐
                │   ROUTE OPTIMIZATION  │
                │ optimizer.py astar.py │
                │ genetic.py  graph.py  │
                │   performance.py      │
                └───────────────────────┘
```

---

## 3. Estructura de Archivos

```
Tesis_2.0/
│
├── main.py                          CLI entry point
├── gui.py                           GUI Tkinter (3 tabs: EVALUACION / RUTAS / BRIEFING)
├── config.py                        Constantes globales (NWP_STATIONS, METAR_STATIONS)
├── fetcher_aviationweather_1.py     METAR+TAF legacy (conservado por compatibilidad)
├── CLAUDE.md                        Instrucciones del proyecto para Claude Code
├── DOCUMENTACION_CHECKPOINT.md      Este archivo
│
├── data/
│   ├── airports.py                  Registro canónico de aeródromos (CSV loader)
│   ├── fetcher_aviationweather.py   Re-export de fetcher_aviationweather_1.py
│   ├── fetcher_openmeteo.py         Pronóstico NWP Open-Meteo
│   ├── airspace.py                  Zonas de espacio aéreo restringido/controlado
│   ├── terrain.py                   Perfil de terreno (Open-Topo-Data SRTM 30m)
│   ├── ar-airports.csv              Base de datos de aeropuertos argentinos (OurAirports)
│   └── runways.csv                  Base de datos de cabeceras de pista (OurAirports)
│
├── parsers/
│   ├── metar_parser.py              RawMetar → ParsedWeather (contrato central)
│   ├── openmeteo_adapter.py         RawNWPHour → ParsedWeather
│   └── taf_parser.py                RawTaf → ParsedTaf + ParsedTafPeriod
│
├── features/
│   ├── crosswind.py                 Viento cruzado + riesgo por pista
│   ├── fog_risk.py                  Riesgo de niebla (spread T/Td + tokens wx)
│   ├── taf_window.py                Análisis TAF en ventana temporal de vuelo
│   ├── flight_category.py           Categoría ANAC vs mínimos de aeronave
│   └── orographic.py               Penalización orográfica (SACC sierra: +0.05)
│
├── risk/
│   ├── aircraft_profiles.py         Perfiles de aeronave (AircraftProfile dataclass)
│   ├── hard_blockers.py             Bloqueadores duros: NO GO inmediato
│   ├── soft_scoring.py              R_total = Σ(w_i × r_i)
│   └── weights.py                   Pesos, funciones r_i, thresholds GO/CAUTION/NO GO
│
├── route/
│   ├── optimizer.py                 Interfaz unificada (delega a A* o GA)
│   ├── astar.py                     Algoritmo A* con heurística haversine
│   ├── genetic.py                   Algoritmo genético multi-objetivo
│   ├── graph.py                     Construcción de grafo con max_leg_km
│   └── performance.py               Cálculos de rendimiento de aeronave
│
├── decision/
│   └── engine.py                    DecisionEngine: pipeline completo
│
└── output/
    ├── formatter.py                 format_decision() + format_short()
    └── briefing.py                  generate_briefing() para el piloto
```

---

## 4. Módulos — Documentación Detallada

### 4.1 DATA LAYER

#### `data/airports.py`
Registro canónico de aeródromos. Carga y parsea dos CSVs de OurAirports al importar:

- **`ar-airports.csv`**: 710 aeródromos `small_airport` en Argentina. Columnas usadas: `ident`, `type`, `name`, `latitude_deg`, `longitude_deg`, `elevation_ft`, `municipality`, `region_name`, `icao_code`.
- **`runways.csv`**: Base de datos global de cabeceras de pista. JOIN por `ident` ↔ `airport_ident`.

**Estrategia de heading (prioridad):**
1. Campo `le_heading_degT` / `he_heading_degT` si está disponible (valor numérico preciso).
2. Derivado desde `le_ident` / `he_ident`: `"14"` → 140°, `"32"` → 320° (multiplicar por 10).
3. Si solo una cabecera es conocida, la opuesta se deriva sumando 180° mod 360.

**Resultado actual:**
- 710 aeródromos cargados
- 429 con datos de cabecera de pista (de los cuales ninguno con menos de 2 cabeceras)
- 281 sin datos de pista en el dataset fuente
- 29 con código ICAO propio (`SA??`), 681 con código OurAirports (`AR-XXXX`)
- 680 con elevación conocida, 30 estimada

**API pública:**
```python
AIRPORTS: Dict[str, AirportInfo]          # {ident: AirportInfo} — dict principal
AIRPORTS_BY_NAME: Dict[str, AirportInfo]  # {name: AirportInfo}
AIRPORT_NAMES: List[str]                  # lista de nombres

get_by_name(name: str) -> Optional[AirportInfo]
get_by_code(code: str) -> Optional[AirportInfo]
search_airports(query: str, province: str = None) -> List[AirportInfo]
```

**Dataclasses:**
```python
@dataclass(frozen=True)
class RunwayInfo:
    label  : str   # "Pista 14  -  140 grados"
    heading: int   # grados verdaderos

@dataclass(frozen=True)
class AirportInfo:
    code          : str
    name          : str
    lat, lon      : float
    elev_ft       : int              # pies AMSL (0 si desconocida)
    runways       : Tuple[RunwayInfo, ...]
    municipality  : str
    province      : str
    icao_code     : Optional[str]    # código ICAO real si existe
    elev_estimated: bool
```

---

#### `data/fetcher_openmeteo.py`
Obtiene pronóstico NWP punto a punto de Open-Meteo (sin API key).

- **Variables solicitadas**: `temperature_2m`, `dewpoint_2m`, `precipitation`, `weathercode`, `cloudcover`, `cloudcover_low/mid/high`, `windspeed_10m`, `winddirection_10m`, `windgusts_10m`, `visibility`, `surface_pressure`
- **Horizonte temporal**: `NWP_HOURS_AHEAD = 12` horas
- **Mock incluido**: `fetch_nwp(station_id, mock=True)` devuelve datos de prueba para SACC
- **Retries**: 3 intentos con delay incremental ante timeout/ConnectionError
- **Código 204**: devuelve `None`, no lanza excepción

**Salida:**
```python
@dataclass
class RawNWP:
    station_id: str
    hours: List[RawNWPHour]

@dataclass
class RawNWPHour:
    time: int           # Unix timestamp UTC
    temp_c, dewpoint_c, precip_mm, weathercode: ...
    cloudcover_pct, cloudcover_low/mid/high_pct: int
    windspeed_kmh, winddir_deg, windgust_kmh: float
    visibility_m: float
    pressure_hpa: float
```

---

#### `data/fetcher_aviationweather.py`
Re-exporta desde `fetcher_aviationweather_1.py` (en raíz).

Obtiene METAR + TAF de `aviationweather.gov` para aeródromos con observación (SACO, SAVY, SADX, SANC, etc.). Sin API key. Mock de SACO incluido.

**Salida:**
```python
RawMetar, RawTaf, RawTafPeriod
```

---

#### `data/airspace.py`
Zonas de espacio aéreo restringido y controlado de Argentina. Polígonos hardcoded (no requiere API externa).

**API:**
```python
zones_along_route(lat1, lon1, lat2, lon2,
                  check_restricted=True,
                  check_controlled=False) -> List[AirspaceZone]

@dataclass
class AirspaceZone:
    name: str
    is_restricted: bool
    is_controlled: bool
    # geometría interna
```

---

#### `data/terrain.py`
Perfil de elevación del terreno usando Open-Topo-Data (SRTM 30m). Usado para cálculo de altitud mínima segura en rutas.

**API:**
```python
get_elevations_m(points: List[Tuple[float,float]]) -> List[float]
terrain_profile_for_route(lat1, lon1, lat2, lon2, n_points=20) -> List[float]
```

---

### 4.2 PARSING LAYER

#### Contrato Central: `ParsedWeather`
Definido en `parsers/metar_parser.py`. Es el **único tipo** que consumen el feature layer y el risk engine. Tanto `MetarParser` como `OpenMeteoAdapter` lo producen.

```python
@dataclass
class ParsedWeather:
    # Identificación
    source        : str            # "metar" | "nwp"
    station_id    : str
    obs_time      : int            # Unix timestamp UTC
    nwp_estimated : bool           # True si es NWP (no observación directa)

    # Viento (kt)
    wind_dir      : Optional[int]   # grados 0-360; None si VRB
    wind_spd_kt   : Optional[float]
    wind_gust_kt  : Optional[float]
    wind_variable : bool

    # Visibilidad (km)
    visibility_km : Optional[float]  # 10.0 = CAVOK / "9999"

    # Nubosidad (ft AGL)
    ceiling_ft    : Optional[int]    # None = CLR o solo FEW/SCT
    sky_layers    : list             # [{"cover": "BKN", "base_ft": 2500}, ...]

    # Termodinámica (°C)
    temp_c        : Optional[float]
    dewpoint_c    : Optional[float]
    spread_c      : Optional[float]   # T - Td; proxy niebla

    # Presión (hPa)
    altimeter_hpa : Optional[float]

    # Fenómenos
    wx_codes      : list              # ["-RA", "BR"], ["TSRA"], etc.

    # Categoría ANAC/OACI
    flight_category : Optional[str]  # "VFR" | "MVFR" | "IFR" | "LIFR"

    # Posición
    lat, lon, elevation_m, station_name, raw_string
```

**Regla clave**: `ceiling_ft = None` significa cielo despejado o solo FEW/SCT. El risk engine lo trata como 99999 ft (sin restricción).

---

#### `parsers/metar_parser.py`
Parsea `RawMetar` → `ParsedWeather`. Maneja:
- Visibilidad ICAO (metros) y SM (millas)
- Ceiling (BKN/OVC) vs capas no-ceiling (FEW/SCT)
- Spread T/Td
- Categoría ANAC/OACI (VFR/MVFR/IFR/LIFR)
- Tokens wx: -RA, TSRA, BR, FG, etc.

---

#### `parsers/openmeteo_adapter.py`
Convierte `RawNWPHour` → `ParsedWeather`. Traduce:
- % de cobertura nubosa → capas estimadas (FEW/SCT/BKN/OVC)
- WMO codes → tokens wx (ej: code 95 → "TSRA")
- Velocidad de viento km/h → kt

---

#### `parsers/taf_parser.py`
Define `ParsedTaf` + `ParsedTafPeriod`. Parsea `RawTaf`. Normaliza TEMPO/BECMG/PROB, calcula `is_transient`.

```python
@dataclass
class ParsedTafPeriod:
    time_from, time_to   : int
    change_indicator     : Optional[str]  # None=BASE, "TEMPO", "BECMG", "PROB30", "PROB40"
    probability          : Optional[int]
    is_transient         : bool
    # + mismos campos de viento/vis/ceiling/wx que ParsedWeather
    flight_category      : Optional[str]
```

---

### 4.3 FEATURE LAYER

#### `features/crosswind.py`
```python
compute_crosswind(wind_dir, wind_spd_kt, runway_heading) -> float
crosswind_risk_score(xw_kt, aircraft) -> float  # 0-1, lineal hasta max_kt
```
- VRB: calcula el peor caso conservador (viento máximo en cualquier dirección)
- `xw >= crosswind_max_kt` → r = 1.0

---

#### `features/fog_risk.py`
```python
r_fog = max(r_spread, r_wx)
```
- `r_spread`: lineal entre 5°C (r=0) y 2°C (r=1) usando spread T/Td
- `r_wx`: matching exacto por token wx (BR=0.3, FG=0.8, etc.)

---

#### `features/taf_window.py`
Analiza el TAF en la ventana temporal `[hora_despegue, hora_despegue + duración]`.
- Herencia BASE→TEMPO/BECMG: campos `None` en TEMPO/BECMG se heredan del BASE
- Worst-case: el periodo más restrictivo en la ventana
- `r_taf`: escalonado por tipo de deterioro
- `next_go_from`: primer timestamp futuro donde el TAF vuelve a ser GO

---

#### `features/flight_category.py`
Categorías ANAC/OACI (umbrales):
```
VFR  : vis >= 5.0 km  AND  ceil >= 1000 ft
MVFR : vis >= 3.0 km  AND  ceil >=  500 ft
IFR  : vis >= 0.8 km  AND  ceil >=  200 ft
LIFR : vis <  0.8 km   OR  ceil <   200 ft
```
La excepción OACI para ≤140 kt NO se implementa (criterio conservador).

---

#### `features/orographic.py`
```python
delta_r = 0.05  # si nwp_estimated=True y station_id="SACC"
```
Penalización por orografía compleja (sierra cordobesa). Dict extensible para futuros aeródromos.

---

### 4.4 RISK ENGINE

#### `risk/aircraft_profiles.py`
```python
@dataclass(frozen=True)
class AircraftProfile:
    name               : str
    crosswind_max_kt   : float
    gust_max_kt        : float
    vis_min_km         : float
    ceiling_min_ft     : int
    vs0_kt             : float
    cruise_kt          : float
    fuel_flow_lph      : float
    fuel_capacity_l    : float
    fuel_reserve_min   : int
    category           : str   # "LSA", "SEP", "MEP"

    # Propiedades calculadas:
    @property fuel_reserve_l(self) -> float
    @property fuel_usable_l(self) -> float
    @property range_km(self) -> float  # endurance × cruise_kt × KT_TO_KMH
```

**Perfiles disponibles:**

| Aeronave | Crucero (kt) | Xwind max (kt) | Rango (km) | Consumo (L/h) |
|---|---|---|---|---|
| Pipistrel Alpha Trainer | 97 | 12 | ~552 | 14 |
| Cessna 172 Skyhawk | 110 | 15 | ~1196 | 32 |
| Piper PA-28 Cherokee | 108 | 17 | ~1122 | 30 |
| Cessna 152 | 90 | 12 | ~390 | 19 |
| Diamond DA40 | 130 | 20 | ~1600 | 20 |

---

#### `risk/hard_blockers.py`
Condiciones de NO GO inmediato. Si cualquiera está activo, se devuelve NO GO sin calcular soft score.

**Tokens activadores:**
```python
HARD_BLOCKER_TOKENS = {"TS", "TSRA", "TSGR", "GR", "FC", "VA", "FZRA", "FZDZ"}
```

**Condiciones adicionales en observación actual:**
- `visibility_km < 1.5`
- `ceiling_ft < 500`

---

#### `risk/weights.py` + `risk/soft_scoring.py`

**Fórmula de scoring:**
```
R_total = W_VIS × r_vis + W_CEIL × r_ceil + W_XWIND × r_xwind
        + W_GUST × r_gust + W_WX × r_wx + W_FOG × r_fog
        + W_TAF × r_taf + delta_orographic
```

**Pesos:**

| Componente | Variable | Peso | Función r_i |
|---|---|---|---|
| Visibilidad | vis_km | 0.25 | Sigmoide: 1 si vis<3km, 0 si vis>8km |
| Ceiling | ceil_ft | 0.25 | Sigmoide: 1 si ceil<500ft, 0 si ceil>2000ft |
| Viento cruzado | xw_kt | 0.20 | Lineal: xw/12; 1.0 si xw≥12kt |
| Ráfagas | gust-spd kt | 0.10 | Lineal: delta/20 |
| Fenómenos | wx_codes | 0.10 | Escalonado por severidad |
| Niebla proxy | spread_c | 0.05 | 1 si spread<2°C, 0 si spread>5°C |
| Riesgo TAF | PROB/TEMPO | 0.05 | Escalonado por tipo de deterioro |

**Penalización orográfica SACC: +0.05** al R_total final.

**Thresholds de decisión (conservadores):**
```
R < 0.25            → GO
0.25 ≤ R < 0.50     → CAUTION
R ≥ 0.50            → NO GO
```

---

### 4.5 ROUTE OPTIMIZATION

#### `route/graph.py`
Construye un grafo dirigido de aeródromos con pesos según el modo.

**Función principal:**
```python
build_graph(
    mode       : str,          # "shortest" | "fastest" | "safest"
    r_map      : Dict[str, float] = None,
    wind_dir   : int = None,
    wind_spd_kt: float = None,
    airports   : Dict = None,
    aircraft   = None,
    max_leg_km : float = 500.0,  # 0 = grafo completo sin límite
) -> RouteGraph
```

**Optimización de performance:**
- Filtro de **bounding box** (lat/lon) antes de calcular haversine, rechaza pares claramente fuera de rango
- `max_leg_km` limita aristas a la distancia máxima por tramo (por defecto 500 km; el optimizer pasa `ac.range_km`)
- Resultado: ~150K aristas vs 503K del grafo completo → ~3x más rápido

**Modos de peso:**
```
shortest : weight = distance_km
fastest  : weight = distance_km / groundspeed_kt
safest   : weight = distance_km × (1 + r_dest)
```

---

#### `route/astar.py`
A* estándar sobre el grafo de aeródromos.

**Heurística admisible:**
```
shortest/safest : h = haversine_km(node, goal)
fastest         : h = haversine_km / CRUISE_KT
```

La heurística es admisible en todos los modos (nunca sobreestima el costo real), garantizando optimalidad global.

---

#### `route/genetic.py`
Algoritmo genético multi-objetivo para el modo "Sugerida".

**Cromosoma:** `[origen, inter_1, ..., inter_k, destino]` — los intermedios evolucionan.

**Fitness (minimizar):**
```
F = 0.50 × t_norm + 0.30 × r_meteo + 0.20 × d_norm
```

**Parámetros:**
```python
POP_SIZE       = 40
N_GENERATIONS  = 80
CROSSOVER_RATE = 0.70
MUTATION_RATE  = 0.20
ELITE_SIZE     = 2
```

**Operadores:** crossover por intercambio de segmentos intermedios + mutación (swap / add / remove).

---

#### `route/optimizer.py`
Interfaz unificada. El modo determina el algoritmo:
- `"shortest"`, `"fastest"`, `"safest"` → A*
- `"suggested"` → GA

**Función principal:**
```python
optimize(
    origin, dest,
    mode="suggested",
    r_map=None,
    wind_dir=None, wind_spd_kt=None,
    avoid_restricted=True,
    avoid_controlled=False,
    airports=None,
    aircraft=None,
    ga_seed=None,
    evaluate_intermediate=False,
    suggest_alternate=False,
    mock=False,
) -> OptimizeResult
```

**`suggest_alternate`:** Sugiere el mejor aeródromo alternativo al destino dentro del alcance del avión. Solo usa `r_map` si está disponible; **nunca hace requests HTTP** para cada candidato (esto causaba demoras de 5+ minutos con 710 aeródromos).

**`evaluate_intermediate`:** Evalúa meteorológicamente los aeródromos intermedios de la ruta. Solo aplica si la ruta tiene más de 2 nodos.

**Resultado:**
```python
@dataclass
class OptimizeResult:
    found            : bool
    mode             : str
    path             : List[str]        # códigos ICAO
    legs             : List[LegDetail]
    total_dist_km    : float
    total_time_h     : float
    total_fuel_l     : float
    needs_fuel_stop  : bool
    fuel_ok          : bool
    airspace_conflicts: List[AirspaceZone]
    aircraft         : AircraftProfile
    intermediate_results: List[IntermediateResult]
    alternate        : Optional[AlternateInfo]
    error            : str
```

---

### 4.6 INTEGRATION — `decision/engine.py`

Pipeline completo de evaluación:
```
1. fetch()          → RawMetar / RawNWP / RawTaf
2. parse()          → ParsedWeather + ParsedTaf
3. hard_blockers()  → NO GO inmediato si aplica
4. taf_window()     → r_taf, next_go_from, worst_period
5. soft_scoring()   → R_total (con delta orográfico)
6. decision()       → GO / CAUTION / NO GO
```

```python
@dataclass
class DecisionResult:
    station_id      : str
    decision        : str           # "GO" | "CAUTION" | "NO GO"
    r_total         : float
    hard_blocked    : bool
    blocker_summary : str
    score_breakdown : SoftScoreResult
    taf_result      : TafWindowResult
    weather_source  : str           # "metar" | "nwp"
    obs_time        : int           # Unix UTC
    next_go_from    : Optional[int]
    fetch_ok        : bool
```

---

### 4.7 OUTPUT

#### `output/formatter.py`
```python
format_decision(result: DecisionResult) -> str   # multi-línea para piloto
format_short(result: DecisionResult) -> str      # una línea resumen
```

#### `output/briefing.py`
```python
generate_briefing(
    origin_result, dest_result,
    route_result=None,
    aircraft=None,
) -> str
```
Genera un briefing en español, lenguaje natural, con secciones:
- Condiciones de origen y destino
- Decisión global
- Resumen de ruta (si disponible)
- Alternativo sugerido
- Advertencias de espacio aéreo
- Firma y timestamp

---

### 4.8 INTERFACES DE USUARIO

#### CLI — `main.py`
```bash
python main.py <station_id> <runway_heading> [opciones]

Argumentos:
  station_id        Código ICAO del aeródromo (ej: SACC)
  runway_heading    Cabecera activa en grados (ej: 140)

Opciones:
  --time   HH:MM    Hora UTC de despegue (default: ahora+30min)
  --date   YYYY-MM-DD  Fecha (default: hoy)
  --duration N      Duración del vuelo en horas (default: 1.0)
  --mock            Usar datos mock (sin conexión)
  --short           Salida de una línea
  --verbose         Salida detallada

Exit codes: 0=GO, 1=CAUTION/NO GO, 2=error
```

#### GUI — `gui.py`

Interfaz Tkinter con 3 pestañas:

**Tab EVALUACION:**
- Selector de aeronave (combobox con 5 perfiles)
- Buscador de origen y destino (Entry + Listbox, filtrado en tiempo real, hasta 100 resultados)
- Selector de cabecera de pista (combobox con cabeceras reales del aeródromo seleccionado)
- Hora UTC y duración del vuelo
- Modo Mock (sin internet)
- Resultado: dos tarjetas GO/CAUTION/NO GO (origen y destino) con R_total y factor dominante

**Tab RUTAS:**
- Buscadores de origen y destino (independientes de la tab de evaluación)
- Modos: Sugerida / Más corta / Más rápida / Más segura
- Opción: Evitar CTR/TMA
- Mapa interactivo (`tkintermapview`) — al calcular muestra:
  - Ruta principal en verde
  - 3 aeródromos cercanos al destino (≤150km, naranja) como alternativa por pista
  - 5 aeródromos más lejanos (150-500km, azul) como alternativa por meteorología
- Panel de resumen: tramos, distancia, tiempo, combustible, alternativo sugerido

**Tab BRIEFING:**
- Genera briefing completo en texto plano
- Requiere evaluación y ruta calculadas

---

## 5. Fuentes de Datos

| Fuente | Tipo | URL | Auth | Uso |
|---|---|---|---|---|
| Open-Meteo | NWP API | open-meteo.com | Sin API key | Pronóstico meteorológico para todos los aeródromos |
| aviationweather.gov | METAR/TAF | aviationweather.gov | Sin API key | Observaciones actuales para aeródromos con estación |
| Open-Topo-Data | SRTM 30m | api.opentopodata.org | Sin API key | Perfil de terreno en rutas |
| OurAirports | CSV estático | ourairports.com | Sin API key | Base de datos de aeródromos y cabeceras de pista |

---

## 6. Decisiones de Diseño Clave

### 6.1 Normalización de Aeródromos
- **Fuente única de verdad**: `data/airports.py` carga todos los aeródromos desde CSV al importar.
- **Clave primaria**: campo `ident` de OurAirports (único, siempre presente). No se usa `icao_code` como clave porque no todos los aeródromos tienen código ICAO.
- **710 aeródromos `small_airport`** (no `medium_airport`, no `large_airport`, no `heliport`).
- La elevación se almacena en **pies** (`elev_ft`). `config.py` convierte a metros para Open-Meteo.
- Si hay conflicto entre datos hardcodeados y el CSV, **prevalece el CSV**.

### 6.2 Cabeceras de Pista
- Se derivan preferentemente desde `le_ident`/`he_ident` (`"14"` → 140°) porque la mayoría de los aeródromos argentinos en OurAirports tienen los campos de heading numérico vacíos.
- Si solo una cabecera es conocida, la opuesta se calcula como `(heading + 180) % 360`.
- **429 de 710 aeródromos** tienen datos de cabecera. Los 281 restantes son una limitación del dataset fuente.

### 6.3 Modelo de Riesgo
- Sistema de **reglas + funciones de riesgo** (sin ML). Los pesos están calibrados para ser conservadores.
- Hard blockers (TS, GR, FC, VA, FZRA, FZDZ) son condiciones de rechazo inmediato **sin score**.
- La penalización orográfica de SACC (+0.05) refleja que los pronósticos NWP son menos precisos en zonas de sierra.
- Thresholds conservadores: GO < 0.25 (no < 0.33 como podría ser).

### 6.4 Performance del Grafo de Rutas
- `build_graph()` tiene parámetro `max_leg_km` (default: 500 km).
- El optimizer pasa `ac.range_km` como `max_leg_km` → aristas mayores al alcance del avión se omiten.
- Filtro de bounding box lat/lon antes de calcular haversine → 3-4x speedup en construcción del grafo.
- Resultado: ~150K aristas vs 503K del grafo completo.

### 6.5 Alternativo Automático
- `_find_alternate()` **nunca hace requests HTTP** para evaluar candidatos.
- Usa `r_map` si está disponible; si no, asume `r=0` y elige el más cercano.
- Razón: con 710 aeródromos y ~310 dentro del rango de un Alpha Trainer, hacer un request HTTP por cada uno generaba demoras de 5+ minutos.

### 6.6 Normativa
- Se aplica **ANAC Argentina / OACI**, no FAA.
- La excepción OACI para aeronaves ≤140 kt NO se implementa (criterio conservador).

### 6.7 Idioma
- **Código**: inglés (variables, funciones, clases)
- **Comentarios, docstrings, mensajes al piloto**: español

---

## 7. Convenciones del Proyecto

### Estructura de módulos
- Cada módulo tiene un bloque `if __name__ == "__main__"` con tests standalone.
- Modo `mock=True` en fetchers para desarrollo sin conexión.
- Logging con `logging.getLogger(__name__)`.
- Retries automáticos: 3 intentos con delay incremental.
- Devolver `None` en lugar de lanzar excepciones ante ausencia de datos.

### Tipos de datos
- Dataclasses tipadas para todas las estructuras.
- Campos opcionales como `Optional[X]`, nunca sentinels (-1, "", etc.).
- Unidades: kt, km, ft AGL, °C, hPa, Unix timestamp UTC.

### Imports entre capas
```
GUI / CLI → Integration → Risk Engine → Feature Layer → Parsing Layer → Data Layer
Route Optimizer → Graph → Performance / Airports
```

El pattern try/except para imports soporta ejecución como script directo:
```python
try:
    from parsers.metar_parser import ParsedWeather
except ImportError:
    from metar_parser import ParsedWeather
```

---

## 8. Cómo Ejecutar

### Requisitos previos
```bash
pip install requests tkintermapview
# Python 3.7+ requerido
```

### CLI (con mock, sin internet)
```bash
cd Tesis_2.0
python main.py SACC 140 --mock
python main.py SACC 140 --mock --short
python main.py SACC 140 --mock --verbose
```

### GUI
```bash
cd Tesis_2.0
python gui.py
```

### Tests standalone de cada módulo
```bash
python data/airports.py          # verifica carga de 710 aeródromos
python route/graph.py            # verifica construcción del grafo
python route/astar.py            # verifica A* (aviso: test de todos-pares es lento)
python route/optimizer.py        # verifica modos shortest/fastest/safest/suggested
python decision/engine.py        # verifica pipeline completo con mock
```

---

## 9. Estado de los Módulos (Checkpoint Mayo 2026)

| Módulo | Estado | Notas |
|---|---|---|
| `data/airports.py` | **COMPLETO** | 710 aeródromos, 429 con cabeceras reales |
| `data/fetcher_openmeteo.py` | **COMPLETO** | NWP sin API key, mock incluido |
| `data/fetcher_aviationweather.py` | **COMPLETO** | METAR+TAF sin API key, mock incluido |
| `data/airspace.py` | **COMPLETO** | Zonas hardcoded Argentina |
| `data/terrain.py` | **COMPLETO** | SRTM 30m via Open-Topo-Data |
| `parsers/metar_parser.py` | **COMPLETO** | ParsedWeather como contrato central |
| `parsers/openmeteo_adapter.py` | **COMPLETO** | NWP → ParsedWeather |
| `parsers/taf_parser.py` | **COMPLETO** | ParsedTaf con herencia BASE→TEMPO |
| `features/crosswind.py` | **COMPLETO** | VRB worst-case incluido |
| `features/fog_risk.py` | **COMPLETO** | spread + tokens wx |
| `features/taf_window.py` | **COMPLETO** | ventana temporal + herencia |
| `features/flight_category.py` | **COMPLETO** | ANAC + mínimos de aeronave |
| `features/orographic.py` | **COMPLETO** | SACC +0.05, dict extensible |
| `risk/aircraft_profiles.py` | **COMPLETO** | 5 perfiles disponibles |
| `risk/hard_blockers.py` | **COMPLETO** | 8 tokens + vis/ceil umbral |
| `risk/soft_scoring.py` | **COMPLETO** | R_total + delta_orographic |
| `risk/weights.py` | **COMPLETO** | Pesos calibrados conservadores |
| `route/graph.py` | **COMPLETO** | max_leg_km + bounding box filter |
| `route/astar.py` | **COMPLETO** | A* con heurística haversine admisible |
| `route/genetic.py` | **COMPLETO** | GA multi-objetivo 80 generaciones |
| `route/optimizer.py` | **COMPLETO** | Interfaz unificada, sin HTTP en alternativo |
| `route/performance.py` | **COMPLETO** | Haversine, GS con viento, fuel |
| `decision/engine.py` | **COMPLETO** | Pipeline fetch→parse→score→decide |
| `output/formatter.py` | **COMPLETO** | format_decision + format_short |
| `output/briefing.py` | **COMPLETO** | Briefing natural language español |
| `config.py` | **COMPLETO** | NWP_STATIONS auto desde airports.py |
| `main.py` | **COMPLETO** | CLI con exit codes 0/1/2 |
| `gui.py` | **COMPLETO** | 3 tabs, buscador de aeródromos, mapa |

---

## 10. Limitaciones Conocidas

1. **Aeródromos sin cabecera de pista (281 de 710)**: limitación del dataset fuente (OurAirports). Para estos aeródromos, el cálculo de viento cruzado usa heading=0 como fallback.

2. **NWP para todos los aeródromos**: Open-Meteo es un modelo de grilla global; la precisión puede ser baja en zonas de orografía compleja (Andes, precordillera). Solo se aplica penalización orográfica a SACC actualmente.

3. **Sin calibración empírica de thresholds**: los thresholds GO/CAUTION/NO GO (0.25 / 0.50) son conservadores pero no han sido validados con datos históricos de accidentes o incidentes.

4. **El alternativo automático es solo geométrico**: cuando no hay `r_map` previo, elige el aeródromo más cercano sin datos meteorológicos (`decision="SIN DATOS"`). No evalúa weather del alternativo en tiempo real.

5. **Test de todos-pares en `astar.py`**: el script de prueba standalone ejecuta 710×709 búsquedas A*, lo cual tarda varios minutos. No afecta producción.

6. **`SAAG` en tests de `genetic.py`**: algunos tests internos aún referencian `SAAG` que era un código hardcodeado pre-v1.2 y puede no existir en el dataset actual.

---

## 11. Próximos Pasos (Pendientes)

1. **Calibración de thresholds**: usar datos históricos METAR + incidentes ANAC para ajustar los umbrales GO/CAUTION/NO GO con respaldo empírico.

2. **ML: Predicción temporal de R_total**: usar API histórica de Open-Meteo para generar series de tiempo de R_total, entrenar modelo XGBoost/LSTM para predicción de deterioro.

3. **Ampliar penalización orográfica**: extender el dict `orographic.py` a más aeródromos con orografía compleja (Mendoza, San Juan, Bariloche, etc.).

4. **Validación de espacio aéreo**: mejorar la precisión de los polígonos hardcodeados en `airspace.py` con datos de publicación oficial (AIP Argentina).

5. **`requirements.txt`**: documentar dependencias formalmente.

6. **Tests automáticos formales**: reemplazar los scripts `__main__` por un framework de testing (pytest).

---

*Documentación generada como checkpoint del estado del proyecto en Mayo 2026.*
