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
| `data/fetcher_openmeteo.py` | **COMPLETO** | Pronostico NWP de Open-Meteo. Produce `RawNWP` + `RawNWPHour`. Con `cruise_alt_ft` pide **todas** las variables del nivel de presion (temperatura, rocio, humedad, nubosidad, viento y altura geopotencial), no solo el viento, y las deja en los campos `level_*` SIN pisar los de superficie. **`get_upper_air()`**: consulta dedicada de aire en altura, con tipos propios (`UpperAir`/`UpperAirHour`). **`get_forecast_ring()`**: consulta el aerodromo + 6 puntos a 10 km en UNA peticion, para muestrear la incertidumbre orografica. Mock incluido. |
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
| `risk/weights.py` | **COMPLETO** | Pesos AHP W_VIS=0.357 W_CEIL=0.357 W_XWIND=0.099 W_FOG=0.071 W_GUST=0.050 W_WX=0.044 W_TAF=0.022. Funciones r_i. Thresholds **calibrados**: t_go=0.22, t_caution=0.59. Los **parametros de forma** de las rampas son constantes nombradas con procedencia declarada (N norma / J juicio): los quiebres de riesgo MAXIMO coinciden con el rechazo categorico del sistema (3 km / 500 ft), que NO es una norma; el minimo VFR de la norma (5 km / 1000 ft) cae dentro de la rampa; los de riesgo NULO son juicio. `r_fog` de este modulo NO corre en runtime (la rampa real esta en `features/fog_risk.py`). |
| `risk/hard_blockers.py` | **COMPLETO** | Tokens TS/TSRA/TSGR/GR/FC/VA/FZRA/FZDZ + vis<3km + ceil<500ft → NO GO inmediato (limites del sistema, no norma: el minimo VFR es 5 km / 1000 ft). |
| `risk/soft_scoring.py` | **COMPLETO** | `compute_soft_score(...)` → `SoftScoreResult`. Score compensatorio + **barrera no-compensatoria** (`conjunctive_floor`): `decision = worst(umbral(R), piso)`. Expone `guardrail_floor`/`guardrail_reason`. La unica frontera de viento es `XWIND_CAUTION_FRACTION=0.85` (J), sobre el cruzado calculado con la RAFAGA. **La rafaga no impone piso por si sola** (septiembre 2026): entra por su componente cruzado. Efecto medido en sensitivity [5]: <=2/38 flips ante +/-30%. |
| `risk/scenarios.py` | **COMPLETO** | Bateria de 38 escenarios de referencia con etiqueta normativa ANAC/OACI (`normative_label`). Fuente compartida por calibracion y sensibilidad. **Declara en su encabezado el ALCANCE de la independencia de la referencia**: vale para vis/techo/wx/TAF, NO para cruzado ni rafaga, donde la etiqueta replica los cortes del motor y la concordancia es por construccion. |
| `risk/calibration.py` | **COMPLETO** | Calibracion de umbrales por anclaje normativo (grid search + costo asimetrico). Resultado: t_go=0.22, t_caution=0.59 (36/38 = 95% concordancia, 0 sub-avisos, 2 sobre-avisos). Reporta ademas la concordancia **por nivel de minimos personales** (sin minimos 95%, PPL 89%, Alumno 71%) y verifica que en ninguno hay sub-avisos: el desvio es siempre por sobre-aviso. Validez de constructo, no empirica. |
| `risk/sensitivity.py` | **COMPLETO** | Sensibilidad en 5 ejes: [1] OAT ±20% por peso, [2] Monte Carlo 7 pesos, [3] umbrales, [4] **parametros de forma de las r_i**, [5] **fraccion de CAUTION de la barrera de cruzado** (la rafaga ya no tiene piso propio). Estabilidad del veredicto 99%; 37/38 escenarios nunca cambian. **Hallazgo clave**: los parametros de forma pesan MAS que los pesos (5.3% de flips contra 0.8%; desde septiembre de 2026 se perturban los dos extremos de cada rampa). |

### INTEGRACION

| Archivo | Estado | Descripcion |
|---|---|---|
| `config.py` | **COMPLETO** | Constantes globales: `NWP_STATIONS` (derivado de los 561 aerodromos de `AIRPORTS`), `METAR_STATIONS` (vacio, vestigio v1.0), `NWP_HOURS_AHEAD`. |
| `decision/enroute.py` | **COMPLETO** | `evaluate_nwp_at_coord()` devuelve **cinco** valores: `(r_total, decision, ref_wx, score, level_hour)`. `ref_wx` es superficie; `level_hour` trae las condiciones DEL NIVEL. Se entregan separados a proposito. Ademas `nwp_series_at_coord()`. **Extraidas de `web/app.py`** (septiembre 2026) porque el copiloto tambien las necesita y que la capa de lenguaje importara de `web/` invertiria las dependencias. En crucero **anulan el viento cruzado** (el piloto crabea; el cruzado es concepto de pista) y aplican el minimo VFR de 8 km sobre FL100. |
| `decision/engine.py` | **COMPLETO** | `DecisionEngine.evaluate()` → `DecisionResult`. Pipeline: fetch→parse→**condiciones del momento**→hard_blockers→soft_score+taf_window→decision. **Regla de fuente**: con codigo ICAO intenta METAR+TAF y cae a NWP si no hay METAR; sin ICAO va directo a NWP. **Regla de momento** (`_condiciones_para_el_momento`): dentro de `VIGENCIA_OBSERVACION_H` manda el METAR; despues manda el TAF y el NWP completa temperatura y rocio; si ninguno cubre el momento, NWP entero. El camino NWP usa **muestreo en anillo** (peor caso en tiempo Y espacio). |
| `output/briefing.py` | **COMPLETO** | `generate_briefing(...)` → briefing meteorologico multi-linea para el piloto (origen, destino, ruta, NOTAMs). 100% reglas, sin IA. |
| `output/flight_plan.py` | **COMPLETO** | `build_flight_plan(...)` → plan de vuelo OACI (casillas 7-19 + mensaje FPL). **No radica** el plan: lo presenta el piloto. |
| `web/app.py` | **COMPLETO** | Backend FastAPI + frontend HTML (`web/static`). **Entry point unico del sistema.** Endpoints: `/api/evaluate`, `/api/profile`, `/api/timeline`, `/api/flightplan`, `/api/airport/{code}`, `/api/airports`, `/api/airports/map`, `/api/aircraft`, `/api/vfr_corridors`, `/api/airspace`, `/api/copilot` (devuelve ademas `proposal`, la propuesta de ruta SIN aplicar), `/api/copilot/status`. Switch VFR/IFR, corredores VFR, perfil vertical, panel del copiloto. **Puntos de paso**: `EvaluateRequest.via` (sobrevuelo o escala), validacion con nombre, ficha por escala a su hora de llegada, costo del desvio y veredicto global que incluye las escalas. |

### COPILOTO (capa de lenguaje natural)

| Archivo | Estado | Descripcion |
|---|---|---|
| `copilot/client.py` | **COMPLETO** | Cliente Gemini por **REST plano, sin SDK** (`requirements.txt` sigue en 4 paquetes). `LLMClient` es un Protocol: el resto del paquete no depende de Gemini. **Cadena de reserva** entre modelos ante los 503 del nivel gratuito. `ScriptedClient` para tests sin red. |
| `copilot/tools.py` | **COMPLETO** | Las **8 herramientas** deterministas + su esquema de function calling. Envoltorios finos sobre `data/airports.py`, `route/performance.py`, `decision/engine.py`, `decision/enroute.py` y `route/airway_router.py`. **No agregan ninguna fuente de datos.** `resolve_airport()` con ranking (codigo > nombre exacto > prefijo > provincia). `proponer_cambio_de_ruta()` PROPONE un cambio de ruta y no lo aplica. |
| `copilot/prompts.py` | **COMPLETO** | Instruccion de sistema con las 5 reglas duras + `verdict_fallback()`, la plantilla determinista de veredicto. |
| `copilot/agent.py` | **COMPLETO** | Bucle pregunta→herramienta→datos→redaccion (tope 3 vueltas) y las **dos garantias que se hacen cumplir en codigo**: `_enforce_verdict()` y `_enforce_codes()`. `trim_history()` recorta en limites de turno. Inyecta `ruta_actual` en las herramientas (el estado de pantalla va por codigo, no por el modelo) y saca la propuesta de ruta del RESULTADO de la herramienta, nunca del texto. |
| `copilot/eval_set.py` | **COMPLETO** | **98 casos** etiquetados, **9 intenciones**, **las 5 regiones**. Un caso puede traer `contexto` (estado del formulario): hay intenciones que solo existen con un vuelo cargado. Incluye 11 casos cuyo dato NO existe, con patron de deteccion objetiva. Un test exige >=5 casos por intencion: agregar una herramienta sin sus casos rompe la suite. |
| `copilot/evaluate.py` | **COMPLETO** | Matriz de confusion, P/R/F1, resolucion de entidad, **tasa de invencion**, integridad de veredicto, latencia, desagregado por region. Cachea en `eval_results.json`. |

