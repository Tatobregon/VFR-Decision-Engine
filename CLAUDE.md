# CLAUDE.md — VFR GO/NO GO Decision Engine

Sistema de decision meteorologica para vuelos VFR de aviacion general en Argentina.
Evalua condiciones reales y pronosticadas y devuelve GO / CAUTION / NO GO.

**Aeronaves**: 5 perfiles seleccionables — Pipistrel Alpha Trainer (LSA), Cessna 152,
Cessna 172 Skyhawk, Piper PA-28 Cherokee, Diamond DA40 (SEP). Altitudes de crucero de
6000 a 16500 ft (el perfil determina que aerovias puede usar segun el MEA).

**Cobertura**: todo el territorio argentino — 561 aerodromos del registro oficial
ANAC/MADHEL, con rutas largas (ej. Salta-Ushuaia) y aerovias inferiores del AIP (ENR-3.1).

> ## ⚠️ REGLA DE ALCANCE — leer antes de tocar nada
>
> El sistema es **NACIONAL y MULTI-AERONAVE**. Cordoba, SACC y el Pipistrel Alpha
> Trainer fueron el alcance de la v1.0 (8 aeroclubes cordobeses) y **hoy son solo
> casos de prueba, NO el dominio del sistema**.
>
> - Ninguna solucion, heuristica, umbral o test puede quedar atada a un aerodromo
>   particular ni a una aeronave particular.
> - Lo que depende de la aeronave se deriva del `AircraftProfile` (crosswind_max_kt,
>   gust_max_kt, cruise_kt, cruise_alt_ft, range_km), nunca de constantes del Alpha.
> - Lo que depende del lugar se deriva de los datos del aerodromo (`AirportInfo`) o
>   del terreno (SRTM), nunca de un `if station_id == "SACC"`.
> - Al probar un cambio, usar aerodromos de **distintas regiones** (NOA, Cuyo,
>   Patagonia, Litoral, Pampa) y **al menos dos aeronaves** de distinto porte.
>
> Sesgo historico ya corregido: la penalizacion orografica fija de SACC fue
> ELIMINADA (agosto 2026) por cubrir 1 de 561 aerodromos. Pendientes: varios
> asserts de test atados a Cordoba / Alpha Trainer (ej. `SAOE`, que ya no existe
> en el registro MADHEL).

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
| `data/fetcher_openmeteo.py` | **COMPLETO** | Pronostico NWP de Open-Meteo. Produce `RawNWP` + `RawNWPHour`. Soporta viento en nivel de presion segun `cruise_alt_ft`. **`get_forecast_ring()`**: consulta el aerodromo + 6 puntos a 10 km en UNA peticion, para muestrear la incertidumbre orografica. Mock incluido. |
| `data/airports.py` | **COMPLETO** | Registro canonico de aerodromos. `AirportInfo`, `RunwayInfo` dataclasses. `AIRPORTS`, `AIRPORTS_PUBLIC`. Fuente unica de verdad para coords, elevacion y cabeceras. |
| `data/airspace.py` | **COMPLETO** | Zonas CTR/TMA/R/P/D. Fuente `ar-airspace.json` (OpenAIP); fallback Cordoba si falta el cache. `zones_along_route()`, `route_intersects_zone()`. |
| `data/airways.py` | **COMPLETO** | Grafo bidireccional de aerovias inferiores del AIP (ENR-3.1) desde `aerovias_argentinas.json`. `AIRWAY_NODES`, `AIRWAY_GRAPH`. |
| `data/fir_zones.py` | **COMPLETO** | FIR de un punto → contacto ATC ("Cordoba Control", etc.) desde `FIRs_Argenina.geojson`. |
| `data/terrain.py` | **COMPLETO** | Terreno SRTM 30m via Open-Topo-Data. Usado por el perfil vertical. |
| `data/cache.py` | **COMPLETO** | Cache en memoria con TTL por tipo de dato (METAR 10min, TAF/NOTAM/NWP 30min). A nivel de modulo (los fetchers se crean por request) y thread-safe. No cachea respuestas vacias ni el modo mock. |
| `data/fetcher_madhel.py` | **HERRAMIENTA** | Regenera `madhel_cache.json`. NO corre en runtime. |
| `data/fetcher_openaip.py` | **HERRAMIENTA** | Regenera `ar-airspace.json`. NO corre en runtime (requiere API key). |

