# CLAUDE.md — VFR GO/NO GO Decision Engine

Sistema de decision meteorologica para vuelos VFR de aviacion general en Argentina.
Evalua condiciones reales y pronosticadas y devuelve GO / CAUTION / NO GO.

**Aeronaves**: 5 perfiles seleccionables — Pipistrel Alpha Trainer (LSA), Cessna 152,
Cessna 172 Skyhawk, Piper PA-28 Cherokee, Diamond DA40 (SEP). Altitudes de crucero de
6000 a 16500 ft (el perfil determina que aerovias puede usar segun el MEA).

**Cobertura**: todo el territorio argentino — 561 aerodromos del registro oficial
ANAC/MADHEL, con rutas largas (ej. Salta-Ushuaia) y aerovias inferiores del AIP (ENR-3.1).
(El alcance v1.0 original eran 8 aeroclubes de Cordoba; el proyecto crecio a escala nacional.)

**Fuentes meteo por aerodromo**: METAR + TAF (aviationweather.gov) cuando el aerodromo
tiene estacion; NWP (Open-Meteo) cuando no (la mayoria de los rurales). El engine elige
automaticamente: si el aerodromo tiene ICAO intenta METAR, si no hay METAR cae a NWP.
**NOTAMs**: AIS oficial de ANAC (POST a ais.anac.gob.ar/notam/pib, todos los aerodromos).

---

## Estado de modulos

### DATA LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `data/fetcher_aviationweather.py` | **COMPLETO** | METAR + TAF de aviationweather.gov. Produce `RawMetar`, `RawTaf`, `RawTafPeriod`. Mock de SACO incluido. |
| `data/fetcher_openmeteo.py` | **COMPLETO** | Pronostico NWP de Open-Meteo. Produce `RawNWP` + `RawNWPHour`. Mock de SACC incluido. |
| `data/airports.py` | **COMPLETO** | Registro canonico de aerodromos. `AirportInfo`, `RunwayInfo` dataclasses. `AIRPORTS`, `AIRPORTS_BY_NAME`, `AIRPORT_NAMES`. Fuente unica de verdad para coords, elevacion y cabeceras. |

### PARSING LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `parsers/metar_parser.py` | **COMPLETO** | Define `ParsedWeather` (contrato central). Parsea `RawMetar` → `ParsedWeather`. Incluye vis ICAO/SM, ceiling, spread T/Td, categoria ANAC/OACI. |
| `parsers/openmeteo_adapter.py` | **COMPLETO** | Convierte `RawNWPHour` → `ParsedWeather`. Traduce % cobertura a capas estimadas, WMO codes a tokens wx. |
| `parsers/taf_parser.py` | **COMPLETO** | Define `ParsedTaf` + `ParsedTafPeriod`. Parsea `RawTaf`. Normaliza TEMPO/BECMG/PROB, calcula `is_transient`. |

### FEATURE LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `features/crosswind.py` | **COMPLETO** | `compute_crosswind()`, `crosswind_risk_score()`. VRB → worst-case conservador. |
| `features/fog_risk.py` | **COMPLETO** | `r_fog = max(r_spread, r_wx)`. Spread lineal 2-5°C, matching exacto por token wx. |
| `features/taf_window.py` | **COMPLETO** | `TafAnalyzer.analyze()`: herencia BASE→TEMPO/BECMG, worst-case, `r_taf`, `next_go_from`. |
| `features/flight_category.py` | **COMPLETO** | Categoria ANAC + evaluacion contra minimos de aeronave. `is_worse_than()`. |
| `features/orographic.py` | **COMPLETO** | `delta_r = 0.05` si `nwp_estimated=True` y `station_id="SACC"`. Dict extensible. |
| `features/density_altitude.py` | **COMPLETO** | `compute_density_altitude(temp_c, elevation_ft, qnh_hpa)` → `DensityAltitudeResult`. Niveles NORMAL/ELEVATED(>5000ft)/HIGH(>8000ft). |

### RISK ENGINE

