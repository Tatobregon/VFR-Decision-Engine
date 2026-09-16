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
- **La altitud VFR la elige el piloto**: se ingresa a mano y el sistema solo la acota al
  techo de servicio del avión. Si se deja vacía, la deriva por la regla de los
  semicírculos. En IFR no se elige: la fija la MEA del tramo.
- **Evalúa la meteorología en ruta** en checkpoints intermedios, a la altitud de crucero
  real y con el viento del nivel de presión correspondiente.
- **Sugiere alternativa**: el aeródromo más cercano al destino que además esté
  meteorológicamente apto.
- **Perfil vertical**: terreno SRTM contra la altitud de crucero, con aviso cuando el
  relieve supera lo que un VFR puede librar.
- **Responde en lenguaje natural**: un asistente que consulta el mismo motor y el mismo
  registro, sin decidir ni inventar nada por su cuenta (ver [El copiloto](#el-copiloto)).

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
| `copilot/` | Interfaz de lenguaje natural sobre todo lo anterior |

## El copiloto

Asistente de planificación **en tierra** al que se le pregunta en lenguaje natural:

> *"¿a quién llamo en Cruz Alta?"* · *"¿dónde cargo combustible cerca de La Cumbre?"* ·
> *"¿puedo volar mañana a las 9?"* · *"¿a qué hora me conviene salir?"* ·
> *"¿cómo está el aire sobre Junín a 7500 pies, por si me desvío por ahí?"*

Lee lo que el piloto ya cargó en la pantalla, así que *"¿cómo está el destino?"* no lo
obliga a repetir datos que están a la vista.

Es una **arquitectura neurosimbólica**, y el reparto de tareas es estricto:

> El modelo de lenguaje no sabe nada. No decide, no calcula y no aporta conocimiento
> propio. Interpreta la pregunta, elige qué herramienta determinista llamar, y redacta
> con lo que esa herramienta devolvió. Si el dato no está, lo dice.

```
piloto ─► [LLM: entender] ─► [motor determinista: buscar/calcular] ─► [LLM: redactar] ─► piloto
```

No agrega ninguna fuente de datos. Hace consultable lo que el sistema ya relevaba y
nadie podía usar: nadie navega 561 fichas para encontrar un teléfono.

Dos reglas no se confían al *prompt*, se **verifican en código** después de generar:

- **El veredicto se transcribe, no se parafrasea.** Si el texto no contiene el veredicto
  que devolvió el motor, o menciona otro, se descarta y se emite una plantilla
  determinista. La integridad es del 100 % por construcción.
- **No se fabrican códigos de aeródromo.** Un código con forma OACI que no salió de las
  herramientas ni existe en el registro se detecta y se corrige.

Y una distinción que el asistente respeta con un test que lee su propio código:

> **El veredicto es de aeródromo. El informe en altura no lleva veredicto.**
> GO / CAUTION / NO GO mide despegue y aterrizaje contra una pista. Preguntar cómo está
> el aire a 7500 ft sobre un punto de paso es otra cosa, y responderlo con la misma
> etiqueta la haría significar dos cosas distintas.

Cuando informa el aire en altura, **siempre dice de dónde salió esa altitud**: elegida
por el piloto, o la MEA real de la aerovía más cercana. Y si no la sabe, la pregunta en
vez de suponer una — el aire a 3000 y a 12000 pies sobre el mismo punto no se parecen.

Y la regla más importante, porque la cobertura del registro es **inversa a la
intuición** — los aeródromos grandes controlados tienen los campos vacíos porque se
publican en el AIP, los rurales chicos los tienen completos:

> **Que un dato no esté publicado no significa que no exista.** El asistente dice
> *"el registro no publica el combustible de ese aeródromo"*, nunca *"no tiene
> combustible"*. La diferencia entre esas dos frases es un piloto que se queda sin
> nafta en el aire.

El asistente es **accesorio**: si no hay credencial o el proveedor no responde, el panel
no aparece y el resto del sistema funciona igual.

### Cómo se lo evaluó

Sobre un conjunto de **89 preguntas etiquetadas**, con 8 intenciones y aeródromos de las
cinco regiones del país:

| | |
|---|---|
| Clasificación de intención | **96,6 %** (F1 macro 0,969) |
| Resolución del aeródromo correcto | **98,6 %** |
| **Invención sobre datos ausentes** | **0 %** |
| Latencia (mediana / p95) | 3,6 s / 12,2 s |

Las tres intenciones meteorológicas —veredicto de aeródromo, aire en altura y mejor hora
para salir— dan **F1 = 1,000**: son las más confundibles entre sí y se separan sin error.

La métrica que importa es la tercera. Once de las preguntas piden a propósito un dato
que **no existe** en el registro, y la detección es objetiva: si la respuesta a un
aeródromo sin teléfono publicado contiene algo con forma de teléfono, lo fabricó.
Ninguna lo hizo.

```bash
.venv/Scripts/python -m copilot.evaluate     # reproduce la evaluación
```

## Cómo decide

**1. Bloqueos absolutos** → NO GO inmediato, sin calcular score: tormenta (TS/TSRA/TSGR),
granizo, engelamiento (FZRA/FZDZ), ceniza volcánica, tornado, visibilidad < 3 km o
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

El cruzado y la ráfaga **no usan la misma escala**, y la razón está en qué significa cada
límite. El cruzado máximo es un valor *demostrado en certificación*: superarlo es salir de
lo que el fabricante probó. La ráfaga máxima es una *referencia de operación normal*, y
además la ráfaga mide variabilidad, no intensidad sostenida. Por eso:

| | CAUTION | NO GO |
|---|---|---|
| Viento cruzado | ≥ 50 % del máximo demostrado | ≥ el máximo demostrado |
| Ráfaga (delta) | ≥ 85 % de la referencia | ≥ 1,5 × la referencia |

**4. Umbrales calibrados** por anclaje normativo sobre 38 escenarios de referencia:

```
R < 0.22          →  GO
0.22 ≤ R < 0.59   →  CAUTION
R ≥ 0.59          →  NO GO
```

Concordancia con la norma: **97 %** (37/38), con **0 sub-avisos** (el sistema nunca avisa
menos que la regulación). Reproducible con `risk/calibration.py` y `risk/sensitivity.py`.

> **Un límite de esa cifra, declarado.** La etiqueta de referencia es independiente del
> motor para visibilidad, techo, fenómenos y tendencia — usa los cortes normativos ANAC,
> que no coinciden con las rampas del score. **No lo es para viento cruzado ni ráfagas**:
> ahí la referencia aplica las mismas fracciones que la barrera, así que en esos factores
> la coincidencia es por construcción y no cuenta como validación externa. No hay norma
> ANAC ni OACI que fije un factor de ráfaga admisible para aviación general, de modo que
> no existe árbitro contra el cual contrastarlo. Está documentado en el encabezado de
> `risk/scenarios.py`.

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
