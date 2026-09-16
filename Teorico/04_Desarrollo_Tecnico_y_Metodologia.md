# 4. DESARROLLO TÉCNICO Y METODOLOGÍA

> **Nota de adaptación.** La plantilla organiza esta sección en torno al ciclo de un modelo
> entrenado: obtención de un *dataset*, análisis exploratorio, preprocesamiento con
> aumentación de datos, entrenamiento con partición en entrenamiento, validación y prueba, y
> ajuste de hiperparámetros. El núcleo de decisión de este trabajo no se entrena (§ 3.2.2),
> y el único componente aprendido —el modelo de lenguaje del asistente— es provisto por un
> tercero y se emplea sin reentrenamiento (§ 3.1.9). Se conserva la estructura de la
> sección (4.1 datos, 4.2 modelo, 4.3 software) y se adaptan los títulos internos a lo que
> el proyecto efectivamente hace. Donde un apartado de la plantilla no tiene equivalente, se
> lo declara y se explica cómo se cubre la necesidad que ese apartado atiende.

---

## Metodología de desarrollo

El sistema se construyó con un enfoque **iterativo e incremental**: cada incremento agregó
una capacidad completa y verificable sobre la anterior, y ninguna se incorporó sin
comprobarse por ejecución. El trabajo se organizó en cuatro incrementos, reconstruidos a
partir del historial de versiones del repositorio.

| Incremento | Período | Resultado |
|---|---|---|
| 1. Núcleo del motor | mayo de 2026 | Canalización completa desde los datos hasta el veredicto, interfaz web con mapa y rutas por aerovías, sobre un primer alcance centrado en aeródromos de Córdoba. |
| 2. Planificación operativa | junio de 2026 | Ventana de despegue, perfil vertical, NOTAM, consulta de aeródromos, régimen VFR/IFR con corredores visuales y exportación del plan de vuelo. Primera depuración del código: se retiraron las interfaces de consola y de escritorio y el optimizador por algoritmo genético. |
| 3. Fundamentación y alcance nacional | agosto y primera semana de septiembre de 2026 | Barrera no compensatoria, calibración de umbrales, pesos AHP derivados de la accidentología y muestreo en anillo del modelo numérico. Consolidación del alcance nacional y multi-aeronave, con la eliminación de los supuestos atados a un aeródromo particular. Respuesta a las observaciones del director. |
| 4. Evaluación por momento y asistente | segunda semana de septiembre de 2026 | Evaluación de cada aeródromo para el momento en que la aeronave estará en él, con el pronóstico de aeródromo como fuente; condiciones del nivel de crucero; puntos de paso con distinción entre sobrevuelo y escala; métricas sobre la trayectoria volada; asistente de consulta en lenguaje natural. |

Los dos primeros incrementos construyeron la funcionalidad; los dos últimos la dotaron del
fundamento metodológico y de la cobertura que el producto final requiere, y son los que
este capítulo desarrolla con mayor detalle.

Tres criterios de trabajo se sostuvieron a lo largo de todo el proceso:

- **Verificación contra la fuente real.** Todo cambio que involucra datos externos se
  comprobó contra las respuestas reales de las fuentes, y no solo contra datos simulados,
  antes de incorporarse.
- **Regla de alcance.** Ninguna solución, umbral o prueba puede depender de un aeródromo o de
  una aeronave particular; los cambios se verifican con aeródromos de distintas regiones y
  con aeronaves de distinto porte.
- **Decisiones declaradas.** Las limitaciones conocidas y las simplificaciones adoptadas se
  registran explícitamente —en el código, en la documentación técnica y en este documento—
  en lugar de quedar implícitas.

El desarrollo contó con la herramienta de asistencia declarada en el § 3.3.2, bajo el
criterio allí descrito.

---

## 4.1. Ciclo de vida de los datos

### 4.1.1. Obtención de los datos

El sistema no dispone de un conjunto de datos de entrenamiento, ni lo necesita: su insumo
son datos de referencia aeronáuticos y datos meteorológicos consultados en el momento de la
evaluación. Se distinguen tres familias.

**Datos de referencia estáticos.** Describen la infraestructura aeronáutica, cambian con
baja frecuencia y se procesan fuera de línea, una sola vez, para versionarse junto con el
código.

| Conjunto | Fuente | Obtención | Volumen resultante |
|---|---|---|---|
| Registro de aeródromos | ANAC, sistema MADHEL | Descarga programática desde su interfaz pública (corte del 10/05/2026) | 561 aeródromos |
| Pistas complementarias | OurAirports | Archivo tabular público | Pistas de 54 aeródromos que el registro no publica |
| Aerovías inferiores | AIP Argentina, sección ENR 3.1 | Reconocimiento óptico de caracteres sobre el documento publicado, con depuración y corrección posteriores | 123 aerovías, 432 puntos de notificación, MEA de 4000 a 25 000 ft |
| Corredores visuales | Cartas VFR del AIP (TMA Buenos Aires y Córdoba) | Digitalización de las cartas como geometrías GeoJSON | 22 corredores |
| Espacios aéreos | OpenAIP | Descarga programática con clave de acceso | 203 zonas, entre ellas 131 de espacio controlado y 71 restringidas |
| Regiones de información de vuelo | Archivo GeoJSON ⬜ *[confirmar origen]* | — | 5 FIR |

