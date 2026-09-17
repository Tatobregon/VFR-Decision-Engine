# Guion de la presentación — Defensa del Trabajo Final

**Duración:** 30 minutos de exposición, más las preguntas del tribunal.
**Tribunal:** docentes de IA y ciencia de datos, sin formación aeronáutica.
**Formato:** 22 diapositivas principales, 6 de respaldo para preguntas, y una demo en vivo con
video de respaldo.

---

## Criterios generales

- **Una idea por diapositiva.** El título dice la idea; el contenido la sostiene. Si una
  diapositiva necesita más de cinco renglones de texto, son dos diapositivas.
- **Lo general, no lo específico.** Cada detalle técnico queda en la tesis y en las
  diapositivas de respaldo. En la exposición, solo las cifras que sostienen una conclusión.
- **El vocabulario aeronáutico se explica una vez, en una frase**, la primera vez que aparece:
  VFR, METAR, pronóstico numérico, mínimos.
- **Lo que el tribunal va a mirar con más atención**, por ser docentes de IA: por qué el motor
  no usa aprendizaje automático, cómo se valida un sistema sin datos etiquetados, cómo se
  controla al modelo de lenguaje y qué límites tiene el trabajo. Esos cuatro temas tienen que
  quedar claros sin que los pregunten.
- **Estilo visual sobrio**: fondo claro, una tipografía sin serifa, un color de acento. Los
  veredictos GO / CAUTION / NO GO siempre con su palabra escrita, nunca solo con color. Las
  figuras del capítulo 5 ya están en ese estilo.

## Distribución del tiempo

| Bloque | Diapositivas | Tiempo |
|---|---|---|
| 1. Apertura y problema | 1 a 5 | 5 min |
| 2. Objetivo y enfoque de IA | 6 a 8 | 4 min |
| 3. Cómo funciona el sistema | 9 a 13 | 6 min |
| 4. Demostración en vivo | 14 | 6 min |
| 5. Resultados y validación | 15 a 18 | 5 min |
| 6. Impacto, conclusiones y futuro | 19 a 22 | 4 min |
| **Total** | | **30 min** |

---

## Bloque 1 · Apertura y problema (5 min)

### Diapositiva 1 — Portada

- **Contenido:** título del proyecto, autor, carrera, institución, tutor académico y
  organización comitente (Aeroatelier).
- **Visual:** una captura limpia del mapa del sistema con una ruta dibujada.
- **Qué decir:** presentarse en una oración y anticipar el tema en otra: "Desarrollé un sistema
  que ayuda a un piloto a decidir si las condiciones meteorológicas le permiten volar."
- **Tiempo:** 30 s.

### Diapositiva 2 — La decisión que el sistema asiste

- **Contenido:**
  - En vuelo visual (VFR), el piloto se guía mirando hacia afuera: necesita ver el terreno y
    el horizonte.
  - Antes de cada vuelo decide: ¿las condiciones lo permiten?
  - Esa decisión se llama *GO / NO GO*.
- **Visual:** dos fotos o ilustraciones contrapuestas: cielo despejado y cielo cubierto.
- **Mensaje clave:** el problema es una decisión, no un pronóstico.
- **Qué decir:** explicar VFR en una frase, sin tecnicismos. Aclarar que el sistema no
  pronostica el tiempo: ayuda a decidir con la información que ya existe.
- **Tiempo:** 1 min.

### Diapositiva 3 — Por qué importa

- **Contenido:**
  - Los accidentes por meteorología son pocos, pero son los más letales.
  - El más frecuente entre ellos: un piloto visual que entra sin querer en nubes o baja
    visibilidad.
- **Visual:** una sola cifra grande: **72,9 %** de los accidentes meteorológicos resultan
  fatales, con la fuente al pie: serie de diez años del Nall Report (AOPA), aviación general
  de Estados Unidos.
- **Mensaje clave:** es una decisión poco frecuente de equivocar, pero con consecuencias
  graves.
- **Qué decir:** contraste entre baja frecuencia y alta letalidad; por eso vale la pena asistir
  la decisión.
- **Tiempo:** 1 min.

### Diapositiva 4 — Cómo se decide hoy

- **Contenido:**
  - La información está dispersa en varias fuentes y plataformas.
  - El piloto la integra mentalmente, cada uno con su criterio.
  - La mayoría de los aeródromos no tiene estación meteorológica.