### ROUTE LAYER

| Archivo | Estado | Descripcion |
|---|---|---|
| `route/optimizer.py` | **COMPLETO** | Interfaz unica: `optimize()`. La web usa siempre `mode="suggested"` (A* sobre corredor geografico, eligiendo el candidato con mayor cobertura de aerovia). Con `via=[ViaPoint(...)]` arma la ruta por segmentos (`_optimize_via`): cada tramo sale cuando aterriza el anterior y el combustible se agrupa en ETAPAS separadas por las escalas. `detour_cost()` compara contra la ruta directa. **No modela tiempo en tierra, por decision explicita** (ver *Un sobrevuelo y una escala...*). |
| `route/graph.py` | **COMPLETO** | Grafo de aerodromos con `max_leg_km` + rechazo por bounding box. Modos shortest/fastest/safest. Expone `max_gs_kt` (cota superior de velocidad de tierra) para la heuristica de A*. El peso de arista NO se redondea: redondearlo violaba la desigualdad de admisibilidad. |
| `route/astar.py` | **COMPLETO** | A* con heuristica haversine admisible en los tres modos. En `fastest` divide por `graph.max_gs_kt` (crucero de LA AERONAVE + viento), no por una constante: dividir por los 97 kt del Alpha rompia la admisibilidad para PA-28, C172 y DA40. Fijado por `tests/test_route.py`, que verifica h(n) <= costo real contra Dijkstra para los 5 perfiles. |
| `route/airway_router.py` | **COMPLETO** | Dijkstra sobre aerovias filtrado por MEA de la aeronave. `find_airways_for_leg()`, `find_airways_for_route_legs()` (camino continuo end-to-end). |
| `route/vfr_corridors.py` | **COMPLETO** | Ruteo VFR por corredores visuales de las TMA BA/Cordoba (grafo + Dijkstra por cluster). `corridor_path_for_leg()` devuelve ademas el **nombre de cada punto**, derivado del `name` del corredor (que lista sus puntos en orden): sin eso los dos extremos comparten el id del corredor y no hay como decir donde se vira. |
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
    source        : str               # "metar" | "metar+taf" | "nwp"
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

Ademas: `visibility_km < 3.0` o `ceiling_ft < 500` en las condiciones del momento evaluado.
Estos dos limites son una decision del sistema, no una norma: el minimo VFR de la
regulacion es 5 km / 1000 ft, y entre ambos el veredicto sale del puntaje y la barrera,
con piso de CAUTION bajo el minimo VFR.
Hasta septiembre de 2026 el limite de visibilidad era 1.5 km.

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
cruzado efectivo >= limite avion       → NO GO   cruzado >= 85%  del limite  → CAUTION
niebla probable (r_fog >= 0.9)     → CAUTION     deterioro TAF (r_taf >= 0.6) → CAUTION
vis < 5 km o techo < 1000 ft (bajo el minimo VFR, norma)              → CAUTION
```
El piso del **minimo VFR** (septiembre 2026) existe porque la suma ponderada no lo
garantizaba: con nivel Avanzado, visibilidad de 4.92 a 4.99 km y el resto ideal
daba GO. Usa las mismas constantes que la categoria de vuelo (`VFR_MIN_VIS_KM`,
`VFR_MIN_CEIL_FT` en `parsers/metar_parser.py`), asi que en visibilidad y techo **la
ausencia de sub-avisos es una garantia por construccion**, no un resultado medido.
El **cruzado efectivo se calcula sobre la RAFAGA**, no sobre el viento sostenido:
es el peor instante que el avion va a encontrar. Por eso la rafaga no necesita un
piso aparte — y tenerlo producia vetos con el viento alineado con la pista (ver
*La rafaga entra por su componente cruzado*).
Cubre los factores de bajo peso que el score diluye; vis/techo (peso alto, deterioro
gradual) siguen compensatorios. Elevo la concordancia con la norma de 66% a 92%.

**Thresholds de decision** — CALIBRADOS por anclaje normativo (`risk/calibration.py`):
```
R < 0.22           → GO
0.22 <= R < 0.59   → CAUTION
R >= 0.59          → NO GO
```
Los cortes se ajustaron sobre la bateria de referencia (`risk/scenarios.py`) minimizando
un costo asimetrico (sub-aviso >> sobre-aviso). Hasta el piso del minimo VFR el optimo era
un rango (t_go∈[0.15,0.28], t_caution∈[0.59,0.66]) que contenia los valores adoptados.
Es validez de CONSTRUCTO (reproduce la regulacion), no empirica. Concordancia 36/38 = 95%.

> **Umbrales mantenidos por decision (septiembre 2026).** Con el piso de CAUTION bajo
> el minimo VFR, la grilla admite t_go en [0.34, 0.45] con 37/38 y 0 sub-avisos: el unico
> escenario que cambia es G2 (nieve moderada, vis 6 km, techo 1500 ft), de CAUTION a GO.
> Se mantiene 0.22 a proposito: es mas conservador por encima del minimo, y mover t_go 0.12
> por un solo escenario seria ajustar a la bateria. `risk/calibration.py` lo avisa.

> **Convergencia (resultado de validacion).** Los umbrales se recalibraron al pasar los
> pesos de juicio experto a derivacion por evidencia. El optimo se movio de
> (0.22, 0.50) a (0.22, 0.59) — t_go ni siquiera cambio — y la concordancia siguio
> siendo 35/36 con 0 sub-avisos, con el mismo unico desacuerdo (G2, sobre-aviso).
> Dos derivaciones independientes de los pesos producen el mismo comportamiento
> decisional: el veredicto no depende de la ponderacion exacta.

### Superficie y altura son datos distintos y no se mezclan

> **Un punto de ruta tiene dos realidades a la vez**: el suelo que queda debajo y el
> aire por el que el avion efectivamente lo cruza. Describir el segundo con datos del
> primero produce errores de decenas de grados.

Bug real (septiembre 2026), encontrado por el piloto usando el asistente: el informe
en altura daba 19.1 C tanto a 6.000 como a 15.000 ft. La causa era que
`get_forecast(cruise_alt_ft=...)` pedia del nivel de presion **solo el viento**:

```
Bell Ville, 08/09 12:00 — medido contra la API
    superficie           15.5 C
    800 hPa (6.581 ft)    5.2 C
    600 hPa (14.154 ft)  -8.1 C
```

**Como quedo resuelto:**
- El fetcher pide **todas** las variables del nivel en la MISMA peticion (no cuesta
  una llamada extra) y las deja en campos `level_*` que NO pisan los de superficie.
- `ParsedWeather` sigue siendo un contrato de **superficie**. Forzar ahi los datos de
  un nivel obligaria a rellenar campos que en altura no significan nada (techo AGL,
  spread para niebla) — que es exactamente como nacio el bug. Por eso `get_upper_air()`
  tiene tipos propios y `evaluate_nwp_at_coord()` devuelve el nivel por separado.
- La interfaz **etiqueta cada dato**: "EN EL NIVEL DE RUTA" contra "EN SUPERFICIE,
  DEBAJO DEL PUNTO", y declara la **altura geopotencial real** del nivel (600 hPa
  estuvo a 14.091 ft el dia de la prueba, no a los 15.000 pedidos).
- **La visibilidad NO se informa en altura**: Open-Meteo no la publica por nivel de
  presion (verificado contra la API). Se dice que no esta, en vez de sustituirla por
  la de superficie.
- Los nombres de variable tienen que coincidir entre pedido y lectura: Open-Meteo
  acepta `windspeed` y `wind_speed` pero devuelve **la grafia que se pidio**, y leer
  la otra hace que el viento caiga EN SILENCIO al de superficie. Hay un test que lo fija.
- **El mock simula el nivel** con gradiente ISA. Sin eso, un test escrito sobre el mock
  pasaria con el bug puesto, que es lo peor que puede hacer una red de seguridad.

> **Limitacion que SIGUE en pie, declarada.** El PUNTAJE de un checkpoint en ruta
> (`r_vis`, `r_ceil`) se calcula con la visibilidad y el techo de SUPERFICIE, porque
> no existe visibilidad por nivel de presion en la fuente. La informacion que se
> MUESTRA ya es correcta y esta etiquetada; el scoring no cambio. Moverlo a la
> nubosidad del nivel alteraria veredictos de ruta y exigiria recalibrar: es una
> decision pendiente, no un olvido.

### El techo de un corredor VFR es AGL; la altitud de vuelo es MSL

Bug real (septiembre 2026): los corredores visuales publican su techo en **AGL** —sobre
el terreno— y `web/app.py` lo usaba tal cual como `cruise_alt_ft`, que es MSL.

```
Corredor TMA Cordoba: upper_limit_ft 1500, limit_reference "AGL"
    tomado como MSL  ->  1500 ft, o sea 2234 ft POR DEBAJO de SACC (3734 ft)
    convertido a MSL ->  5207 ft sobre el terreno de la salida
