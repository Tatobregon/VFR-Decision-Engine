# Fundamentación de la tesis y cambios

**Documento de trazabilidad.** Registra las observaciones del director sobre la primera
iteración de los capítulos 1 a 3, la respuesta metodológica a cada una y los cambios
efectivamente aplicados al sistema y al documento. No es parte del cuerpo de la tesis:
es la bitácora que permite reconstruir por qué el sistema es hoy como es.

Fecha de la devolución: **agosto de 2026**.

---

## 0. Índice

1. [Las tres observaciones del director](#1-las-tres-observaciones-del-director)
2. [Observación 1 — El salto lógico en el AHP · RESUELTA](#2-observación-1--el-salto-lógico-en-el-ahp--resuelta)
3. [Observación 2 — Confianza en el NWP sobre terreno escarpado · RESUELTA](#3-observación-2--confianza-en-el-nwp-sobre-terreno-escarpado--resuelta)
4. [Observación 3 — Coherencia del relevamiento (telemetría EFIS) · PENDIENTE](#4-observación-3--coherencia-del-relevamiento-telemetría-efis--pendiente)
5. [Registro completo de cambios aplicados](#5-registro-completo-de-cambios-aplicados)
6. [Pendientes abiertos](#6-pendientes-abiertos)

---

## 1. Las tres observaciones del director

El director aprobó la fundamentación epistemológica —el anclaje en la IA simbólica vía
Newell y Simon, el paralelismo con MYCIN y el uso de Rudin para defender la
interpretabilidad nativa— y planteó tres objeciones metodológicas:

| # | Observación | Estado |
|---|---|---|
| **1** | En el § 3.1.4 se afirma que los juicios del AHP "se fundamentaron en la accidentología", pero no se explicita **qué operación matemática** traduce un porcentaje estadístico a un valor discreto de la escala de Saaty. Sin esa operación, el AHP es un juicio subjetivo disfrazado de rigor matemático. | ✅ **Resuelta** |
| **2** | El NWP se integra para cubrir aeródromos sin estación, pero los modelos de grilla **suavizan la orografía**, lo que altera temperatura y punto de rocío y, por lo tanto, el LCL que fundamenta la estimación del techo de nubes. ¿Hay validación cruzada del error del NWP contra METAR en aeródromos serranos? | ✅ **Resuelta** (por diseño, no por medición — ver § 3.5) |
| **3** | En el § 2.1 se releva la telemetría EFIS de la flota, pero el motor no la consume. Todo dato relevado debe tributar a la solución: ¿se usa en una fase posterior o es información de relleno a depurar? | ⬜ Pendiente |

La observación 1 se atacó primero porque es la que compromete la cadena
§ 3.1.4 → § 4.2 → § 5.1 completa: si la derivación de los pesos no se sostiene, todo lo
que se construye sobre ellos queda en el aire.

---

## 2. Observación 1 — El salto lógico en el AHP · RESUELTA

### 2.1. El problema, con precisión

El código tenía esto:

```python
# RV vs V  = 3 : la perdida de referencia visual (VFR-into-IMC) es la causa
#                de mayor letalidad; pesa moderadamente mas que el viento.
```

El comentario explicaba la **dirección** de la comparación pero no el origen del
**número**. Y el § 3.1.4 del documento decía "los juicios se fundamentaron en la
accidentología expuesta en el § 1.1.3", una frase que promete un procedimiento que no
existía. Esa frase era la fisura: prometía más de lo que el resto del método entregaba.

La objeción era correcta y no admitía defensa. La salida no fue justificar mejor los
números viejos sino construir el procedimiento que faltaba.

### 2.2. El protocolo adoptado

Implementado en [`risk/ahp_weights.py`](../risk/ahp_weights.py), reproducible por
ejecución.

**Paso 1 — Índice de riesgo por criterio.** Se adopta el índice del **Manual de gestión
de la seguridad operacional de la OACI (Doc 9859)**: riesgo = probabilidad × severidad.
Aplicado a accidentología se simplifica:

```
I = (n_cat / N) × (n_fatales / n_cat) = n_fatales / N
```

es decir, la participación de la categoría en el total de accidentes. **No es una
construcción propia**: es la misma operacionalización que aplica la **Junta de Seguridad
en el Transporte** argentina en su *Anuario Estadístico* para ordenar por riesgo las
categorías de suceso de la taxonomía OACI/ADREP. Esto importa para la defensa: la
operación es la del organismo nacional de seguridad operacional, no un invento del autor.

**Paso 2 — La operación de traducción.** La escala de Saaty es una **escala de razón**:
`a_ij` debe aproximar `w_i / w_j`. Por lo tanto, disponiendo de un índice por criterio, el
cociente de índices no es una *interpretación* de la evidencia sino la **entrada correcta**
de la matriz:

```
a_ij = redondeo_Saaty( I_i / I_j )
```

donde el redondeo lleva el cociente al valor más próximo de {1,…,9} y sus recíprocos.

**Paso 3 — Aplicación numérica.**

| Grupo | Categoría de la fuente | Accidentes | Fatales | Severidad |
|---|---|---|---|---|
| Referencia visual | *Weather* (Nall, fig. 1.7.1) | 473 | **345** | 72,9 % |
| Viento | *Landing* (Nall, fig. 1.1.1) | 3410 | **51** | 1,5 % |

```
a(referencia visual, viento) = redondeo_Saaty(345 / 51) = redondeo_Saaty(6,76) = 7
```

El código tenía **3**. La formalización **cambió** la matriz — lo cual es la prueba de que
el protocolo no es una racionalización a posteriori de los números que ya estaban.

**Paso 4 — Declaración de procedencia.** No todas las comparaciones admiten anclaje
empírico. Presentarlas como si lo tuvieran sería tan objetable como no tener
procedimiento. Cada entrada declara su origen:

| Comparación | Valor | Procedencia | Fundamento |
|---|---|---|---|
| Ref. visual vs. Viento | 7 | **E** Evidencia | Cociente de índices (paso 3). |
| Viento vs. Fenóm./tendencia | 3 | **N** Norma / arquitectura | Los fenómenos peligrosos no llegan al puntaje: los extrae la capa categórica. Lo que resta es residuo no inhabilitante más tendencia. No hay categoría de accidentología para ese residuo. |
| Ref. visual vs. Fenóm./tend. | 9 | **D** Derivada | Por transitividad sería 7 × 3 = 21, fuera de rango; se trunca al techo de la escala. |
| Visibilidad vs. Techo | 1 | **N** Norma | La regulación los exige conjuntamente, sin jerarquía. |
| Visibilidad vs. Niebla | 5 | **J** Juicio | La visibilidad es magnitud medida; la niebla es estimación indirecta por spread T/Td. |
| Cruzado vs. Ráfaga | 2 | **J** Juicio | El cruzado tiene límite publicado por aeronave. La accidentología no separa ambas causas. |
| Fenómenos vs. Tendencia | 2 | **J** Juicio epistémico | Lo observado es más cierto que lo pronosticado. No compara peligrosidad sino confiabilidad del dato. |

**Paso 5 — La consistencia como medida de la truncación.** `CR = 0,069`, dentro del
límite de 0,10. Su interpretación es precisa: si las tres entradas del nivel superior
fueran mutuamente transitivas, `CR` sería nulo; el valor obtenido **mide exactamente la
distorsión introducida al truncar 21 al techo de 9**. El indicador no valida los juicios,
cuantifica su coherencia interna — que es lo único que el método promete.

**Paso 6 — Acotación del residuo subjetivo.** Las entradas marcadas **J** no desaparecen
por estar declaradas. Su efecto se acota empíricamente con el análisis de sensibilidad
(`risk/sensitivity.py`): si el veredicto es estable ante perturbaciones de los pesos, la
precisión de cada juicio individual deja de ser crítica.

### 2.3. El hallazgo que da vuelta la crítica

Al aplicar el protocolo apareció un resultado no previsto.

Si el índice se construye con **severidad sola** (72,9 % vs. 1,5 %), el cociente asciende
a **≈49**: casi seis veces el techo de la escala.

Que la evidencia exceda el rango representable **no es un defecto del dato**. El techo de
9 de la escala de Saaty no es una convención arbitraria: expresa el límite más allá del
cual dos criterios dejan de ser comparables en una misma razón y, por lo tanto, dejan de
ser legítimamente **compensables** entre sí.

La imposibilidad de representar el cociente es la señal formal de que **el viento cruzado
no debe gestionarse por su peso dentro de la suma ponderada** — y es exactamente lo que
hace la arquitectura: se trata en la **barrera no compensatoria**, que impone un piso de
veredicto cuando supera los límites de la aeronave. Su peso (0,099) carga solo la
contribución residual dentro de la banda admisible; la barrera carga el resto.

**La objeción del director termina respaldando cuantitativamente la arquitectura del
sistema**, que hasta ahora estaba justificada solo de manera conceptual.

Corolario práctico, fijado en un test de regresión: **no subir `W_XWIND` "porque parece
bajo"**. Es bajo a propósito.

### 2.4. El resultado principal: validación convergente

La sustitución de la ponderación cambió los siete pesos y obligó a recalibrar. Lo
decisivo es lo que pasó después:

| | Pesos por juicio experto | Pesos por evidencia |
|---|---|---|
| a(ref. visual, viento) | 3 | **7** |
| Grupos (RV / V / FT) | 0,614 / 0,268 / 0,117 | **0,785 / 0,149 / 0,066** |
| Visibilidad = Techo | 0,279 | **0,357** |
| Viento cruzado | 0,179 | **0,099** |
| Niebla (spread) | 0,056 | **0,071** |
| Ráfagas | 0,090 | **0,050** |
| Fenómenos wx | 0,078 | **0,044** |
| Tendencia TAF | 0,039 | **0,022** |
| CR | 0,063 | **0,069** |
| Umbrales óptimos | 0,22 / 0,50 | 0,22 / **0,59** |
| **Concordancia con la norma** | **35/36 (97 %)** | **35/36 (97 %)** |
| **Sub-avisos** | **0** | **0** |
| Único desacuerdo | G2 (nieve moderada) — sobre-aviso | **el mismo** |
| Estabilidad Monte Carlo | 97,0 % | **99,0 %** |
| Escenarios que nunca cambian | 35/36 | 35/36 |
| Cambios de veredicto OAT ±20 % | — | 4 de 504 (0,8 %) |

**Dos derivaciones independientes de los pesos —una por juicio experto, otra anclada en
accidentología— producen el mismo veredicto en los 36 escenarios de la batería.** El
umbral inferior ni siquiera se movió. La sensibilidad incluso mejoró.

Esto es **validación convergente**, y es un resultado más robusto que cualquiera de las
dos ponderaciones tomada por separado: demuestra que las conclusiones del sistema no
dependen de la ponderación exacta. Va reportado en el § 5.1.

### 2.5. Limitaciones declaradas

Tres precisiones que conviene consignar antes de que las plantee el tribunal.

**Origen de los datos.** La serie cuantitativa es estadounidense (AOPA / Nall Report)
mientras que el marco normativo del trabajo es argentino. Se recurrió a ella porque la
fuente nacional registra volúmenes demasiado reducidos para un cociente estable: **27
accidentes de aviación general en 2020, 4 de ellos fatales** (promedio 2015-2019: 32 y 5).
La fuente nacional se usa para **corroborar dirección y orden de magnitud**, y lo hace de
manera inequívoca:

- **UIMC** (vuelo no intencionado en IMC): severidad **100 %** — 2 sucesos, ambos
  fatales, 9 fallecidos. Baja probabilidad de ocurrencia.
- **LOC-I**: severidad 44 %, la mayor cantidad de fallecidos (26), elevada probabilidad.
- **RE / LOC-G / ARC** (excursión de pista, pérdida de control en tierra, contacto anormal
  con la pista) — donde se manifiesta el viento cruzado: *"elevada probabilidad, **baja
  severidad**"*.

Ambas fuentes coinciden en la dirección; solo una tiene masa estadística para producir el
número.

**Ventana temporal.** Se usa una serie de **diez años** y no un ejercicio aislado porque
el cociente es muy sensible al ruido interanual: en 2016 la letalidad meteorológica cayó
al 52 % frente a un promedio decenal del 73 %, lo que habría dado `a = 2` en lugar de 7.
La elección de la ventana es en sí misma un parámetro del procedimiento y se declara.

**Alcance del índice.** El índice mide el daño agregado que cada familia de peligros
produce en la población de accidentes, **no** la capacidad discriminante del criterio en
un vuelo concreto. Se adopta como operacionalización de la importancia relativa —es la que
emplea la normativa de gestión de seguridad operacional— pero la equivalencia entre ambas
magnitudes es un **supuesto del modelo**, no un hecho demostrado.

### 2.6. Fuentes utilizadas

| Fuente | Uso | Referencia |
|---|---|---|
| OACI, Doc 9859 (4.ª ed., 2018) | Define el índice riesgo = probabilidad × severidad | Manual de gestión de la seguridad operacional |
| JST, *Anuario Estadístico 2020* (Vol. 1, Aeronáutico) | Corrobora dirección y orden de magnitud con datos argentinos; aplica el mismo índice | Ministerio de Transporte de la Nación |
| AOPA Air Safety Institute, *28.º Joseph T. Nall Report* (2019) | Serie decenal 2007-2016, aviación general no comercial de ala fija | Figuras 1.1.1, 1.7.1 y 1.11 |
| Saaty (1980, 1990) | El método AHP, la escala fundamental y el criterio CR ≤ 0,10 | Ya citados en § 3.1.4 |

---

## 3. Observación 2 — Confianza en el NWP sobre terreno escarpado · RESUELTA

### 3.1. Por qué la objeción era correcta

Apunta al criterio de **mayor peso del modelo** (techo, 0,357). La cadena de propagación
del error es directa:

```
la grilla suaviza la orografia -> error en T y Td -> error en el spread
                               -> error en el LCL (regla de Espy)
                               -> error en la base de nubes -> error en r_ceil -> error en R
```

Open-Meteo aplica descenso de escala por elevación con un DEM de 90 m, lo que corrige la
temperatura por gradiente vertical, pero no cubre la microfísica del punto de rocío, la
canalización del viento en los valles ni la nubosidad de ladera. La objeción se sostenía.

### 3.2. La solución adoptada: muestreo en anillo

Se descartaron tres alternativas antes de elegir:

| Alternativa | Por qué no |
|---|---|
| Corregir el NWP con la estación METAR más cercana | El METAR más cercano a La Cumbre es Córdoba: 60 km, mil pies más abajo y en la llanura. Importaría el clima equivocado — es exactamente el error que la tesis argumenta que existe. |
| Bandera de confianza por rugosidad, sin tocar el puntaje | Honesta y barata, pero no cambia la decisión: el piloto recibe el mismo veredicto optimista con una advertencia al lado. |
| Comparar la base de nubes contra el terreno en MSL | Es lo físicamente más correcto y usa infraestructura ya existente (SRTM), pero excede el límite de alcance funcional que fijó el autor. Queda como trabajo futuro (§ 6.3). |

**La solución implementada** (`data/fetcher_openmeteo.get_forecast_ring`): en lugar de
consultar el pronóstico en la coordenada del aeródromo, se lo consulta también en seis
puntos equiespaciados sobre una circunferencia de 10 km, y el motor adopta la peor
condición del conjunto. Las siete consultas viajan en **una única petición HTTP**.

Radio de 10 km: del orden de la celda de un modelo global, y la porción de terreno que la
aeronave sobrevuela inmediatamente después del despegue. Seis puntos: cobertura angular
uniforme cada 60°, preferida a los cuatro rumbos cardinales porque estos se alinean con la
grilla del modelo y podrían caer sistemáticamente en las mismas celdas.

**La pieza que lo hace funcionar:** a los puntos del anillo no se les pasa el parámetro
`elevation`. Open-Meteo aplica entonces su propio descenso de escala con un DEM de 90 m y
cada punto recibe la altura real de su ubicación. Verificado en el aeródromo del
comitente: siendo el campo de 1138 m, **el anillo abarca de 732 a 1629 m** — casi
novecientos metros de desnivel que la consulta a un punto único promedia y pierde.

### 3.3. Tres precisiones que delimitan el mecanismo

**No es una penalización, es un muestreo.** No se suma un término al puntaje ni se castiga
al aeródromo por estar en la sierra: se amplía el muestreo y se toma la peor lectura. La
distinción importa porque una versión anterior del sistema tenía una penalización
orográfica fija, eliminada por estar atada a un único aeródromo.

**Se autorregula por el terreno.** En llanura los siete puntos caen sobre la misma masa de
aire y el resultado es idéntico al de la consulta simple. Verificado: Río Cuarto,
Resistencia y Rosario dan delta 0,000. La intensidad la determina el relieve, no el
identificador del campo — cumple la regla de alcance nacional.

**Del anillo se toma la masa de aire, no el viento.** Esta salvedad surgió de un error de
la primera implementación, detectado al probarla contra datos reales. En La Cumbre, un
punto del anillo situado en un cordón a 1572 m tenía viento del 056° con ráfaga de 12,1 kt
mientras el aeródromo tenía 331° con 2,4 kt — canalización orográfica de manual. Contra
la pista 320 eso daba un cruzado efectivo de 12,0 kt, exactamente el límite del Alpha
Trainer, y disparaba **NO GO en un día de calma en el aeródromo**.

El diagnóstico: el viento cruzado se define contra la **pista** y contra el máximo
demostrado del avión. Comparar la ráfaga de un cordón cuatrocientos metros por encima del
campo con ese límite es un error de categoría, porque la aeronave no aterrizará allí. El
motor conserva ahora el viento del aeródromo y toma del anillo únicamente visibilidad,
techo, humedad y fenómenos. Por la misma razón las reglas de rechazo categórico se evalúan
solo sobre el aeródromo: son normativas y la norma se refiere al aeródromo.

### 3.4. Evidencia de que el mecanismo capta lo que debe

Barrido sobre los aeródromos de sierra y montaña, rango de condiciones dentro del anillo
en 24 horas:

| Aeródromo | Elevaciones del anillo | Techo mín → máx | Spread mín | Vis mín |
|---|---|---|---|---|
| SAVE Esquel | 680–1616 m | **100 ft** → ilimitado | 0,1 °C | 0,2 km |
| SAZS Bariloche | 768–1082 m | **100 ft** → ilimitado | 0,0 °C | 0,6 km |
| SAWH Ushuaia | 0–471 m | **100 ft** → ilimitado | 0,1 °C | 2,2 km |
| SAWC El Calafate | 179–678 m | 8000 ft → ilimitado | 0,9 °C | 0,3 km |
| SACC La Cumbre | **732–1629 m** | ilimitado (día despejado) | 4,4 °C | 38,7 km |

En un mismo instante y dentro de 10 km, el modelo describe techos que van de 100 pies a
cielo despejado. Consultar un único punto es elegir uno de esos valores según dónde caiga
el centroide de la celda.

### 3.5. Lo que NO se hizo, y por qué — decisión declarada

Se había diseñado y construido la infraestructura para un **estudio de validación
empírica**: comparar el pronóstico archivado contra el METAR observado en estaciones
estratificadas por rugosidad de terreno, y medir en qué fracción de casos cambia el
veredicto. Está operativa en `validation/` (clientes de archivo probados, estratificación
de las 61 estaciones argentinas, disponibilidad real medida, muestra seleccionada).

**El autor decidió no ejecutarla**, por dos razones: la medición por sí sola no corrige el
problema, y el alcance funcional del proyecto está cerrado.

Corresponde declarar el costo de esa decisión, porque es real: **el muestreo en anillo es
una decisión de diseño fundamentada pero no medida**. Se sabe que capta variación
orográfica (§ 3.4) y que no actúa en llanura, pero no se cuantificó cuánto reduce el
desacuerdo de veredicto frente a la observación real. Si el director insiste en la
validación cruzada que pidió, la infraestructura está lista y el estudio es cuestión de
días, no de semanas.

Hallazgos técnicos que quedan registrados de esa etapa, por si se retoma:

- La **Previous Runs API** de Open-Meteo NO devuelve `cloud_cover_low/mid/high` ni
  `visibility` (vienen nulas). Para reproducir el techo hay que usar la **Historical
  Forecast API**, que sí entrega el juego completo.
- De las **61 estaciones argentinas** del IEM, **solo 25 reportan** efectivamente. La
  metadata no sirve para filtrar: La Rioja y Catamarca figuran activas desde 1939 sin
  fecha de cierre y devuelven cero observaciones. Hay que sondear disponibilidad real.
- La Cumbre tiene rugosidad de **234 m**. Las estaciones SERRANO activas van de 113 a
  180 m y Ushuaia está en 391 m, de modo que el aeródromo del comitente queda **entre
  medio**: la inferencia sería interpolación, no extrapolación.
- El estrato MONTAÑOSO tiene **una sola estación activa** (Ushuaia). El extremo superior
  de cualquier curva de error se apoyaría en un único punto.

---

## 4. Observación 3 — Coherencia del relevamiento (telemetría EFIS) · PENDIENTE

### 4.1. El principio es correcto, la solución no es borrar

Todo lo relevado en el § 2 debe tributar a la solución. Pero el error del texto actual no
es *incluir* la telemetría: es **presentarla sin decir qué hace o no hace por el proyecto**.

Un relevamiento organizacional inventaría lo que existe, **incluido lo que se decidió no
usar y por qué**. Eso no es relleno: es el diagnóstico. Lo que falta es cerrar el circuito.

### 4.2. Cómo hacer que tribute (tres elementos)

**Uso potencial real.** El `crosswind_max_kt = 12` del Alpha Trainer sale del *máximo
demostrado* del fabricante — un valor de **certificación**, no un límite operativo, y no
necesariamente lo que los pilotos de la escuela efectivamente manejan. Las trayectorias
GPS más el viento de aterrizajes reales darían la **distribución de componente cruzada
realmente volada**, que es exactamente lo que permitiría validar empíricamente ese umbral
y calibrar `risk/personal_minima.py` por nivel de experiencia.

**Por qué queda fuera de la v1.0.** Requiere acuerdo de cesión de datos con el comitente,
y los registros identifican vuelos de pilotos individuales — una consideración de
privacidad que el § 5.3 pide explícitamente abordar.

**Referencia cruzada al § 6.3** como trabajo futuro.

Con eso la telemetría deja de ser relleno y pasa a ser el **único insumo relevado con
potencial de validación empírica** en un trabajo que hoy es todo validez de constructo.

### 4.3. Problema de estructura detectado de paso

La numeración del capítulo 2 se separó de la estructura obligatoria del director:

| Estructura del director | Estado actual |
|---|---|
| 2.1 Análisis de la Organización | 2.1.1 ✅ y 2.1.2 (mercado) |
| **2.2 Relevamiento Tecnológico** | ❌ **no existe** — el hardware está dentro de 2.1.1 |
| 2.3 Cuadro de Diagnóstico y Propuesta | numerado **2.2.1** ❌ |

Se corrige moviendo el párrafo de flota / EFIS / simuladores a un **§ 2.2** propio —que
además pide conectividad y fuentes de datos disponibles, hoy ausentes— y renumerando el
cuadro a **§ 2.3**.

---

## 5. Registro completo de cambios aplicados

Todos los cambios están en el *working tree*. **El push se hace desde GitHub Desktop.**

### 5.1. Código

| Archivo | Cambio |
|---|---|
| `risk/ahp_weights.py` | **Reescrito el núcleo de la derivación.** Nuevo bloque `_EVIDENCE` con los datos de accidentología y sus fuentes; funciones `to_saaty()` y `evidence_ratio()`; matriz de nivel 1 **derivada**, no escrita a mano; cada entrada de las cuatro matrices documenta su procedencia (E/N/D/J); nuevo `_print_evidence_report()` que imprime la traducción evidencia → escala paso por paso; docstring con el protocolo completo. |
| `risk/weights.py` | Pesos nuevos: `W_VIS/W_CEIL 0.357`, `W_XWIND 0.099`, `W_FOG 0.071`, `W_GUST 0.050`, `W_WX 0.044`, `W_TAF 0.022`. `THRESHOLD_CAUTION` 0.50 → **0.59** (`THRESHOLD_GO` sin cambios en 0.22). Nota explicando por qué el peso del cruzado es bajo a propósito. Bloque "historia de la calibración" con el resultado de convergencia. Self-test actualizado. |
| `risk/soft_scoring.py` | Docstring de umbrales actualizado. El self-test del guardrail ahora compara contra `THRESHOLD_CAUTION` importado en vez del literal `0.50` — así no vuelve a quedar desincronizado. Import agregado en los dos bloques try/except. |
| `features/taf_window.py` | Docstrings que citaban `w=0.039` → `w=0.022`. |
| `data/fetcher_openmeteo.py` | **`get_forecast_ring()`** nueva: consulta el aerodromo mas 6 puntos a 10 km en UNA peticion, sin pasar `elevation` para que cada punto use su propia altura del DEM. `sample_ring()` genera la geometria. `_get()` ahora acepta respuestas de tipo lista (con varias coordenadas la API devuelve una por punto). Constantes `RING_RADIUS_KM` y `RING_POINTS`. |
| `decision/engine.py` | `_evaluate_nwp()` usa el anillo. El peor caso pasa de ser solo temporal a ser **temporal y espacial**. De los puntos del anillo se toma la masa de aire y se conserva el viento del aerodromo (`dataclasses.replace`). Los hard blockers siguen evaluandose solo en el aerodromo. |
| `tests/test_engine_data.py` | 3 tests nuevos del anillo: que toma la peor masa de aire, que ignora el viento de los puntos vecinos y que es inocuo cuando el entorno es homogeneo. Ademas se corrigio el parcheo de `test_sin_datos_el_veredicto_es_conservador`, que apuntaba al metodo viejo. |
| `validation/` (nuevo) | Paquete de estudios metodologicos offline. `sources.py` (clientes de archivo IEM y Open-Meteo), `terrain_strata.py` (estratificacion de las 61 estaciones por rugosidad SRTM). No corre en runtime. Construido para el estudio de validacion que finalmente no se ejecuto (ver 3.5); queda disponible. |
| `tests/test_risk.py` | `test_jerarquia_de_pesos_ahp` reescrito (el orden cambió: la niebla ahora supera a ráfagas y fenómenos). `test_umbrales_calibrados` → 0.59. `test_apply_decision_threshold` reparametrizado. **Test nuevo**: `test_el_cruzado_no_alcanza_el_umbral_de_caution_por_si_solo`, que fija `W_XWIND < THRESHOLD_GO` y documenta la dependencia con la barrera. |

### 5.2. Documentación técnica

| Archivo | Cambio |
|---|---|
| `CLAUDE.md` | Tabla de módulos (filas de `ahp_weights`, `weights`, `calibration`, `sensitivity`), tabla de pesos del soft scoring, ejemplo del cruzado en la barrera, bloque de umbrales + nota de convergencia. |
| `README.md` | Tabla de pesos, CR, ejemplo del cruzado, bloque de umbrales. |
| `DOCUMENTACION_CHECKPOINT_2.md` | § 4.2 (tabla de pesos + explicación del protocolo), § 4.3 (ejemplo del cruzado), § 4.4 (umbrales, rango óptimo, nota de convergencia), referencias sueltas a 0.039 y a los umbrales viejos. |

### 5.3. Documento de tesis

| Archivo | Cambio |
|---|---|
| `Teorico/03_Marco_Teorico.md` | **§ 3.1.4 pasó de un párrafo a tres subsecciones nuevas**: `3.1.4.1` protocolo de traducción (6 pasos + tabla de procedencia + convergencia), `3.1.4.2` el hallazgo de no conmensurabilidad y su relación con la barrera, `3.1.4.3` limitaciones declaradas. § 3.2.4 actualizado con el peso nuevo y remisión al § 3.1.4.2. Dos referencias nuevas en la bibliografía (AOPA 2019, JST 2021). Pendiente ⚠️ de verificación visual agregado como punto 1 de la lista. |

### 5.4. Verificación posterior al cambio

Todo ejecutado y verde:

```
pytest                    126 tests, sin red        OK
risk/ahp_weights.py       CR ≤ 0.10 en las 4 matrices, suma = 1.000000   OK
risk/weights.py           self-test                 OK
risk/soft_scoring.py      self-test                 OK
risk/calibration.py       35/36 (97 %), 0 sub-avisos, óptimo 0.22 / 0.59  OK
risk/sensitivity.py       Monte Carlo 99,0 %, 35/36 nunca cambian         OK
web/app.py                importa                   OK
```

---

## 6. Pendientes abiertos

### 6.1. Crítico antes de la entrega

✅ **Verificación visual del Nall Report — HECHA (2026-08-27).** Se renderizaron las
páginas 5, 6 y 17 del PDF del 28.º Nall Report y se leyeron las figuras directamente.
**Los veinte valores coinciden exactamente con los utilizados.**

| Serie | Valores confirmados | Suma |
|---|---|---|
| Meteorología, totales 2007→2016 | 56 · 54 · 65 · 52 · 56 · 51 · 41 · 36 · 39 · 23 | **473** |
| Meteorología, fatales | 44 · 38 · 47 · 36 · 42 · 38 · 30 · 28 · 30 · 12 | **345** |
| Aterrizaje, totales | 416 · 411 · 351 · 360 · 368 · 343 · 282 · 282 · 263 · 334 | **3410** |
| Aterrizaje, fatales | 7 · 4 · 3 · 9 · 2 · 7 · 4 · 6 · 3 · 6 | **51** |

La única discrepancia de la transcripción original fue de **orden**, no de valor: la serie
de fatales de aterrizaje se había extraído invertida (2016→2007). Como el índice usa la
suma, el resultado no cambia: `345 / 51 = 6,76 → Saaty 7`, **verificado contra la fuente**.

La figura 1.11 confirma también los valores de 2016 de todas las categorías: aterrizaje
334/6, otros 133/36, despegue y ascenso 121/24, combustible 63/7, maniobra 43/25,
descenso/aproximación 38/11, meteorología 23/12.

**Hallazgo adicional — figura 1.7.2, tipos de accidente meteorológico (2016):** VFR into
IMC **13 accidentes, 7 fatales**; turbulencia 4/1; tormenta 3/2; técnica IFR deficiente
2/2; engelamiento 1/0. Es el dato específico del fenómeno que da nombre a la tesis, con
figura citable — conviene incorporarlo al § 1.1.3.

⚠️ **Corrección pendiente en el § 1.1.3.** El texto afirma que los accidentes
meteorológicos tienen "una letalidad cercana al 80 %", atribuido genéricamente a AOPA. La
serie verificada da **72,9 % en la década** (rango anual 52 %-79 %). El 80 % es el techo
del rango, no el valor central. Conviene reemplazarlo por 72,9 % citando la figura 1.7.1,
que es un dato exacto y verificable. **Decisión del autor: es su texto.**

### 6.2. Del capítulo 3

- Enmienda vigente de la RAAC Parte 91 a la fecha de entrega (compartido con el cap. 1).
- Fecha de consulta de la página del FAA Safety Team sobre FRAT.
- Decidir si la obra original de Espy (1841) entra en la bibliografía o queda como
  mención histórica.
- Verificar las remisiones cruzadas a §§ 4.2, 5.1 y 6.3 cuando esas secciones estén
  escritas.
- Decidir si las herramientas de desarrollo asistido por IA se consignan en el § 3.3.2
  (stack) o en el § 6.2 (lecciones aprendidas). **Decisión del autor y su director.**
- Dos figuras sugeridas: arquitectura de tres capas del modelo de riesgo (§ 3.2.4) y
  canalización por capas (§ 3.2.5).

### 6.3. Del capítulo 1

- Denominación formal exacta del comitente.
- Título del Anexo N.º 1.
- Dónde va la imagen de la nota firmada.
- Fecha de consulta de la fuente de AOPA Air Safety Institute.
- DOI de Goh y Wiegmann (2002) y de Wiggins y O'Hare (1995).
- Dos mejoras propuestas y **no aplicadas**: citar `(ANAC, 2022, Parte 91)` en el párrafo
  de encuadre ético del § 1.3.1, y formalizar la referencia al AIP ENR 1.10 en el § 1.3.2.

### 6.4. Observaciones del director aún sin resolver

- **Observación 3** — cierre del circuito de la telemetría EFIS y corrección de la
  numeración del capítulo 2 (§ 4 de este documento).