**Datos dinámicos.** Se consultan en cada evaluación, a través de las interfaces descritas en
el § 3.3.3: observación y pronóstico de aeródromo (aviationweather.gov), pronóstico numérico
en superficie y en niveles de presión (Open-Meteo), NOTAM oficiales (AIS de la ANAC) y
elevación del terreno (Open-Topo-Data).

**Conjuntos construidos para el modelo.** Tres conjuntos no provienen de una fuente externa,
sino que se elaboraron como parte del trabajo: las series de accidentología a partir de las
cuales se derivan los pesos (§ 3.1.4), la batería de escenarios de referencia contra la que
se calibran los umbrales (§ 4.2.2) y el conjunto de consultas etiquetadas con el que se
evalúa el asistente (§ 4.2.4).

### 4.1.2. Análisis exploratorio de las fuentes

El análisis exploratorio se orientó a caracterizar la cobertura y la calidad de los datos,
porque ambas condicionan el diseño: una fuente incompleta o heterogénea obliga a decidir
qué hace el sistema cuando el dato falta.

**Completitud del registro de aeródromos.**

| Atributo | Aeródromos con dato | Proporción |
|---|---|---|
| Coordenadas | 561 | 100 % |
| Elevación publicada | 538 | 95,9 % |
| Al menos una pista | 553 | 98,6 % |
| Código OACI | 138 | 24,6 % |
| Teléfono | 395 | 70,4 % |
| Normas particulares | 376 | 67,0 % |
| Combustible | 110 | 19,6 % |
| Horario de atención | 44 | 7,8 % |

Del total, 274 aeródromos son públicos y 52 son controlados.

**La cobertura es inversa a la jerarquía del aeródromo.** Al desagregar la completitud según el
tipo de aeródromo aparece un patrón que no es intuitivo. De los 52 aeródromos controlados,
solo uno publica teléfono, uno publica normas particulares y ninguno publica combustible;
entre los 509 no controlados, en cambio, 394 publican teléfono, 375 normas particulares y
110 combustible. Los aeródromos de mayor jerarquía, cuya información operativa se publica en
el AIP, son precisamente los que tienen esos campos vacíos en el registro. La consecuencia de
diseño es directa: en esta fuente, la ausencia de un dato no permite inferir la ausencia de
lo que describe, y el sistema no debe presentarla como tal (§ 3.2.7).

**Concentración geográfica.** Las 24 jurisdicciones del país tienen aeródromos en el registro,
pero la distribución es desigual: Buenos Aires (179), Santa Fe (75) y Córdoba (69) reúnen
323 aeródromos, el 57,6 % del total. Un sistema probado solo sobre la región pampeana
quedaría validado sobre la mayoría de los casos y no sobre la diversidad del país; por eso
las pruebas y la evaluación del asistente se distribuyen deliberadamente entre las cinco
regiones.

**Cobertura de observación meteorológica.** Tener código OACI no implica tener estación
meteorológica. En una medición del 15/09/2026, de los 138 aeródromos con código OACI, 48
emitían METAR y 37 de ellos, además, TAF; los 90 restantes no publicaban ninguno de los dos
productos. En términos del registro completo, **513 de los 561 aeródromos (91,4 %) no tienen
observación directa**, y su evaluación depende del modelo numérico. Este resultado es el que
justifica tratar al pronóstico numérico como fuente principal y no como respaldo, y dedicarle
el tratamiento de incertidumbre descrito en el § 3.2.5.2.

**Calidad de los datos.** El análisis detectó defectos en las fuentes que, sin tratamiento,
se habrían propagado al veredicto. Se resumen con el tratamiento adoptado para cada uno.

| Fuente | Hallazgo | Tratamiento |
|---|---|---|
| Registro MADHEL | Dos registros repiten el identificador, las coordenadas y la provincia de otro aeródromo; solo el nombre corresponde a un aeródromo distinto. | Ante un identificador repetido se conserva el registro cuyo nombre declara ese identificador, y se descarta el inconsistente con aviso. |
| Registro MADHEL | El nombre publicado combina el nombre con identificadores y datos administrativos, en formatos no uniformes, y algunos nombres incluyen marcas de codificación invisibles. | Extracción del nombre a partir del grupo de identificadores, válida para todos los formatos observados, y eliminación de las marcas. |
| Registro MADHEL | 23 aeródromos sin elevación publicada. | Se marcan como elevación estimada y la interfaz lo señala. |
| aviationweather.gov | La visibilidad se entrega como texto en millas terrestres, sin unidad, y el tope de la escala se codifica como "6+". | Conversión explícita a kilómetros y reconocimiento del tope como visibilidad ilimitada. |
| Open-Meteo | La nubosidad se expresa como fracción de cobertura, sin altura de base, y la visibilidad no se publica por nivel de presión. | Estimación de la base de nubes (§ 3.1.7) y declaración explícita del dato ausente en altura. |