```

Consecuencias que producia: un falso "terreno por encima de tu altitud VFR" en toda
ruta por corredor, y una consulta NWP al nivel de presion de 1500 ft para un vuelo que
va a 5200.

`_corridor_alts_msl()` resuelve el terreno de todos los puntos del tramo en **una sola**
peticion SRTM y suma el limite AGL. Si el terreno falla, degrada interpolando entre las
elevaciones de los dos aerodromos: peor estimacion, pero del orden correcto. Un techo
que ya venga en MSL se usa sin tocar.

**Nota:** al ser AGL, la altitud del corredor SIGUE al terreno y no es un nivel plano
(5207 -> 3951 -> 3216 ft en SACC-JES). El perfil vertical la dibuja como una linea
horizontal tomando el primer valor: es una aproximacion, ya no un disparate.

### El horizonte del pronostico sigue a la salida pedida, y se declara si no llega

Bug real (septiembre 2026): `_evaluate_nwp` pedia siempre `NWP_HOURS_AHEAD` (12 h).
Una salida planificada para dentro de 19 h quedaba FUERA de ese filtro, el motor caia
en "la hora disponible mas cercana" y **no lo decia**:

```
pedido 15:00Z  ->  evaluaba 06:00Z   (6.3 C, llovizna)   <- 9 horas de desvio
pedido 06:00Z  ->  evaluaba 06:00Z   (6.3 C, llovizna)
```

El piloto recibia condiciones de la madrugada como si fueran de su vuelo del mediodia.
Los datos estaban disponibles (el fetcher trae 2 dias); los descartaba el filtro.

- `_horizonte_necesario(departure_time, flight_duration_h)` deriva las horas de la
  ventana pedida, con piso en `NWP_HOURS_AHEAD` y techo en `MAX_FORECAST_HOURS` (48).
  Cubre hasta el ATERRIZAJE, no hasta el despegue.
- Si aun asi la salida cae fuera, `DecisionResult.forecast_out_of_range` lo declara y
  la interfaz lo muestra como advertencia. Devolver condiciones de otro momento como
  si fueran las pedidas es peor que no responder: el piloto no tiene como notarlo.
- **El aviso mira el DESVIO REAL, no si la ventana quedo vacia.** Hay dos causas muy
  distintas para que ninguna hora caiga dentro de la ventana, y solo una es un
  problema: un vuelo de 14 min que sale 20:11 no contiene ni las 20:00 ni las 21:00,
  y eso es lo normal en tramos cortos —la hora mas cercana esta a 11 minutos—. Como el
  pronostico es horario, el redondeo legitimo nunca cuesta mas de media hora:
  `DESVIO_MAX_ACEPTABLE_H = 1.0` deja margen al doble. Confundir los dos casos producia
  un falso "fuera de alcance" en casi todo vuelo corto.

**Y la tarjeta ahora dice SIEMPRE para que momento son los datos**, en hora local y
UTC. Sin eso, un pronostico de madrugada se lee como el estado actual. El campo de
hora del formulario decia `"Hora local UTC"` —una contradiccion— y mostraba la
equivalencia de ninguna: ahora dice `06:00 UTC = 03:00 hora local argentina` mientras
se escribe.

### La nubosidad viene bajo la clave "clouds", no "skyCondition"

Bug critico y preexistente (septiembre 2026), el mas grave encontrado en el proyecto.
Los dos fetchers pedian `"skyCondition"`; la API devuelve `"clouds"`:

```python
sky = d.get("skyCondition") or []      # METAR — siempre []
sky = fcst.get("skyCondition") or []   # TAF   — siempre []
```

Consecuencia: `sky_layers=[]` y `ceiling_ft=None` **siempre**, en todo aerodromo con
METAR. El sistema estaba **ciego al TECHO**, que pesa 0.357 — tanto como la
visibilidad, el maximo del modelo.

Medido sobre los 48 METAR argentinos de una toma: **11 tenian techo real y no se veia
ninguno**, incluido `SARI ... 8000 OVC003` — 300 ft, por debajo del bloqueo duro de
500. El sistema lo clasificaba VFR con techo `None`.

- Los nombres de campo reales son `cover` y `base` (no `skyCover`/`cloudBase`),
  verificado sobre 48 METAR y 36 TAF. El parser acepta ambos por compatibilidad.
- **`NSC`** ("No Significant Cloud") aparece solo en TAF y SIEMPRE sin base — 47 de 47
  casos. Queda como capa sin base y por lo tanto no constituye techo, que es el
  tratamiento correcto.

> **Y el mock encodaba el formato equivocado.** Usaba `skyCondition`/`skyCover`/
> `cloudBase` y visibilidad en metros, formatos que la API no devuelve nunca. Por eso
> la suite pasaba con el bug puesto en vez de atraparlo. Es el mismo patron que el mock
> del NWP: **un mock que no imita a la fuente no es una red de seguridad, es una
> confirmacion del error**. Hay un test que verifica que el mock use las claves reales.

### La visibilidad de aviationweather viene en MILLAS TERRESTRES

Bug critico y preexistente (septiembre 2026). La API entrega la visibilidad como texto
y en millas terrestres, SIN sufijo; el parser la tomaba como metros:

```
TAF SAEZ: "... 4000 BR ..."        la API devuelve  '2.49'  (millas)
    parser:  2.49 / 1000 = 0.002 km   -> debajo del bloqueo duro de entonces (1.5 km) -> NO GO
    real  :  2.49 x 1.609 = 4.01 km   -> perfectamente operable
```

Medido sobre 28 periodos TAF de 7 aerodromos controlados: **8 bloqueos duros falsos**.
Tres aerodromos (SACO, SAAR, SAME) daban NO GO por esto en el momento de la prueba.
Peor todavia, el sintoma era indistinguible de un bloqueo legitimo: SACO y SANC
bloqueaban por TSRA real y los otros por nada.

Tres defectos de la misma familia, todos corregidos:
- Un numero suelto es **milla terrestre**, no metros. La distincion con el formato OACI
  en metros es por MAGNITUD y no tiene ambiguedad: la API tope la escala en "6+", asi
  que una visibilidad en millas nunca pasa de ~10 y una en metros nunca baja de las
  centenas (`_UMBRAL_SM_VS_METROS`).
- **`'6+'` no se reconocia** y devolvia `None`: es como la API codifica el CAVOK, asi
  que el sistema estaba CIEGO a la visibilidad en todos los aerodromos con METAR
  (los 17 muestreados reportaban `'6+'`).
- **`SM_TO_KM` era 1.852**, la milla NAUTICA. La terrestre son 1.609344 km, asi que
  toda visibilidad en SM se sobreestimaba un 15 %.

Verificado que el arreglo no vuelve permisivo lo que si es peligroso: en SACO, la
franja con `TSRA` sigue dando NO GO y las de CAVOK y `SHRA` dan GO.

### Cada extremo del vuelo se evalua PARA SU MOMENTO

El origen importa cuando se despega; el destino, cuando se aterriza. `web/app.py`
evaluaba los DOS con `dep_time` y la ventana del vuelo entero:

```
SACC -> Cruz Alta, salida 14:00, llegada 16:26
    origen   ventana [14:00, 16:26]  mostraba 14:00   (ok)
    destino  ventana [14:00, 16:26]  mostraba 14:00   <- 3 h antes de aterrizar
