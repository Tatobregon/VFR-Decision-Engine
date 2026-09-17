# 5. RESULTADOS, VALIDACIÓN E IMPACTO

> **Nota de adaptación.** La plantilla pide para esta sección matrices de confusión, curvas de
> pérdida y de precisión, F1 y tiempos de inferencia, que son las métricas de un modelo
> entrenado. El asistente de consulta admite esas métricas de manera directa. El motor de
> decisión no se entrena: sus resultados se expresan como concordancia con una clasificación
> de referencia, y el equivalente de la curva de pérdida es la curva de operación de sus
> umbrales. Se conserva la estructura de la sección y se declara, en cada caso, qué mide
> cada resultado y qué no.

---

## 5.1. Métricas de rendimiento

### 5.1.1. Motor de decisión: concordancia con la clasificación de referencia

El motor se evaluó contra la batería de 38 escenarios descrita en el § 4.2.2. Con los
umbrales adoptados —0,22 y 0,59—, el sistema coincide con la clasificación de referencia en
**36 de los 38 escenarios (95 %)** y **no incurre en ningún sub-aviso** (Figura 5.1).

⬜ **Figura 5.1.** *Matriz de confusión del motor sobre la batería de referencia.*
(`Teorico/figuras/cap5/fig_5_1_matriz_bateria.png`)

Los dos desacuerdos son sobre-avisos: el sistema advierte más de lo que la referencia exige.
En el escenario G2 —nieve moderada, visibilidad de 6 km y techo de 1500 ft— la suma de los
tres factores lleva el puntaje a 0,33 y el veredicto a CAUTION, donde la referencia indica
GO. En el escenario J1 —visibilidad de 5 km justos, techo de 1100 ft y viento cruzado de
7 kt— la acumulación de tres condiciones próximas a su límite lleva el puntaje a 0,51, con el
mismo resultado. Ambos casos están en el mínimo VFR o por encima, y resultan conservadores. El
resultado cumple la meta operacional 4 del § 1.2.2: concordancia de al menos el 90 % y
ningún sub-aviso.

**La franja que la batería no muestreaba.** Una batería verifica un conjunto finito de
puntos, y la ausencia de sub-avisos en ellos no asegura la ausencia en los puntos
intermedios. Para comprobarlo se recorrió de forma sistemática la franja comprendida entre el
rechazo categórico y el mínimo VFR —visibilidad de 3 a 5 km y techo de 500 a 1000 ft—, con
las cinco aeronaves y los tres niveles de experiencia. El recorrido encontró una franja
estrecha sin cubrir: con el nivel Avanzado, una visibilidad de entre 4,92 y 4,99 km y el resto
de las condiciones ideales, el puntaje quedaba apenas por debajo del umbral de GO. El hallazgo
dio origen al piso de precaución bajo el mínimo VFR (§ 3.2.4), que cierra la franja por
construcción, y una prueba automatizada la recorre completa en cada ejecución de la suite.

**Alcance del resultado.** La concordancia debe leerse con el alcance que precisa el
§ 4.2.2. En visibilidad, techo y viento cruzado, la referencia y el motor aplican los mismos
cortes, de modo que en esos factores la ausencia de sub-avisos es una garantía por
construcción y lo que la comparación mide es cuánto sobre-avisa el modelo. Se trata, además,
de validez de constructo y no de validez empírica: la referencia es una clasificación
derivada de la norma, no un registro de decisiones de pilotos, cuyo contraste corresponde a
las pruebas de campo (§ 5.2).

**Concordancia según el nivel de experiencia.** Con mínimos personales, el sistema se aparta
de la referencia de manera deliberada y hacia el lado conservador (Figura 5.3). En el nivel
Avanzado, que no agrega margen, la concordancia se mantiene en 36/38; en PPL desciende a
34/38 (89 %) y en Alumno a 27/38 (71 %). La diferencia se compone por completo de
sobre-avisos: en ningún nivel aparece un sub-aviso. No es una degradación del modelo, sino el
efecto buscado de exigir más margen a un piloto con menos experiencia.