### 4.1.3. Preparación y normalización de los datos

La preparación se realiza en dos momentos: la consolidación de los datos de referencia, fuera
de línea, y la normalización de los datos dinámicos, en cada evaluación.

**Consolidación del registro.** El registro descargado contiene 712 entradas: 563
aeródromos y 149 helipuertos. Los helipuertos se excluyen, porque el sistema está definido
para aeronaves de ala fija. A los aeródromos se les aplica una regla de identificador única
—el código OACI cuando existe y el identificador local en caso contrario—, se extrae el
nombre, se resuelven los identificadores repetidos, se convierten las elevaciones a pies y
se interpretan los campos de texto que describen pistas y umbrales. Los aeródromos cuyas
pistas no figuran en el registro —54, de los cuales 50 son controlados— se completan con
OurAirports. El resultado son 561 aeródromos, con 1296 pistas descritas.

**Consolidación de la red de navegación.** De las aerovías se retienen únicamente las
inferiores, las que utiliza la aviación general, y se construye con ellas un grafo
bidireccional cuyos nodos son los puntos de notificación y cuyas aristas conservan su MEA.
Los identificadores de punto que el documento repite para ubicaciones distintas se
desambiguan, para que el algoritmo de camino mínimo no encuentre atajos inexistentes. Los
corredores visuales se integran como un grafo por área terminal; como su techo se publica
sobre el terreno, se convierte a altitud sobre el nivel del mar con la elevación de cada
punto al momento de calcular la ruta.

**Normalización a un contrato único.** Cada dato meteorológico, cualquiera sea su fuente, se
traduce a una única estructura tipada antes de llegar al modelo de riesgo (§ 3.2.5.1), con
unidades fijas para todo el sistema: viento en nudos, visibilidad en kilómetros, alturas de
nubes en pies sobre el terreno, temperatura en grados Celsius, presión en hectopascales y
tiempo en segundos UTC. Cada fuente tiene su adaptador: el del METAR interpreta capas de
nubes, techo, visibilidad y fenómenos; el del pronóstico numérico convierte la cobertura en
capas estimadas y los códigos meteorológicos de la OMM en los mismos indicadores de fenómeno
que usa el METAR; el del TAF resuelve la herencia entre el período base y sus grupos de
cambio. Sobre esas estructuras se compone la condición del momento evaluado (§ 3.2.5.3).

```python
@dataclass
class ParsedWeather:
    source        : str
    station_id    : str
    obs_time      : int                       # segundos UTC
    nwp_estimated : bool = False

    wind_dir      : Optional[int]   = None    # grados
    wind_spd_kt   : Optional[float] = None
    wind_gust_kt  : Optional[float] = None
    wind_variable : bool            = False

    visibility_km : Optional[float] = None
    ceiling_ft    : Optional[int]   = None    # pies AGL; None = sin techo
    sky_layers    : list            = field(default_factory=list)

    temp_c        : Optional[float] = None
    dewpoint_c    : Optional[float] = None
    spread_c      : Optional[float] = None
    altimeter_hpa : Optional[float] = None
    wx_codes      : list            = field(default_factory=list)
    ...
```

*Fragmento 4.1. Contrato de datos meteorológicos (`parsers/metar_parser.py`, extracto).*

El contrato establece también la convención para los campos opcionales: nunca se usa un
valor centinela, y el significado de un campo sin valor se define para cada uno —un techo sin
valor, por ejemplo, significa que ninguna capa de nubes constituye techo—, de modo que cada
capa del modelo sabe cómo tratarlo.

**Robustez de la adquisición.** Las consultas a servicios externos siguen un mismo esquema.
Los fallos transitorios —límite de solicitudes, error del servidor o de conexión— se
reintentan con espera creciente hasta completar tres intentos; los errores de la solicitud no
se reintentan, porque repetirla no cambia el resultado. Ante la falta definitiva de un dato,
la ausencia se propaga como tal y la capa de decisión recurre a la siguiente fuente
disponible. Las respuestas se almacenan en memoria con un vencimiento acorde a la
frecuencia de actualización de cada fuente:

| Dato | Vencimiento |
|---|---|
| METAR | 10 minutos |
| TAF | 30 minutos |
| NOTAM | 30 minutos |
| Pronóstico numérico | 30 minutos |

Las respuestas vacías o fallidas no se almacenan, de modo que un error transitorio no se
arrastra hasta el vencimiento.

**Aumentación de datos.** No corresponde: no hay un modelo que generalice a partir de
ejemplos. La necesidad que la aumentación atiende en un modelo entrenado —que el sistema
responda correctamente ante condiciones poco frecuentes— se cubre aquí de otro modo: la
batería de escenarios de referencia se construye deliberadamente con casos adversos y de
borde, que en la operación real son raros (§ 4.2.2).

