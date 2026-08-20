# VFR GO/NO GO — Checkpoint 2

**Fecha:** 20 de agosto de 2026
**Versión del sistema:** v1.3.0 (`web/app.py`)
**Estado:** operativo — verificado por ejecución en esta fecha
**Reemplaza a:** `DOCUMENTACION_CHECKPOINT.md` (mayo 2026), que quedó obsoleto y se conserva solo como registro histórico.

> **Por qué existe este documento.** El checkpoint anterior describe una arquitectura
> que ya no existe (CLI `main.py`, GUI Tkinter `gui.py`, algoritmo genético
> `route/genetic.py`, `output/formatter.py`, `features/flight_category.py`, base de
> 710 aeródromos de OurAirports, pesos 0.25/0.25/0.20 y umbral 0.25). Todo eso fue
> eliminado o reemplazado. Este documento describe el sistema **tal como está hoy**, y
> cada cifra que aparece acá fue **verificada ejecutando el código**, no leída de la
> documentación previa.

---

## 1. Alcance del sistema

| Parámetro | Valor |
|---|---|
| **Cobertura geográfica** | **Todo el territorio argentino** — 561 aeródromos cargados del registro oficial ANAC/MADHEL |
| **Aeronaves** | **Las 5 del registro**, seleccionables por el usuario: Pipistrel Alpha Trainer (LSA), Cessna 152, Piper PA-28 Cherokee, Cessna 172 Skyhawk, Diamond DA40 |
| **Normativa** | ANAC Argentina / OACI (**no** FAA) |
| **Interfaz** | Web app (FastAPI + frontend HTML/Alpine/Leaflet) |
| **Régimen** | VFR (principal) e IFR (switch, habilita ruteo por aerovías) |

> ⚠️ **Regla de alcance (crítica).** El sistema es **nacional y multi-aeronave**. Córdoba,
> SACC y el Pipistrel Alpha Trainer fueron el alcance de la v1.0 y **hoy son solo casos
> de prueba, no el dominio del sistema**. Ninguna solución, test, umbral ni heurística
> puede quedar atada a un aeródromo o a una aeronave particular. Cuando un valor dependa
> de la aeronave, debe derivarse del `AircraftProfile`; cuando dependa del lugar, de los
> datos del aeródromo o del terreno.

### Conteo real de aeródromos (verificado)

`data/madhel_cache.json` trae 712 registros: **563 aeródromos (`AD`)** y 149 helipuertos
(`HEL`, excluidos porque el sistema es de ala fija). De esos 563, **561 quedan cargados**
en `AIRPORTS` (2 se descartan por falta de identificador local o de coordenadas válidas).

---

## 2. Arquitectura

Entry point único: **`web/app.py`** (FastAPI). No hay CLI ni GUI.

```
                    ┌──────────────────────────────────┐
                    │  web/static/index.html           │  Alpine.js + Leaflet
                    │  (frontend, 1 archivo)           │  (CDN: Tailwind/Alpine/Leaflet)
                    └───────────────┬──────────────────┘
                                    │ REST/JSON
                    ┌───────────────▼──────────────────┐
                    │  web/app.py — FastAPI v1.3.0     │
                    │  /api/evaluate /timeline /profile│
                    │  /airport/{code} /flightplan ... │
                    └───┬──────────────┬───────────────┘
                        │              │
        ┌───────────────▼───┐   ┌──────▼──────────────────────────┐
        │ decision/engine.py│   │ route/optimizer.py              │
        │ (pipeline meteo)  │   │ + graph, astar, airway_router,  │
        └───────┬───────────┘   │   vfr_corridors, performance    │
                │               └──────┬──────────────────────────┘
        ┌───────▼──────────────────────▼───────┐
        │ RISK: weights (AHP), soft_scoring,   │
        │ hard_blockers, aircraft_profiles,    │
        │ personal_minima                      │
        └───────┬──────────────────────────────┘
        ┌───────▼──────────────────────────────┐
        │ FEATURE: crosswind, fog_risk,        │
        │ taf_window, density_altitude,        │
        │ vfr_altitude, daylight, notam_impact,│
        │ orographic                           │
        └───────┬──────────────────────────────┘
        ┌───────▼──────────────────────────────┐
        │ PARSING: metar_parser [ParsedWeather]│
        │ openmeteo_adapter, taf_parser        │
        └───────┬──────────────────────────────┘
        ┌───────▼──────────────────────────────┐
        │ DATA: fetcher_aviationweather (METAR/│
        │ TAF/NOTAM), fetcher_openmeteo (NWP), │
        │ airports, airspace, airways,         │
        │ fir_zones, terrain                   │
        └──────────────────────────────────────┘

APÉNDICE METODOLÓGICO (no se importa en runtime):
  risk/ahp_weights.py · risk/scenarios.py · risk/calibration.py · risk/sensitivity.py
```