- **Visual:** un esquema con cuatro o cinco íconos de fuentes que convergen en un piloto con un
  signo de pregunta.
- **Mensaje clave:** el conocimiento existe; lo que falta es integrarlo de forma explícita y
  consistente.
- **Qué decir:** esta es la frase que prepara el argumento del enfoque de IA: no hay que
  descubrir un patrón en los datos, hay que integrar un conocimiento que ya está escrito.
- **Tiempo:** 1 min 30 s.

### Diapositiva 5 — La organización y el pedido

- **Contenido:** Aeroatelier, escuela de vuelo; su necesidad; el requerimiento formal firmado.
- **Visual:** logo o foto de la organización y una imagen de la nota del requerimiento.
- **Mensaje clave:** el proyecto responde a una necesidad real de una organización concreta.
- **Tiempo:** 1 min.

---

## Bloque 2 · Objetivo y enfoque de IA (4 min)

### Diapositiva 6 — Objetivo y alcance

- **Contenido:**
  - **Objetivo:** un sistema que reúna la información oficial y entregue un veredicto
    justificado GO / CAUTION / NO GO.
  - **Hace:** evalúa origen, destino y ruta; explica el porqué; responde consultas en lenguaje
    natural.
  - **No hace:** no decide por el piloto, no se usa en vuelo, no presenta el plan de vuelo.
- **Visual:** dos columnas, "Hace" y "No hace".
- **Mensaje clave:** es una herramienta de apoyo; la decisión es siempre del piloto.
- **Tiempo:** 1 min.

### Diapositiva 7 — Por qué el motor no usa aprendizaje automático

- **Contenido:** tres razones, una por renglón:
  1. No existen datos etiquetados de decisiones de pilotos con su resultado.
  2. En seguridad, el veredicto tiene que poder explicarse, no solo acertar.
  3. El conocimiento ya está formalizado en la norma y en la accidentología.
- **Visual:** una tabla simple de dos columnas, "Aprendizaje automático" frente a "Reglas y
  análisis multicriterio", con tres filas.
- **Mensaje clave:** la elección de la técnica responde a las condiciones del problema, no a una
  preferencia.
- **Qué decir:** esta es la diapositiva más importante para este tribunal. Aclarar que no se
  descarta el aprendizaje automático en general: se descarta para esta tarea, con los datos que
  hoy existen.
- **Tiempo:** 1 min 30 s.

### Diapositiva 8 — Un sistema neurosimbólico

- **Contenido:**
  - El **motor simbólico** decide: reglas, pesos, umbrales.
  - El **modelo de lenguaje** solo interpreta la pregunta y redacta la respuesta.
  - El modelo de lenguaje nunca emite ni modifica un veredicto.
- **Visual:** dos cajas: "Modelo de lenguaje (interpreta)" y "Motor de decisión (decide)",
  con una flecha "consulta" y una barrera entre ambas.
- **Mensaje clave:** se usa aprendizaje profundo donde sirve —el lenguaje— y no donde sería un
  riesgo —la decisión—.
- **Qué decir:** el mismo criterio de la diapositiva anterior, aplicado dos veces: para
  interpretar lenguaje sí hay datos masivos y no hay reglas escribibles, así que ahí la respuesta
  se invierte.
- **Tiempo:** 1 min 30 s.

---

## Bloque 3 · Cómo funciona el sistema (6 min)

### Diapositiva 9 — Arquitectura general

- **Contenido:** capas de datos, interpretación, riesgo, decisión, ruta e interfaz; el
  asistente al costado.
- **Visual:** la Figura 4.1 (arquitectura), simplificada a cinco o seis cajas.
- **Mensaje clave:** cada capa tiene una responsabilidad, y todas las fuentes terminan en un
  mismo formato de datos.
- **Tiempo:** 1 min.

### Diapositiva 10 — El modelo de riesgo en tres capas

- **Contenido:**
  1. **Rechazos inmediatos:** condiciones que no admiten discusión (tormenta, visibilidad muy
     baja).
  2. **Puntaje ponderado:** combina los factores según su peso.
  3. **Barrera:** un factor grave no puede quedar escondido en el promedio.
- **Visual:** un embudo de tres niveles que termina en GO / CAUTION / NO GO.
- **Mensaje clave:** un promedio solo no alcanza en seguridad; la tercera capa es el aporte
  central del diseño.
