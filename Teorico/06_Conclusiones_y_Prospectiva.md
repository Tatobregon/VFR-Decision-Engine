# 6. CONCLUSIONES Y PROSPECTIVA

## 6.1. Balance de objetivos

El objetivo general del trabajo (§ 1.2.1) era desarrollar un sistema de apoyo a la decisión
meteorológica para la operación VFR en el territorio argentino, que integrara las fuentes
oficiales en un modelo de riesgo explícito, trazable y reproducible, y que asistiera al piloto
al mando con un veredicto justificado, accesible también mediante una interfaz en lenguaje
natural que no interviniera en su emisión. El sistema construido cumple ese objetivo: está
desplegado y operativo, cubre los 561 aeródromos del registro oficial y los cinco perfiles de
aeronave, emite un veredicto acompañado de su justificación e incorpora un asistente de
consulta confinado a la interfaz. El balance detallado por meta permite, sin embargo,
distinguir lo que se cumplió plenamente de lo que se cumplió con limitaciones, y precisar
dónde están esas limitaciones.

**Metas técnicas**

| Meta | Estado | Evidencia |
|---|---|---|
| 1. Integrar las fuentes oficiales en un flujo homogéneo con contrato de datos único | Cumplida | § 3.2.5.1, § 4.1.3 |
| 2. Seleccionar la fuente según el momento y evaluar cada aeródromo para el suyo | Cumplida | § 3.2.5.3 |
| 3. Modelo de riesgo con rechazos categóricos y funciones continuas por factor | Cumplida | § 3.2.4 |
| 4. Derivar los pesos por AHP, de forma reproducible y fundada en la accidentología | Cumplida | § 3.1.4; razón de consistencia 0,069 |
| 5. Barrera no compensatoria para los factores inhabilitantes | Cumplida | § 3.2.4, § 5.1.1 |
| 6. Calibrar los umbrales por anclaje normativo y analizar la sensibilidad | Cumplida | § 4.2.2, § 5.1.2 y § 5.1.3 |
| 7. Escalabilidad a los 561 aeródromos y a los cinco perfiles | Cumplida | § 4.1.2, § 4.3.4 |
| 8. Interfaz web con veredicto justificado, ruta, tramos y plan de vuelo | Cumplida | § 4.3.2 |
| 9. Asistente neurosimbólico con verificaciones en código y evaluación sobre casos etiquetados | Cumplida | § 3.2.7, § 5.1.4 |

Las metas técnicas se cumplieron en su totalidad, con dos precisiones que no alteran su estado
pero que corresponde consignar. Los umbrales de decisión se mantuvieron en los valores
calibrados originalmente aunque la calibración actual admite otros con un sobre-aviso menos,
por las razones expuestas en el § 5.1.2. Y las métricas del asistente describen su desempeño
sobre el conjunto con el que se desarrolló, no sobre casos independientes (§ 5.1.4).

**Metas operacionales y de seguridad**

| Meta | Estado |
|---|---|
| 1. Reducir la variabilidad de la decisión con un criterio explícito y homogéneo | Parcialmente cumplida |
| 2. Extender la evaluación a los aeródromos sin estación meteorológica | Cumplida con limitaciones |
| 3. Hacer utilizable el registro de aeródromos con tasa nula de invención | Cumplida |
| 4. No emitir GO por debajo del mínimo VFR, con concordancia de al menos el 90 % y sin sub-avisos | Cumplida con limitaciones |
| 5. Aceptación y uso efectivo por pilotos reales | Cumplida |
| 6. Herramienta gratuita, en español y accesible desde un navegador | Cumplida |

**Meta 1.** El sistema aporta lo que la meta requiere de él: un criterio explícito, idéntico
para todos los pilotos y reproducible, en el que las mismas condiciones producen siempre el
mismo veredicto y su justificación. Lo que no se midió es el efecto de ese criterio sobre la
variabilidad de las decisiones entre pilotos, que exigiría comparar decisiones tomadas con el
sistema y sin él en condiciones equivalentes. Por eso se la considera parcialmente cumplida.

**Meta 2.** La evaluación se extendió a los 513 aeródromos del registro que no tienen
observación directa (§ 4.1.2), mediante el pronóstico numérico y su muestreo en anillo. La
limitación está en la precisión de esa fuente, que no se validó frente a observaciones reales:
la infraestructura para hacerlo quedó construida, pero el estudio no se ejecutó. Las pruebas
de campo mostraron por qué importa (véase la meta 4).

**Meta 3.** El asistente no inventó ningún dato en los casos en que el registro no lo publica y
reconoció la ausencia en todos ellos (§ 5.1.4).

