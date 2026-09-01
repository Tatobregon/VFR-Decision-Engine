# 3. MARCO TEÓRICO

> **Nota de adaptación.** La plantilla de la carrera propone para esta sección los
> fundamentos propios de un proyecto de aprendizaje automático (aprendizaje supervisado,
> *deep learning*, redes convolucionales; elección entre YOLOv8 y SSD; TensorFlow o
> PyTorch). El presente trabajo se inscribe en la otra tradición de la inteligencia
> artificial —la simbólica— y su fundamento científico es, en consecuencia, otro: la
> representación del conocimiento, los sistemas basados en reglas y el análisis de
> decisión multicriterio. Se conserva íntegramente la estructura de la sección (3.1
> fundamentos científicos, 3.2 modelos y arquitecturas, 3.3 *stack* tecnológico) y se
> sustituye su contenido por el que efectivamente sostiene la solución construida. El
> § 3.2.2 desarrolla en profundidad la justificación de esa elección, anticipada en el
> § 1.3.1.

---

## 3.1. Fundamentos científicos

### 3.1.1. Las dos tradiciones de la inteligencia artificial y el lugar de este trabajo

El uso corriente del término *inteligencia artificial* tiende hoy a identificarlo con
una sola de sus vertientes, la del aprendizaje automático a partir de datos. La
disciplina, sin embargo, se constituyó históricamente sobre dos programas de
investigación distintos, que difieren en aquello que consideran el sustrato de la
conducta inteligente.

El programa **simbólico** sostiene que la inteligencia consiste en la manipulación de
estructuras de símbolos según reglas. Newell y Simon (1976) lo formularon como la
*hipótesis del sistema de símbolos físicos*: un sistema de símbolos físicos posee los
medios necesarios y suficientes para la acción inteligente general. Bajo esta hipótesis,
construir un sistema inteligente es una tarea de representación —codificar el
conocimiento del dominio en estructuras explícitas— y de búsqueda —recorrer el espacio
de esas estructuras hasta hallar la que resuelve el problema—. El conocimiento vive en
el sistema de manera legible: se puede leer, discutir, auditar y corregir.

El programa **subsimbólico o conectivista**, del que el aprendizaje automático
contemporáneo es la expresión dominante, invierte el punto de partida. En lugar de
codificar el conocimiento, se lo *induce*: a partir de un conjunto suficientemente
grande de ejemplos etiquetados, un modelo paramétrico ajusta sus parámetros hasta
minimizar el error sobre esos ejemplos, y se espera que la función así aprendida
generalice a casos nuevos. El conocimiento queda distribuido en millones de parámetros
numéricos que no admiten lectura directa.

Ninguno de los dos programas es superior en abstracto; cada uno resuelve una clase de
problema distinta. El aprendizaje automático es la respuesta adecuada cuando la relación
entre entrada y salida es real pero no se sabe expresar —reconocer un rostro, transcribir
habla, detectar un objeto en una imagen— y existen datos abundantes que la ejemplifiquen.
La inteligencia artificial simbólica es la respuesta adecuada cuando ocurre lo inverso:
el conocimiento existe y está formalizado, pero está disperso, es voluminoso y su
aplicación consistente excede la capacidad de atención de una persona.

La decisión meteorológica de despacho pertenece a esta segunda clase. Los mínimos VFR
están escritos en la regulación; los límites de viento cruzado están publicados en el
manual de vuelo de cada aeronave; la accidentología indica qué factores concentran la
letalidad. Lo que falta no es descubrir una regularidad oculta en los datos, sino
integrar de manera explícita, consistente y trazable un conocimiento que ya está
disponible. Por eso este trabajo se sitúa deliberadamente en la tradición simbólica, en
la intersección de dos de sus líneas maduras: los sistemas basados en conocimiento
(§ 3.1.2) y el análisis de decisión multicriterio (§ 3.1.3).

### 3.1.2. Sistemas basados en conocimiento y representación del conocimiento

Un **sistema basado en conocimiento** separa aquello que el sistema sabe —la base de
conocimiento— de aquello que el sistema hace con ese saber —el motor de inferencia—.
Esta separación, que los sistemas expertos de los años setenta y ochenta convirtieron en
arquitectura estándar, tiene una consecuencia práctica decisiva: el conocimiento del
dominio puede modificarse sin reescribir el programa, y puede ser revisado por un experto
que no sea programador.

El caso canónico es MYCIN, el sistema de diagnóstico de infecciones bacterianas
desarrollado en Stanford y documentado exhaustivamente por Buchanan y Shortliffe (1984).
MYCIN es pertinente para este trabajo por tres rasgos que comparte con él. Primero, opera
en un dominio donde el error tiene consecuencias sobre la vida, lo que impone que cada
conclusión sea justificable. Segundo, representa el conocimiento como **reglas de
producción** de la forma `SI condición ENTONCES conclusión`, directamente legibles por el
especialista del dominio. Tercero, y quizá lo más importante, sus autores comprendieron
que en medicina un sistema que acierta pero no explica es inutilizable, porque el médico
—no el sistema— conserva la responsabilidad del acto. La misma estructura de
responsabilidad rige aquí: el piloto al mando decide, y para decidir necesita entender.

Davis, Shrobe y Szolovits (1993) precisaron qué es, técnicamente, una representación del
conocimiento, al distinguir cinco papeles que cumple simultáneamente: es un *sustituto*
del objeto del mundo sobre el que se razona; es un *compromiso ontológico* acerca de qué
entidades y propiedades del mundo se consideran existentes y relevantes; es una *teoría
fragmentaria del razonamiento inteligente*; es un *medio de cómputo eficiente*; y es un
*medio de expresión humana*. Este último papel es el que la práctica suele descuidar y el
que aquí resulta central: la representación es también el lenguaje en que las personas
se comunican lo que saben del dominio.

En el sistema desarrollado, el compromiso ontológico se materializa en un contrato de
datos único —una estructura tipada que describe el estado meteorológico de un punto y un
momento, con campos, unidades y semántica fijos— al que se traducen todas las fuentes,
sean observaciones reales o pronósticos numéricos. Una vez adoptado ese vocabulario, la
regulación se vuelve expresable como predicados sobre él, y el motor de inferencia puede
evaluarla de manera uniforme. Las reglas categóricas de rechazo inmediato (*hard
blockers*) son, literalmente, reglas de producción en el sentido de MYCIN.

### 3.1.3. Análisis de decisión multicriterio: modelos compensatorios y no compensatorios

Codificar la norma no basta. Un vuelo puede satisfacer todos los mínimos escritos y
seguir siendo imprudente por la acumulación de factores que, individualmente, no llegan
a inhabilitarlo. Esa es la porción semi-estructurada del problema descrita en el § 1.1.4,
y es el terreno del **análisis de decisión multicriterio** (MCDM/MCDA).

Roy (1996) propuso para esta disciplina una distinción que aquí se adopta como principio
rector: la diferencia entre *decidir* y *ayudar a decidir* (*aide à la décision*). El
analista no produce la decisión óptima —que en presencia de criterios inconmensurables a
menudo no existe—, sino que construye un modelo que permite al decisor comprender su
propio problema, explicitar sus preferencias y sostener su elección. Belton y Stewart
(2002) sistematizan las familias de métodos disponibles y subrayan que el valor del
modelo reside tanto en el proceso de estructuración como en el número que produce.

Dentro de esa disciplina, la distinción metodológica que gobierna el diseño de este
sistema es la que existe entre modelos compensatorios y no compensatorios.

Un **modelo compensatorio** admite compensaciones entre criterios: un mal desempeño en
uno puede ser contrarrestado por un buen desempeño en otro. Su forma más difundida es la
suma ponderada, cuyo fundamento axiomático desarrollaron Keeney y Raiffa (1976): dado un
conjunto de criterios con pesos que suman la unidad y una función de valor por criterio,
el valor global es la suma ponderada de los valores parciales. Es un modelo transparente,
estable y fácil de comunicar, y captura correctamente la idea de que el riesgo de un
vuelo crece de manera gradual y acumulativa.

Un **modelo no compensatorio** rechaza esas compensaciones: si un criterio cae por debajo
de un umbral, ninguna virtud en el resto lo redime. Einhorn (1970) formalizó esta familia
y mostró que describe adecuadamente numerosos juicios humanos reales, en particular los
de selección y descarte. Sus dos formas clásicas son el modelo **conjuntivo** —una
alternativa es aceptable solo si supera un mínimo en *todos* los criterios— y el
**lexicográfico** —los criterios se examinan en orden de importancia y el primero que
discrimina resuelve—.