```

El piloto leia en la ficha de destino la temperatura y el viento de la hora de
SALIDA. No es una imprecision de detalle: es informacion de seguridad presentada
como si fuera del momento pedido.

- El origen se evalua para `[salida, salida + VENTANA_EXTREMO_H]` y el destino para
  `[llegada, llegada + VENTANA_EXTREMO_H]` (1 h). La ventana del vuelo entero metia
  en el origen condiciones de horas despues de haberse ido.
- La llegada no se conoce hasta calcular la ruta, asi que se usa la estimacion
  rapida y se **reevalua el destino** si la ETA real cae en OTRO slot horario.
  Es barato: el NWP se cachea por coordenada, no por hora, asi que solo se
  vuelve a puntuar.
- **El momento se REDONDEA al slot horario mas cercano** (`_hora_redonda`). Sin eso,
  una ventana que arranca exacto en el momento deja afuera el slot mas cercano: para
  una llegada a las 14:49, `[14:49, 15:49]` excluye las 14:00 y termina mostrando las
  16:00 — 71 minutos despues. Con redondeo el desvio maximo es de **media hora**, que
  es el piso teorico de un pronostico horario.

> **Con METAR la pregunta es otra.** Un METAR es una OBSERVACION del pasado reciente:
> no existe "el METAR de las 19:00". Por eso la ficha lleva `obs_time` (cuando se
> observo) y `window_start` (para que momento se evaluo) por separado, y la interfaz
> dice *"Observacion de las 11:00 · evaluado para la llegada de las 15:02 con el
> pronostico TAF"*. Presentar la observacion como "las condiciones de la llegada"
> seria enganoso.

Verificado en las cinco regiones: desvio de 0 a 21 min en los extremos NWP.

### El veredicto sale del PEOR momento de la ventana, y hay que decirlo

En el camino NWP el motor evalua **toda la ventana de vuelo** y se queda con el peor
caso. La tarjeta, en cambio, muestra la hora de referencia (la mas cercana a la salida).
Casi nunca son la misma hora, y sin decirlo los dos numeros parecen contradecirse:

```
SACC — la tarjeta mostraba          17:00  viento 302/3.5 G15  →  Xwind 1.1 kt
       el cartel de factor limitante decia  "viento cruzado 10 kt"
       porque el peor caso estaba en 21:00  viento 093/1.8 G13.8 → 10.09 kt
```

El piloto no tiene forma de reconciliarlo mirando la pantalla, y parece un error del
sistema aunque la logica sea correcta. `DecisionResult.worst_obs_time` informa ahora de
que muestra salio el veredicto, y la interfaz lo dice cuando difiere de lo mostrado.

### Un sobrevuelo y una escala dan la misma linea y no son el mismo vuelo

El motor de ruta ya sabia pasar por puntos intermedios (`ViaPoint`, `_optimize_via`,
`detour_cost`), pero no habia forma de pedirselo: `/api/evaluate` no tenia el
parametro y el formulario no tenia el campo. La capa web lo expone.

```
"quiero PASAR POR Rosario"        SOBREVUELO  el aerodromo es forma de la ruta
"quiero hacer ESCALA en Rosario"  ESCALA      se aterriza ahi
```

La geometria es identica —misma linea, misma distancia, mismo combustible
total— y el vuelo no lo es. La diferencia se pide **explicita** en el formulario
(el desplegable ofrece los dos botones y el chip lo sigue diciendo) y viaja como
`is_stop`; el backend no la adivina del texto. De la escala se siguen tres cosas:

- **Se evalua como aerodromo**, con ficha propia, a la hora en que se ATERRIZA
  ahi —derivada de los tramos reales de la ruta ya calculada, no de la
  estimacion inicial—, y con los bloqueos que NO son meteorologicos: aterrizar
  en una escala despues del ocaso veta el vuelo por la misma razon que veta
  aterrizar en el destino, y un NOTAM de cierre tambien. El optimizador evalua
  la escala pero solo la meteorologia; la noche y los NOTAM se conocen en la web.
- **Su veredicto pesa en el global**, que es el peor de todos los aerodromos
  donde se toca el suelo. El `R` que acompana al veredicto tambien: si el
  veredicto sale de una escala, el numero tiene que ser el de ella.
- **Un sobrevuelo NO genera ficha.** El veredicto de aerodromo mide despegue y
  aterrizaje contra una pista concreta; emitir uno para un punto por el que solo
  se pasa volviria a darle dos significados a la misma etiqueta.

**El desvio se cobra.** `RouteCard.detour` informa cuanto agrega contra la ruta
directa —km, minutos, litros— porque sin ese numero aceptar un punto de paso es
un boton a ciegas. La comparacion NO pide alternativo ni evalua intermedios: es
geometria y performance, sin una sola peticion de red. El **quiebre de
autonomia** se declara aparte de los kilometros: que un desvio convierta una
ruta viable en una que no cierra no es un detalle de magnitud, es un cambio de
viabilidad. Verificado: SAAR-SACO con el Alpha Trainer cierra directo (368 km) y
no cierra por SAZB (1579 km) -> `rompe_la_autonomia=True`; SACO-SAEZ con el
mismo avion ya no cerraba antes del desvio -> `False`, porque el desvio no lo
rompio.

**Un punto que el piloto pidio nunca se degrada a marcador lateral.** Un
aerodromo intermedio sale de la linea de ruta cuando la aerovia es continua a su
alrededor (pasa a ser referencia de emergencia al costado). Un punto pedido no:
no esta ahi como consecuencia del calculo, esta porque el piloto lo puso.
`is_via` / `is_via_stop` lo declaran en cada waypoint.

**La entrada invalida falla con NOMBRE**, no a mitad de camino: codigo
inexistente, repetido, igual al origen o al destino, o mas de `MAX_VIA_POINTS`
(5) devuelven 400 diciendo cual es el punto conflictivo. Resolver un codigo mal
escrito por aproximacion mandaria al piloto a otro aerodromo, que es el mismo
modo de falla que la regla R4 del copiloto existe para evitar.

> **No se modela tiempo en tierra, y es deliberado.** `_optimize_via` hace
> `salida_del_siguiente_tramo = llegada_del_anterior`, asi que una escala sale a
> la misma hora en que aterriza. Para la escala en si no cambia nada —se evalua
> a la hora de llegada, que es cuando se aterriza—, pero corre hacia atras la
> ETA de todo lo que viene despues.
>
> **Decision del piloto (septiembre 2026), con su fundamento**: el tiempo en
> tierra es la variable menos predecible del vuelo —depende del servicio de
> combustible, del tramite, de la espera— y elegir un numero seria inventar la
> premisa del calculo, que es justo lo que este sistema no hace en ningun otro
> lado. La conducta correcta es la real: **si se hace escala, se vuelve a la web
> desde la escala** y se recalculan tiempos y riesgo hasta el destino con la hora
> de salida verdadera. Un pronostico a varias horas vista recalculado sobre el
> terreno vale mas que uno proyectado con una demora supuesta.

> **Y el modo mock no llegaba al terreno.** `_corridor_alts_msl()` llamaba a
> `get_elevations_m()` **sin propagar `mock`**, de modo que `mock=True` —que el
> proyecto declara como "desarrollo sin conexion"— igual salia a Open-Topo-Data
> por ese camino. Se encontro porque los tests nuevos de la capa web, escritos
> sobre mock, tardaban 0.3 s cada uno; con la red bloqueada a proposito la suite
> completa baja de 8 s a 4 s. Un test que depende en silencio de que responda
> una API externa no prueba lo que dice probar.

### Evitar espacios aereos es OPT-IN: el rodeo solo puede apoyarse en aerodromos

Bug real encontrado por el piloto (septiembre 2026): pidio SACC-JES (43 km
directo) con una escala de paso en SACD y le salio una ruta de 313 km que
**pasaba dos veces por el mismo aerodromo**:

```
SACC -> VDR -> SACD -> VDR -> JES     313.4 km   con evitacion
SACC -> SACD -> JES                   120.9 km   sin evitacion
```

La causa no es el modulo de puntos de paso: es que el grafo de ruteo solo tiene
**aerodromos** como puntos intermedios. Esquivar una zona no es correr la linea
unos kilometros, es rodearla por el aerodromo disponible mas cercano — y para
salir de la TMA Cordoba el mas cercano era Villa del Rosario, a 114 km. Cada
tramo lo resolvio por su cuenta (SACC->VDR->SACD y SACD->VDR->JES) y al
concatenarlos el avion vuelve sobre sus pasos.

**Decision: el switch arranca APAGADO** y lo prende el piloto si lo necesita. Un
rodeo de ese tamaño es una decision operativa suya, no un comportamiento por
defecto; y al lado del switch se avisa que puede alargar mucho la ruta. El costo
del desvio ya se informaba correcto (+270.1 km): lo que fallaba no era la
medicion sino que nadie hubiera elegido esa ruta si la hubiera visto antes.

> **Limitacion que SIGUE en pie, declarada.** Con la evitacion prendida el
> camino fusionado todavia puede repetir un aerodromo. Arreglarlo de verdad pide
> waypoints que no sean aerodromos —o resolver el camino completo con los puntos
> de paso como obligatorios, en vez de tramo por tramo—, y las dos cosas tocan
> el nucleo del ruteo. Es una decision pendiente, no un olvido.

**Y el copiloto rutea con las MISMAS reglas que la pantalla.** `pageContext()`
manda ahora `avoid_airspace` y `proponer_cambio_de_ruta` lo propaga a
`optimize()`. Sin eso el asistente calculaba el costo del desvio sin la
restriccion puesta y daba un numero que no coincidia con el de la ficha de ruta:
dos cifras para la misma cosa en la misma pantalla.

### La tabla describe el camino que se VUELA, con un rumbo por tramo

Bug real encontrado por el piloto (septiembre 2026): el mapa dibujaba SACC-JES
como dos tramos con rumbos distintos —el corredor visual dobla la ruta en
Ascochinga— y la tabla de abajo mostraba **un solo rumbo**, el directo. El
piloto veia el viraje en el mapa y no tenia donde leer a que rumbo virar.

Detras habia algo mas grande: el optimizador mide la ruta como la **recta entre
aerodromos**, pero el avion no vuela esa recta. Distancia, tiempo, combustible y
hora de llegada se estaban subestimando:

```
                                      recta    volado   diferencia