- **Qué decir:** usar el ejemplo del viento cruzado: por su peso, un viento cruzado por encima
  del límite del avión casi no movería el promedio, y la barrera lo veta igual.
- **Tiempo:** 1 min 30 s.

### Diapositiva 11 — De dónde salen los números

- **Contenido:**
  - **Pesos:** método AHP, derivados de la estadística de accidentes, con verificación de
    consistencia.
  - **Umbrales:** calibrados para reproducir los criterios de la norma sobre 38 escenarios de
    referencia.
  - **Todo reproducible:** cada cálculo es código que se puede volver a ejecutar.
- **Visual:** tres íconos en fila: accidentología → pesos, norma → umbrales, código →
  reproducibilidad.
- **Mensaje clave:** ningún número está puesto a ojo; cada uno tiene una procedencia declarada.
- **Qué decir:** es el equivalente del "entrenamiento" en este proyecto: los parámetros no se
  aprenden de ejemplos, se derivan de la evidencia y de la norma.
- **Tiempo:** 1 min 15 s.

### Diapositiva 12 — Datos y cobertura nacional

- **Contenido:**
  - Fuentes oficiales y abiertas: observaciones y pronósticos de aeródromo, pronóstico numérico,
    avisos a pilotos (NOTAM) y el registro nacional de aeródromos.
  - **561 aeródromos** y **5 aeronaves**.
  - **9 de cada 10 aeródromos no tienen estación**: para ellos se usa el pronóstico numérico.
- **Visual:** mapa de Argentina con los aeródromos, distinguiendo los que tienen estación.
- **Mensaje clave:** la cobertura nacional obligó a tratar el pronóstico numérico como fuente
  principal, no como respaldo.
- **Tiempo:** 1 min.

### Diapositiva 13 — El asistente de consulta

- **Contenido:**
  - El piloto pregunta en lenguaje natural: contactos, combustible, meteorología, cambios de
    ruta.
  - El modelo elige una de **8 herramientas del sistema** y redacta con su resultado.
  - **Verificado en código:** si no transcribe bien el veredicto, se reemplaza; si inventa un
    código de aeródromo, se corrige.
- **Visual:** flujo de cuatro pasos: pregunta → herramienta → datos → respuesta verificada.
- **Mensaje clave:** el modelo de lenguaje no sabe nada por sí mismo; todo lo que afirma sale de
  una herramienta.
- **Tiempo:** 1 min 15 s.

---

## Bloque 4 · Demostración en vivo (6 min)

### Diapositiva 14 — Demostración

- **Contenido de la diapositiva:** solo el título y la dirección de la web, para cambiar de
  pantalla.
- **Guion de la demo:**
  1. **Evaluar un vuelo** entre dos aeródromos, con una aeronave (1 min 30 s). Mostrar el
     veredicto de origen y destino, y leer en voz alta su justificación.
  2. **La ruta en el mapa** (1 min): corredores o aerovías, tramos con rumbo, perfil vertical.
  3. **Agregar una escala** (1 min): mostrar cómo cambia la ruta y cómo se evalúa la escala a su
     hora de llegada.
  4. **Preguntar al asistente** (1 min 30 s): una consulta de contacto o combustible y una
     propuesta de cambio de ruta; mostrar que la propuesta no se aplica hasta confirmarla.
  5. **Volver a la presentación** (30 s).
- **Plan B:** si la web no responde o la conexión falla, pasar directamente al video grabado
  con el mismo recorrido.
- **Tiempo:** 6 min.

---

## Bloque 5 · Resultados y validación (5 min)

### Diapositiva 15 — Validación del motor frente a la norma

- **Contenido:**
  - Sobre 38 escenarios de referencia: **95 % de coincidencia**.
  - **Ningún caso en que el sistema avise menos de lo que exige la norma.**
  - Los desacuerdos son siempre del lado conservador.
- **Visual:** la Figura 5.1 (matriz de confusión).
- **Mensaje clave:** sin datos etiquetados, la validación se hace contra la norma: el sistema
  la reproduce y, cuando se aparta, advierte de más, nunca de menos.
- **Qué decir:** aclarar que es validez de constructo —el sistema es coherente con la norma— y
  que la validación con pilotos viene en la diapositiva 18.
- **Tiempo:** 1 min 15 s.

### Diapositiva 16 — Robustez

- **Contenido:**
  - Al perturbar los pesos, el veredicto se mantiene en el **99 %** de los casos.
  - Dos derivaciones independientes de los pesos producen **los mismos veredictos**.