---

## 4.2. Ingeniería del modelo de decisión

### 4.2.1. Diseño: arquitectura y flujo de datos

La arquitectura del sistema es la canalización por capas descrita en el § 3.2.5, sobre la
que el asistente de consulta actúa como una capa de lenguaje que accede al motor sin formar
parte de la emisión del veredicto.

⬜ **Figura 4.1.** *Arquitectura del sistema.* Capas de datos, interpretación,
características, riesgo, decisión, ruta, salida y servicio web; capa de lenguaje con acceso
a datos, decisión y ruta a través de herramientas; fuentes externas y datos de referencia.

**Flujo de una evaluación.** Una solicitud de evaluación de vuelo recorre las siguientes
etapas:

1. **Validación** de la solicitud: aeródromos, perfil de aeronave, hora de salida en UTC,
   nivel de experiencia del piloto y puntos de paso.
2. **Evaluación de los extremos, en paralelo.** El origen se evalúa para la hora de salida y
   el destino para la hora estimada de llegada. En cada aeródromo se selecciona la fuente
   según el momento, se normalizan los datos, se derivan las características —componente de
   viento cruzado sobre la pista más favorable, riesgo de niebla, altitud de densidad, luz
   diurna— y se aplican las tres capas del modelo de riesgo: rechazos categóricos, puntaje
   ponderado y barrera no compensatoria.
3. **Planificación de la ruta** mediante los algoritmos del § 3.2.6: A\* sobre el grafo de
   aeródromos, aerovías en régimen IFR, corredores visuales en las áreas terminales y
   descomposición por tramos cuando hay puntos de paso.
4. **Trayectoria volada.** Se calculan los tramos efectivamente recorridos, con su rumbo,
   distancia, tiempo y combustible por etapa, y de ellos la hora real de llegada; si esa hora
   cae en otra franja horaria que la estimada, el destino se reevalúa.
5. **Escalas y puntos en ruta.** Cada escala se evalúa como aeródromo a su hora de llegada;
   los puntos de control en ruta se evalúan en paralelo e informan las condiciones del nivel
   de crucero.
6. **Restricciones operativas no meteorológicas**: vuelo nocturno en régimen VFR y NOTAM de
   cierre del aeródromo.
7. **Consolidación**: el veredicto global es el más restrictivo entre los de todos los
   aeródromos en los que la aeronave despega o aterriza; se generan el informe meteorológico
   y el borrador del plan de vuelo.

⬜ **Figura 4.2.** *Flujo de datos de una evaluación.* Las siete etapas anteriores, con las
fuentes consultadas en cada una y los puntos de ejecución en paralelo.

El centro del modelo de riesgo es la composición de sus dos capas finales. El puntaje se
traduce a una banda por umbrales, la barrera fija un piso por factor, y el veredicto es el
más restrictivo de ambos:

```python
# Decision compensatoria (umbrales sobre R_total)
threshold_decision = apply_decision_threshold(r_total)

# Barrera no-compensatoria (veto conjuntivo)
xw_limit = aircraft.crosswind_max_kt * pm.xwind_mult
guardrail_floor, guardrail_reason = conjunctive_floor(...)

# Decision final = peor entre el score compensatorio y el piso conjuntivo.
decision = _worst_verdict(threshold_decision, guardrail_floor)
```

*Fragmento 4.2. Composición del veredicto (`risk/soft_scoring.py`, extracto).*

Junto con el veredicto, el modelo devuelve la contribución de cada factor al puntaje, el
factor dominante y, cuando la barrera actúa, el factor limitante y su motivo. Esa
información no se reconstruye después: se produce en el mismo cálculo, y es la que la
interfaz presenta al piloto.

⬜ **Figura 4.3.** *Flujo de una consulta al asistente.* Pregunta, elección de herramienta,
ejecución determinista, redacción y verificaciones en código.

### 4.2.2. Construcción del modelo: derivación, calibración y control de robustez

Donde un modelo aprendido se entrena, este modelo se **construye**: sus parámetros se derivan
de la evidencia y de la norma mediante procedimientos explícitos. Cada procedimiento está
implementado como un módulo ejecutable que reproduce su resultado, y ninguno interviene en
tiempo de ejecución: los valores que producen quedan fijados como constantes del motor.

**Derivación de los pesos.** Los siete pesos del puntaje se obtienen por AHP, con el
protocolo del § 3.1.4.1. Los criterios se organizan en tres grupos —referencia visual, viento,
fenómenos y tendencia—, lo que reduce las comparaciones de a pares de veintiuna a ocho. Cada
entrada de las matrices declara su procedencia (evidencia, norma, derivación o juicio), y
las que provienen de la evidencia se calculan con la operación explícita
*a*<sub>ij</sub> = redondeo de Saaty (*I*<sub>i</sub> / *I*<sub>j</sub>), donde *I* es el
índice de riesgo de cada categoría. La razón de consistencia resultante es de 0,069, por
debajo del límite de 0,10.