⬜ **Figura 5.3.** *Concordancia con la referencia según el nivel de experiencia del piloto.*
(`Teorico/figuras/cap5/fig_5_3_concordancia_por_nivel.png`)

### 5.1.2. Curva de operación y elección de los umbrales

El equivalente de las curvas de pérdida y de precisión de un modelo entrenado es, en este
sistema, la curva de operación del umbral inferior: cómo varían los desacuerdos con la
referencia al desplazar t_go, con t_caution fijo en 0,59 (Figura 5.2).

⬜ **Figura 5.2.** *Curva de operación del umbral inferior de decisión.*
(`Teorico/figuras/cap5/fig_5_2_curva_umbral.png`)

| Umbral inferior (t_go) | Sobre-avisos | Sub-avisos | Concordancia |
|---|---|---|---|
| 0,05 | 7 | 0 | 31/38 |
| 0,07 a 0,14 | 3 | 0 | 35/38 |
| **0,15 a 0,33** (incluye el adoptado, 0,22) | **2** | **0** | **36/38** |
| 0,34 a 0,50 | 1 | 0 | 37/38 |
| 0,51 a 0,58 | 0 | 0 | 38/38 |

Los sub-avisos se mantienen en cero en todo el recorrido, porque el rechazo categórico y el
piso de precaución impiden por construcción un GO por debajo del mínimo VFR. Los
sobre-avisos, en cambio, disminuyen a medida que el umbral sube. El procedimiento de
calibración, que explora el umbral inferior hasta 0,45, identifica como óptimo el rango de
0,34 a 0,45, con 37 de 38 escenarios.

Se mantuvo, no obstante, el valor 0,22, por dos razones. La primera es que la mejora descansa
en un único escenario (G2), y la curva muestra adónde conduce seguirla: con el umbral en 0,51
la concordancia sería total, pero el puntaje prácticamente dejaría de producir advertencias
por sí mismo y la franja de precaución quedaría a cargo solo de la barrera. Ajustar el umbral
para acertar la batería sería, en ese punto, ajustar el modelo a su propia referencia. La
segunda es que, entre dos umbrales sin sub-avisos, el criterio de costo asimétrico del
§ 3.1.5 favorece el que advierte antes. El valor adoptado coincide, además, con el rango
óptimo que la calibración identificaba antes de incorporar el piso de precaución (0,15 a
0,28).

### 5.1.3. Robustez del veredicto

**Convergencia.** Las dos derivaciones de los pesos —la de juicio experto y la derivada de la
accidentología (§ 3.1.4.1)— se compararon nuevamente sobre el sistema actual, cada una con
sus umbrales calibrados. Ambas alcanzan 36/38 sin sub-avisos, presentan los mismos dos
desacuerdos y **no difieren en el veredicto de ningún escenario**.

**Sensibilidad.** La Figura 5.4 resume cuántos de los 38 veredictos cambian ante cada
perturbación.

⬜ **Figura 5.4.** *Escenarios que cambian de veredicto ante cada perturbación.*
(`Teorico/figuras/cap5/fig_5_4_sensibilidad.png`)

| Perturbación | Resultado |
|---|---|
| Cada peso por separado, ±20 % | 4 cambios en 532 evaluaciones (0,8 %) |
| Los siete pesos a la vez, ±20 %, 5000 sorteos | 99,1 % de veredictos estables; el peor sorteo conserva el 97,4 %; 37 de 38 escenarios no cambian nunca |
| Umbrales, ±0,05 | a lo sumo 1 de 38 |
| Extremos de las rampas de riesgo | a lo sumo 2 de 38 (5,3 %); los de riesgo máximo, a lo sumo 1 |
| Piso de precaución de la barrera de cruzado, de 0,85 a valores entre 0,60 y 1,11 | a lo sumo 2 de 38 |

El único escenario sensible a los pesos es B4 —visibilidad de 3,5 km y techo de 900 ft—, cuyo
puntaje (0,583) está a 0,007 del umbral de NO GO y cambia en el 35,1 % de los sorteos. Los
resultados muestran que el veredicto no depende de la precisión de ningún parámetro
individual, y que los parámetros de forma de las rampas influyen más que los pesos: es en
ellos donde debe concentrarse el esfuerzo de justificación.