| Archivo | Estado | Descripcion |
|---|---|---|
| `risk/aircraft_profiles.py` | **COMPLETO** | `AircraftProfile` dataclass frozen. `ALPHA_TRAINER` instancia. `get_profile(name)`. |
| `risk/weights.py` | **COMPLETO** | Pesos W_VIS=0.25 W_CEIL=0.25 W_XWIND=0.20 W_GUST=0.10 W_WX=0.10 W_FOG=0.05 W_TAF=0.05. Funciones r_i. Thresholds GO/CAUTION/NO GO. |
| `risk/hard_blockers.py` | **COMPLETO** | Tokens TS/TSRA/TSGR/GR/FC/VA/FZRA/FZDZ + vis<1.5km + ceil<500ft → NO GO inmediato. |
| `risk/soft_scoring.py` | **COMPLETO** | `compute_soft_score(weather, runway_heading, aircraft, taf_r_taf)` → `SoftScoreResult`. Llama a features internamente. |

### INTEGRACION

| Archivo | Estado | Descripcion |
|---|---|---|
| `config.py` | **COMPLETO** | Constantes globales: `NWP_STATIONS` (SACC coords), `METAR_STATIONS`, `NWP_HOURS_AHEAD`. |
| `decision/engine.py` | **COMPLETO** | `DecisionEngine.evaluate()` → `DecisionResult`. Pipeline completo: fetch→parse→hard_blockers→soft_score+taf_window→decision. NWP para SACC, METAR+TAF para SACO/etc. |
| `output/formatter.py` | **COMPLETO** | `format_decision(result)` → texto multi-linea para el piloto. `format_short()` para una linea. |
| `main.py` | **COMPLETO** | CLI entry point. Args: station_id, runway_heading, --time, --date, --duration, --mock, --short, --verbose. Exit 0=GO, 1=CAUTION/NO GO, 2=error. |
| `gui.py` | **COMPLETO** | GUI Tkinter. Selector de aeropuerto con cabeceras automaticas, origen + destino, hora UTC, duracion. Evaluacion en thread. Decision global GO/CAUTION/NO GO con colores. |

---

## Contrato central: ParsedWeather

Definido en `parsers/metar_parser.py`. Es el unico tipo que consumen el feature
layer y el risk engine. Tanto `MetarParser` como `OpenMeteoAdapter` lo producen.

```python
@dataclass
class ParsedWeather:
    # Identificacion
    source        : str               # "metar" | "nwp"
    station_id    : str               # ICAO o identificador del punto
    obs_time      : int               # Unix timestamp UTC
    nwp_estimated : bool = False      # True si es NWP (no observacion directa)

    # Viento (unidad: kt)
    wind_dir      : Optional[int]   = None    # grados 0-360; None si VRB
    wind_spd_kt   : Optional[float] = None
    wind_gust_kt  : Optional[float] = None
    wind_variable : bool            = False

    # Visibilidad (unidad: km)
    visibility_km : Optional[float] = None    # 10.0 = CAVOK / "9999"

    # Nubosidad (unidad: ft AGL)
    ceiling_ft    : Optional[int]   = None    # None = CLR o solo FEW/SCT
    sky_layers    : list            = ...     # [{"cover": "BKN", "base_ft": 2500}, ...]

    # Termodinamica (unidad: Celsius)
    temp_c        : Optional[float] = None
    dewpoint_c    : Optional[float] = None
    spread_c      : Optional[float] = None    # T - Td; proxy niebla

    # Presion (unidad: hPa)
    altimeter_hpa : Optional[float] = None

    # Fenomenos
    wx_codes      : list            = ...     # ["-RA", "BR"], ["TSRA"], etc.

    # Categoria ANAC/OACI
    flight_category : Optional[str] = None   # "VFR" | "MVFR" | "IFR" | "LIFR"

    # Posicion
    lat, lon, elevation_m, station_name, raw_string
```

**Regla clave**: `ceiling_ft = None` significa cielo despejado o solo FEW/SCT
(no es ceiling). El risk engine trata None como 99999 ft (sin restriccion).

### ParsedTafPeriod (contrato TAF)

Definido en `parsers/taf_parser.py`.

```python
@dataclass
class ParsedTafPeriod:
    time_from, time_to   : int          # Unix UTC
    change_indicator     : Optional[str]  # None=BASE, "TEMPO", "BECMG", "PROB30", "PROB40"
    probability          : Optional[int]  # 30 o 40
    is_transient         : bool           # True si TEMPO o PROB (eleva score TAF)
    # + mismos campos de viento/vis/ceiling/wx que ParsedWeather (sin temp/dewpoint)
    flight_category      : Optional[str]  # None si el periodo es parcial (herencia pendiente)
```