**La batería de escenarios de referencia.** Es el equivalente del conjunto de datos: el
material contra el cual se ajustan y se verifican las decisiones del sistema. Consta de 38
escenarios construidos para cubrir el espacio de condiciones de manera deliberada, incluidos
los casos que en la operación real son poco frecuentes.

| Grupo | Escenarios | | Grupo | Escenarios |
|---|---|---|---|---|
| VFR pleno | 6 | | Combinado | 4 |
| VFR marginal | 6 | | Niebla | 2 |
| Viento | 6 | | Fenómenos | 2 |
| IFR | 5 | | TAF | 2 |
| Ráfagas | 5 | | | |

Los escenarios se reparten entre los cinco perfiles de aeronave. Cada uno recibe una
**etiqueta normativa** —GO, CAUTION o NO GO— derivada de una regla explícita e independiente
del puntaje: para visibilidad y techo, la categoría de vuelo de la regulación ANAC/OACI; para
los demás factores, umbrales relativos a los límites de cada aeronave; y como veredicto del
escenario, el más restrictivo de todos. La distribución resultante es de 19 escenarios GO,
10 CAUTION y 9 NO GO. La independencia de la etiqueta respecto del motor rige para
visibilidad, techo, fenómenos y tendencia; para el viento cruzado, la referencia aplica los
mismos cortes que la barrera, de modo que en ese factor la concordancia se da por
construcción, limitación que el propio módulo declara.

**Calibración de los umbrales.** Los dos umbrales que traducen el puntaje a banda se fijan por
búsqueda exhaustiva sobre una grilla —el umbral inferior entre 0,05 y 0,45 y el superior hasta
0,75, con paso de 0,01— evaluando en cada punto el **sistema completo**, con rechazos
categóricos y barrera incluidos. El criterio es una función de costo asimétrica, que penaliza
el sub-aviso —que el sistema advierta menos que la norma— muy por encima del sobre-aviso,
coherentemente con el fundamento del § 3.1.5:

| Veredicto del sistema ↓ / normativo → | GO | CAUTION | NO GO |
|---|---|---|---|
| **GO** | 0 | 4 | 12 |
| **CAUTION** | 1 | 0 | 4 |
| **NO GO** | 3 | 1 | 0 |

```python
candidates = []
t = 5
while t <= 45:                      # t_go de 0.05 a 0.45
    c = t + 2
    while c <= 75:                  # t_caution de t_go+0.02 a 0.75
        candidates.append(_score_thresholds(rows, t / 100.0, c / 100.0))
        c += 1
    t += 1

def _key(m):
    return (m["cost"], m["dangerous"],
            abs(m["t_go"] - THRESHOLD_GO) + abs(m["t_caution"] - THRESHOLD_CAUTION))

best = min(candidates, key=_key)
```

*Fragmento 4.3. Búsqueda de los umbrales (`risk/calibration.py`, extracto).*

Ante empate de costo se prefiere el par con menos sub-avisos y, después, el más próximo a los
umbrales vigentes. El resultado adoptado es 0,22 y 0,59; el óptimo no es un punto aislado
sino un rango, lo que se analiza junto con la concordancia obtenida en el § 5.1.

**La ausencia de partición, y cómo se compensa.** Un modelo entrenado reserva datos que no vio
para medir su generalización. Aquí esa separación no es posible: la batería sirve a la vez
para calibrar los umbrales y para medir la concordancia, y no existe un conjunto independiente
de decisiones reales etiquetadas contra el cual contrastar. La validez que el procedimiento
establece es, en consecuencia, **de constructo** —el sistema reproduce la estructura de la
norma— y no empírica, y así se la reporta. Tres controles acotan lo que esa limitación deja
abierto:

- **Convergencia.** Dos derivaciones independientes de los pesos, una por juicio experto y
  otra por evidencia, producen el mismo veredicto en todos los escenarios (§ 3.1.4.1).
- **Análisis de sensibilidad.** Se mide cuánto cambia el veredicto al perturbar lo que no
  proviene de la norma: cada peso por separado en ±20 %, los siete pesos a la vez en 5000
  sorteos aleatorios dentro de ±20 %, los umbrales en ±0,05, los parámetros de forma de las
  funciones de riesgo y la fracción de precaución de la barrera.
- **Prueba de regresión.** Una prueba automatizada fija el comportamiento sobre la batería:
  ningún sub-aviso, concordancia mínima del 90 %, rechazo de todos los escenarios con factor
  inhabilitante, estabilidad ante perturbaciones de los pesos y monotonía ante el
  endurecimiento de los umbrales. Cualquier cambio posterior que altere esas propiedades se
  detecta de inmediato.

Los resultados de los tres controles se presentan en el § 5.1.

### 4.2.3. Parámetros de ajuste