### 5.1.4. Asistente de consulta

La evaluación se realizó el 11/09/2026 sobre los 98 casos del § 4.2.4, y ningún caso quedó
afectado por una indisponibilidad del proveedor: cuando el primer modelo de la cadena de
reserva no respondió, respondió el segundo. El primero atendió 80 casos y el segundo, 18.

| Métrica | Resultado |
|---|---|
| Exactitud de clasificación de intención | 94/98 (95,9 %) |
| F1 macro-promedio (9 intenciones) | 0,957 |
| Exactitud de resolución del aeródromo | 82/83 (98,8 %) |
| **Tasa de invención ante datos ausentes** | **0 %** (0 de 9 casos con detección objetiva) |
| Reconocimiento explícito de la ausencia del dato | 11/11 |
| Veredictos que la verificación en código tuvo que reemplazar | 0 de 11 |
| Códigos de aeródromo que hubo que corregir | 0 |

⬜ **Figura 5.5.** *Matriz de confusión de intenciones del asistente.*
(`Teorico/figuras/cap5/fig_5_5_matriz_asistente.png`)

⬜ **Figura 5.6.** *F1 por intención.* (`Teorico/figuras/cap5/fig_5_6_f1_asistente.png`)

**Los desaciertos.** Los cuatro errores de clasificación ocurren entre intenciones vecinas
(Figura 5.5). Dos corresponden a consultas sobre la ficha de un aeródromo que el asistente
resolvió con otra herramienta de información del registro: una pregunta por combustible
atendida con la búsqueda de combustible cercano, y una pregunta sobre un aeródromo
inexistente atendida con la búsqueda de aeródromos, que informó correctamente que no existe.
Uno confunde la evolución del día con el veredicto de un momento, y otro la consulta por el
aire sobre un punto con una modificación de la ruta. En los cuatro casos la respuesta fue
correcta respecto de los datos y ninguna fue peligrosa.

La única falla de resolución corresponde a una consulta que no nombra ningún aeródromo —"volvamos
a la ruta directa, sacale el punto de paso"—, en la que el conjunto esperaba que el modelo lo
infiriera del vuelo cargado. Eso mide la inferencia desde el contexto y no la resolución de
entidades; la etiqueta se corrigió para las corridas siguientes, y el valor informado es, por
lo tanto, conservador.

El desempeño no depende de la región: Cuyo 12/12, Litoral 14/14, Pampa 30/30, Patagonia
18/19 y NOA 10/12.

**Límites de estas cifras.** Tres consideraciones acotan su lectura. Con entre 8 y 17 casos
por intención, un solo caso desplaza el F1 de esa intención entre 6 y 12 puntos, y el promedio
macro combina clases de soporte desigual. El conjunto se utilizó también durante el
desarrollo (§ 4.2.4), de modo que las métricas describen el desempeño sobre el conjunto de
desarrollo y no sobre casos que el diseño no haya visto. Y los resultados corresponden a la
versión del modelo que el proveedor servía en la fecha de la corrida: una nueva versión puede
modificarlos, lo que obliga a repetir la evaluación antes de comparar.

### 5.1.5. Tiempos de respuesta

**Motor de decisión.** Se midió el tiempo de la evaluación completa de un vuelo —desde la
solicitud hasta la respuesta, incluidas las consultas a las fuentes externas— sobre diez
rutas, dos por región, con dos aeronaves y una repetición de cada evaluación, en el equipo de
desarrollo y en la instancia desplegada en el plan gratuito de Render, el 16/09/2026. Como el
almacenamiento temporal guarda cada dato por separado, se distinguen tres situaciones: la
primera evaluación de una ruta, que consulta todas las fuentes; la misma ruta con otra
aeronave, que reutiliza los datos de superficie pero no los del nivel de crucero; y la
evaluación repetida, que encuentra todo almacenado. Las 80 evaluaciones respondieron
correctamente.