### PARSING LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `parsers/metar_parser.py` | **COMPLETO** | Define `ParsedWeather` (contrato central). Parsea `RawMetar` → `ParsedWeather`. Incluye vis ICAO/SM, ceiling, spread T/Td, categoria ANAC/OACI. |
| `parsers/openmeteo_adapter.py` | **COMPLETO** | Convierte `RawNWPHour` → `ParsedWeather`. Traduce % cobertura a capas estimadas, WMO codes a tokens wx. La base de nubes bajas se calcula con la **regla de Espy** (`400 ft x spread T/Td`), no con una constante: con aire saturado da nubes al ras del suelo. |
| `parsers/taf_parser.py` | **COMPLETO** | Define `ParsedTaf` + `ParsedTafPeriod`. Parsea `RawTaf`. Normaliza TEMPO/BECMG/PROB, calcula `is_transient`. |

### FEATURE LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `features/crosswind.py` | **COMPLETO** | `compute_crosswind()`, `crosswind_risk_score()`. VRB → worst-case conservador. |
| `features/fog_risk.py` | **COMPLETO** | `r_fog = max(r_spread, r_wx)`. Spread lineal 2-5°C, matching exacto por token wx. |
| `features/taf_window.py` | **COMPLETO** | `TafAnalyzer.analyze()`: herencia BASE→TEMPO/BECMG, worst-case, `r_taf`, `next_go_from`. Ademas `nwp_trend_r_taf()`: **sintetiza la tendencia desde la serie NWP** para los aerodromos sin TAF (la mayoria del pais), con la misma escala de severidad que un TAF real. |
| `features/vfr_altitude.py` | **COMPLETO** | `hemispheric_vfr_altitude()`: altitud de crucero VFR por regla de los semicirculos (rumbo magnetico). `magnetic_declination_ar()` aprox AR. |
| `features/density_altitude.py` | **COMPLETO** | `compute_density_altitude(temp_c, elevation_ft, qnh_hpa)` → `DensityAltitudeResult`. Niveles NORMAL/ELEVATED(>5000ft)/HIGH(>8000ft). |
| `features/daylight.py` | **COMPLETO** | Orto/ocaso sin dependencias externas. `daylight_status()` habilita el bloqueo por vuelo nocturno (solo VFR) y el aviso de luz ajustada (<45 min al ocaso). |
| `features/notam_impact.py` | **COMPLETO** | `assess_notam_impact()`: solo el cierre total (AD CLSD o todas las cabeceras cerradas) bloquea; el resto es informativo. |

### RISK ENGINE