Los hiperparámetros de un modelo aprendido tienen su equivalente en los parámetros que el
modelo no deriva de la norma ni de la evidencia. Ninguno se ajustó a datos: cada uno tiene una
procedencia declarada y su efecto sobre el veredicto se acota en el análisis de sensibilidad.

| Parámetro | Valor | Procedencia |
|---|---|---|
| Umbrales de decisión | 0,22 y 0,59 | Calibración por anclaje normativo |
| Rampa de riesgo de visibilidad (máximo / nulo) | 3 km / 8 km | Norma / juicio |
| Rampa de riesgo de techo (máximo / nulo) | 500 ft / 2000 ft | Norma / juicio |
| Rampa de riesgo de niebla, según diferencia entre temperatura y punto de rocío | 2 °C / 5 °C | Juicio |
| Piso de precaución de la barrera de viento cruzado | 85 % del máximo demostrado | Juicio |
| Mínimos personales: multiplicadores de visibilidad, techo y cruzado | Alumno 1,6 / 1,5 / 0,6 · PPL 1,2 / 1,2 / 1,0 · Avanzado 1 / 1 / 1 | Juicio |
| Muestreo en anillo | 6 puntos a 10 km | Diseño: orden de la celda del modelo |
| Vigencia de la observación | 1 hora | Periodicidad del METAR |
| Horizonte máximo de pronóstico | 48 horas | Disponibilidad de la fuente |

En las rampas de riesgo, el extremo de riesgo máximo coincide con la frontera de la categoría
IFR de la regulación, y el extremo de riesgo nulo agrega un margen de juicio sobre el mínimo
VFR. Los mínimos personales endurecen los límites según la experiencia del piloto sin
modificar los pesos, de modo que el perfil del piloto cambia la exigencia y no la estructura
del modelo.

### 4.2.4. Configuración y evaluación del asistente de consulta

El asistente no se entrena. Su comportamiento se obtiene de tres elementos que se diseñan y
se evalúan: la instrucción de sistema, el catálogo de herramientas y las verificaciones en
código. La arquitectura que los organiza se fundamentó en el § 3.2.7; este apartado describe
cómo se construyeron y cómo se midió el resultado.

**Acceso al modelo.** El modelo de lenguaje se consulta por solicitudes HTTP directas, con una
cadena de reserva de tres modelos de la misma familia ordenada según la disponibilidad y la
latencia medidas en el nivel gratuito del proveedor. Cada consulta admite hasta 600
caracteres, conserva hasta 12 turnos de historial, tiene un tiempo máximo de espera de 15
segundos por solicitud y un tope de tres vueltas de herramienta.

| Orden | Modelo |
|---|---|
| 1 | `gemini-flash-lite-latest` |
| 2 | `gemini-3.1-flash-lite` |
| 3 | `gemini-3.5-flash` |

**Instrucción de sistema.** Define el rol —asistente de planificación en tierra, nunca en
vuelo—, delimita lo que el asistente no hace —no decide, no calcula, no aporta conocimiento
propio—, fija las cinco reglas del § 3.2.7 e incorpora en cada consulta la fecha, la hora
local y el estado del formulario, para que el modelo pueda resolver referencias como "mañana"
o "el destino" sin preguntar lo que el piloto ya cargó.

**Catálogo de herramientas.** Las ocho herramientas se declaran en el esquema de llamada a
funciones del proveedor, con su nombre, sus parámetros y una descripción en lenguaje natural.
La descripción es la principal palanca de diseño: es lo que el modelo lee para decidir qué
herramienta usar. Las intenciones vecinas —el veredicto de un aeródromo, el estado del aire
sobre un punto y la modificación de la ruta, que pueden formularse con frases parecidas— se
separaron redactando descripciones que contrastan explícitamente cada herramienta con las
demás. Del lado del sistema, las herramientas cumplen un contrato uniforme: devuelven datos
estructurados, nunca un campo vacío, y declaran de manera explícita cuándo un dato no está
publicado.

**Verificaciones en código.** Las garantías que no se confían a la instrucción se implementan
después de la redacción. La del veredicto compara el texto con el resultado del motor y, si no
lo transcribe o menciona otro, lo sustituye por una plantilla determinista:

```python
datos = resultados_meteo[-1]
canonico = str(datos.get("veredicto", "")).upper().strip()
if canonico not in ("GO", "CAUTION", "NO GO"):
    return texto, False

mencionados = verdicts_mentioned(texto)
if hay_serie_de_veredictos:
    if canonico in mencionados:
        return texto, False
elif mencionados == {canonico}:
    return texto, False

return verdict_fallback(datos), True
```

*Fragmento 4.4. Verificación de la transcripción del veredicto (`copilot/agent.py`, extracto).
Cuando la consulta devuelve una serie horaria, el texto menciona legítimamente varios
veredictos y se exige solo que el del motor esté presente.*

**Conjunto de evaluación.** El desempeño se mide sobre 98 consultas redactadas como las
escribiría un piloto, cada una etiquetada con la intención esperada y el aeródromo que
debería resolverse.