### Inventario de módulos (33 archivos `.py`)

| Módulo | Rol |
|---|---|
| **DATA** | |
| `data/airports.py` | Registro canónico. Carga MADHEL + completa pistas con OurAirports (`runways.csv`). Expone `AIRPORTS`, `AIRPORTS_PUBLIC` |
| `data/fetcher_aviationweather.py` | METAR + TAF (aviationweather.gov) y **NOTAMs del AIS de ANAC** (POST + parseo HTML) |
| `data/fetcher_openmeteo.py` | NWP punto a punto. Soporta viento en **nivel de presión** según altitud de crucero |
| `data/airspace.py` | Zonas CTR/TMA/R/P/D. Fuente: `ar-airspace.json` (OpenAIP); fallback Córdoba |
| `data/airways.py` | Grafo bidireccional de aerovías inferiores del AIP (ENR 3.1) |
| `data/fir_zones.py` | FIR de un punto → contacto ATC ("Córdoba Control", etc.) |
| `data/terrain.py` | Terreno SRTM 30 m (Open-Topo-Data) |
| `data/fetcher_madhel.py`, `data/fetcher_openaip.py` | **Herramientas offline** que regeneran los caches. No corren en runtime |
| **PARSING** | |
| `parsers/metar_parser.py` | Define **`ParsedWeather`** (contrato central) + categoría ANAC/OACI |
| `parsers/openmeteo_adapter.py` | `RawNWPHour` → `ParsedWeather` (WMO→wx, % nubes→capas) |
| `parsers/taf_parser.py` | `ParsedTaf` / `ParsedTafPeriod`, normaliza TEMPO/BECMG/PROB |
| **FEATURE** | |
| `features/crosswind.py` | Cruzado/proa, VRB worst-case, **`favored_runway()`** (pista automática) |
| `features/fog_risk.py` | `r_fog = max(r_spread, r_wx)` |
| `features/taf_window.py` | Herencia BASE→TEMPO/BECMG, worst-case en ventana, `r_taf`, `next_go_from` |
| `features/density_altitude.py` | DA/PA/desvío ISA + nivel NORMAL/ELEVATED/HIGH |
| `features/vfr_altitude.py` | Regla de los semicírculos + declinación magnética aproximada AR |
| `features/daylight.py` | Orto/ocaso (sin dependencias). Habilita el bloqueo por vuelo nocturno |
| `features/notam_impact.py` | Impacto operacional del NOTAM (solo cierre total bloquea) |
| `features/orographic.py` | Penalización NWP por terreno complejo — **hoy solo SACC (ver §7)** |
| **RISK** | |
| `risk/aircraft_profiles.py` | `AircraftProfile` (5 perfiles) + límites, performance, designador OACI |
| `risk/personal_minima.py` | Mínimos personales por experiencia (Alumno / PPL / Avanzado) |
| `risk/weights.py` | Pesos AHP, funciones r_i, umbrales de decisión |
| `risk/soft_scoring.py` | `R_total` + **barrera no-compensatoria** (`conjunctive_floor`) |
| `risk/hard_blockers.py` | NO GO inmediato por fenómeno/vis/techo |
| **DECISION / ROUTE / OUTPUT** | |
| `decision/engine.py` | Pipeline completo → `DecisionResult` |
| `route/optimizer.py` | Interfaz única. Modo `suggested` = A* sobre corredor geográfico |
| `route/graph.py`, `route/astar.py` | Grafo con `max_leg_km` + bbox; A* con heurística admisible |
| `route/airway_router.py` | Dijkstra sobre aerovías, filtrado por MEA de la aeronave |
| `route/vfr_corridors.py` | Corredores visuales publicados de TMA BA y Córdoba |
| `route/performance.py` | Haversine, rumbo, groundspeed con viento, combustible |
| `route/weather_sampler.py` | Muestreo meteo en ruta — **inactivo** (ver §7) |
| `output/briefing.py` | Briefing en español, 100 % determinista por reglas (sin IA) |
| `output/flight_plan.py` | Plan de vuelo OACI (casillas 7-19 + mensaje FPL) |