| Situación | Local: mediana (máximo) | Render: mediana (máximo) |
|---|---|---|
| Primera evaluación de la ruta (10) | 6,3 s (19,4 s) | 7,9 s (23,9 s) |
| Misma ruta, otra aeronave (10) | 2,2 s (5,2 s) | 2,6 s (6,4 s) |
| Evaluación repetida (20) | 1,7 s (6,5 s) | 2,5 s (4,6 s) |

El tiempo de la primera evaluación lo determinan las consultas a las fuentes externas, cuya
demora varía de una ruta a otra; con los datos ya almacenados, la evaluación se resuelve en
un par de segundos. La instancia desplegada agrega entre 0,4 y 1,6 s a la mediana. Aparte de
esas cifras, la primera respuesta de la instancia desplegada, que llevaba tiempo sin recibir
consultas, tardó 42,7 s: es el arranque del servicio en el plan gratuito, que ocurre una vez
tras un período sin uso y no en cada evaluación. La versión desplegada al momento de la
medición no incluía todavía el rechazo categórico en 3 km ni el piso de precaución bajo el
mínimo VFR, cambios que no modifican de manera apreciable el tiempo de cálculo.

**Asistente de consulta.** La latencia por consulta, medida sobre los 98 casos, incluye la
interpretación, la ejecución de las herramientas y la redacción:

| Media | Mediana | Percentil 95 | Máximo |
|---|---|---|---|
| 14,60 s | 9,59 s | 39,89 s | 68,08 s |

Los valores altos corresponden a las consultas que ejecutan cálculos completos: la propuesta
de cambio de ruta calcula la ruta vigente y la propuesta y, si se trata de una escala, evalúa
además la meteorología del aeródromo.

⬜ **Figura 5.7.** *Tiempos de respuesta del motor, en local y en la instancia desplegada, y del
asistente.* (`Teorico/figuras/cap5/fig_5_7_tiempos_de_respuesta.png`)

---

## 5.2. Pruebas de campo

⬜ *[Lo redacta el autor.]* Lo que la sección tiene que cubrir, según la plantilla y lo que
comprometen los capítulos anteriores:

- Las pruebas realizadas en Aeroatelier: quiénes usaron el sistema (por rol, sin identificar
  personas), en qué período, cómo lo usaron y en cuántas planificaciones.
- El uso del autor como piloto y las correcciones que surgieron de ese uso.
- La retroalimentación directa de los usuarios y los cambios que motivó.
- Los casos en que el veredicto del sistema no coincidió con el criterio del instructor o del
  piloto, y cómo se resolvieron.
- La relación con la meta operacional 4, que remite al § 5.2 la validación frente a un
  criterio externo e independiente, y con la meta operacional 5 (aceptación y uso efectivo
  por pilotos reales).

---

## 5.3. Análisis de impacto

⬜ *[Lo redacta el autor.]* Lo que la sección tiene que cubrir, según la plantilla y lo que
comprometen los capítulos anteriores:

- **Impacto económico**: costo de uso del sistema (gratuito, § 1.2.2) frente a las
  herramientas disponibles y al tiempo de preparación del vuelo.
- **Impacto social**: el acceso a una evaluación explicada para alumnos y pilotos recién
  licenciados, y la cobertura de los aeródromos sin estación (§ 4.1.2: 513 de 561).
- **Impacto ambiental**, si corresponde.
- **Consideraciones éticas y de privacidad**:
  - La responsabilidad del piloto al mando y el aviso de uso de la herramienta.
  - Las condiciones de uso del nivel gratuito del modelo de lenguaje (§ 3.3.3) y el aviso al
    usuario del asistente, declarado como pendiente del producto.
  - La atribución de Open-Meteo que exige su licencia CC BY 4.0, declarada como pendiente del
    producto.
  - El consentimiento informado y la anonimización que requeriría la telemetría EFIS
    (§ 2.2.5).

---

## ⬜ Pendientes de esta sección

1. **Secciones 5.2 y 5.3**: las redacta el autor.
2. **Tiempos en la versión publicada**: la instancia desplegada se midió con la versión anterior
   a los cambios del 16/09 (§ 5.1.5). Repetir la medición después de publicarlos confirmaría
   las cifras con la versión documentada.