| Intención | Casos | | Intención | Casos |
|---|---|---|---|---|
| Contacto de aeródromo | 17 | | Propuesta de cambio de ruta | 10 |
| Servicios de aeródromo | 15 | | Fuera de alcance | 9 |
| Búsqueda de aeródromo | 11 | | Combustible cercano | 8 |
| Evaluación meteorológica | 10 | | Mejor hora de salida | 8 |
| Estado del aire en un punto | 10 | | | |

El conjunto tiene tres propiedades de diseño. Cubre las cinco regiones del país —Pampa 30
casos, Patagonia 19, Litoral 14, NOA 12 y Cuyo 12; los 11 restantes no se asocian a una
región—. Incluye 11 casos que piden a propósito un dato que el registro no publica; en 9 de
ellos la invención se detecta con un patrón objetivo: si la respuesta contiene algo con la
forma del dato ausente —un número de teléfono, por ejemplo—, el modelo lo inventó. Y 10 casos incluyen el estado del formulario, porque hay consultas que
solo tienen sentido con un vuelo cargado.

Sobre ese conjunto se calculan la matriz de confusión de intenciones, la precisión, la
exhaustividad y el F1 por intención, la exactitud en la resolución del aeródromo, la tasa de
invención sobre datos ausentes, la integridad del veredicto y de los códigos, la latencia y el
desagregado por región. Los resultados se almacenan en un archivo versionado y se presentan en
el § 5.1. Una corrida se considera interpretable solo si ningún caso registra
indisponibilidad del proveedor, porque el agente convierte esa indisponibilidad en una
respuesta de fuera de alcance que la métrica computaría como error de clasificación.

**Una limitación del procedimiento.** El conjunto de evaluación se utilizó también durante el
desarrollo: el ajuste de las descripciones de las herramientas se midió sobre él. No hay, por
lo tanto, un conjunto reservado que el diseño no haya visto, y las métricas deben leerse como
desempeño sobre el conjunto de desarrollo. La mitigación es parcial —cada herramienta se
incorporó con casos propios, y una prueba automatizada exige un mínimo de casos por
intención—, y la construcción de un conjunto independiente queda planteada en el § 6.3.

---

## 4.3. Desarrollo de software

### 4.3.1. Servicio de aplicación

El sistema se expone como un único servicio web construido con FastAPI, que es a la vez la
interfaz programática del motor y el servidor de la interfaz de usuario. Las solicitudes y
respuestas se definen como modelos tipados, de modo que la validación de entrada ocurre antes
de llegar al motor y los errores se devuelven con el campo que los provoca.

| Método | Ruta | Función |
|---|---|---|
| GET | `/` | Interfaz de usuario |
| POST | `/api/evaluate` | Evaluación completa del vuelo: extremos, escalas, ruta, informe |
| POST | `/api/timeline` | Evolución horaria del riesgo, para elegir la hora de salida |
| POST | `/api/profile` | Perfil vertical: terreno, altitud mínima y altitud de crucero |
| POST | `/api/flightplan` | Borrador del plan de vuelo OACI |
| GET | `/api/airports` | Búsqueda de aeródromos |
| GET | `/api/airport/{code}` | Ficha del aeródromo: pistas, meteorología, servicios, NOTAM, entorno |
| GET | `/api/airports/map` | Capa de aeródromos para el mapa |
| GET | `/api/airspace` | Espacios aéreos |
| GET | `/api/vfr_corridors` | Corredores visuales |
| GET | `/api/aircraft` | Perfiles de aeronave |
| GET | `/api/copilot/status` | Disponibilidad del asistente |
| POST | `/api/copilot` | Consulta al asistente |

El servidor no conserva estado entre solicitudes. Las consultas independientes de una misma
evaluación se ejecutan en paralelo: los dos extremos del vuelo, las escalas y los puntos de
control en ruta. El almacenamiento temporal de las respuestas externas (§ 4.1.3) es compartido
por todas las solicitudes y seguro ante la ejecución concurrente.

### 4.3.2. Interfaz de usuario

La interfaz es una aplicación de una sola página, con Alpine.js para la reactividad y Leaflet
para la cartografía, sin etapa de compilación. Se organiza en dos vistas.

**Planificación del vuelo.** El formulario recibe origen, destino, aeronave, régimen VFR o IFR,
hora de salida, nivel de experiencia y puntos de paso, cada uno marcado como sobrevuelo o
escala. La hora se ingresa en UTC y se muestra su equivalencia en hora local. El resultado
se presenta en bloques:

- **Veredicto por aeródromo**, con el puntaje, el factor dominante, el factor limitante
  cuando la barrera actúa, la fuente de los datos y el momento para el que se evaluaron, y
  los informes METAR y TAF originales.
- **Ventana de despegue**, con la evolución del riesgo en las horas siguientes.
- **Ruta en el mapa**, con aerovías, corredores visuales, escalas y aeródromos de emergencia
  próximos a la ruta, y una tabla de tramos con el rumbo, la distancia y el tiempo de cada
  uno.