Herencia TAF: campos `None` en TEMPO/BECMG se heredan del BASE. La herencia
la resuelve `features/taf_window.py`, no el parser.

---

## Decisiones de diseno

### Fuentes de datos

- **aviationweather.gov**: METAR + TAF para cualquier aerodromo argentino con estacion
  (los controlados: SAEZ, SACO, SASA, SAME, SAWH, SARE, SAAR, etc.). Sin API key.
  - 204 = aeropuerto sin datos (devuelve None, el engine cae a NWP).
- **Open-Meteo**: NWP punto a punto para cualquier coordenada (aerodromos sin METAR y
  checkpoints en ruta). Sin API key. Penalizacion orografica +0.05 al R_total (solo NWP).
- **AIS / ANAC** (`ais.anac.gob.ar/notam/pib`): NOTAMs oficiales argentinos. POST con
  `indicador=<local_id>` y header `X-Requested-With: XMLHttpRequest`. Cubre todos los
  aerodromos (incluso rurales). Parser HTML → RawNotam.
- **MADHEL/ANAC** (`madhel_cache.json`): registro de 561 aerodromos (coords, elevacion,
  pistas, servicios). **OurAirports** (`runways.csv`): completa las pistas que MADHEL no trae
  (~62 aerodromos grandes). **Open-Topo-Data** (SRTM): terreno para el perfil vertical.
- **Iowa State Mesonet**: archivo historico. NO implementar en v1.0.

### Normativa: ANAC/OACI (NO FAA)

Categorias de vuelo (umbrales):
```
VFR  : vis >= 5.0 km  AND  ceil >= 1000 ft
MVFR : vis >= 3.0 km  AND  ceil >=  500 ft
IFR  : vis >= 0.8 km  AND  ceil >=  200 ft
LIFR : vis <  0.8 km   OR  ceil <   200 ft
```
La excepcion OACI para <= 140 kt NO se implementa (criterio conservador).

### Hard Blockers (NO GO automatico)

Activados por tokens en `ParsedWeather.wx_codes` o en periodos TAF activos:

```python
HARD_BLOCKER_TOKENS = {"TS", "TSRA", "TSGR", "GR", "FC", "VA", "FZRA", "FZDZ"}
```

Ademas: `visibility_km < 1.5` o `ceiling_ft < 500` en observacion actual.

Si cualquier hard blocker esta activo → NO GO inmediato, sin calcular score.

### Soft Scoring

`R_total = sum(w_i * r_i)` donde `R_total ∈ [0, 1]`

| Componente | Variable | Peso | Funcion r_i |
|---|---|---|---|
| Visibilidad | vis_km | 0.25 | Sigmoide: 1 si vis<3km, 0 si vis>8km |
| Ceiling | ceil_ft | 0.25 | Sigmoide: 1 si ceil<500ft, 0 si ceil>2000ft |
| Crosswind | xw_kt | 0.20 | Lineal: xw/12. Si xw>=12kt → 1.0 |
| Rafagas | gust-spd kt | 0.10 | Lineal: delta/20 |
| Fenomenos | wx_codes | 0.10 | Escalonado por severidad |
| Niebla proxy | spread_c | 0.05 | 1 si spread<2°C, 0 si spread>5°C |
| Riesgo TAF | PROB/TEMPO | 0.05 | Escalonado por tipo de deterioro |

Penalizacion orografica SACC: +0.05 al R_total final (cuando `nwp_estimated=True`).

**Thresholds de decision** (conservadores, pendientes calibracion):
```
R < 0.25           → GO
0.25 <= R < 0.50   → CAUTION
R >= 0.50          → NO GO
```

### Perfiles de aeronave (5)

Definidos en `risk/aircraft_profiles.py`. Cada uno tiene limites (crosswind/gust max,
minimos VFR), velocidades (vs0, cruise_kt), `cruise_alt_ft` (clave: determina que
aerovias puede usar segun el MEA) y datos de combustible (consumo, capacidad, reserva).