La decisión de despacho tiene, empíricamente, ambas naturalezas a la vez. Un techo de
1 200 pies con visibilidad de 6 kilómetros y viento moderado es un caso compensatorio:
ningún factor es prohibitivo y lo que importa es su acumulación. Un viento cruzado
superior al máximo demostrado de la aeronave es un caso no compensatorio: un cielo
despejado no lo compensa, porque el límite no es meteorológico sino de la aeronave y del
piloto. Un modelo puramente compensatorio diluye este segundo caso; uno puramente
conjuntivo pierde toda sensibilidad al primero. La arquitectura híbrida que se deriva de
esta constatación se justifica en el § 3.2.4.

### 3.1.4. El Proceso Analítico Jerárquico (AHP)

Adoptado el modelo compensatorio como una de las capas, queda pendiente el problema de
dónde salen los pesos. Fijarlos por apreciación directa —"la visibilidad pesa 0,30 porque
me parece razonable"— haría del modelo un artefacto arbitrario, imposible de defender y
de auditar.

El **Proceso Analítico Jerárquico**, formulado por Saaty (1980, 1990), resuelve ese
problema convirtiendo la asignación de pesos en un procedimiento estructurado y
verificable. Consta de cuatro pasos.

1. **Descomposición jerárquica.** El objetivo se descompone en criterios y, si
   corresponde, en subcriterios. La jerarquía evita comparar criterios heterogéneos entre
   sí y reduce drásticamente el número de juicios necesarios.
2. **Comparaciones de a pares.** Para cada nivel se construye una matriz recíproca
   *A = [a<sub>ij</sub>]* donde *a<sub>ij</sub>* expresa cuántas veces el criterio *i* es
   más importante que el *j*, sobre la escala fundamental de Saaty (1 = igual importancia,
   3 = moderadamente más, 5 = fuertemente más, 7 = muy fuertemente más, 9 = extremadamente
   más, con los valores pares como intermedios), y se cumple *a<sub>ji</sub>* = 1/*a<sub>ij</sub>*.
   Comparar de a dos es cognitivamente mucho más fiable que puntuar en abstracto.
3. **Derivación del vector de prioridades.** Los pesos se obtienen del autovector
   principal de la matriz, aproximado en la práctica por la media geométrica normalizada
   de las filas.
4. **Verificación de la consistencia.** Este es el aporte distintivo del método. Si los
   juicios fueran perfectamente coherentes, el autovalor principal *λ<sub>máx</sub>*
   igualaría el orden *n* de la matriz. La desviación mide la incoherencia mediante el
   índice de consistencia *CI* = (*λ<sub>máx</sub>* − *n*)/(*n* − 1), que se normaliza
   contra el índice aleatorio *RI* —el *CI* esperado de matrices generadas al azar del
   mismo orden— para obtener la razón de consistencia *CR* = *CI*/*RI*. Saaty establece
   como aceptable *CR* ≤ 0,10.

Ese último paso es lo que separa al AHP de una asignación directa disfrazada: el método
no garantiza que los juicios sean correctos, pero detecta cuándo son mutuamente
contradictorios y obliga a revisarlos. El resultado es una ponderación que puede ser
discutida juicio por juicio, reproducida y —si el experto disiente— recalculada.

En este trabajo, los siete criterios del modelo de riesgo se organizaron en tres grupos
según la naturaleza de lo que miden —referencia visual, viento, y fenómenos y tendencia—,
lo que redujo las comparaciones necesarias de veintiuna a ocho.

### 3.1.4.1. Traducción de la evidencia a la escala de Saaty

Queda por resolver el punto más delicado del método, y el que con mayor frecuencia se
despacha con una afirmación genérica: de dónde salen los valores concretos de la escala.
Decir que los juicios "se fundamentan en la accidentología" no constituye un
procedimiento; sin una operación explícita que convierta el dato estadístico en el valor
discreto de la matriz, el AHP se reduce a una asignación subjetiva revestida de aparato
matemático. Este trabajo adopta, por lo tanto, un protocolo formal.

**Paso 1 — Índice de riesgo por criterio.** Se adopta el índice de riesgo del Manual de
gestión de la seguridad operacional de la OACI (2018a): el riesgo de una categoría de
suceso es el producto de la probabilidad de ocurrencia por la severidad de la
consecuencia. Aplicado a datos de accidentología, el índice se simplifica:

> *I* = (*n<sub>cat</sub>* / *N*) × (*n<sub>fatales</sub>* / *n<sub>cat</sub>*) = *n<sub>fatales</sub>* / *N*

es decir, la participación de la categoría en el total de accidentes. La elección no es
una construcción propia: es la misma operacionalización que aplica la Junta de Seguridad
en el Transporte (2021) en su anuario estadístico para ordenar por riesgo las categorías
de suceso de la taxonomía OACI.