- **Perfil vertical** de la ruta, con el terreno, la altitud mínima segura y la altitud de
  crucero.
- **Informe meteorológico** y **borrador del plan de vuelo**.

**Consulta de aeródromo.** Ficha con pistas, meteorología en vivo, servicios, espacios aéreos
y aeródromos cercanos.

La interfaz responde a cuatro criterios de usabilidad, derivados de la naturaleza del
producto:

1. **Todo veredicto se explica.** El piloto ve qué factor lo determina, no solo la etiqueta.
2. **La procedencia es visible.** Cada dato indica su fuente y su momento, de modo que un
   pronóstico no se lea como una observación.
3. **La degradación es explícita.** Cuando un dato falta o una fuente no responde, la interfaz
   lo informa en lugar de completar el hueco.
4. **La decisión es del piloto.** Un aviso al ingresar declara que el sistema es una
   herramienta de apoyo que no reemplaza el criterio del piloto al mando, y se refuerza al
   generar el borrador del plan de vuelo, que el sistema no radica.

### 4.3.3. Integración del asistente con la interfaz

El asistente se presenta como un panel de la interfaz que solo aparece si el servicio del modelo de
lenguaje está configurado; en caso contrario, el resto de la interfaz funciona sin cambios.
Cada consulta envía al servidor la pregunta, el historial de la conversación y el estado del
formulario, leído directamente del estado de la página. La respuesta incluye, además del
texto, la intención clasificada, las herramientas utilizadas, las indicaciones de si alguna
verificación en código tuvo que intervenir, la latencia y, cuando corresponde, una propuesta
de cambio de ruta.

La propuesta se construye en el servidor a partir del resultado de la herramienta, y la
interfaz la presenta como una tarjeta con la ruta resultante, el costo del cambio en
distancia, tiempo y combustible, el veredicto de la escala a su hora de llegada y la
advertencia de autonomía cuando el cambio la compromete. El piloto puede aplicarla o
descartarla. Aplicarla modifica el formulario, pero no dispara la evaluación: la nueva
evaluación la solicita el piloto.

### 4.3.4. Aseguramiento de la calidad

La calidad del software se sostiene sobre una suite de regresión de 397 pruebas automatizadas,
organizadas por capa:

| Archivo | Pruebas | Alcance |
|---|---|---|
| `test_features_parsers.py` | 59 | Interpretación de METAR, TAF y pronóstico numérico; cálculo de características |
| `test_risk.py` | 77 | Pesos, funciones de riesgo, barrera, perfiles de aeronave y mínimos personales |
| `test_regression_scenarios.py` | 9 | Comportamiento del veredicto sobre la batería de referencia |
| `test_engine_data.py` | 85 | Canalización de decisión, registro de aeródromos, selección de fuente, almacenamiento temporal y degradación |
| `test_route.py` | 34 | Grafo de rutas, admisibilidad de la heurística de A\* para los cinco perfiles y aerovías |
| `test_web.py` | 43 | Servicio web: puntos de paso, trayectoria volada y validación de entradas |
| `test_copilot.py` | 90 | Asistente: verificaciones en código, contrato de las herramientas y conjunto de evaluación |

Cuatro prácticas le dan a la suite su valor:

- **Ejecución sin red.** La suite corre sin conexión, condición verificada bloqueando el acceso
  a la red durante su ejecución. Una prueba que depende en silencio de un servicio externo no
  prueba lo que declara.
- **Simulaciones fieles a la fuente.** Los datos simulados reproducen el formato real de cada
  fuente, verificado contra sus respuestas; una simulación que no imita a la fuente no detecta
  los errores de interpretación.
- **Cobertura del alcance.** Las pruebas que dependen del lugar o de la aeronave se repiten
  sobre aeródromos de distintas regiones y sobre varios perfiles.
- **Módulos autoverificables.** Cada módulo incluye además una verificación ejecutable propia,
  y los módulos metodológicos —derivación AHP, calibración y sensibilidad— reproducen sus
  resultados al ejecutarse.

### 4.3.5. Despliegue

El sistema se despliega en la plataforma Render, en su plan gratuito, a partir del
repositorio. La configuración se declara en un archivo versionado que fija la versión de
Python —la misma del entorno de desarrollo—, instala las cuatro dependencias de producción e
inicia el servidor. La clave de acceso al modelo de lenguaje se configura como variable de
entorno secreta del servicio, fuera del repositorio. Todo cambio incorporado a la rama
principal se despliega automáticamente.

---

## ⬜ Pendientes de esta sección

1. **Figuras 4.1, 4.2 y 4.3**, a elaborar con el resto de las figuras del documento.
2. **Origen del archivo de regiones de información de vuelo (FIR)**: confirmar la fuente para
   consignarla en el cuadro del § 4.1.1.
3. **Numeración cruzada**: verificar las remisiones a los §§ 5.1 y 6.3 una vez redactadas esas
   secciones.