| Perfil | cruise_alt_ft | cruise_kt | crosswind_max | Categoria |
|---|---|---|---|---|
| Pipistrel Alpha Trainer | 6000 | 97 | 12 kt | LSA |
| Cessna 152 | 5500 | 90 | 12 kt | SEP |
| Piper PA-28 Cherokee | 7500 | 108 | 17 kt | SEP |
| Cessna 172 Skyhawk | 10000 | 110 | 15 kt | SEP |
| Diamond DA40 | 16500 | 130 | 20 kt | SEP |

Regla critica: las soluciones deben ser escalables a TODAS las aeronaves y aerodromos.
Nunca basar tests solo en el Alpha Trainer o en un aerodromo unico (ej. SACC).

### Modulo TAF — ventana temporal

- Input usuario: hora estimada de despegue (UTC) + duracion del vuelo (horas).
- Ventana = [ETA_dep, ETA_dep + duration].
- Extraer periodos TAF que intersectan la ventana.
- Periodo mas restrictivo = worst-case para el score.
- TEMPO/PROB en ventana → elevar componente de riesgo TAF.

---

## Orden de construccion — estado actual

### Completado

Capas 1-11 completas. Data → Parsing → Feature → Risk → Integracion (engine) operativos.

### Completado

Todas las capas del sistema v1.0 estan completas y testeadas:
- Data layer (fetchers METAR+TAF, NWP)
- Parsing layer (metar_parser, openmeteo_adapter, taf_parser)
- Feature layer (crosswind, fog_risk, taf_window, flight_category, orographic)
- Risk engine (aircraft_profiles, weights, hard_blockers, soft_scoring)
- Integracion (config, decision/engine)
- Output + CLI (output/formatter, main.py)

El sistema es completamente funcional con `python main.py SACC 150 --mock`.

### Interfaz del engine (referencia para implementacion)

```python
# Firma esperada de DecisionEngine.evaluate()
@dataclass
class DecisionResult:
    station_id       : str
    decision         : str             # "GO" | "CAUTION" | "NO GO"
    r_total          : float
    hard_blocked     : bool
    blocker_summary  : str             # descripcion si hard_blocked
    score_breakdown  : SoftScoreResult # None si hard_blocked
    taf_result       : TafWindowResult # None si no hay TAF
    weather_source   : str             # "metar" | "nwp"
    obs_time         : int             # Unix UTC de la observacion
    next_go_from     : Optional[int]   # del TAF o None
```

---

## Convenciones del proyecto

### Idioma
- Todo el codigo en **ingles** (nombres de variables, funciones, clases).
- Todos los comentarios, docstrings y mensajes de log en **espanol**.

### Estructura de modulos
- Cada modulo tiene un `__main__` con script de prueba standalone.
- Modo `mock=True` en fetchers para desarrollo sin conexion.
- Logging con `logging.getLogger(__name__)`, nivel DEBUG para detalles, INFO para operaciones normales.
- Reintentos automaticos: 3 intentos con delay incremental ante timeout/ConnectionError.
- Devolver `None` en lugar de lanzar excepciones ante ausencia de datos (el engine maneja la ausencia).

### Tipos de datos
- **Dataclasses tipadas** para todas las estructuras (no dicts sueltos entre capas).
- Campos opcionales como `Optional[X]`, nunca usar sentinels (-1, "", etc.).
- Unidades estandarizadas en todo el sistema: kt, km, ft AGL, Celsius, hPa, Unix timestamp UTC.

### Imports entre capas
- El feature layer importa de `parsers/`.
- El risk engine importa de `features/` y `parsers/`.
- El engine importa de todo.
- Los parsers importan entre si con el patron try/except para soportar ejecucion como script:

```python
try:
    from parsers.metar_parser import ParsedWeather, _compute_flight_category
except ImportError:
    from metar_parser import ParsedWeather, _compute_flight_category
```

### Lo que NO hacer
- No implementar FastAPI ni endpoints REST (fase 2, fuera de v1.0).
- No usar frameworks de ML; el sistema es motor de reglas + funciones de riesgo.
- No implementar Iowa State Mesonet (archivo historico, fuera de v1.0).
- No implementar la excepcion OACI para aeronaves <= 140 kt (criterio conservador).
- No usar FAA como referencia normativa (usar ANAC/OACI).