**Paso 2 — La operación de traducción.** La escala de Saaty es una escala de *razón*: la
entrada *a<sub>ij</sub>* debe aproximar el cociente *w<sub>i</sub>*/*w<sub>j</sub>*. Por
consiguiente, disponiendo de un índice por criterio, el cociente de índices no es una
*interpretación* de la evidencia sino la entrada correcta de la matriz. La operación es:

> *a<sub>ij</sub>* = redondeo<sub>Saaty</sub>( *I<sub>i</sub>* / *I<sub>j</sub>* )

donde el redondeo lleva el cociente al valor más próximo del conjunto {1, …, 9} y sus
recíprocos.

**Paso 3 — Aplicación.** Sobre la serie decenal 2007-2016 de aviación general no
comercial de ala fija (AOPA Air Safety Institute, 2019), la categoría meteorológica
registra 345 accidentes fatales sobre 473 (severidad 72,9 %) y la categoría de aterrizaje
—donde se manifiesta el viento cruzado, con la pérdida de control direccional como causa
principal— registra 51 sobre 3410 (severidad 1,5 %). De allí:

> *a*(referencia visual, viento) = redondeo<sub>Saaty</sub>(345 / 51) = redondeo<sub>Saaty</sub>(6,76) = **7**

**Paso 4 — Declaración de procedencia.** No todas las comparaciones admiten anclaje
empírico, y presentarlas como si lo tuvieran sería tan objetable como no tener
procedimiento alguno. Cada entrada de la jerarquía declara su origen:

| Comparación | Valor | Procedencia | Fundamento |
|---|---|---|---|
| Ref. visual vs. Viento | 7 | **Evidencia** | Cociente de índices de accidentología (paso 3). |
| Viento vs. Fenómenos/tendencia | 3 | **Norma / arquitectura** | Los fenómenos peligrosos no llegan al puntaje: los extrae la capa categórica (§ 3.2.4). Lo que resta en el grupo es el residuo no inhabilitante más la tendencia pronosticada, secundario por construcción. No existe categoría de accidentología para ese residuo. |
| Ref. visual vs. Fenómenos/tendencia | 9 | **Derivada** | Por transitividad correspondería 7 × 3 = 21, fuera del rango representable; se trunca al techo de la escala. |
| Visibilidad vs. Techo | 1 | **Norma** | La regulación los exige conjuntamente, sin jerarquía entre ellos. |
| Visibilidad vs. Niebla | 5 | **Juicio** | La visibilidad es una magnitud medida; la niebla entra como estimación indirecta derivada del spread térmico, con incertidumbre propia. |
| Cruzado vs. Ráfaga | 2 | **Juicio** | El cruzado tiene límite publicado por aeronave; la ráfaga agrava pero no define la maniobra. La accidentología disponible no separa ambas causas. |
| Fenómenos vs. Tendencia | 2 | **Juicio epistémico** | La condición observada es más cierta que la pronosticada. No compara peligrosidad sino confiabilidad del dato. |

**Paso 5 — La consistencia como medida de la truncación.** La razón de consistencia
resultante es *CR* = 0,069, dentro del límite admisible. Su interpretación aquí es
precisa: si las tres entradas del nivel superior fueran mutuamente transitivas el *CR*
sería nulo, de modo que el valor obtenido mide exactamente la distorsión introducida al
truncar 21 al techo de 9. El indicador no valida los juicios; cuantifica su coherencia
interna, que es lo que el método promete.

**Paso 6 — Acotación del residuo subjetivo.** Las entradas declaradas como juicio no
desaparecen por estar declaradas. Su efecto sobre el resultado se acota empíricamente
mediante el análisis de sensibilidad (§ 5.1): si el veredicto del sistema permanece
estable ante perturbaciones de los pesos, la precisión de cada juicio individual deja de
ser crítica para las conclusiones.

**Convergencia entre dos derivaciones independientes.** El protocolo descrito sustituyó a
una ponderación anterior fijada por juicio experto directo, que asignaba a la comparación
entre referencia visual y viento el valor 3 en lugar de 7. La sustitución modificó los
siete pesos de manera sustantiva —el del viento cruzado descendió de 0,179 a 0,099— y
obligó a recalibrar los umbrales de decisión, cuyo óptimo se desplazó de (0,22 · 0,50) a
(0,22 · 0,59). El resultado decisivo es que, tras esa recalibración, **el sistema emite
exactamente el mismo veredicto en los treinta y seis escenarios de la batería de
referencia**: la misma concordancia del 97 %, los mismos cero sub-avisos y el mismo único
desacuerdo, que es un sobre-aviso. El umbral inferior ni siquiera se movió.

Que dos derivaciones independientes de los pesos —una por juicio experto, otra anclada en
accidentología— produzcan idéntico comportamiento decisional constituye una validación
convergente del modelo, y es un resultado más robusto que cualquiera de las dos
ponderaciones tomada por separado: muestra que las conclusiones del sistema no dependen
de la ponderación exacta. Se reporta en detalle en el § 5.1.

### 3.1.4.2. Un hallazgo del procedimiento: cuándo un criterio deja de ser conmensurable

La aplicación del protocolo produjo un resultado que no estaba previsto y que ilumina la
arquitectura del modelo. Si el índice se construye con la severidad sola —72,9 % frente a
1,5 %— el cociente asciende a aproximadamente 49, casi seis veces el techo de la escala.

Que la evidencia exceda el rango representable no es un defecto del dato. El techo de 9
de la escala de Saaty no es una convención arbitraria: expresa el límite más allá del
cual dos criterios dejan de ser comparables en una misma razón y, por lo tanto, dejan de
ser legítimamente compensables entre sí. La imposibilidad de representar el cociente es
la señal formal de que el viento cruzado no debe gestionarse por su peso dentro de la
suma ponderada.

Y es exactamente lo que hace la arquitectura descrita en el § 3.2.4: el viento cruzado se
trata en la barrera no compensatoria, que impone un piso de veredicto cuando supera los
límites de la aeronave. Su peso en el puntaje —0,099— carga únicamente su contribución
residual dentro de la banda admisible; la barrera carga el resto. La justificación de esa
capa, que en el diseño original era conceptual, queda así respaldada cuantitativamente
por el propio procedimiento de ponderación.

### 3.1.4.3. Limitaciones declaradas de la evidencia utilizada

Tres precisiones que corresponde consignar antes de que el lector las plantee.

La primera es de **origen de los datos**. La serie cuantitativa proviene de accidentología
estadounidense, mientras que el marco normativo del trabajo es argentino. Se recurrió a
ella porque la fuente nacional —el anuario estadístico de la Junta de Seguridad en el
Transporte (2021)— registra volúmenes demasiado reducidos para un cociente estable:
veintisiete accidentes de aviación general en 2020, cuatro de ellos fatales. La fuente
nacional se emplea, en cambio, para **corroborar la dirección y el orden de magnitud**, y
lo hace de manera inequívoca: clasifica la categoría UIMC —vuelo no intencionado en
condiciones meteorológicas instrumentales— con severidad del 100 %, y las categorías de
excursión de pista, pérdida de control en tierra y contacto anormal con la pista como de
"elevada probabilidad y baja severidad". Ambas fuentes coinciden; solo una tiene masa
estadística suficiente para producir el número.

La segunda es de **ventana temporal**. Se emplea una serie de diez años y no un ejercicio
aislado porque el cociente resulta muy sensible al ruido interanual: en 2016 la letalidad
de la categoría meteorológica descendió al 52 % frente a un promedio decenal del 73 %, lo
que habría alterado el valor de la escala. La elección de la ventana es, en sí misma, un
parámetro del procedimiento y se declara como tal.

La tercera es de **alcance del índice**. El índice mide el daño agregado que cada familia
de peligros produce en la población de accidentes, no la capacidad discriminante del
criterio en un vuelo concreto. Se adopta como operacionalización de la importancia
relativa —es la que emplea la propia normativa de gestión de seguridad operacional— pero
la equivalencia entre ambas magnitudes es un supuesto del modelo, no un hecho
demostrado.

### 3.1.5. Decisión bajo incertidumbre y costo asimétrico del error

Un modelo de riesgo produce un número continuo; una decisión operativa exige una
categoría. El paso de uno a otro se hace por umbrales, y la elección de esos umbrales no
es un detalle de implementación sino una decisión de política de seguridad.

La **teoría de detección de señales** (Green y Swets, 1966) ofrece el marco conceptual
preciso. En toda tarea de detección bajo ruido se distinguen la sensibilidad del
observador —su capacidad de discriminar señal de ruido, propiedad del sistema— y el
criterio de respuesta —el punto de corte a partir del cual declara "señal presente",
propiedad de la política—. El aporte central de la teoría es que ese criterio es
desplazable y que su ubicación óptima depende de los costos relativos de los dos errores
posibles: la falsa alarma y la omisión.

En la decisión de despacho esos costos son groseramente asimétricos. Una falsa alarma
—advertir sobre un vuelo que habría sido seguro— cuesta un vuelo postergado. Una omisión
—no advertir sobre un vuelo peligroso— puede costar la aeronave y sus ocupantes. La
teoría indica entonces que el criterio debe desplazarse deliberadamente hacia el lado
conservador, y provee el fundamento formal de lo que en el § 1.3.1 se enunció como
principio ético: el sistema se inclina hacia la advertencia ante ambigüedad o ausencia de
datos. Esa asimetría se implementa como una función de costo explícita en la calibración
de los umbrales (§ 4.2).

Conviene situar este enfoque respecto del estándar de la industria. La gestión de la
seguridad operacional aeronáutica emplea, siguiendo el Manual de gestión de la seguridad
operacional de la OACI (2018), una matriz de riesgo que cruza la severidad de un evento
con su probabilidad y clasifica el resultado en tolerable, tolerable con mitigación o
intolerable. Ese instrumento es adecuado para evaluar peligros *genéricos* de una
organización, pero no para clasificar un *vuelo concreto* en un momento concreto: opera
sobre categorías cualitativas gruesas y no admite el detalle continuo de las variables
meteorológicas. El modelo aquí desarrollado puede leerse como una especialización de esa
lógica al caso de la decisión individual: conserva su sesgo conservador y su carácter de
clasificación en bandas, pero sustituye las categorías cualitativas por funciones de
riesgo continuas y ponderadas.

### 3.1.6. Explicabilidad como requisito del dominio

En el § 1.3.1 se afirmó que el dominio exige explicabilidad total del veredicto. Conviene
precisar el sentido técnico de esa exigencia, porque el término admite dos lecturas muy
distintas.

Rudin (2019) las separa con nitidez. Un modelo **explicado** es un modelo opaco al que se
le adosa, a posteriori, un segundo modelo que intenta aproximar sus razones. Un modelo
**interpretable** es aquel cuya estructura es en sí misma comprensible, de modo que su
razonamiento no necesita ser reconstruido. Su argumento —dirigido específicamente a las
decisiones de alto riesgo— es que las explicaciones a posteriori son, por construcción,
aproximaciones fieles al modelo solo en promedio, y que precisamente en los casos
atípicos, que son los que importan en seguridad, es donde más divergen. De allí su tesis:
en decisiones de alto riesgo debe usarse un modelo interpretable, no un modelo opaco con
una explicación adosada.

Para este sistema la exigencia es doble. Es operativa, porque un veredicto sin razones no
puede ser evaluado por el piloto, que conserva la autoridad y debe poder disentir de
manera informada: "NO GO" sin más es una orden, mientras que "NO GO por viento cruzado de
16 nudos sobre un máximo demostrado de 12" es información sobre la que se puede razonar.
Y es metodológica, porque la validación del sistema (§ 5) se realiza contra el criterio
normativo, lo que requiere poder inspeccionar *por qué* el sistema decidió lo que decidió
en cada escenario, no solo *qué* decidió.

Un modelo construido con reglas explícitas y una suma ponderada de funciones de riesgo
conocidas satisface la definición de modelo interpretable de manera nativa: la
contribución de cada factor al puntaje total es un producto identificable, y por lo tanto
el sistema puede informar el factor dominante y el factor limitante de cada veredicto sin
maquinaria adicional.

### 3.1.7. Fundamentos meteorológicos y de ciencia de datos aplicados

**Observación y pronóstico aeronáuticos.** La información meteorológica aeronáutica está
normalizada internacionalmente por el Anexo 3 al Convenio de Chicago (OACI, 2018b). El
**METAR** es un informe de observación de superficie, emitido con periodicidad regular
—típicamente horaria o semihoraria— por una estación situada en el aeródromo, que codifica
viento, visibilidad, fenómenos presentes, nubosidad por capas, temperatura, punto de rocío
y presión. El **TAF** es un pronóstico de aeródromo que describe la evolución esperada
durante un período de validez, con grupos de cambio (`BECMG`, `TEMPO`, `PROB`) que
califican la naturaleza y la probabilidad de cada variación. Ambos son productos de
estación: existen únicamente donde hay estación meteorológica aeronáutica, lo que en la
Argentina significa un subconjunto reducido de aeródromos concentrado en los centros de
mayor tráfico.

**Predicción numérica del tiempo (NWP).** Para los aeródromos sin estación, la
alternativa es el modelo numérico. La predicción numérica del tiempo resuelve las
ecuaciones de la dinámica atmosférica sobre una malla tridimensional a partir de un
estado inicial construido por asimilación de observaciones. Bauer, Thorpe y Brunet (2015)
documentan la mejora sostenida de su exactitud a lo largo de cuatro décadas —de
aproximadamente un día de horizonte útil ganado por década— hasta el punto de calificarla
de "revolución silenciosa". Su propiedad decisiva para este proyecto es distinta de la
exactitud: a diferencia del METAR, el modelo numérico entrega una estimación para
*cualquier coordenada*, y no solo donde hay instrumental. Esto es lo que hace
técnicamente posible la cobertura de la totalidad de los aeródromos del registro
nacional. Su contrapartida —que se trata de una estimación y no de una observación— es
una limitación que el sistema informa explícitamente al usuario.

Existe, sin embargo, una segunda limitación que no se resuelve informándola, y que resulta
crítica para el caso argentino. Para resolver numéricamente la atmósfera, el modelo
discretiza el terreno en celdas —del orden de diez kilómetros en los modelos globales— y
en ese proceso **suaviza la orografía**: la celda que contiene un aeródromo de sierra
promedia el valle y la ladera en una única superficie. Consultar un solo punto es, en
consecuencia, obtener ese promedio, que no describe ni el fondo del valle —donde se forma
la niebla nocturna— ni la ladera —donde se apoya la nubosidad orográfica—. El efecto se
propaga directamente al criterio de mayor peso del modelo: un sesgo en la temperatura o en
el punto de rocío altera el spread y, por la relación del § 3.1.7, la base de nubes
estimada.

La respuesta adoptada no consiste en corregir el modelo —no se dispone de datos con qué
calibrar tal corrección— sino en **muestrearlo**: el sistema consulta el pronóstico en el
aeródromo y en un anillo de puntos de su entorno, y adopta la peor condición del conjunto.
El fundamento de diseño se desarrolla en el § 3.2.5.

**Estimación de la base de nubes.** Los modelos numéricos entregan la nubosidad como
fracción de cobertura por niveles, no como altura de la base en pies sobre el terreno, que
es la magnitud que la regulación VFR utiliza. La conversión se apoya en un resultado
clásico de la termodinámica atmosférica: la base de la nubosidad convectiva coincide
aproximadamente con el nivel de condensación por ascenso (LCL), y ese nivel es
proporcional a la diferencia entre la temperatura y el punto de rocío en superficie. La
primera formulación de esta relación se debe a Espy en 1836, y su forma práctica —del
orden de 125 metros, o unos 400 pies, por cada grado Celsius de diferencia— sigue siendo
la regla de estimación en uso, con expresiones exactas modernas disponibles para el mismo
nivel (Romps, 2017). La consecuencia operativa es directa y correcta: con aire saturado la
diferencia tiende a cero y la nubosidad se sitúa al ras del suelo, que es exactamente la
situación de niebla que el sistema debe detectar.

**Terreno.** El perfil de elevación a lo largo de la ruta se construye sobre el modelo
digital de elevación de la Shuttle Radar Topography Mission (Farr et al., 2007), obtenido
por interferometría radar durante la misión del transbordador espacial del año 2000 y
disponible con resolución de aproximadamente 30 metros. Es la base cartográfica estándar
para el análisis de relieve a escala regional.

**Ciencia de datos: integración de fuentes heterogéneas.** El trabajo de ciencia de datos
del proyecto no consiste en entrenar un modelo sino en la etapa que en cualquier proyecto
de datos consume la mayor parte del esfuerzo: la adquisición, integración y normalización
de fuentes que no fueron diseñadas para ser combinadas. Las cinco fuentes utilizadas
difieren en protocolo, en formato, en unidades y en disponibilidad —texto codificado
según norma OACI, respuestas JSON, páginas HTML sin interfaz programática, archivos
tabulares y colecciones geoespaciales—, y ninguna de ellas garantiza estar disponible en
el momento de la consulta. La estrategia adoptada es la clásica del patrón *extracción,
transformación y carga*: cada fuente se encapsula en un adaptador propio, responsable de
traducirla al contrato de datos único descrito en el § 3.1.2, de modo que las capas
superiores del sistema son indiferentes al origen del dato. A ello se suman las
salvaguardas habituales de todo consumo de servicios externos —reintentos con espera
incremental, degradación explícita ante ausencia de datos y almacenamiento temporal con
vencimiento diferenciado según la volatilidad de cada tipo de dato— que se detallan en el
§ 4.

### 3.1.8. Búsqueda en grafos para la planificación de ruta

La planificación de una navegación entre dos aeródromos distantes, con escalas
intermedias posibles y aerovías publicadas, es un problema de camino mínimo sobre un
grafo. Se emplean dos algoritmos clásicos.

El algoritmo de **Dijkstra** (1959) determina el camino de costo mínimo desde un origen a
todos los demás nodos de un grafo con pesos no negativos, expandiendo siempre el nodo
pendiente de menor costo acumulado. Es el algoritmo adecuado cuando el grafo es la red de
aerovías publicadas, cuyo trazado es fijo y cuyos nodos son puntos de notificación
conocidos.

El algoritmo **A\*** (Hart, Nilsson y Raphael, 1968) generaliza al anterior incorporando
una función heurística *h(n)* que estima el costo restante desde cada nodo hasta el
destino, y ordenando la expansión por *f(n) = g(n) + h(n)*. Su propiedad fundamental es
que si la heurística es **admisible** —nunca sobrestima el costo real restante—, el camino
encontrado es óptimo, con una exploración típicamente muy inferior a la de Dijkstra. En
este trabajo la heurística es la distancia de círculo máximo (fórmula del haversine) al
destino, que es admisible por construcción: ninguna ruta real entre dos puntos de la
esfera puede ser más corta que el arco que los une. La justificación de por qué se aplica
cada uno a cada subproblema se desarrolla en el § 3.2.6.

---

## 3.2. Modelos y arquitecturas

Esta sección justifica las elecciones de diseño. Donde la plantilla pregunta por qué se
eligió una arquitectura de red neuronal en lugar de otra, aquí se responde por qué se
eligió un motor de reglas con ponderación multicriterio en lugar de un modelo aprendido,
qué método de ponderación se adoptó y por qué, y cómo se organiza el modelo de riesgo.

### 3.2.1. Antecedentes directos: las herramientas de evaluación de riesgo de vuelo

El instrumento más cercano al que aquí se propone no proviene de la informática sino de
la propia comunidad de seguridad aeronáutica: las **herramientas de evaluación de riesgo
de vuelo** (*Flight Risk Assessment Tool*, FRAT), promovidas para la aviación general por
el FAA Safety Team y por el General Aviation Joint Steering Committee (FAA Safety Team,
s.f.). Un FRAT es un formulario que el piloto completa antes del vuelo, en el que un
conjunto de factores —tipo de operación, entorno, aeronave, entrenamiento y experiencia
reciente de la tripulación— recibe una puntuación; la suma se clasifica en tres bandas de
riesgo: verde (bajo), amarillo (medio) y rojo (alto).

La correspondencia conceptual con el presente trabajo es directa y merece reconocerse
explícitamente: la estructura de puntuación ponderada de factores y clasificación en tres
bandas para asistir una decisión de ir o no ir es, en esencia, la del FRAT. Es un
antecedente validado por años de uso operacional, lo que respalda la forma general de la
solución. Corresponde aclarar que se lo invoca como antecedente conceptual y no como
referencia normativa: su marco regulatorio es el estadounidense, y el criterio normativo
de este trabajo es el argentino (ANAC/OACI), según se estableció en el § 1.1.2.

Sobre esa base común, el sistema desarrollado incorpora cinco diferencias sustantivas,
que constituyen su aporte:

| Dimensión | FRAT convencional | Sistema desarrollado |
|---|---|---|
| **Obtención de los datos** | El piloto los ingresa manualmente; la evaluación depende de que haya consultado y comprendido correctamente la información meteorológica cruda. | Los datos meteorológicos y aeronáuticos se obtienen automáticamente de las fuentes oficiales para el vuelo planificado. |
| **Origen de los pesos** | Asignados por criterio de la organización que publica el formulario; no se documenta su derivación. | Derivados por AHP con verificación de consistencia, reproducibles y auditables. |
| **Modelo de agregación** | Suma puramente compensatoria: un factor grave puede quedar diluido por el conjunto. | Arquitectura de tres capas con barrera no compensatoria (§ 3.2.4). |
| **Especificidad de la aeronave** | Genérico o, a lo sumo, por categoría amplia. | Los límites se derivan del perfil de la aeronave seleccionada (viento cruzado y ráfaga máximos, velocidad de crucero, altitud de crucero). |
| **Umbrales de banda** | Fijados por convención. | Calibrados por anclaje normativo con función de costo asimétrica y sometidos a análisis de sensibilidad (§ 4.2 y § 5.1). |

### 3.2.2. Motor de reglas frente a aprendizaje automático

Esta es la elección arquitectónica de fondo del trabajo, anticipada en el § 1.3.1 y
desarrollada aquí. La comparación se organiza sobre las dimensiones que resultan
decisivas en un dominio de seguridad crítica.

| Criterio | Aprendizaje automático supervisado | Motor de reglas + MCDM |
|---|---|---|
| **Insumo requerido** | Un conjunto de casos etiquetados: pares (condiciones, decisión correcta) validados, en cantidad y variedad suficientes. | El conocimiento del dominio, ya formalizado en la regulación, los manuales de vuelo y la accidentología. |
| **Disponibilidad de ese insumo** | No existe. No hay un registro de decisiones de despacho de aviación general argentina con su resultado validado. | Disponible y públicamente verificable. |
| **Explicabilidad** | Requiere explicación a posteriori, fiel solo en promedio y menos confiable justamente en los casos atípicos (Rudin, 2019). | Interpretable de manera nativa: cada contribución al puntaje es identificable. |
| **Comportamiento fuera de distribución** | Degradación silenciosa: ante una combinación no representada en el entrenamiento produce una salida confiada sin señalar su propia ignorancia. | Comportamiento definido: si falta un dato, la regla correspondiente lo declara y el sistema se inclina al lado conservador. |
| **Actualización ante cambio normativo** | Exige reetiquetar y reentrenar. | Se edita la constante o la regla afectada. |
| **Validación** | Se valida contra un conjunto de prueba, que hereda los sesgos del conjunto de entrenamiento. | Se valida contra la norma escrita, que es el criterio de referencia externo e independiente. |
| **Trazabilidad para la defensa académica** | El modelo es reproducible pero sus razones no son inspeccionables juicio por juicio. | Cada peso, umbral y regla es inspeccionable y discutible individualmente. |

La ausencia de datos etiquetados merece una precisión, porque es el punto donde un
tribunal podría objetar con razón. No es que no existan datos meteorológicos históricos:
existen y son abundantes. Lo que no existe es la **etiqueta**: el juicio validado de si un
vuelo determinado, bajo determinadas condiciones, *debió* o *no debió* despegar. Los
registros de accidentes proveen únicamente la clase positiva y solo en su forma más
extrema —los vuelos que terminaron mal—, sin ninguna muestra de los vuelos que fueron
correctamente cancelados, que son la contraparte necesaria. Entrenar sobre esa base
produciría un modelo severamente sesgado por selección. Marcus (2020) sostiene, en línea
con esto, que en dominios donde ya existe conocimiento estructurado los enfoques
puramente inductivos desaprovechan la información disponible y resultan frágiles fuera de
la distribución de entrenamiento.

Corresponde señalar el límite honesto de esta posición. La elección no afirma que el
aprendizaje automático sea inaplicable al dominio meteorológico —de hecho, los propios
modelos numéricos de pronóstico que el sistema consume incorporan hoy componentes
aprendidos—, sino que no es aplicable *a esta tarea concreta*, la de emitir un veredicto
normativo trazable, *en el estado actual de disponibilidad de datos*. Si el sistema
llegara a operar de manera sostenida y a registrar decisiones de pilotos junto con sus
resultados, se constituiría con el tiempo el conjunto etiquetado que hoy falta; esa vía
se contempla como trabajo futuro en el § 6.3.

### 3.2.3. Elección del método de ponderación: AHP frente a las alternativas

Establecido que hay una capa compensatoria, resta justificar el método por el cual se
derivan sus pesos. Se consideraron cinco familias.

**Asignación directa por juicio experto.** Es la más simple y la más frágil: el experto
declara los pesos sin procedimiento intermedio. No ofrece ningún control sobre la
coherencia interna de la asignación ni deja rastro de cómo se llegó a ella. Se descartó
por no ser auditable.

**Método Delphi.** Consiste en iterar consultas anónimas a un panel de expertos hasta que
las opiniones convergen. Es metodológicamente sólido y sería la vía natural para
consolidar el criterio de varios instructores, pero requiere un panel comprometido a lo
largo de varias rondas, lo que excede los recursos de este trabajo. Se registra como una
línea de mejora posible: los pesos derivados por AHP podrían ser sometidos a validación
Delphi en un desarrollo posterior.

**Métodos objetivos basados en datos (entropía, CRITIC).** Derivan los pesos de la
dispersión estadística de los criterios en una matriz de alternativas. Son inaplicables
aquí por dos razones. La primera es práctica: no existe la matriz de alternativas que
requieren. La segunda es conceptual y más importante: estos métodos asignan más peso al
criterio que más varía, y la variabilidad de un factor meteorológico no guarda relación
con su peligrosidad. La visibilidad podría ser constante durante meses en una región
árida y seguir siendo el factor más letal.

**Métodos de superación (ELECTRE, PROMETHEE) y de distancia al ideal (TOPSIS).** Con
frecuencia se los presenta como alternativas al AHP, pero la comparación está mal
planteada en dos sentidos. Primero, no son métodos de *ponderación* sino de *agregación*:
consumen pesos, no los producen, de modo que no resuelven el problema en cuestión.
Segundo, y decisivo, están diseñados para **ordenar un conjunto de alternativas** entre
sí. El problema de este trabajo no es de ordenamiento sino de **clasificación contra
umbrales absolutos**: hay una sola alternativa —el vuelo planificado— y se la debe ubicar
en una de tres bandas de riesgo definidas de antemano. Ordenar vuelos entre sí carecería
de sentido operativo.

**Proceso Analítico Jerárquico.** Se adoptó por cuatro razones. Produce pesos a partir de
juicios cualitativos de a pares, que es la única forma en que el conocimiento del dominio
está efectivamente disponible. Provee una medida explícita de coherencia (*CR*), que
convierte la ponderación en un objeto verificable. Su estructura jerárquica se
corresponde con la organización natural de los criterios en grupos, evitando
comparaciones entre magnitudes inconmensurables. Y es, de las cinco familias, la de mayor
difusión y documentación, lo que facilita que un tercero replique o impugne la derivación.

Corresponde consignar la objeción más conocida al método. Belton y Gear (1983) mostraron
que el AHP puede exhibir **inversión de rango**: al agregarse una alternativa
irrelevante, el orden relativo de las anteriores puede invertirse. La crítica es válida
pero **no aplica a este uso**, precisamente por lo señalado más arriba: aquí el AHP no
ordena alternativas —no hay conjunto de alternativas que ordenar—, sino que deriva un
vector de pesos que luego se aplica a funciones de riesgo con umbrales absolutos. La
subjetividad residual de los juicios de a pares, que sí persiste, se aborda por la vía
del análisis de sensibilidad: si el veredicto del sistema se mantiene estable ante
perturbaciones de los pesos, la precisión de cada juicio individual deja de ser crítica
(§ 5.1).

### 3.2.4. Arquitectura del modelo de riesgo: tres capas

Del § 3.1.3 se sigue que ni un modelo compensatorio ni uno no compensatorio, tomados por
separado, describen adecuadamente la decisión de despacho. La arquitectura adoptada los
combina en tres capas evaluadas en cascada.

**Capa 1 — Reglas de rechazo categórico (*hard blockers*).** Un conjunto acotado de
condiciones que la regulación o el sentido operacional consideran directamente
incompatibles con el vuelo visual: presencia de fenómenos peligrosos —tormenta, granizo,
engelamiento en precipitación, ceniza volcánica, tromba— o valores de visibilidad y techo
por debajo del mínimo absoluto. Es una capa **conjuntiva** en el sentido de Einhorn: su
activación produce el veredicto negativo de inmediato y sin calcular puntaje, porque en
estos casos no hay nada que ponderar. Su justificación es que estas condiciones no
admiten compensación por definición normativa.

**Capa 2 — Puntuación compensatoria.** Si ninguna regla categórica se activó, se calcula
un puntaje de riesgo global como suma ponderada de las funciones de riesgo por factor,
*R* = Σ *w<sub>i</sub>* · *r<sub>i</sub>*, con los pesos derivados por AHP (§ 3.1.4) y
cada *r<sub>i</sub>* normalizada al intervalo [0, 1] mediante rampas lineales entre un
valor sin riesgo y un valor de riesgo máximo. Esta capa captura la acumulación gradual de
condiciones subóptimas, que es el modo en que efectivamente se degrada la seguridad de un
vuelo visual. El puntaje se traduce a banda por dos umbrales.

**Capa 3 — Barrera no compensatoria (piso conjuntivo).** Aquí reside el aporte
arquitectónico central. La capa 2, por ser compensatoria, tiene un defecto estructural
conocido: un factor de peso bajo pero individualmente inhabilitante queda diluido. El
caso testigo es el viento cruzado. Con un peso de 0,099, un viento cruzado exactamente
igual al máximo demostrado de la aeronave —esto es, en el límite de lo que el avión y el
piloto pueden manejar— aporta como máximo 0,099 al puntaje total, muy por debajo del
primer umbral de decisión (0,22); si el resto de las condiciones es favorable, el sistema
emitiría un veredicto favorable para un vuelo que no debe realizarse. Como se mostró en
el § 3.1.4.2, ese peso bajo no es un defecto de la ponderación sino su resultado
correcto: la evidencia indica que ambos criterios no son conmensurables, y por eso el
factor se gestiona en esta capa y no en la anterior.

La barrera corrige esto imponiendo un **piso de veredicto por factor**, independiente del
puntaje: ciertos factores, al superar determinados niveles, fijan un veredicto mínimo que
el resultado global no puede mejorar. El veredicto final es entonces el peor entre el que
surge del puntaje y el que impone el piso:

```
decisión = peor( banda(R) , piso_conjuntivo )
```

Los factores cubiertos por la barrera son aquellos de peso bajo cuyo deterioro es
abrupto: viento cruzado y ráfaga respecto de los límites de la aeronave, probabilidad de
niebla y deterioro pronosticado. La visibilidad y el techo quedan deliberadamente fuera
de ella: tienen peso alto —no se diluyen— y su degradación es gradual, de modo que el
tratamiento compensatorio los describe correctamente y ya están cubiertos en su extremo
por la capa 1.

Esta arquitectura híbrida es la respuesta directa a la constatación del § 3.1.3 de que la
decisión de despacho tiene simultáneamente naturaleza compensatoria y no compensatoria.
Su efecto cuantitativo sobre la concordancia con el criterio normativo se reporta en el
§ 5.1.

### 3.2.5. Arquitectura del sistema: canalización por capas

El sistema se organiza como una canalización de responsabilidades separadas, en la que
cada capa depende únicamente de las inferiores:

```
Datos → Interpretación → Características → Riesgo → Decisión → Ruta → Salida → Web
```

- **Capa de datos.** Un adaptador por fuente externa, responsable del protocolo, los
  reintentos y el almacenamiento temporal. Devuelve estructuras crudas, sin interpretar.
- **Capa de interpretación.** Traduce cada estructura cruda al contrato de datos único.
  Es aquí donde el informe METAR codificado y la serie del modelo numérico convergen en un
  mismo tipo, aplicando el patrón **adaptador**: dos orígenes de naturaleza distinta,
  una sola interfaz de salida.
- **Capa de características.** Deriva las magnitudes que la norma no entrega directamente
  —componente de viento cruzado sobre la pista más favorable, proxy de niebla, altitud
  densidad, disponibilidad de luz diurna, análisis de la ventana temporal del pronóstico—.
- **Capa de riesgo.** Implementa las tres capas del § 3.2.4.
- **Capa de decisión.** Orquesta el flujo y aplica la regla de selección de fuente.
- **Capas de ruta y salida.** Planificación de la navegación y generación de los productos
  para el piloto (informe y borrador de plan de vuelo).

La decisión de diseño más consecuente de esta organización es el **contrato de datos
único**. Al obligar a que toda fuente se traduzca a la misma estructura tipada antes de
llegar al motor de riesgo, se consigue que el modelo de riesgo sea completamente
independiente del origen del dato: las mismas condiciones producen el mismo puntaje
provengan de una observación o de un pronóstico. Esta es una decisión metodológica
deliberada y conviene explicitarla, porque podría objetarse que un pronóstico es menos
confiable que una observación y debería penalizarse. Se resolvió no hacerlo, por dos
razones. La primera es que penalizar la fuente confundiría dos magnitudes distintas —el
riesgo meteorológico y la incertidumbre de la estimación—, degradando la interpretabilidad
del puntaje. La segunda es que la diferencia de confiabilidad es información que el piloto
necesita, pero como *contexto* del veredicto y no como una corrección oculta dentro de un
número: el sistema la informa explícitamente en la interfaz.

La segunda decisión relevante es el **muestreo en anillo del modelo numérico**, respuesta
a la limitación orográfica expuesta en el § 3.1.7. En lugar de consultar el pronóstico en
la coordenada del aeródromo, el sistema lo consulta también en seis puntos equiespaciados
sobre una circunferencia de diez kilómetros a su alrededor —un radio del orden de la celda
del modelo, y la porción de terreno que la aeronave sobrevuela inmediatamente después del
despegue— y adopta la peor condición del conjunto. Las siete consultas viajan en una única
petición, de modo que el costo es despreciable.

El mecanismo se apoya en una propiedad del proveedor: a los puntos del anillo no se les
impone la elevación del aeródromo, con lo cual el servicio aplica su propio descenso de
escala con un modelo digital de elevación de noventa metros y cada punto recibe la altura
real de su ubicación. Un anillo alrededor de un aeródromo de sierra muestrea así alturas
distintas sin necesidad de consultar el terreno en tiempo de ejecución. En el aeródromo de
la organización comitente, situado a 1138 metros, el anillo abarca elevaciones de 732 a
1629 metros: casi novecientos metros de desnivel que la consulta a un único punto promedia
y pierde.

Tres precisiones delimitan el alcance del mecanismo, y las tres importan.

La primera es que **es un tratamiento de la incertidumbre, no una penalización**. No se
suma un término correctivo al puntaje ni se castiga al aeródromo por estar en la sierra:
se amplía el muestreo del modelo y se adopta la lectura más desfavorable de las obtenidas.
La distinción es sustantiva porque una versión anterior del sistema incluía efectivamente
una penalización orográfica fija, que fue eliminada por estar atada a un único aeródromo
y ser, por lo tanto, un parámetro arbitrario sin validez nacional.

La segunda es que **el mecanismo es autorregulado por el terreno**. En llanura los siete
puntos caen sobre la misma masa de aire, la peor condición coincide con la del punto
central y el resultado es idéntico al de la consulta simple: el muestreo no actúa donde no
hace falta. Esto satisface la exigencia de que ninguna solución quede atada a un aeródromo
particular, dado que la intensidad del efecto la determina el relieve y no el identificador
del campo.

La tercera es que **del anillo se toma el estado de la masa de aire y no el viento**.
Visibilidad, techo, humedad y fenómenos describen el aire de la zona y admiten un
tratamiento de peor caso. El viento cruzado, en cambio, se define contra la pista del
aeródromo y contra el máximo demostrado de la aeronave: comparar la ráfaga de un cordón
situado cuatrocientos metros por encima del campo con el límite de viento cruzado del avión
es un error de categoría, porque la aeronave no aterrizará allí. Omitir esta salvedad
produce vetos por viento en días de calma en el aeródromo, comportamiento que se verificó
empíricamente y que habría deteriorado la confianza del piloto en el instrumento. Por la
misma razón, las reglas de rechazo categórico del § 3.2.4 se evalúan únicamente sobre el
aeródromo: son la traducción de una norma que se refiere al aeródromo, y extenderla a un
punto distante sería inventar una regla que la regulación no contiene.

La tercera decisión relevante es la **regla de selección de fuente**: si el aeródromo
posee código OACI se intenta obtener observación y pronóstico de estación, y si no hay
observación disponible se recurre al modelo numérico; si no posee código, se va
directamente al modelo. Esta regla es la que hace operativa la cobertura nacional
completa: garantiza que todo aeródromo del registro sea evaluable, con el mejor dato
disponible en cada caso.

### 3.2.6. Elección de algoritmos para la planificación de ruta

La planificación se descompone en tres subproblemas, cada uno con la estructura de grafo
que le corresponde.

**Ruta entre aeródromos.** El grafo tiene por nodos los aeródromos del registro y por
aristas los tramos factibles, limitados por la autonomía de la aeronave y filtrados por
un rechazo previo de los que se apartan excesivamente del corredor entre origen y destino.
Se emplea **A\*** con heurística de distancia de círculo máximo al destino. La elección
sobre Dijkstra se funda en que aquí sí existe un destino único conocido, lo que permite
dirigir la búsqueda; la heurística es admisible por construcción (§ 3.1.8) y por lo tanto
la optimalidad se conserva.

**Aerovías publicadas.** El grafo es la red de aerovías inferiores del AIP, con sus puntos
de notificación como nodos, filtrada previamente por la altitud mínima de enrutamiento
(MEA) que la aeronave seleccionada puede alcanzar. Se emplea **Dijkstra**, porque el
grafo es pequeño y fijo, y porque el filtrado por MEA hace que la conectividad efectiva
dependa de la aeronave, situación en la que una heurística geométrica pierde valor
orientador.

**Corredores visuales.** En las áreas terminales de Buenos Aires y Córdoba, el tránsito
VFR debe circular por corredores publicados. Se los modela como un grafo propio por
conglomerado y se resuelve nuevamente con Dijkstra, dado que el grafo es reducido y
enteramente determinado por la publicación aeronáutica.

Se descartó explícitamente el uso de metaheurísticas —algoritmos genéticos, colonias de
hormigas y similares—. En un grafo del tamaño del aquí considerado, los algoritmos
exactos encuentran el óptimo en tiempos muy inferiores al segundo; una metaheurística
aportaría no determinismo y pérdida de garantía de optimalidad sin ninguna ventaja
compensatoria. En un sistema cuyo valor principal es la trazabilidad, el no determinismo
es un costo, no una característica.

---

## 3.3. Stack tecnológico

### 3.3.1. Lenguaje y entorno

El sistema está desarrollado íntegramente en **Python 3.12**. La elección se funda en
tres consideraciones: es el lenguaje de referencia de la carrera y del ecosistema de
ciencia de datos; su biblioteca estándar cubre por completo las necesidades de cálculo
del modelo; y su disponibilidad en plataformas de despliegue gratuitas es universal. La
versión se fijó de manera idéntica en el entorno de desarrollo y en el de producción para
eliminar toda divergencia de comportamiento entre ambos.

Una decisión deliberada, y poco habitual en un proyecto de esta carrera, es la **ausencia
de dependencias científicas pesadas**. El modelo de riesgo, la derivación AHP, la
calibración y el análisis de sensibilidad están implementados en aritmética de la
biblioteca estándar, sin NumPy, pandas ni scikit-learn. No se trata de una limitación
sino de una consecuencia coherente del enfoque: el modelo es una suma ponderada de siete
funciones y un conjunto de comparaciones, y no requiere álgebra matricial de alto
rendimiento. Los beneficios son concretos: el conjunto de dependencias de producción se
reduce a cuatro paquetes, la instalación es prácticamente instantánea, el despliegue cabe
holgadamente en el plan gratuito de la plataforma y desaparece toda posibilidad de que un
cambio de versión de una biblioteca de terceros altere silenciosamente un resultado
numérico del que depende una conclusión de la tesis.

### 3.3.2. Componentes

| Capa | Tecnología | Rol y fundamento de la elección |
|---|---|---|
| Lenguaje | Python 3.12 | Idéntico en desarrollo y producción. |
| Servidor de aplicación | **FastAPI** + **Uvicorn** | Marco web asíncrono con validación de esquema y documentación OpenAPI generada automáticamente. Se prefirió sobre Flask por la validación automática de las peticiones y sobre Django por no requerir base de datos ni capa de persistencia: el sistema no almacena estado entre consultas. |
| Cliente HTTP | **requests** | Consumo de las interfaces externas; API sencilla y control explícito de tiempos de espera y reintentos. |
| Formularios | **python-multipart** | Requerido por FastAPI para el envío de formularios. |
| Interfaz | HTML + **Tailwind CSS** | Estilos por clases utilitarias, sin etapa de compilación. |
| Interactividad | **Alpine.js 3.14.1** | Reactividad declarativa embebida en el propio HTML. Se prefirió sobre React o Vue por no requerir empaquetador ni proceso de construcción, para una interfaz de una sola vista. |
| Cartografía | **Leaflet 1.9.4** | Biblioteca de mapas interactivos de código abierto, con capa base de teselas oscuras. Se prefirió sobre alternativas comerciales por no requerir clave de servicio ni imponer cuotas. |
| Pruebas | **pytest** | Suite de regresión ejecutable sin conexión de red. |
| Control de versiones | **Git** / **GitHub** | Repositorio público, enlazado en el Anexo conforme a la consigna. |
| Despliegue | **Render** (plan gratuito) | Despliegue automático a partir del repositorio, definido de manera declarativa en un archivo de configuración versionado. |

### 3.3.3. Fuentes de datos externas

| Fuente | Producto | Protocolo | Rol en el sistema |
|---|---|---|---|
| **aviationweather.gov** | METAR y TAF | JSON sobre HTTP, sin clave | Observación y pronóstico en aeródromos con estación. |
| **Open-Meteo** (Zippenfenig, 2023) | Pronóstico numérico | JSON sobre HTTP, sin clave | Estimación punto a punto por coordenadas para aeródromos sin estación y puntos en ruta. |
| **AIS / ANAC** | NOTAM oficiales argentinos | HTML, extracción por análisis del documento | Estado operativo del aeródromo. Al no existir interfaz programática publicada, se implementó un extractor propio. |
| **MADHEL / ANAC** y **OurAirports** | Registro de aeródromos y pistas | Archivo tabular procesado fuera de línea | Coordenadas, elevación, cabeceras y servicios de los 561 aeródromos. |
| **Open-Topo-Data** (SRTM) | Elevación del terreno | JSON sobre HTTP | Perfil vertical de la ruta y detección de conflicto con el terreno. |

Todas las fuentes en línea son de acceso público y sin costo, condición necesaria para
sostener el objetivo de gratuidad enunciado en el § 1.2.2. Los conjuntos de datos
estáticos —registro de aeródromos, aerovías, espacios aéreos y regiones de información de
vuelo— se procesan una sola vez fuera de línea y se versionan junto con el código, de
modo que el sistema no depende en tiempo de ejecución de la disponibilidad de esos
servicios.

### 3.3.4. Reproducibilidad metodológica

Un rasgo del *stack* que merece consignarse en el marco teórico, por ser consecuencia
directa del enfoque adoptado, es que **los aportes metodológicos del trabajo son código
ejecutable**. La derivación AHP de los pesos, la calibración de los umbrales y el análisis
de sensibilidad no son cálculos realizados aparte y transcritos al documento, sino módulos
que se ejecutan y reproducen sus resultados. No participan del tiempo de ejecución del
motor —los valores que producen están fijados como constantes—, sino que documentan su
procedencia y permiten que cualquier lector los verifique o los recalcule bajo otros
supuestos.

A ello se suma una suite de pruebas de regresión que fija el comportamiento del veredicto
sobre la batería de escenarios de referencia y se ejecuta sin conexión de red. Su función
es metodológica antes que técnica: garantiza que ninguna modificación posterior de los
pesos, los umbrales o la barrera altere las conclusiones reportadas en el § 5 sin que
ello quede en evidencia de inmediato.

---

## Referencias citadas en esta sección

Administración Nacional de Aviación Civil. (2022). *Regulaciones Argentinas de Aviación Civil (RAAC), Parte 91: Reglas de vuelo y operación general*. ANAC. ⬜ *[verificar la enmienda vigente a la fecha de entrega — mismo pendiente que en la § 1]*

Bauer, P., Thorpe, A., & Brunet, G. (2015). The quiet revolution of numerical weather prediction. *Nature, 525*(7567), 47-55. https://doi.org/10.1038/nature14956

Belton, V., & Gear, T. (1983). On a short-coming of Saaty's method of analytic hierarchies. *Omega, 11*(3), 228-230. https://doi.org/10.1016/0305-0483(83)90047-6

Belton, V., & Stewart, T. J. (2002). *Multiple criteria decision analysis: An integrated approach*. Kluwer Academic Publishers. https://doi.org/10.1007/978-1-4615-1495-4

Buchanan, B. G., & Shortliffe, E. H. (Eds.). (1984). *Rule-based expert systems: The MYCIN experiments of the Stanford Heuristic Programming Project*. Addison-Wesley.

Davis, R., Shrobe, H., & Szolovits, P. (1993). What is a knowledge representation? *AI Magazine, 14*(1), 17-33. https://doi.org/10.1609/aimag.v14i1.1029

Dijkstra, E. W. (1959). A note on two problems in connexion with graphs. *Numerische Mathematik, 1*(1), 269-271. https://doi.org/10.1007/BF01386390

Einhorn, H. J. (1970). The use of nonlinear, noncompensatory models in decision making. *Psychological Bulletin, 73*(3), 221-230. https://doi.org/10.1037/h0028695

FAA Safety Team. (s.f.). *Flight Risk Assessment Tool (FRAT)*. Federal Aviation Administration. https://www.faa.gov/general/flight-risk-assessment-tool-frat-faa-safety-team ⬜ *[agregar fecha de consulta]*

AOPA Air Safety Institute. (2019). *28th Joseph T. Nall report: General aviation accidents in 2016*. Aircraft Owners and Pilots Association. (Series decenales tomadas de las figuras 1.1.1 y 1.7.1; valores de 2016 contrastados con la figura 1.11.)

Farr, T. G., Rosen, P. A., Caro, E., Crippen, R., Duren, R., Hensley, S., Kobrick, M., Paller, M., Rodriguez, E., Roth, L., Seal, D., Shaffer, S., Shimada, J., Umland, J., Werner, M., Oskin, M., Burbank, D., & Alsdorf, D. (2007). The Shuttle Radar Topography Mission. *Reviews of Geophysics, 45*(2), RG2004. https://doi.org/10.1029/2005RG000183

Junta de Seguridad en el Transporte. (2021). *Anuario estadístico 2020: Seguridad en el transporte* (Vol. 1, Aeronáutico). Ministerio de Transporte de la Nación Argentina.

Green, D. M., & Swets, J. A. (1966). *Signal detection theory and psychophysics*. John Wiley & Sons.

Hart, P. E., Nilsson, N. J., & Raphael, B. (1968). A formal basis for the heuristic determination of minimum cost paths. *IEEE Transactions on Systems Science and Cybernetics, 4*(2), 100-107. https://doi.org/10.1109/TSSC.1968.300136

Keeney, R. L., & Raiffa, H. (1976). *Decisions with multiple objectives: Preferences and value tradeoffs*. John Wiley & Sons.

Marcus, G. (2020). *The next decade in AI: Four steps towards robust artificial intelligence*. arXiv. https://arxiv.org/abs/2002.06177

Newell, A., & Simon, H. A. (1976). Computer science as empirical inquiry: Symbols and search. *Communications of the ACM, 19*(3), 113-126. https://doi.org/10.1145/360018.360022

Organización de Aviación Civil Internacional. (2018a). *Manual de gestión de la seguridad operacional* (Doc 9859, 4.ª ed.). OACI.

Organización de Aviación Civil Internacional. (2018b). *Anexo 3 al Convenio sobre Aviación Civil Internacional: Servicio meteorológico para la navegación aérea internacional* (20.ª ed.). OACI.

Romps, D. M. (2017). Exact expression for the lifting condensation level. *Journal of the Atmospheric Sciences, 74*(12), 3891-3900. https://doi.org/10.1175/JAS-D-17-0102.1

Roy, B. (1996). *Multicriteria methodology for decision aiding*. Kluwer Academic Publishers. https://doi.org/10.1007/978-1-4757-2500-1

Rudin, C. (2019). Stop explaining black box machine learning models for high stakes decisions and use interpretable models instead. *Nature Machine Intelligence, 1*(5), 206-215. https://doi.org/10.1038/s42256-019-0048-x

Saaty, T. L. (1980). *The analytic hierarchy process: Planning, priority setting, resource allocation*. McGraw-Hill.

Saaty, T. L. (1990). How to make a decision: The analytic hierarchy process. *European Journal of Operational Research, 48*(1), 9-26. https://doi.org/10.1016/0377-2217(90)90057-I

Zippenfenig, P. (2023). *Open-Meteo.com weather API* [Software]. Zenodo. https://doi.org/10.5281/zenodo.7970649

> **Nota sobre la referencia a Espy.** La regla de estimación de la base de nubes por
> diferencia entre temperatura y punto de rocío se atribuye a James Pollard Espy (1836),
> quien dio la primera formulación del nivel de condensación por ascenso. Se cita en el
> texto por su valor histórico, y se acompaña de Romps (2017) como referencia técnica
> moderna y verificable del mismo concepto. ⬜ *[decidir si se incorpora la obra original
> de Espy —* The Philosophy of Storms*, 1841— a la lista de referencias o si alcanza con
> la mención en el texto]*

---

## ⬜ Pendientes de esta sección

1. ✅ **Verificación visual del Nall Report — HECHA (2026-08-27).** Se renderizaron y
   revisaron las páginas 5, 6 y 17 del 28.º Nall Report. Los veinte valores de las series
   decenales coinciden **exactamente** con los utilizados: meteorología 56/54/65/52/56/51/
   41/36/39/23 (totales) y 44/38/47/36/42/38/30/28/30/12 (fatales), suma 473 y 345;
   aterrizaje 416/411/351/360/368/343/282/282/263/334 y 7/4/3/9/2/7/4/6/3/6, suma 3410 y
   51. La figura 1.11 confirma los valores de 2016 de ambas categorías. El cociente
   345/51 = 6,76 → Saaty 7 queda verificado contra la fuente.
2. **Enmienda vigente de la RAAC Parte 91** (compartido con la § 1).
3. **Fecha de consulta** de la página del FAA Safety Team sobre FRAT.
4. **Obra de Espy**: decidir si se agrega a la lista de referencias o queda como mención
   histórica en el texto.
5. **Numeración cruzada**: las remisiones a los § 4.2, § 5.1 y § 6.3 deben verificarse
   una vez redactadas esas secciones.
6. **Herramientas de desarrollo asistido por IA**: definir si corresponde consignarlas en
   el *stack* tecnológico (§ 3.3.2) o en las lecciones aprendidas (§ 6.2). Es una decisión
   del autor y de su director; se deja explícitamente abierta.
7. **Figura sugerida**: un diagrama de la arquitectura de tres capas del modelo de riesgo
   (§ 3.2.4) y otro de la canalización por capas (§ 3.2.5) mejorarían sustancialmente la
   sección. Podrían reutilizarse en el § 4.2.