| Archivo | Estado | Descripcion |
|---|---|---|
| `risk/aircraft_profiles.py` | **COMPLETO** | `AircraftProfile` dataclass frozen. 5 perfiles + `get_profile(name)` + `PROFILE_NAMES`. Incluye designador OACI y estela para el plan de vuelo. |
| `risk/personal_minima.py` | **COMPLETO** | Minimos personales por experiencia (Alumno / PPL / Avanzado): endurecen vis, techo y tolerancia al cruzado. NO tocan los pesos AHP. |
| `risk/ahp_weights.py` | **COMPLETO** | Derivacion AHP de los pesos. Los juicios de a pares NO son a ojo: se derivan de accidentologia con la operacion explicita `a_ij = redondeo_Saaty(I_i/I_j)`, con `I = prob x severidad` (Doc 9859 OACI). Cada entrada declara su procedencia (E evidencia / N norma / D derivada / J juicio). CR=0.069. |
| `risk/weights.py` | **COMPLETO** | Pesos AHP W_VIS=0.357 W_CEIL=0.357 W_XWIND=0.099 W_FOG=0.071 W_GUST=0.050 W_WX=0.044 W_TAF=0.022. Funciones r_i. Thresholds **calibrados**: t_go=0.22, t_caution=0.59. Los **parametros de forma** de las rampas son constantes nombradas con procedencia declarada (N norma / J juicio): los quiebres de riesgo MAXIMO son la frontera IFR de la norma; los de riesgo NULO son juicio. `r_fog` de este modulo NO corre en runtime (la rampa real esta en `features/fog_risk.py`). |
| `risk/hard_blockers.py` | **COMPLETO** | Tokens TS/TSRA/TSGR/GR/FC/VA/FZRA/FZDZ + vis<1.5km + ceil<500ft → NO GO inmediato. |
| `risk/soft_scoring.py` | **COMPLETO** | `compute_soft_score(...)` → `SoftScoreResult`. Score compensatorio + **barrera no-compensatoria** (`conjunctive_floor`): `decision = worst(umbral(R), piso)`. Expone `guardrail_floor`/`guardrail_reason`. `CAUTION_FRACTION=0.5` es constante nombrada con procedencia (J juicio) y su efecto esta medido en sensitivity [5]: robusta entre 0.35 y 0.65. |
| `risk/scenarios.py` | **COMPLETO** | Bateria de 38 escenarios de referencia con etiqueta normativa ANAC/OACI (`normative_label`). Fuente compartida por calibracion y sensibilidad. |
| `risk/calibration.py` | **COMPLETO** | Calibracion de umbrales por anclaje normativo (grid search + costo asimetrico). Resultado: t_go=0.22, t_caution=0.59 (97% concordancia, 0 sub-avisos). Reporta ademas la concordancia **por nivel de minimos personales** (sin minimos 97%, Avanzado 97%, PPL 89%, Alumno 67%) y verifica que en ninguno hay sub-avisos: el desvio es siempre por sobre-aviso. Validez de constructo, no empirica. |
| `risk/sensitivity.py` | **COMPLETO** | Sensibilidad en 5 ejes: [1] OAT ±20% por peso, [2] Monte Carlo 7 pesos, [3] umbrales, [4] **parametros de forma de las r_i**, [5] **fraccion de CAUTION de la barrera**. Estabilidad del veredicto 99%; 35/36 escenarios nunca cambian. **Hallazgo clave**: los parametros de forma pesan MAS que los pesos (5.6% de flips contra 0.8%). |

### INTEGRACION

| Archivo | Estado | Descripcion |
|---|---|---|
| `config.py` | **COMPLETO** | Constantes globales: `NWP_STATIONS` (derivado de los 561 aerodromos de `AIRPORTS`), `METAR_STATIONS` (vacio, vestigio v1.0), `NWP_HOURS_AHEAD`. |
| `decision/engine.py` | **COMPLETO** | `DecisionEngine.evaluate()` → `DecisionResult`. Pipeline: fetch→parse→hard_blockers→soft_score+taf_window→decision. **Regla de fuente**: con codigo ICAO intenta METAR+TAF y cae a NWP si no hay METAR; sin ICAO va directo a NWP. El camino NWP usa **muestreo en anillo** (peor caso en tiempo Y espacio). |
| `output/briefing.py` | **COMPLETO** | `generate_briefing(...)` → briefing meteorologico multi-linea para el piloto (origen, destino, ruta, NOTAMs). 100% reglas, sin IA. |
| `output/flight_plan.py` | **COMPLETO** | `build_flight_plan(...)` → plan de vuelo OACI (casillas 7-19 + mensaje FPL). **No radica** el plan: lo presenta el piloto. |
| `web/app.py` | **COMPLETO** | Backend FastAPI + frontend HTML (`web/static`). **Entry point unico del sistema.** Endpoints: `/api/evaluate`, `/api/profile`, `/api/timeline`, `/api/flightplan`, `/api/airport/{code}`, `/api/airports`, `/api/airports/map`, `/api/aircraft`, `/api/vfr_corridors`, `/api/airspace`. Switch VFR/IFR, corredores VFR, perfil vertical. |