SACC-JES     corredor TMA Cordoba      43.3      45.4      +2.1 km
SADP-SAAG    corredor TMA Bs As       178.4     180.2      +1.8 km
SASA-SACO    aerovias (IFR)           728.1     732.5      +4.4 km
SAME-SACO    corredor + ruta larga    463.9     505.9     +42.0 km   (+9.1 %)
SAZN-SAZS    sin doblez               353.9     353.9      +0.0 km
```

**Todo lo que se informa pasa a medir el camino volado** (`_tramos_volados`):
una fila por cada segmento que dibuja el mapa, con su rumbo, y los totales,
`fuel_ok` y la ETA derivados de ahi. La ETA se corre entre 0 y 16 min segun la
ruta; en los cinco casos medidos no cambio la hora de pronostico que se consulta
para el destino, pero puede hacerlo.

- **La tabla y el mapa usan la MISMA espina** (waypoints menos los aerodromos de
  emergencia, que estan al costado y no se sobrevuelan). No pueden discrepar
  porque salen del mismo filtro; hay un test que lo fija contando segmentos.
- **Un punto SOBRE la linea no alarga nada.** Los checkpoints meteorologicos se
  interpolan sobre el tramo: partir una recta en dos no cambia su longitud. Que
  SAZN-SAZS de +0.0 km con un checkpoint en el medio es la verificacion.
- **Cada fila declara QUE es el punto** —aerodromo, corredor, aerovia o
  checkpoint meteo—, porque no todos son un viraje: un checkpoint no cambia el
  rumbo y leerlo como instruccion de navegacion seria un error.
- **El combustible se sigue agrupando en ETAPAS** separadas por las escalas: en
  cada una se vuelve a cargar, y el tanque no tiene que aguantar el vuelo entero.
- **Los waypoints se generan ANTES de fijar la ETA.** Si se hicieran despues, el
  destino quedaria evaluado para una hora a la que el avion todavia no llego.
  Para elegir a que hora consultar el pronostico de cada checkpoint se usa la
  estimacion en recta, que es el unico dato disponible en ese punto y desvia
  menos que la resolucion horaria de la fuente.
- El ETA por waypoint **ya** se calculaba sobre la espina real y ajustado por
  viento (paso 3 de `_generate_route_waypoints`): lo que estaba mal era el
  resumen, no el recorrido.

> **El nombre de un punto de corredor no estaba en ningun lado.** El `code` de
> un waypoint de corredor es el id del CORREDOR y se repite en sus dos extremos,
> asi que la tabla decia `VFR-COR-04 -> VFR-COR-04`. La solucion no fue inventar
> un nombre: el `name` publicado de cada corredor **lista sus puntos en orden**,
> separados por " - " y uno por coordenada ("ASCOCHINGA - AD. LA CUMBRE" son sus
> dos extremos; "RIO SEGUNDO - TOLEDO - AD CORONEL OLMEDO" sus tres). Verificado
> sobre **los 22 corredores de las dos TMA: la convencion se cumple en todos**, y
> los puntos compartidos entre corredores distintos reciben el mismo nombre. Un
> corredor que no la cumpliera dejaria el punto SIN nombre, nunca mal nombrado, y
> hay un test de integridad del dato que avisa.

### La rafaga entra por su componente cruzado, no por su magnitud cruda

Bug real encontrado por el piloto (septiembre 2026). SACC, viento **145/12.5 kt
racheado a 27** sobre la pista **140**: el viento entra a 5 grados de la pista.

```
cruzado sostenido        1.1 kt
cruzado CON RAFAGA       2.4 kt        contra un maximo demostrado de 18 kt
delta de rafaga         +14 kt        contra una referencia de 20 kt
```

La pantalla mostraba **"Factor limitante (no se compensa con el resto): rafaga
+18 kt"** en un dia en que el avion no recibe carga lateral. La barrera de
rafaga medía **cuanto varia el viento, sin mirar hacia donde**.

**La barrera sobre el delta crudo de rafaga se ELIMINO.** El fundamento es el
estatus de cada numero, el mismo que ya sostenia la separacion de escalas:

- `crosswind_max_kt` es un maximo **DEMOSTRADO en certificacion**: define un
  limite operativo, y un veto se apoya en un limite.
- `gust_max_kt` es una **referencia de operacion normal**, no un limite. Vetar
  —que por definicion no se compensa con nada— sobre una referencia era darle a
  ese numero una autoridad que no tiene.

**La rafaga NO desaparece del veredicto**, entra por dos caminos:
1. Por el **cruzado de rafaga**, que es lo que ya mide `xw_eff_kt`: si la rafaga
   carga lateralmente, veta por ahi. Verificado con el mismo viento del caso
   contra una pista perpendicular: NO GO por cruzado de 27 kt.
2. Como componente **compensatorio** `r_gust` (peso 0.050), que representa la
   turbulencia y el corte de viento y se promedia con el resto.

Lo que deja de existir es su capacidad de vetar sin componente cruzado.

**Efecto medido** sobre la bateria de 38 escenarios: concordancia con la
referencia **38/38**, **0 sub-avisos**; calibracion **sin cambios** (t_go=0.22,
t_caution=0.59, 36/38 = 95%, los mismos dos sobre-avisos G2 y J1). La bateria
acompaño el cambio: `normative_label` dejo de votar por el delta crudo, porque
si no mediria el desacuerdo contra un criterio que el propio proyecto descarto —
es la misma circularidad ya declarada para este factor, no una nueva.

`GUST_CAUTION_FRACTION` y `GUST_NOGO_FACTOR` se conservan como escala de
referencia de `r_gust` y de la bateria, pero **ya no gobiernan ningun piso**:
hay un test que verifica que cambiarlas no mueve ningun veredicto, y el eje [5]
de sensibilidad dejo de barrerlas (daban 0/38 flips en todos los valores, que no
es robustez sino una perilla desconectada).

> **Consecuencia declarada, pendiente de decision.** Sin piso por rafaga, un
> viento **alineado con la pista** de 15 kt racheado a 47 —delta +32 kt, 160% de
> la referencia de un C152— da **GO con R=0.050** (escenario E5 de la bateria).
> El cruzado es cero, asi que ninguna barrera lo ve, y `r_gust` aporta como
> mucho 0.050 por su peso AHP. Es coherente con el criterio adoptado, pero un
> gradiente de 32 kt es cortante de viento en corta final. Si se quiere cubrir,
> el lugar correcto NO es reponer la barrera al 85% —que es lo que se acaba de
> quitar— sino un veto de gradiente extremo, muy por encima, con su propia
> justificacion.

### El TAF decide el momento evaluado; el METAR solo mientras siga vigente

Un aerodromo con estacion tiene **tres** fuentes, y cual describe el momento del
vuelo depende de para **que momento** se pregunte. Hasta septiembre de 2026 el
motor puntuaba siempre la OBSERVACION y usaba el TAF solo como un componente de
tendencia de peso 0.022: una salida planificada para dentro de seis horas se
decidia con el estado del aire de hace un rato.

```
Fuente             Que aporta                                    Cuando manda
observacion METAR  todo, medido                                  |momento - obs| <= 1 h
TAF                visibilidad, techo, viento, fenomenos          el TAF cubre el momento
NWP                temperatura y punto de rocio                   siempre que haya TAF
NWP (completo)     todo, modelado                                 ni la obs ni el TAF llegan
```

- **`VIGENCIA_OBSERVACION_H = 1.0`**. El METAR se emite cada hora y describe el
  instante en que se tomo. Dentro de esa vigencia es el mejor dato posible: una
  medicion real le gana a cualquier pronostico del mismo momento. Pasada esa
  hora, el pronostico describe mejor lo que va a haber que una observacion vieja.
- **El TAF es la verdad, no una tendencia.** Es el pronostico oficial DEL
  AERODROMO, emitido por el mismo servicio que el METAR y con la misma autoridad
  normativa. Si existe y cubre el momento, sus valores SON las condiciones que
  se puntuan.
- **El NWP completa lo que el TAF no publica**: temperatura y punto de rocio, y
  con ellos el spread del que depende `r_fog` y la altitud de densidad. Sin ese
  complemento el spread quedaria en el de la observacion — en SACO, 13 °C
  observados contra **1.9 °C pronosticados** para seis horas despues, que es la
  diferencia entre no ver la niebla y verla.
- **El QNH lo sigue aportando el METAR**: es la unica fuente que lo publica y
  cambia despacio.

> **Cada MAGNITUD se toma entera de una sola fuente.** El viento es direccion y
> velocidad juntas; el cielo son las capas y el techo juntos. Combinar la
> velocidad pronosticada con la direccion del modelo produce un viento que
> ninguna de las dos fuentes predijo — informacion inventada por el promedio.
> Por eso `VRB` del TAF (`wind_dir=None`, `wind_variable=True`) **no** se
> completa con la direccion del NWP: VRB es una afirmacion, no un hueco. Lo
> mismo con `NSC`/`SKC`: en el TAF, "sin capa significativa" significa SIN TECHO,
> no "dato ausente", y tiene que borrar el techo que se observo horas antes.

**Se toma el peor caso de la ventana** (`worst_case`: base efectiva + TEMPO/PROB),
no la base sola. Es el mismo criterio con el que el motor ya evaluaba los
fenomenos peligrosos del TAF y la misma filosofia del camino NWP (peor caso en
tiempo y en espacio). Medido sobre los 36 aerodromos argentinos que hoy emiten
METAR y TAF, x 5 aeronaves x 6 horarios = **1070 evaluaciones**: incluir los
transitorios cambia el veredicto en **10 casos (0.9 %)**, todos CAUTION → NO GO
y todos por TEMPO, ninguno por PROB.

**Si ninguna fuente del aerodromo describe el momento, se va a NWP.** El TAF
cubre entre 24 y 30 h; el pronostico numerico llega a 48. Para quien planifica
con un dia de anticipacion —que es cuando esta decision se toma— la observacion
puede tener veinte horas. Entregarla como "las condiciones del vuelo" es el
mismo error que el motor ya evita cuando el aerodromo no tiene estacion: la
regla es la misma, un nivel mas adentro. Afecta tambien a los **12 aerodromos
que emiten METAR pero no TAF** (SAAG, SAMR, SAZG, SAOU y otros ocho).

**Efecto medido contra el motor anterior** (mismas 1070 evaluaciones, replicando
el viejo con su bloqueo por fenomeno peligroso del TAF ya incluido, para no
atribuirle al cambio bloqueos que ya existian):

| viejo → nuevo | casos | |
|---|---|---|
| coincide | 715 / 1070 | **66.8 %** |
| GO → CAUTION | 210 | endurece |
| GO → NO GO | 80 | endurece |
| CAUTION → NO GO | 11 | endurece |
| NO GO → GO / CAUTION | 44 | **ablanda** |
| CAUTION → GO | 10 | ablanda |

Los 101 NO GO nuevos se explican uno por uno: 40 por **niebla probable** (spread
del NWP, que antes no existia), 37 por **cruzado con rafaga** pronosticado que la
observacion no tenia, 13 por deterioro de visibilidad o techo, 11 por barrera de
cruzado sostenido. Ninguno es un fenomeno peligroso nuevo: esos ya bloqueaban. Y
en 54 casos el cambio **ablanda**, porque el TAF pronostica una mejora que la
observacion vieja no mostraba.

**La pantalla declara cual mando.** `DecisionResult.conditions_source` lleva la
procedencia en texto y `weather_source` pasa a tener tres valores
(`metar` | `metar+taf` | `nwp`), con insignia propia cada uno. Se muestra ademas
el **TAF crudo** junto al METAR crudo: si el veredicto sale del pronostico, el
pronostico tiene que poder verificarse igual que se verifica la observacion.
Sin esto la tarjeta decia "Observación de las 19:00" sobre una visibilidad
pronosticada — el mismo modo de falla que este proyecto viene corrigiendo.

**Verificado contra la API real**, no sobre el mock: 6 aerodromos de las 5
regiones, 3 aeronaves, 4 momentos cada uno, comparando **campo por campo** el
resultado contra el TAF, contra el NWP y contra el METAR. Un campo que no
coincide con ninguna fuente seria un valor inventado por la combinacion; no hubo
ninguno. Fijado por 13 tests en `tests/test_engine_data.py`.

> **Hallazgo del parser, de paso**: la API de AWC **normaliza los fenomenos
> compuestos**. El TAF de SARI dice `TSRAGR` en el texto crudo y la API entrega
> `wx_string: "TSRA TSGR"`. El parser hace bien en confiar en el campo
> estructurado; era la verificacion la que estaba mal escrita.

### Copiloto en lenguaje natural — arquitectura neurosimbolica

Asistente de planificacion **previo al vuelo, en tierra**. No asiste vuelo en curso
(coherente con la exclusion de uso en cabina ya declarada en el alcance).

> **Principio rector.** El LLM no sabe nada. No decide, no calcula y no aporta
> conocimiento propio. Interpreta la pregunta, elige que herramienta determinista
> llamar, y redacta con lo que esa herramienta devolvio. Si el dato no esta, lo dice.

El modelo de lenguaje es el **unico componente no interpretable** del sistema. Por
eso queda confinado por arquitectura a un rol donde sus modos de falla no pueden
alcanzar al veredicto: la decision la sigue tomando el motor deterministico.

**Que problema resuelve** (no es "IA porque si"): el registro ya tiene 395 telefonos
de jefes de aerodromo y 376 conjuntos de normas particulares que **nadie puede
consultar**, porque nadie navega 561 fichas. El asistente no agrega informacion:
hace utilizable la que ya se relevo.

**Las 5 reglas.** R1 nunca responde de conocimiento propio · R2 el veredicto se
transcribe, no se parafrasea · R3 ausencia de dato != ausencia de la cosa ·
R4 siempre nombra el codigo exacto que resolvio · R5 fuera de alcance se dice.

**R2 y R4 no son solo prompt: se verifican en codigo.** Un prompt es una sugerencia;
una validacion es una garantia.
- `_enforce_verdict()`: si el texto no transcribe el veredicto del motor, o menciona
  otro, se **descarta el texto** y se emite `verdict_fallback()`. La integridad de
  veredicto es 100% por construccion, no por confianza en el modelo.
- `_enforce_codes()`: detecta codigos con forma OACI argentina (`SA__`) que el modelo
  **fabrico** — no estan en las herramientas de ese turno NI en el registro. Modo de
  falla real observado en vivo: la herramienta devolvio `ALT` (Cruz Alta, sin OACI) y
  el modelo escribio `SAAL`. Un codigo mal escrito manda al piloto a otro aerodromo.

**R3 es la regla critica de seguridad.** La cobertura del registro es **inversa a la
intuicion**: los aerodromos grandes y controlados (SACO, SAAR) tienen `fuel`, `phones`
y `norms` VACIOS porque se publican en el AIP; los rurales chicos los tienen completos.
Las herramientas nunca devuelven `""`: devuelven `{"publicado": false, "nota": ...}`.
Decir "no tiene combustible" donde el registro solo no lo publica es un piloto que se
queda sin nafta en el aire.

**La intencion `fuera_de_alcance` es de diseno**: sin una clase explicita para
"no puedo contestar esto", el clasificador queda obligado a elegir una herramienta y
el modelo inventa para encajar.

**Las 8 herramientas** (9 intenciones con `fuera_de_alcance`): `buscar_aerodromo`,
`contacto_aerodromo`, `servicios_aerodromo`, `combustible_cercano`, `evaluar_meteo`,
`atmosfera_en_punto`, `mejor_hora_para_salir` y `proponer_cambio_de_ruta`.

> ### El veredicto es de AERODROMO; el informe en altura NO lleva veredicto
>
> `atmosfera_en_punto` responde "como esta el aire sobre tal lugar, a tal altura, para
> pasar por ahi". **Deliberadamente NO devuelve GO / CAUTION / NO GO**, y hay un test
> que lo verifica leyendo el codigo fuente de la funcion.
>
> El motivo es de fondo: el veredicto mide despegue y aterrizaje contra una PISTA
> concreta. Si una consulta sobre el aire a 7500 ft devolviera tambien un veredicto,
> la etiqueta pasaria a significar dos cosas distintas y dejaria de ser el objeto
> unico y bien definido sobre el que se apoya todo el motor.
>
> **De donde sale la altitud** (y la respuesta siempre lo declara, para que el piloto
> pueda verificar que no se la inventaron):
> - La eligio el piloto -> se respeta, acotada a `service_ceiling_ft`.
> - IFR sin altitud -> **MEA real del tramo de aerovia mas cercano**
>   (`route.airway_router.nearest_airway_segment()`, que lee el `mea` del grafo del AIP).
> - VFR sin altitud -> **la herramienta la PIDE**. No se elige una por defecto: el aire
>   a 3000 ft y a 12000 ft sobre el mismo punto no se parecen en nada, y elegirla seria
>   inventar la premisa de la respuesta.
>
> Incluye margen contra el terreno (grilla SRTM 3x3 a 10 km, UNA peticion) y degrada
> sin el si Open-Topo-Data falla: el informe de atmosfera se entrega igual.

> ### El copiloto PROPONE la ruta; aplicarla es un acto del piloto
>
> `proponer_cambio_de_ruta` es la **octava** herramienta y la unica que no
> responde sino que propone. Agrega, convierte o quita un punto de paso del
> vuelo cargado. Alcance deliberadamente acotado a los puntos de paso: el
> origen, el destino, la aeronave y la hora se siguen tocando a mano.
>
> **No aplica nada, por arquitectura.** Devuelve como QUEDARIA la ruta; la
> pantalla la muestra con su costo y el piloto la aplica con un click. El
> modelo de lenguaje elige que proponer; el codigo deterministico calcula que
> implica; el piloto decide. Ningun camino permite que el LLM modifique el
> vuelo de nadie. Aplicar tampoco evalua: deja el formulario cargado y el
> piloto aprieta **Evaluar** cuando quiere.
>
> **La ruta actual la aporta el CODIGO, no el modelo.** `ruta_actual` se
> inyecta desde el estado de pantalla igual que `engine_factory`. Hacer que el
> modelo transcriba origen, destino y puntos ya cargados es exactamente como
> nacio el bug de las tres horas: el formulario decia una cosa, el modelo
> copiaba otra, y nadie lo notaba.
>
> **La intencion no se adivina.** "Pasar por Rosario" y "hacer escala en
> Rosario" dibujan la misma linea y son dos vuelos distintos. La herramienta
> mapea las formas del castellano ("pasar por", "sobrevolar", "de paso por" ->
> sobrevuelo; "bajar en", "aterrizar en", "parar a cargar en" -> escala;
> "sacar", "sin pasar por", "volver a la directa" -> quitar) y cuando de verdad
> no se puede saber —"meteme Rosario en la ruta"— devuelve `falta_tipo` y el
> modelo **repregunta**. Verificado en vivo: con esa frase el asistente
> pregunta en vez de elegir.
>
> **Una escala propuesta trae su veredicto**, evaluado a la hora en que se
> aterriza ahi y con la hora de salida del FORMULARIO (que esta en UTC, no en
> hora local: se interpreta en codigo, sin pasar por el parser de hora local).
> Proponer una escala sin decir si se puede aterrizar seria ofrecer un boton a
> ciegas. Un sobrevuelo NO trae veredicto: no se aterriza ahi, y el veredicto
> de aerodromo mide despegue y aterrizaje contra una pista.
>
> **El veredicto de la escala entra a la barrera R2** como cualquier otro: el
> agente lo agrega a `resultados_meteo`, asi que si el texto no lo transcribe
> se reemplaza por la plantilla determinista. Sin eso habria un camino nuevo
> por el que un veredicto llega al piloto sin verificar.
>
> **El costo se mide contra la ruta que hay AHORA**, no contra la directa: con
> dos puntos ya cargados, lo que el piloto necesita saber es cuanto agrega ESTE
> cambio. Medido: agregar Parana a un vuelo que ya pasa por Rosario cuesta
> +210.9 km; agregarlo a la ruta directa, +82.3 km.
>
> **Las tres intenciones vecinas se separaron contrastando descripciones**, que
> es lo mismo que funciono con el trio meteorologico. Medido en vivo antes y
> despues, sobre las mismas seis frases:
>
> | Frase | Antes | Despues |
> |---|---|---|
> | "quiero pasar por Rosario" | repreguntaba el tipo | sobrevuelo |
> | "podriamos pasar por arriba de Rio Cuarto?" | `atmosfera_en_punto` | sobrevuelo |
> | "necesito bajar a cargar nafta en Villa Dolores" | `combustible_cercano` | escala |
>
> `atmosfera_en_punto` dice ahora que INFORMA y no toca la ruta;
> `combustible_cercano`, que dice DONDE hay combustible y que bajar a cargar es
> un cambio de ruta. Ninguna de las dos cambio de comportamiento: F1 = 1.000 y
> 0.941 respectivamente despues del cambio.

**Contexto de pantalla.** El endpoint recibe el estado del formulario (origen, destino,
aeronave, regimen, altitud, experiencia) y se inyecta en la instruccion de sistema, de
modo que "como esta el destino?" no obligue a repreguntar lo que el piloto ya cargo.
El frontend lo lee con `Alpine.$data()` **sin tocar el componente `app()`**.
⚠️ El formulario trabaja en **UTC** y el parametro `cuando` de las herramientas en
**hora local**: la conversion la hace `prompts._hora_salida_local()` en codigo, no el
modelo (copiarlo sin convertir corria la consulta tres horas).

**La barrera R2 se relaja cuando hay una serie horaria.** Con `mejor_hora_para_salir`
en el mismo turno, el texto menciona varios veredictos legitimamente ("ahora CAUTION,
desde las 15 GO"). Ahi se exige que el veredicto del motor ESTE presente, no que sea el
unico; exigir exclusividad producia una correccion falsa.

**Hallazgos operativos** (medidos contra la API real, documentados en `client.py`):
`gemini-2.5-flash` esta retirado (404 para cuentas nuevas) · el nivel gratuito tiene
503 intermitentes con tasa que va de 0/5 a 6/6 **segun el modelo** (de ahi la cadena
de reserva) · `gemini-flash-lite-latest` rechaza `thinkingBudget=0` con 400 ·
hay que reenviar el `content` del modelo TAL CUAL porque lleva `thoughtSignature`.

**Resultados medidos** sobre los 98 casos de `copilot/eval_set.py`, 17/09/2026,
**modelo unico `gemini-3.5-flash-lite`** (el titular de la cadena,
`gemini-flash-lite-latest`, resuelve a ese mismo modelo):

| Metrica | Valor |
|---|---|
| Exactitud de clasificacion de intencion | **94/98 = 95.9 %** |
| F1 macro-promedio (9 intenciones) | **0.959** |
| Exactitud de resolucion de aerodromo | **81/82 = 98.8 %** |
| **Tasa de invencion sobre datos ausentes** | **0 %** (0 de 9 con deteccion objetiva) |
| Reconocimiento explicito de la ausencia | **10/11** (en el restante repregunto) |
| Veredictos que hubo que forzar | **0 de 11** |
| Codigos fabricados que hubo que corregir | **0** |
| Latencia (media / mediana / p95) | 5.75 s / 3.62 s / 19.68 s |

> **Un solo modelo por corrida, y las caidas se reintentan.** La cadena de reserva
> es del PRODUCTO: para medir, `--modelo` fija uno solo, porque un F1 sobre
> respuestas de dos modelos distintos no describe a ninguno. Ante un 503, el
> evaluador reintenta el MISMO caso (4 intentos, 30 s entre ellos) en vez de dejar
> que conteste el siguiente modelo; si se agotan, corta la corrida y se retoma sin
> `--forzar`. Si los resultados mezclan modelos, el reporte se niega a consolidar.
> Historico: una corrida con 8 caidas degradadas daba 87.8 % y otra con 0, sobre el
> mismo codigo, 95.9 %; esa diferencia no era del clasificador.

**Comparacion con la version anterior**, que tenia 7 herramientas y 89 casos:
intencion 96.6 % -> **95.9 %** y F1 macro 0.969 -> **0.959**, con una intencion
mas y nueve casos mas. Practicamente plano: agregar la modificacion de ruta no
degrado la clasificacion de las otras ocho. **La latencia** con modelo unico es de
5.75 s de media; la corrida anterior, con cadena de reserva, daba 14.60 s porque
sumaba los intentos fallidos contra el primer modelo. `proponer_cambio_de_ruta`
sigue siendo la consulta mas cara: calcula DOS rutas completas —la actual y la
propuesta— y, si es escala, corre ademas una evaluacion meteorologica. Es el
precio de que la propuesta traiga su costo y su
veredicto en vez de ser un boton a ciegas.

**Contrastar las descripciones es lo que separa las intenciones vecinas**, y se
aplico dos veces con el mismo efecto y sin cambiar de modelo: primero al trio
meteorologico (que `evaluar_meteo` es de SUPERFICIE contra una pista y
`atmosfera_en_punto` es del AIRE sobre un punto de paso) y despues al agregar la
modificacion de ruta, que competia con esas dos por las mismas frases. Las tres
meteorologicas quedan en F1 0.933 (`mejor_hora_para_salir`), 0.952
(`evaluar_meteo`) y 1.000 (`atmosfera_en_punto`); `proponer_cambio_de_ruta`
tambien da 1.000, o sea que la cuarta vecina no se come a las otras.

Por region: CUYO 12/12, LITORAL 14/14, PAMPA 29/30, PATAGONIA 18/19, NOA 11/12 y
10/11 en los casos sin region asociada — el comportamiento no depende de la
region (regla de alcance).

**Los 4 desaciertos, analizados uno por uno** (la matriz de confusion NO se
retoca, se informa el analisis por separado):
1. *"hay nafta en Andalgala?"* -> uso `combustible_cercano` en vez de
   `servicios_aerodromo`. Confusion real entre dos intenciones vecinas; la
   respuesta igual fue correcta y declaro la ausencia del dato. Es el mismo
   desacierto de la version anterior.
2. *"que pista tiene el aeropuerto de Wakanda?"* -> uso `buscar_aerodromo`,
   razonable para un nombre desconocido, y reporto correctamente que no existe.
   Tambien venia de la version anterior.
3. *"como viene el dia en Piedra del Aguila?"* -> uso `evaluar_meteo` en vez de
   `mejor_hora_para_salir`. Las dos hablan del veredicto del mismo aerodromo;
   la respuesta fue correcta para un momento en vez de para el dia.
4. *"a quien llamo en Cordoba?"* -> **pidio aclaracion** en vez de invocar
   `contacto_aerodromo`, sin herramienta: "Cordoba" nombra mas de un aerodromo.
   Repreguntar ante una ambiguedad es la conducta correcta, pero la metrica la
   cuenta como desacierto porque no coincide con la intencion esperada. El
   reporte los separa (`[PIDIO ACLARACION]` contra `[CONFUSION]`) y NO retoca
   el conteo.

Ninguno de los cuatro produjo una respuesta incorrecta o peligrosa. El
desacierto de *"puedo pasar por arriba de Tucuman sin aterrizar?"*, que la
version anterior tenia, ya no aparece: `proponer_cambio_de_ruta` da F1 1.000.

**La unica falla de resolucion** fue *"a quien llamo en Cordoba?"*: el conjunto
espera SACO y el asistente **repregunto cual de los aerodromos de Cordoba**, sin
invocar herramienta. Es conducta segura ante un nombre ambiguo, pero la metrica la
cuenta como falla de resolucion y como desacierto de intencion, y por eso tambien
el reconocimiento de ausencia queda en 10/11. El numero reportado es conservador.

**Fuera de alcance, explicitamente**: uso en vuelo, GPS y cualquier fuente de datos
nueva. La modificacion de ruta por lenguaje natural era la fase 2 y ya esta hecha,
acotada a los puntos de paso.

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
- Ventana = [ETA_dep, ETA_dep + duration]. En la web cada extremo se evalua con
  su propia ventana de 1 h (`VENTANA_EXTREMO_H`): el origen alrededor del
  despegue, el destino alrededor del aterrizaje.
- Extraer periodos TAF que intersectan la ventana.
- Periodo mas restrictivo = worst-case.
- TEMPO/PROB en ventana → elevar componente de riesgo TAF.

El `worst_case` **ya no es solo un componente de score**: es la fuente de las
condiciones que se puntuan cuando la observacion vencio (ver *El TAF decide el
momento evaluado*). El componente `r_taf` (peso 0.022) sigue existiendo y mide
otra cosa: la INESTABILIDAD del pronostico —que haya TEMPO o PROB en la
ventana—, no el nivel de deterioro, que ahora entra por `r_vis`, `r_ceil`,
`r_xwind` y `r_wx` como cualquier otra condicion.

---

## Estado actual

Todas las capas estan completas y operativas: Data → Parsing → Feature → Risk →
Decision → Route → Output → Web, mas la capa de lenguaje natural (Copiloto).
El sistema corre como web app.

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

**Credencial del copiloto.** El asistente necesita `GEMINI_API_KEY` (Google AI Studio,
nivel gratuito). Va en `.env` en la raiz — **que esta en `.gitignore`** — y en Render
como variable de entorno del servicio, **nunca en `render.yaml` ni en codigo**:

```
GEMINI_API_KEY=...
```

Sin la variable la app arranca igual: `/api/copilot/status` responde
`available: false`, el panel del frontend no se muestra y **el resto del sistema
funciona normalmente**. El copiloto es accesorio y su caida no arrastra a nadie.

Suite de regresion (415 tests, sin red):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

`tests/test_regression_scenarios.py` es el test critico: fija el comportamiento del
veredicto sobre la bateria de referencia (0 sub-avisos, concordancia >= 90%). Si un
cambio en pesos, umbrales o barrera altera lo que el sistema decide, ahi salta.

`tests/test_web.py` cubre lo que agrega la capa web por encima del motor:
validacion de los puntos de paso, derivacion de la hora de llegada a cada escala
y marcado de los puntos pedidos. **Sin red**: los waypoints se generan con
`mock=True` sobre tramos cortos. Verificado bloqueando `socket.connect` para toda
la suite, que es la unica forma de probar que un test no sale a internet.

`tests/test_copilot.py` fija el contrato de seguridad del asistente: que un veredicto
parafraseado o invertido se reemplace por la plantilla determinista, que un codigo
fabricado se detecte sin marcar los legitimos, y que un dato ausente nunca se devuelva
como vacio. Corre **sin red**: el modelo se inyecta simulado (`ScriptedClient`).

Test standalone de un modulo (se conservan, son parte de la convencion del proyecto):
`.\.venv\Scripts\python.exe risk\calibration.py`

Evaluacion cuantitativa del copiloto (**si** sale a la red, ~7 min por el limite de
15 solicitudes/min del nivel gratuito; cachea en `copilot/eval_results.json`):
```powershell
.\.venv\Scripts\python.exe -m copilot.evaluate                # corre y cachea (modelo titular)
.\.venv\Scripts\python.exe -m copilot.evaluate --solo-reporte # solo reporta el cache
.\.venv\Scripts\python.exe -m copilot.evaluate --forzar --modelo gemini-3.5-flash-lite
```
Corre con un modelo unico y reintenta el MISMO caso ante un 503 (`--reintentos`,
4 por defecto); si se agotan, corta y se retoma con el mismo comando SIN
`--forzar`. `--cadena` usa la cadena de reserva del producto, pero entonces la
metrica resultante **no describe a un solo modelo** y no se reporta.

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
    weather_source   : str             # "metar" | "metar+taf" | "nwp"
    conditions_source: str             # procedencia en texto, para la pantalla
    raw_taf          : Optional[str]   # TAF crudo usado, para que se pueda verificar
    obs_time         : int             # Unix UTC del momento evaluado
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
- No usar frameworks de ML; el motor de decision es reglas + funciones de riesgo.
- **No dejar que el LLM decida, calcule ni afirme nada que no venga de una herramienta.**
  El copiloto es una interfaz de lenguaje sobre el motor, no una segunda opinion. Si
  se agrega una herramienta nueva, tiene que devolver la ausencia de dato de forma
  explicita (`{"publicado": false, ...}`), nunca `""` ni una lista vacia a secas.
- No agregar el SDK de Google: el cliente va por REST plano para no romper la
  propiedad de 4 dependencias y para poder cambiar de proveedor tocando un archivo.
- No implementar Iowa State Mesonet (archivo historico, fuera de v1.0).
- No implementar la excepcion OACI para aeronaves <= 140 kt (criterio conservador).
- No usar FAA como referencia normativa (usar ANAC/OACI).
