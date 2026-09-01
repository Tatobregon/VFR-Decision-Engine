# VFR GO / NO GO — Motor de decisión meteorológica

Sistema de apoyo a la decisión para vuelos **VFR de aviación general en Argentina**.
Dado un origen, un destino, una aeronave y una hora de salida, evalúa las condiciones
meteorológicas reales y pronosticadas y devuelve un veredicto **GO / CAUTION / NO GO**
con briefing, ruta, perfil vertical del terreno y plan de vuelo OACI exportable.

🔗 **En línea:** https://vfr-decision-engine.onrender.com

> ⚠️ **Herramienta de apoyo, no reemplaza al briefing oficial.** La decisión final y la
> responsabilidad del vuelo son siempre del piloto al mando. Los datos provienen de
> fuentes públicas y pueden estar demorados o no disponibles.

---

## Qué hace

- **Evalúa origen y destino** con METAR + TAF cuando el aeródromo tiene estación, y con
  pronóstico numérico (NWP) cuando no — que es el caso de la mayoría del país.
- **Calcula un score de riesgo** `R ∈ [0,1]` combinando visibilidad, techo, viento
  cruzado, ráfagas, fenómenos presentes, riesgo de niebla y tendencia pronosticada.
- **Bloquea condiciones inaceptables** sin promediarlas: tormenta, granizo, engelamiento,
  visibilidad o techo por debajo del mínimo absoluto, vuelo nocturno en VFR, o NOTAM de
  cierre del aeródromo.
- **Calcula la ruta**: directa en VFR (con corredores visuales publicados al cruzar las
  TMA de Buenos Aires o Córdoba) o por aerovías inferiores del AIP en IFR, respetando el
  MEA que la aeronave puede mantener.
- **Evalúa la meteorología en ruta** en checkpoints intermedios, a la altitud de crucero
  real y con el viento del nivel de presión correspondiente.
- **Sugiere alternativa**: el aeródromo más cercano al destino que además esté
  meteorológicamente apto.
- **Perfil vertical**: terreno SRTM contra la altitud de crucero, con aviso cuando el
  relieve supera lo que un VFR puede librar.

## Alcance

| | |
|---|---|
| **Aeródromos** | 561 de todo el país (registro oficial ANAC/MADHEL) |
| **Aeronaves** | Pipistrel Alpha Trainer, Cessna 152, Piper PA-28, Cessna 172, Diamond DA40 |
| **Normativa** | ANAC Argentina / OACI (no FAA) |
| **Regímenes** | VFR (principal) e IFR |

## Cómo correr el proyecto

Requiere **Python 3.12**.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
.venv/Scripts/python -m uvicorn web.app:app --reload --port 8000
```

Y abrir http://127.0.0.1:8000

Cada módulo trae su propio test standalone:

```bash
.venv/Scripts/python risk/calibration.py     # calibración de umbrales
.venv/Scripts/python risk/sensitivity.py     # análisis de sensibilidad
.venv/Scripts/python decision/engine.py      # pipeline completo (mock)
```

Y la suite de regresión:

```bash
.venv/Scripts/python -m pytest -q
```

## Arquitectura

```
web/app.py (FastAPI)  ──►  decision/engine.py  ──►  risk/  ──►  features/  ──►  parsers/  ──►  data/
       │                                                                            ▲
       └────────────────►  route/  (A*, aerovías, corredores VFR, performance) ──────┘
```

| Capa | Responsabilidad |
|---|---|
| `data/` | Traer datos crudos (METAR/TAF, NOTAM, NWP, terreno) y los registros locales |
| `parsers/` | Normalizar todo a `ParsedWeather`, el contrato único del sistema |
| `features/` | Convertir meteorología en factores de riesgo (crosswind, niebla, luz diurna…) |
| `risk/` | Pesos, score, barrera no-compensatoria y veredicto |
| `decision/` | Orquestar el pipeline completo |
| `route/` | Ruta, aerovías, corredores visuales, combustible |
| `output/` | Briefing en español y plan de vuelo OACI |

## Cómo decide

**1. Bloqueos absolutos** → NO GO inmediato, sin calcular score: tormenta (TS/TSRA/TSGR),
granizo, engelamiento (FZRA/FZDZ), ceniza volcánica, tornado, visibilidad < 1.5 km o
techo < 500 ft. También vuelo nocturno en VFR y NOTAM de cierre total.

**2. Score compensatorio** `R = Σ wᵢ·rᵢ`, con pesos derivados por **AHP**
(*Analytic Hierarchy Process*, CR = 0.069), derivados de accidentologia:

| Factor | Peso | | Factor | Peso |
|---|---|---|---|---|
| Visibilidad | 0.357 | | Ráfagas | 0.050 |
| Techo | 0.357 | | Fenómenos | 0.044 |
| Viento cruzado | 0.099 | | Tendencia | 0.022 |
| Niebla (spread) | 0.071 | | | |

**3. Barrera no-compensatoria.** Un promedio ponderado deja que un factor bueno tape a uno
malo: un viento cruzado por encima del límite del avión aportaría apenas 0.099 y saldría
GO. Por eso cada factor crítico impone un **piso** de veredicto, relativo a los límites de
*cada* aeronave, y la decisión final es la peor entre el umbral y ese piso.

**4. Umbrales calibrados** por anclaje normativo sobre 36 escenarios de referencia:

```
R < 0.22          →  GO
0.22 ≤ R < 0.59   →  CAUTION
R ≥ 0.59          →  NO GO
```

Concordancia con la norma: **97 %**, con **0 sub-avisos** (el sistema nunca avisa menos
que la regulación). Reproducible con `risk/calibration.py` y `risk/sensitivity.py`.

## Fuentes de datos

| Fuente | Uso |
|---|---|
| [aviationweather.gov](https://aviationweather.gov) | METAR y TAF |
| [AIS de ANAC](https://ais.anac.gob.ar/notam) | NOTAM oficiales argentinos |
| [Open-Meteo](https://open-meteo.com) | Pronóstico NWP, superficie y niveles de presión |
| [Open-Topo-Data](https://www.opentopodata.org) | Terreno SRTM 30 m |
| ANAC/MADHEL, OurAirports, OpenAIP, AIP ENR 3.1 | Aeródromos, pistas, espacio aéreo, aerovías |

Ninguna requiere API key. Las respuestas se cachean en memoria con TTL acorde a la
frecuencia de actualización de cada fuente (ver `data/cache.py`).

## Limitaciones conocidas

- La calibración de umbrales es de **validez de constructo** (reproduce la regulación
  vigente), no empírica: no existe un conjunto de casos reales etiquetados por pilotos.
- El pronóstico NWP es un modelo de grilla: en terreno complejo su precisión es menor que
  una observación directa. La base de nubes se estima con la regla de Espy a partir del
  spread T/Td, no se observa.
- La tendencia meteorológica de los aeródromos sin TAF se sintetiza de la propia serie
  NWP, que es determinista y no publica probabilidad de ocurrencia.
- La declinación magnética usa una aproximación lineal para Argentina (error 1-2°), no el
  modelo WMM oficial.

## Documentación

- `DOCUMENTACION_CHECKPOINT_2.md` — estado del sistema verificado por ejecución
- `CLAUDE.md` — convenciones, contratos y reglas de diseño del proyecto
- `limpieza_1.md` — registro de la limpieza de código muerto

---

Proyecto de tesis de grado — Lautaro Tomás Obregón