### ROUTE LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `route/optimizer.py` | **COMPLETO** | Interfaz unica: `optimize()`. La web usa siempre `mode="suggested"` (A* sobre corredor geografico, eligiendo el candidato con mayor cobertura de aerovia). |
| `route/graph.py` | **COMPLETO** | Grafo de aerodromos con `max_leg_km` + rechazo por bounding box. Modos shortest/fastest/safest. Expone `max_gs_kt` (cota superior de velocidad de tierra) para la heuristica de A*. El peso de arista NO se redondea: redondearlo violaba la desigualdad de admisibilidad. |
| `route/astar.py` | **COMPLETO** | A* con heuristica haversine admisible en los tres modos. En `fastest` divide por `graph.max_gs_kt` (crucero de LA AERONAVE + viento), no por una constante: dividir por los 97 kt del Alpha rompia la admisibilidad para PA-28, C172 y DA40. Fijado por `tests/test_route.py`, que verifica h(n) <= costo real contra Dijkstra para los 5 perfiles. |
| `route/airway_router.py` | **COMPLETO** | Dijkstra sobre aerovias filtrado por MEA de la aeronave. `find_airways_for_leg()`, `find_airways_for_route_legs()` (camino continuo end-to-end). |
| `route/vfr_corridors.py` | **COMPLETO** | Ruteo VFR por corredores visuales de las TMA BA/Cordoba (grafo + Dijkstra por cluster). `corridor_path_for_leg()`. |
| `route/performance.py` | **COMPLETO** | Haversine, rumbo, groundspeed con viento, combustible, altitud segura. |
| `route/weather_sampler.py` | **INACTIVO** ⚠️ | Muestreo meteo en ruta con rerouteo. Solo se activa con `weather_reroute=True`, que la web nunca pasa. |

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

    # Categoria ANAC/OACI (etiquetas OACI, NO las siglas MVFR/LIFR de la FAA/NWS)
    flight_category : Optional[str] = None   # "VFR" | "VFR marginal" | "IFR" | "IFR bajo mínimos"

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
  checkpoints en ruta). Sin API key. Soporta viento en nivel de presion segun la
  altitud de crucero (meteo en ruta, no solo en superficie).
- **AIS / ANAC** (`ais.anac.gob.ar/notam/pib`): NOTAMs oficiales argentinos. POST con
  `indicador=<local_id>` y header `X-Requested-With: XMLHttpRequest`. Cubre todos los
  aerodromos (incluso rurales). Parser HTML → RawNotam.
- **MADHEL/ANAC** (`madhel_cache.json`): registro de 561 aerodromos (coords, elevacion,
  pistas, servicios). **OurAirports** (`runways.csv`): completa las pistas que MADHEL no trae
  (~62 aerodromos grandes). **Open-Topo-Data** (SRTM): terreno para el perfil vertical.
- **Iowa State Mesonet**: archivo historico. NO implementar en v1.0.

### Normativa: ANAC/OACI (NO FAA)