**Meta 4.** Sobre la batería de referencia el sistema alcanza el 95 % de concordancia sin
ningún sub-aviso, y el recorrido sistemático de la franja comprendida entre el rechazo
categórico y el mínimo VFR permitió convertir esa ausencia en una garantía por construcción
(§ 5.1.1). Esa garantía tiene, no obstante, un alcance que las pruebas de campo pusieron en
evidencia: vale sobre los datos que el sistema recibe, no sobre el error del pronóstico. En uno
de los alrededor de 50 vuelos evaluados en campo, el sistema emitió GO a partir de un pronóstico
numérico cuyo techo resultó, en la realidad, más bajo que el pronosticado y por debajo del
mínimo VFR. El motor procedió según su diseño; lo que falló fue la estimación de la fuente,
condicionada por la resolución de la grilla del modelo. El caso no invalida la meta, pero
precisa su lectura: el sistema garantiza no avisar menos de lo que exigen los datos disponibles,
y esa garantía es tan buena como la fuente. De ahí que la validación del pronóstico numérico
encabece el trabajo futuro (§ 6.3).

**Meta 5.** El sistema fue probado durante dos semanas por más de quince pilotos —instructores,
pilotos privados, pilotos comerciales y alumnos en la etapa de navegaciones— sobre alrededor de
50 vuelos, en paralelo con el procedimiento manual habitual, y su veredicto coincidió con el
resultado de ese procedimiento en todos los vuelos menos uno. Corresponde declarar tres
precisiones. La prueba siguió un protocolo informal, sin un instrumento estandarizado de
usabilidad. Se realizó a principios de septiembre de 2026, con una versión del motor anterior
a la selección de la fuente según el momento evaluado y a las correcciones en la
interpretación de la visibilidad y de las capas de nubes de los informes METAR y TAF, que
afectan a los aeródromos con estación; la base de la organización comitente es un aeródromo
sin estación, que se evalúa mediante el pronóstico numérico. Y el asistente de consulta,
desarrollado después, no llegó a probarse en campo.

**Meta 6.** El sistema es gratuito, está en español y se usa desde un navegador. Su gratuidad
depende de los niveles gratuitos de los servicios que lo sostienen, con dos consecuencias
visibles: el arranque de la instancia desplegada cuando lleva un tiempo sin uso (§ 5.1.5) y las
condiciones de uso de datos del modelo de lenguaje (§ 3.3.3).

---

## 6.2. Lecciones aprendidas

⬜ *[Borrador para revisión del autor.]*

### 6.2.1. La calidad de las fuentes no se presume: se verifica

La lección técnica más costosa del proyecto fue que el formato documentado de una fuente y el
formato que efectivamente entrega pueden no coincidir, y que la diferencia no siempre produce un
error visible. La visibilidad de los informes meteorológicos llegaba en millas terrestres sin
indicación de unidad; el registro oficial de aeródromos contenía registros que repetían el
identificador y la ubicación de otro aeródromo. En ambos casos el sistema funcionaba y
producía resultados verosímiles: el defecto solo apareció al contrastar esos resultados con los
datos crudos de la fuente.

De esa experiencia surgieron tres prácticas que el proyecto sostuvo desde entonces. Todo cambio
que involucra una fuente se verifica contra sus respuestas reales antes de incorporarse. Los
datos simulados de las pruebas reproducen el formato real de cada fuente, porque una simulación
que no la imita no detecta los errores de interpretación y termina confirmándolos. Y una
propiedad que se afirma de manera general se verifica recorriendo su dominio y no solo en un
conjunto de casos: la ausencia de sub-avisos, comprobada primero sobre 38 escenarios, solo
quedó garantizada después de recorrer la franja completa en la que podía fallar.

### 6.2.2. El alcance nacional como regla de diseño

El sistema nació con un alcance acotado: una organización comitente, un aeródromo y una
aeronave. Llevarlo a los 561 aeródromos del país y a cinco perfiles de aeronave no fue una
ampliación de datos, sino una revisión de supuestos que el alcance inicial había dejado
implícitos. Entre ellos, una penalización orográfica configurada para un solo aeródromo y una
heurística de ruteo calculada con la velocidad de una sola aeronave, que dejaba de ser válida
para las demás.

La lección fue convertir el alcance en una regla verificable: ninguna solución, umbral ni prueba
puede depender de un aeródromo o de una aeronave particular, y los cambios se comprueban con
aeródromos de distintas regiones y aeronaves de distinto porte. La escala nacional aportó
además conocimiento que el caso particular no mostraba —que el 91 % de los aeródromos carece de
observación directa, o que la cobertura del registro es inversa a la jerarquía del aeródromo—,
y ese conocimiento terminó determinando decisiones centrales del diseño.

### 6.2.3. Desarrollo asistido por inteligencia artificial