- **Visual:** la Figura 5.4 (sensibilidad), o una versión reducida con tres barras.
- **Mensaje clave:** las conclusiones no dependen de la precisión de ningún número individual.
- **Tiempo:** 1 min.

### Diapositiva 17 — El asistente

- **Contenido:**
  - **95,9 %** de intenciones bien clasificadas sobre 98 consultas, con un único modelo.
  - **0 %** de datos inventados cuando el dato no existe.
  - Tiempo de respuesta típico: unos 4 segundos (mediana 3,6 s).
- **Visual:** la Figura 5.6 (F1 por intención), o tres cifras grandes.
- **Mensaje clave:** la métrica que importa es la de invención, porque un dato inventado parece
  verdadero.
- **Qué decir:** mencionar el límite: el conjunto de evaluación se usó también durante el
  desarrollo.
- **Tiempo:** 1 min 15 s.

### Diapositiva 18 — Pruebas de campo

- **Contenido:**
  - Más de 15 pilotos: instructores, pilotos privados, comerciales y alumnos.
  - Unos 50 vuelos en dos semanas, comparados con el procedimiento manual.
  - Coincidió en todos menos uno: el pronóstico numérico estimó un techo de nubes más alto que el
    real.
- **Visual:** foto del uso en el aeroclub, si hay, y una cifra grande: "1 desacuerdo en unos
  50 vuelos".
- **Mensaje clave:** el sistema funcionó con usuarios reales, y el único desacuerdo señala con
  precisión dónde está su límite: la calidad del pronóstico.
- **Qué decir:** presentar el desacuerdo con honestidad; es un resultado, no una falla del
  diseño, y justifica la primera línea del trabajo futuro.
- **Tiempo:** 1 min 30 s.

---

## Bloque 6 · Impacto, conclusiones y futuro (4 min)

### Diapositiva 19 — Impacto

- **Contenido:**
  - **Económico:** gratuito, frente a suscripciones pagas.
  - **Accesibilidad:** en español y desde un navegador.
  - **Operativo:** menos congestión en la oficina de pilotos y visibilidad a aeródromos poco
    visitados.
  - **Ético:** la decisión sigue siendo del piloto; el uso de datos del asistente se informa.
- **Visual:** cuatro íconos con una palabra cada uno.
- **Tiempo:** 1 min.

### Diapositiva 20 — Balance de objetivos y limitaciones

- **Contenido:**
  - **Cumplido:** sistema operativo, cobertura nacional, veredicto explicado, asistente
    confiable.
  - **Con límites:** la validación es frente a la norma, no frente a decisiones reales; la
    precisión del pronóstico numérico no se validó; la reducción de variabilidad entre pilotos no
    se midió.
- **Visual:** dos columnas, "Logrado" y "Con límites".
- **Mensaje clave:** declarar los límites es parte del resultado.
- **Tiempo:** 1 min 15 s.

### Diapositiva 21 — Trabajo futuro

- **Contenido:**
  1. Validar el pronóstico numérico contra observaciones reales.
  2. Registrar decisiones reales de pilotos para calibrar con datos.
  3. Perfiles de aeronave personalizados.
- **Visual:** una línea de tiempo simple con tres hitos.
- **Mensaje clave:** el proyecto deja preparada la infraestructura para pasar de la validación
  normativa a la empírica.
- **Tiempo:** 1 min.

### Diapositiva 22 — Cierre

- **Contenido:** una frase de cierre y "Gracias. ¿Preguntas?".
- **Frase sugerida:** "Un sistema que no reemplaza el criterio del piloto, sino que lo hace
  explícito, consistente y disponible para cualquiera."
- **Tiempo:** 45 s.

---

## Diapositivas de respaldo (después del cierre, solo si preguntan)

| # | Tema | Contenido |
|---|---|---|
| R1 | Derivación de los pesos | La jerarquía AHP y la razón de consistencia (0,069). |
| R2 | Elección de los umbrales | La Figura 5.2 y por qué se mantuvo el umbral más conservador. |
| R3 | Concordancia por nivel de experiencia | La Figura 5.3: con mínimos personales, solo más advertencias. |
| R4 | Matriz del asistente | La Figura 5.5 y los cuatro desaciertos. |
| R5 | Tiempos de respuesta | La Figura 5.7, con el arranque del plan gratuito. |
| R6 | Privacidad del asistente | Condiciones de uso de datos del nivel gratuito del modelo de lenguaje. |