---

## 3. Recorrido de una consulta (`POST /api/evaluate`)

1. **Validación** — origen/destino existen, perfil de aeronave válido, hora `HH:MM` UTC (vacío = ahora + 1 h), mínimos personales según experiencia.
2. **Duración estimada** — haversine / `cruise_kt` × 1.3, para abrir la ventana meteo antes de conocer la ruta.
3. **Origen y destino en paralelo** (`ThreadPoolExecutor`) → `DecisionEngine.evaluate()`:
   - **Fuente**: con código ICAO → intenta METAR+TAF; si no reporta METAR, **cae a NWP**. Sin ICAO → NWP directo.
   - **Pista**: si el usuario no eligió, `favored_runway()` toma la cabecera más alineada al viento (menor cruzado; desempate por mayor viento de proa).
   - **Hard blockers** → NO GO inmediato sin score. En NWP se revisa **toda la ventana horaria**; en METAR también los períodos TAF activos.
   - **Soft score** `R = Σ wᵢ·rᵢ + δ_orográfico`, umbrales 0.22 / 0.50, y **barrera no-compensatoria**. Veredicto = **el peor** de los dos.
   - NWP toma la **peor hora** de la ventana; METAR usa la observación actual + `r_taf`.
4. **Ruta** — `optimize(mode="suggested")`: A* sobre un grafo restringido a un corredor geográfico (ancho `clamp(20 % de la distancia, 80, 250)` km, más banda latitudinal ±3°), probando dos topes de tramo (500 km y sin tope) y quedándose con el candidato de **mayor cobertura de aerovía**.
5. **Aerovías (solo IFR)** — primero un camino continuo origen→destino; fallback por tramo. Filtrado por MEA ≤ `cruise_alt_ft`.
6. **NOTAMs** de origen y destino (AIS de ANAC).
7. **Briefing** de texto.
8. **Bloqueos operacionales no meteorológicos** — vuelo nocturno (solo VFR; el despegue se evalúa a la hora de salida y el aterrizaje **a la ETA**), NOTAM de cierre total, y capa BKN/OVC por debajo del crucero.
9. **Waypoints** — corredores VFR si el tramo cruza TMA BA/Córdoba; waypoints de aerovía en IFR; checkpoints NWP cada 230 km si no hay ninguno. Se evalúan **en paralelo**, con viento al nivel de presión del crucero y crosswind anulado (en ruta el piloto corrige por deriva). ETA acumulado con groundspeed real.
10. **Desvíos** por waypoint (≤ 80 km, sin HTTP) y respuesta JSON.

Luego el frontend pide `/api/timeline` (serie horaria de 24 h para elegir hora de despegue) y `/api/profile` (terreno SRTM + MEA + altitud VFR, con detección de conflicto de terreno).

### Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/` | Frontend |
| POST | `/api/evaluate` | Evaluación completa (origen + destino + ruta + briefing) |
| POST | `/api/timeline` | Serie horaria de R en origen y destino |
| POST | `/api/profile` | Perfil vertical (terreno, MEA, altitud VFR) |
| POST | `/api/flightplan` | Plan de vuelo OACI (borrador; **no radica**) |
| GET | `/api/airports?q=` | Autocompletado |
| GET | `/api/airport/{code}` | Ficha completa: datos, pistas, meteo, NOTAMs, espacio aéreo, cercanos |
| GET | `/api/airports/map` | Capa de aeródromos para Leaflet |
| GET | `/api/airspace` | Zonas de espacio aéreo |
| GET | `/api/aircraft` | Perfiles de aeronave |
| GET | `/api/vfr_corridors` | Corredores visuales (GeoJSON) |

---

## 4. Modelo de riesgo — verificado por ejecución

### 4.1 Hard blockers → NO GO inmediato

Tokens `TS, TSRA, TSGR, GR, FC, VA, FZRA, FZDZ`; `visibility_km < 1.5`; `ceiling_ft < 500`.

### 4.2 Pesos por AHP (`risk/ahp_weights.py`)

Jerarquía de 3 grupos → 8 comparaciones de a pares en vez de 21. Salida reproducida hoy:

| Criterio | Peso | Grupo (peso) |
|---|---|---|
| Visibilidad | **0.279** | Referencia visual (0.6144) |
| Techo | **0.279** | Referencia visual |
| Niebla (spread) | **0.056** | Referencia visual |
| Viento cruzado | **0.179** | Viento (0.2684) |
| Ráfagas | **0.090** | Viento |
| Fenómenos wx | **0.078** | Fenómenos/tendencia (0.1172) |
| Tendencia TAF | **0.039** | Fenómenos/tendencia |

**CR global = 0.0634** (≤ 0.10 → consistente). Suma = 1.000000.

### 4.3 Barrera no-compensatoria (`conjunctive_floor`)

El promedio ponderado es compensatorio: un factor bueno tapa a uno malo. Un cruzado que
supera el límite del avión aportaría a lo sumo 0.179 y daría GO. La barrera impone un
**piso** por factor, relativo a **los límites de cada aeronave**:

```
cruzado ≥ límite del avión   → NO GO      cruzado ≥ 50 % del límite → CAUTION
Δ ráfaga ≥ gust_max          → NO GO      Δ ráfaga ≥ 50 % gust_max  → CAUTION
r_fog ≥ 0.9 (niebla probable)→ CAUTION    r_taf ≥ 0.6 (deterioro)   → CAUTION

decisión = peor(umbral(R), piso)
```

### 4.4 Umbrales calibrados (`risk/calibration.py`)

```
R < 0.22          → GO
0.22 ≤ R < 0.50   → CAUTION
R ≥ 0.50          → NO GO
```

Resultado reproducido hoy sobre los 38 escenarios de referencia:

- **Concordancia 37/38 (97 %)**, **0 sub-avisos**, 1 sobre-aviso, costo 1.
- El óptimo **no es un punto sino un rango** (`t_go ∈ [0.14, 0.22]`, `t_caution ∈ [0.46, 0.52]`) → robusto.
- Único desacuerdo: escenario G2 (nieve moderada, vis 6 km, techo 1500 ft) → el sistema dice CAUTION, la norma GO. Es un **sobre-aviso**, el error del lado seguro.
- Con `t_go = 0.24` o más aparecen **2 sub-avisos** y el costo salta de 1 a 9: por eso 0.22 y no 0.25.

**Naturaleza de la calibración:** validez de **constructo** (reproduce la regulación), **no** validez empírica. No hay casos reales etiquetados por pilotos. Declararlo así en el documento de tesis es parte del rigor, y la validación con juicio experto queda como trabajo futuro.

### 4.5 Sensibilidad (`risk/sensitivity.py`)