El proyecto se desarrolló con la asistencia de una herramienta de programación basada en
inteligencia artificial (§ 3.3.2), empleada también en la redacción y revisión de este
documento. Su aporte fue sustancial en la velocidad de implementación, en la exploración de
alternativas y en la sistematicidad de la verificación: buena parte de las mediciones, de los
recorridos exhaustivos y de las pruebas automatizadas del proyecto se construyeron con su
asistencia, y varios defectos —como los registros duplicados del padrón de aeródromos o la
franja sin cubrir bajo el mínimo VFR— se detectaron en ese proceso.

Sus riesgos fueron igualmente concretos. El más importante es que produce contenido verosímil
que no siempre está respaldado: una cifra desactualizada respecto del código, una afirmación sin
fuente, un nivel de detalle que no corresponde a la sección. Por eso su uso se sujetó a un
criterio fijo. El autor definió los requisitos y tomó las decisiones de diseño; todo cambio de
código se verificó con la suite de pruebas y, cuando correspondía, contra los datos reales;
toda cita se comprobó contra la fuente, y las cifras del documento se regeneraron desde el
código en lugar de transcribirse.

La lección de fondo es que la herramienta acelera la ejecución, pero no reemplaza el
conocimiento del dominio. Algunas de las correcciones más relevantes del modelo provinieron del
criterio del autor como piloto y no de la herramienta: que una ráfaga alineada con la pista no
debía vetar el vuelo, o que los cortes de 3 km y 500 ft no eran una norma sino una decisión del
sistema. La calidad del resultado dependió de esa división de roles, que es, en escala del
propio proceso de desarrollo, la misma que el sistema aplica entre su asistente de lenguaje y
su motor de decisión.

---

## 6.3. Trabajo futuro

La hoja de ruta se organiza en tres líneas, ordenadas según su aporte a la validez del sistema.

### 6.3.1. Validación y calibración con datos reales

1. **Validación del pronóstico numérico frente a observaciones.** Es la línea prioritaria, por
   lo que mostraron las pruebas de campo (§ 6.1, meta 4). La infraestructura está construida:
   clientes de archivo de observaciones y de pronósticos emitidos, y una estratificación de las
   estaciones argentinas según la rugosidad del terreno. El estudio mediría con qué frecuencia el
   veredicto obtenido del pronóstico difiere del que habría resultado de la observación, y
   permitiría evaluar un tratamiento explícito de la incertidumbre del pronóstico cuando las
   condiciones están próximas al mínimo VFR.
2. **Registro de decisiones reales de pilotos.** Si el sistema registrara las decisiones de los
   pilotos junto con su resultado, se constituiría con el tiempo el conjunto etiquetado que hoy
   no existe (§ 3.2.2). Ese conjunto permitiría una calibración empírica de los umbrales y, más
   adelante, evaluar enfoques aprendidos con un criterio de comparación real.
3. **Telemetría de a bordo.** Con consentimiento informado y anonimización, la telemetría de la
   flota del comitente permitiría contrastar los límites de viento cruzado y calibrar con datos
   los mínimos personales por experiencia (§ 2.2.5).
4. **Evaluación independiente del asistente.** Un conjunto de consultas no utilizado durante el
   desarrollo (§ 4.2.4) y una prueba de campo del asistente, que no llegó a realizarse.

### 6.3.2. Evolución del modelo

5. **Puntaje de la ruta con las condiciones del nivel de crucero.** Hoy el nivel interviene en
   el veredicto de los puntos en ruta como barrera, pero el puntaje ponderado sigue midiendo
   la superficie (§ 3.2.5.4); integrarlo exigiría derivar pesos para esos factores y
   recalibrar.
6. **Ruteo con puntos intermedios que no sean aeródromos**, para que la evitación de espacios
   aéreos y de terreno no dependa del aeródromo disponible más cercano, y para resolver el
   camino completo con los puntos de paso como obligatorios en lugar de tramo por tramo
   (§ 3.2.6).

### 6.3.3. Evolución del producto

7. **Perfiles de aeronave personalizados**, para que cada piloto cargue los límites y la
   performance de su aeronave en lugar de elegir entre los cinco perfiles predefinidos.
8. **Correcciones comunitarias a la base de aeródromos**, al estilo de los reportes de piloto,
   para mantener actualizada la información operativa que el registro oficial no publica o
   publica desactualizada.
9. **Condiciones de publicación pendientes**: la atribución que exige la licencia de datos de
   Open-Meteo y el aviso al usuario sobre las condiciones de uso de datos del modelo de lenguaje
   (§ 3.3.3).

---

## ⬜ Pendientes de esta sección

1. **Revisión del § 6.2** por el autor, en particular del § 6.2.3.
2. **Coherencia con los §§ 5.2 y 5.3**, que redacta el autor: el balance de las metas
   operacionales 4 y 5 se apoya en su contenido (alrededor de 50 vuelos, más de quince pilotos,
   un desacuerdo por el techo del pronóstico numérico).