---

## Preguntas probables del tribunal

**¿Por qué no usaste aprendizaje automático para el veredicto?**
Porque no existen datos etiquetados de decisiones de despacho con su resultado, porque el
veredicto tiene que ser explicable en un dominio de seguridad y porque el conocimiento ya está
formalizado en la norma. Si el sistema registrara decisiones reales, ese conjunto de datos
podría construirse: está planteado como trabajo futuro.

**¿Cómo validás un sistema sin datos etiquetados?**
En dos niveles. Contra la norma, con una batería de escenarios de referencia —validez de
constructo—, y con pilotos reales en las pruebas de campo. Además, con análisis de sensibilidad,
que muestra que el veredicto no depende de la precisión de los parámetros.

**¿El 95 % no es alto porque calibraste los umbrales sobre los mismos escenarios?**
Es una observación correcta y está declarada: la batería sirve para calibrar y para medir. Por
eso se complementa con la convergencia de dos derivaciones de pesos, con el análisis de
sensibilidad y con las pruebas de campo. Y la decisión de no mover el umbral hacia el óptimo
del momento responde justamente a no ajustar el modelo a su propia referencia.

**¿Qué pasa si el pronóstico está mal?**
El sistema decide con los datos que recibe; si el pronóstico se equivoca, el veredicto hereda
ese error. Pasó una vez en las pruebas de campo. Por eso la validación del pronóstico numérico
contra observaciones reales es la primera línea del trabajo futuro, y la infraestructura para
hacerla ya está construida.

**¿Cómo evitás que el modelo de lenguaje invente información?**
Por arquitectura: no responde desde su conocimiento, sino desde herramientas del sistema. Y las
propiedades críticas se verifican en código después de que responde: el veredicto se compara con
el del motor y los códigos de aeródromo con el registro. La tasa de invención medida fue 0 %.

**¿Las métricas del asistente corresponden a un solo modelo?**
Sí, y es una condición de la medición. En operación hay una cadena de reserva: si el modelo
titular no responde, contesta el siguiente y el piloto igual recibe su respuesta. Eso es una
decisión de disponibilidad del producto, no del experimento —un F1 calculado sobre respuestas de
dos modelos distintos no describiría a ninguno de los dos—. Por eso la evaluación fija un único
modelo y, ante una caída del proveedor, reintenta el mismo caso en lugar de pasar al siguiente;
si los reintentos se agotan, la corrida se detiene y se retoma. Los 98 casos informados pasaron
por el mismo modelo.

**¿Qué pasa con la privacidad de las consultas al asistente?**
En el nivel gratuito, el proveedor puede usar el contenido para mejorar sus productos. El sistema
no agrega datos personales, pero la consulta se transmite; está declarado en la tesis y el aviso
al usuario queda como tarea pendiente de publicación.

**¿Usaste herramientas de IA para desarrollar el proyecto?**
Sí, una herramienta de programación asistida, declarada en la tesis. El autor definió los
requisitos y las decisiones de diseño, y todo se verificó con pruebas automatizadas y contra los
datos reales. Varias de las correcciones más importantes vinieron del criterio como piloto.

**¿Sirve para otros aeroclubes o para otros aviones?**
Sí: cubre los 561 aeródromos del registro nacional y cinco perfiles de aeronave, y ninguna regla
está atada a un aeródromo o a un avión particular. Los perfiles personalizados son trabajo
futuro.

---

## Preparación antes de la defensa

- [ ] **Abrir la web entre 2 y 5 minutos antes** de exponer: en el plan gratuito, la primera
      respuesta después de un rato sin uso tarda unos 40 segundos.
- [ ] **Grabar el video de respaldo** con exactamente el mismo recorrido de la demo.
- [ ] **Elegir de antemano los aeródromos de la demo** y probar que den veredictos claros ese día;
      tener una segunda ruta preparada por si el tiempo real no se presta.
- [ ] **Tener abierta la Figura 5.1 y la de arquitectura** fuera de la presentación, por si hay que
      volver a una sin navegar diapositivas.
- [ ] **Ensayar con cronómetro** al menos dos veces; si sobra tiempo, se amplía la demo, no el
      texto.
- [ ] **Llevar la presentación en un segundo formato** (PDF) por si falla el programa.