- **Monte Carlo** (5000 sorteos, los 7 pesos a la vez ±20 %): **estabilidad del veredicto 97.1 %**; peor sorteo 92.1 %; **33/38 escenarios (87 %) nunca cambian**.
- Los cambios se concentran en escenarios cuyo R cae **pegado a un umbral** (B1: R=0.223, B3: R=0.223), no en los showstoppers, que quedan clavados por la barrera.
- Umbrales ±0.05: entre 0 y 2 cambios sobre 38.

**Conclusión defendible:** el veredicto no depende de los pesos exactos del AHP.

---

## 5. Fuentes de datos

| Fuente | Uso | Auth |
|---|---|---|
| `aviationweather.gov/api/data` | METAR + TAF | no |
| `ais.anac.gob.ar/notam/pib` | NOTAMs oficiales argentinos (POST, parseo HTML) | no |
| `api.open-meteo.com` | NWP superficie + viento en nivel de presión | no |
| `api.opentopodata.org` (SRTM 30 m) | Perfil de terreno | no |
| MADHEL/ANAC → `data/madhel_cache.json` | 712 registros (563 AD + 149 HEL) | cache local |
| OurAirports → `data/runways.csv` | Pistas que MADHEL no trae | cache local |
| OpenAIP → `data/ar-airspace.json` | Espacio aéreo nacional | cache local |
| AIP ENR 3.1 → `data/aerovias_argentinas.json` | Aerovías inferiores | cache local |
| `FIRs_Argenina.geojson`, `corredores_vfr_TMA_{BA,CBA}.geojson` | FIRs y corredores visuales | cache local |

**Frontend por CDN:** Tailwind, Alpine.js 3.14.1, Leaflet 1.9.4, tiles CartoDB dark.

---

## 6. Entorno y despliegue

### Desarrollo local (instalado el 2026-08-20 en la PC nueva)