Categorias de vuelo (umbrales). Se usan **etiquetas OACI en espanol**, no las siglas
MVFR/LIFR que son de la FAA/NWS (`_compute_flight_category` en `parsers/metar_parser.py`):
```
VFR              : vis >= 5.0 km  AND  ceil >= 1000 ft
VFR marginal     : vis >= 3.0 km  AND  ceil >=  500 ft
IFR              : vis >= 0.8 km  AND  ceil >=  200 ft
IFR bajo mínimos : vis <  0.8 km   OR  ceil <   200 ft
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

`R_total = sum(w_i * r_i)` donde `R_total ∈ [0, 1]`. Pesos derivados por AHP (ver `risk/ahp_weights.py`).

| Componente | Variable | Peso (AHP) | Funcion r_i |
|---|---|---|---|
| Visibilidad | vis_km | 0.357 | Rampa: 1 si vis<3km, 0 si vis>8km |
| Ceiling | ceil_ft | 0.357 | Rampa: 1 si ceil<500ft, 0 si ceil>2000ft |
| Crosswind | xw_kt | 0.099 | Lineal: xw/xw_max. Si xw>=xw_max → 1.0 |
| Niebla proxy | spread_c | 0.071 | 1 si spread<2°C, 0 si spread>5°C |
| Rafagas | gust-spd kt | 0.050 | Lineal: delta/gust_max |
| Fenomenos | wx_codes | 0.044 | Escalonado por severidad |
| Riesgo TAF | PROB/TEMPO | 0.022 | Escalonado por tipo de deterioro |

El score NO penaliza la fuente del dato: las mismas condiciones dan el mismo R vengan
de METAR (observacion) o de NWP (pronostico). La distincion se le informa al piloto en
la interfaz, no se le carga al puntaje.

> **Penalizacion orografica: ELIMINADA (agosto 2026).** Existia un `delta_r = +0.05`
> que se sumaba al R_total cuando la fuente era NWP, pero solo estaba configurado para
> SACC: cubria 1 de 561 aerodromos, con lo cual no corregia nada a escala nacional y
> violaba la regla de alcance. Se removio junto con `features/orographic.py` y con los
> dos escenarios de la bateria que existian solo para probarla. Verificado: los
> resultados de calibracion y sensibilidad NO cambiaron (misma concordancia 97%, mismos
> umbrales optimos, misma estabilidad). El perfil de terreno (SRTM) se conserva: sigue
> alimentando el perfil vertical de ruta y la deteccion de conflicto de terreno en VFR.

> ## Muestreo en anillo — tratamiento de la incertidumbre orografica (agosto 2026)
>
> El modelo global resuelve celdas de ~11 km y SUAVIZA la orografia: un unico punto
> entrega el promedio de la celda, que no describe ni el valle (niebla) ni la ladera
> (nubosidad orografica). `get_forecast_ring()` consulta el aerodromo + 6 puntos a 10 km
> en UNA peticion y el engine se queda con el peor caso.
>
> - **No es la penalizacion orografica que se elimino.** Aquella sumaba un delta fijo y
>   solo cubria SACC. Esto no suma nada: amplia el muestreo y toma la peor lectura.
> - **Se autorregula por el terreno.** En llanura los 7 puntos dan lo mismo y el
>   resultado es identico al de la consulta simple. La intensidad la determina el
>   relieve, no el identificador del aerodromo -> cumple la REGLA DE ALCANCE.
> - **A los puntos del anillo NO se les pasa `elevation`**: Open-Meteo aplica su propio
>   downscaling con DEM de 90 m y cada punto recibe su altura real. En SACC (1138 m) el
>   anillo abarca de 732 a 1629 m.
> - **Del anillo se toma la masa de aire, NO el viento.** El cruzado se define contra la
>   PISTA y el maximo demostrado del avion; la rafaga de un cordon 400 m mas arriba no
>   aplica. Sin esta salvedad hay NO GO por viento en dias de calma (verificado en SACC).
> - **Solo entran los puntos a la elevacion del campo O POR ENCIMA.** La niebla de
>   radiacion y el encharcamiento de aire frio se forman por drenaje al fondo del valle,
>   son capas estables y NO ascienden: una niebla con tope en 900 m no afecta a un campo
>   a 1138 m. La nubosidad de un cordon POR ENCIMA si importa (es el aire que se
>   atraviesa al despegar). Sin este filtro, el valle de Punilla (730 m) daba r_vis=1 y
>   r_ceil=1 -> R=0.714 -> NO GO en SACC con la pista despejada. La regla no tiene
>   parametro que calibrar: el corte es la elevacion del aerodromo. En SACC quedan 2 de
>   6 puntos; en llanura los descartados estan a metros y no cambian nada.
> - **Los hard blockers se evaluan solo en el aerodromo**: son normativos y la norma se
>   refiere al aerodromo.
> - Fijado por 5 tests en `tests/test_engine_data.py`.

**Barrera no-compensatoria (veto conjuntivo)** — `conjunctive_floor` en `soft_scoring.py`.
El promedio ponderado es compensatorio: un factor bueno tapa a uno malo. Eso deja
pasar showstoppers de bajo peso (un cruzado SOBRE el limite del avion aportaria
solo 0.099 y daria GO). La barrera impone un PISO por factor y
`decision = worst(umbral(R), piso)`:
```
cruzado efectivo >= limite avion   → NO GO      cruzado >= 50% limite → CAUTION
delta rafaga >= gust_max avion     → NO GO      delta >= 50% gust_max → CAUTION
niebla probable (r_fog >= 0.9)     → CAUTION     deterioro TAF (r_taf >= 0.6) → CAUTION
```
Cubre los factores de bajo peso que el score diluye; vis/techo (peso alto, deterioro
gradual) siguen compensatorios. Elevo la concordancia con la norma de 66% a 92%.

**Thresholds de decision** — CALIBRADOS por anclaje normativo (`risk/calibration.py`):
```
R < 0.22           → GO
0.22 <= R < 0.59   → CAUTION
R >= 0.59          → NO GO
```
Los cortes se ajustaron sobre la bateria de referencia (`risk/scenarios.py`) minimizando
un costo asimetrico (sub-aviso >> sobre-aviso); el optimo es un rango
(t_go∈[0.15,0.28], t_caution∈[0.59,0.66]) → robusto.
Es validez de CONSTRUCTO (reproduce la regulacion), no empirica. Concordancia final 97%.

> **Convergencia (resultado de validacion).** Los umbrales se recalibraron al pasar los
> pesos de juicio experto a derivacion por evidencia. El optimo se movio de
> (0.22, 0.50) a (0.22, 0.59) — t_go ni siquiera cambio — y la concordancia siguio
> siendo 35/36 con 0 sub-avisos, con el mismo unico desacuerdo (G2, sobre-aviso).
> Dos derivaciones independientes de los pesos producen el mismo comportamiento
> decisional: el veredicto no depende de la ponderacion exacta.

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

## Estado actual

Todas las capas estan completas y operativas: Data → Parsing → Feature → Risk →
Decision → Route → Output → Web. El sistema corre como web app.

**Documento de referencia**: `DOCUMENTACION_CHECKPOINT_2.md` (agosto 2026) tiene el
estado verificado por ejecucion, el inventario completo de modulos, los resultados
reproducidos de AHP/calibracion/sensibilidad y la lista de deuda tecnica abierta.
`DOCUMENTACION_CHECKPOINT.md` (mayo 2026) quedo **obsoleto** — describe la CLI, la GUI
Tkinter y el algoritmo genetico, todos eliminados. Conservar solo como historico.

### Entorno de desarrollo

Python **3.12** (misma version que Render), entorno virtual en `.venv/`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn web.app:app --reload --port 8000
```

Suite de regresion (122 tests, sin red, < 1 s):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

`tests/test_regression_scenarios.py` es el test critico: fija el comportamiento del
veredicto sobre la bateria de referencia (0 sub-avisos, concordancia >= 90%). Si un
cambio en pesos, umbrales o barrera altera lo que el sistema decide, ahi salta.

Test standalone de un modulo (se conservan, son parte de la convencion del proyecto):
`.\.venv\Scripts\python.exe risk\calibration.py`

Si `python` o `git` no se reconocen en una terminal nueva, refrescar el PATH:
```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
```

**Despliegue**: Render (plan free), `render.yaml` → `uvicorn web.app:app --host 0.0.0.0 --port $PORT`.
URL: https://vfr-decision-engine.onrender.com · Repo: `github.com/Tatobregon/VFR-Decision-Engine` (rama `master`).

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
- No atar ninguna solucion a un aerodromo o a una aeronave particular (ver REGLA DE ALCANCE).
- No usar frameworks de ML; el sistema es motor de reglas + funciones de riesgo.
- No implementar Iowa State Mesonet (archivo historico, fuera de v1.0).
- No implementar la excepcion OACI para aeronaves <= 140 kt (criterio conservador).
- No usar FAA como referencia normativa (usar ANAC/OACI).