- **Python 3.12.10** (`winget install Python.Python.3.12`) — misma versión que Render.
- **Git 2.55.0** (`winget install Git.Git`).
- **Entorno virtual** en `.venv/` (ignorado por git) con `fastapi`, `uvicorn[standard]`, `requests`, `python-multipart`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn web.app:app --reload --port 8000
```

> Si `python` o `git` no se reconocen en una terminal recién abierta, refrescar el PATH:
> `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`

Tests standalone de un módulo: `.\.venv\Scripts\python.exe risk\calibration.py`

### Despliegue

`render.yaml`: Python 3.12, `pip install -r requirements.txt`,
`uvicorn web.app:app --host 0.0.0.0 --port $PORT`, plan **free**.
URL: https://vfr-decision-engine.onrender.com — **al 2026-08-20 responde HTTP 503**.
Repo: `github.com/Tatobregon/VFR-Decision-Engine`, rama `master`.

---

## 7. Estado verificado y deuda técnica

### 7.1 Verificación por ejecución (2026-08-20)

**19 módulos OK.** Fallan **3 bloques de test**, todos por *asserts desactualizados*, no por lógica:

| Módulo | Falla | Causa |
|---|---|---|
| `config.py` | espera 710 aeródromos, hay 561 | assert de la era OurAirports |
| `data/airports.py` | espera ≥ 700, hay 561 | ídem |
| `risk/aircraft_profiles.py` | espera `cruise_alt_ft = 5500` en Alpha, vale 6000 | assert viejo |
| `route/graph.py` | (a) algún nodo sin vecinos a ≤ 500 km; (b) aristas `SAOM`/`SAOE` | (a) posible aislamiento real; (b) test atado a Córdoba |

**End-to-end verificado:** `POST /api/evaluate` con SASA→SAAR (1004 km) en Cessna 172 responde **200 en 7.5 s**, decisión GO, METAR en ambos extremos, 3 NOTAMs en Salta, 6 waypoints.

### 7.2 ⚠️ Lo desplegado NO es lo que está en el disco

El working tree tiene **cambios sin commitear** que incluyen la contribución
metodológica central:

| Estado | Archivos |
|---|---|
| Modificados sin commitear | `CLAUDE.md`, `decision/engine.py`, `output/briefing.py`, `risk/soft_scoring.py` (+150 líneas), `risk/weights.py`, `web/app.py`, `web/static/index.html` |
| Nuevos sin trackear | `risk/scenarios.py`, `risk/calibration.py`, `risk/sensitivity.py` |

El último commit (`908ed89 "fixes"`), que es **lo que corre en Render**, tiene
`THRESHOLD_GO = 0.25`, **sin barrera no-compensatoria** y **sin los tres módulos
metodológicos**. Es decir: la versión desplegada decide distinto que la local y no
contiene la calibración ni el análisis de sensibilidad de la tesis.

### 7.3 Hallazgos abiertos

| # | Hallazgo | Ubicación |
|---|---|---|
| 1 | **Un fallo transitorio de aviationweather.gov tumba la evaluación con HTTP 500.** `_get()` lanza `ValueError` ante cualquier status ≠ 200/204/429 y nadie lo captura; el diseño (y el propio CLAUDE.md) dice devolver `None` y degradar a NWP. Observado en vivo con un 502 del proveedor | `data/fetcher_aviationweather.py:297` |
| 2 | **La alternativa al destino es arbitraria.** `_find_alternate` compara por `r_map`, que solo contiene origen y destino; todos los candidatos valen 0.0 y gana el primero del diccionario. Verificado: para Rosario propuso **General Acha (La Pampa), a 609 km**, con decisión "SIN DATOS" | `route/optimizer.py:269` |
| 3 | **Umbral 0.25 hardcodeado** en tres lugares pese a que el calibrado es 0.22 | `web/app.py:632`, `web/app.py:1271`, `route/optimizer.py:323` |
| 4 | **El briefing se arma antes de aplicar los bloqueos operacionales**: puede decir GO mientras la tarjeta dice NO GO por noche o NOTAM | `web/app.py:1547` vs `1560` |
| 5 | **Corrección orográfica en 1 de 561 aeródromos** (solo SACC), y base de nubes NWP fija en 2000 ft calibrada para Sierras Chicas aplicada a todo el país | `features/orographic.py:37`, `parsers/openmeteo_adapter.py:60` |
| 6 | **El componente TAF (w=0.039) vale 0 en casi todo el país**: solo existe en el camino METAR | `decision/engine.py` |
| 7 | `duration_hours` **declarado dos veces** en `EvaluateRequest`; el segundo anula el `Field(ge/le)` | `web/app.py:74,78` |
| 8 | **Código inalcanzable desde la web**: modos `shortest`/`fastest`/`safest`, `evaluate_intermediate`, todo `route/weather_sampler.py` (`weather_reroute` nunca se activa), y `r_fog()` en `weights.py` (se usa el de `features/fog_risk`) | varios |
| 9 | **Datos huérfanos** (~1.6 MB): `ar-airports.csv`, `datos_vfr_argentina.json`, `aerovias_LOWER_limpio.geojson`, `manual_runways.json` — ningún `.py` los referencia | `data/` |
| 10 | **Sin caché de requests**: cada evaluación dispara decenas de llamadas HTTP; pesa en el plan free de Render | `web/app.py` |
| 11 | **Sin README ni pytest**: la verificación son bloques `__main__` manuales | raíz |

---

## 8. Convenciones vigentes

- **Código en inglés**; comentarios, docstrings y mensajes al piloto **en español**.
- **Dataclasses tipadas** entre capas, nunca dicts sueltos. `Optional[X]`, jamás sentinels.
- Unidades: **kt, km, ft AGL, °C, hPa, Unix UTC**.
- Los fetchers devuelven `None` ante ausencia de datos; el engine maneja la ausencia.
- Cada módulo trae un bloque `__main__` con test standalone.
- Imports entre capas con patrón `try/except ImportError` para permitir ejecución como script.
- **Escalabilidad obligatoria**: nada atado a un aeródromo o a una aeronave particular.

---

*Checkpoint 2 — generado el 20 de agosto de 2026. Todas las cifras verificadas por ejecución del código en esta fecha.*
