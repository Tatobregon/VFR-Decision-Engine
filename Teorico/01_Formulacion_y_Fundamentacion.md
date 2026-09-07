# 1. FORMULACIÓN Y FUNDAMENTACIÓN DEL PROYECTO

> **Versión revisada.** Documento del autor con correcciones aplicadas sobre citas,
> precisión técnica y redacción. Los puntos que requieren una decisión o un dato del
> autor están marcados con ⬜ y listados al final.

---

## 1.1. Pedido del Usuario y Problematización

### 1.1.1. El pedido del usuario

El presente proyecto se origina en el requerimiento formulado por **CIAC Aeroatelier — Polo Aerodeportivo & Flight School** ⬜ *[confirmar la denominación formal exacta tal como figura en la nota firmada; debe coincidir con la portada]*, en su carácter de organización dedicada a la instrucción y a la operación de aeronaves de aviación general. El comitente manifestó la necesidad de contar con una herramienta de apoyo a la decisión meteorológica que asistiera a sus pilotos e instructores en la evaluación previa al vuelo de las condiciones para operar con seguridad bajo VFR, integrando en un único instrumento la información que hoy deben recopilar e interpretar de fuentes dispersas y de manera manual.

El requerimiento, en sus términos operativos, solicita un sistema capaz de:

- reunir automáticamente la información meteorológica y aeronáutica oficial correspondiente a un vuelo planificado (aeródromo de origen, destino, horario y aeronave);
- evaluar esa información según criterios normativos y operacionales explícitos, y
- devolver un veredicto claro y justificado —del tipo GO / CAUTION / NO GO— que sirva de insumo a la decisión final del piloto al mando, sin sustituir su autoridad.

La condición del aeródromo de base resulta significativa para dimensionar el problema. La institución opera desde el Aeródromo de La Cumbre, provincia de Córdoba, que no cuenta con estación meteorológica aeronáutica emisora de informes METAR: la operación se planifica, por lo tanto, sin observación oficial en el propio campo, a partir de reportes de aeródromos distantes, de la apreciación visual directa o de servicios meteorológicos de uso general no aeronáutico. A ello se suma su emplazamiento en las Sierras Chicas, un entorno de relieve complejo donde los fenómenos locales —nubosidad de ladera, canalización del viento en los valles, formación de niebla en las primeras horas— pueden diferir sensiblemente de las condiciones reportadas en la llanura próxima. El comitente opera, en consecuencia, en el escenario que concentra la dificultad que este trabajo aborda.

El acta de conformidad y la definición del alcance acordado con el comitente se incluyen en el **Anexo N.º 1 — Acta de conformidad**.

### 1.1.2. Antecedentes: el vuelo visual en la aviación general

La aviación general comprende el conjunto de operaciones aéreas civiles distintas del transporte comercial regular: instrucción de vuelo, aviación deportiva y recreativa, y navegaciones privadas entre aeródromos. En la República Argentina esta actividad se sostiene sobre una extensa red de aeroclubes y aeródromos —muchos de ellos no controlados y sin servicio meteorológico en el lugar— y opera, en su gran mayoría, con aeronaves monomotoras livianas bajo Reglas de Vuelo Visual (VFR).

El vuelo VFR se define por un principio operacional básico: la aeronave se conduce mediante referencia visual con la superficie terrestre y manteniendo separación de las nubes, dentro de condiciones meteorológicas de vuelo visual (VMC) cuyos mínimos de visibilidad y distancia a las nubes fija la regulación (Organización de Aviación Civil Internacional [OACI], 2005, cap. 4; Administración Nacional de Aviación Civil [ANAC], 2022, Parte 91). Bajo VFR, la responsabilidad de "ver y evitar" —el terreno, los obstáculos, las nubes y el tránsito— recae directamente sobre el piloto. Esa autonomía es la que otorga al vuelo visual su flexibilidad y su bajo costo, pero es también la que lo vuelve intrínsecamente dependiente del estado meteorológico.

### 1.1.3. El problema: el ingreso inadvertido a condiciones instrumentales

El supuesto que sostiene toda la operación visual —la existencia de referencia con la superficie— es, precisamente, el que la meteorología puede retirar. Cuando un piloto que opera bajo reglas visuales ingresa, de manera inadvertida o por una evaluación optimista de las condiciones, en condiciones meteorológicas instrumentales (IMC) —niebla, techos bajos, precipitación o visibilidad reducida—, se configura el escenario que la literatura de seguridad operacional denomina *VFR-into-IMC*.

Su gravedad no reside tanto en su frecuencia como en su desproporcionada letalidad. Al perder las referencias visuales externas, el piloto sin habilitación ni entrenamiento reciente en vuelo por instrumentos queda expuesto a la desorientación espacial: el sistema vestibular, privado de la corrección que aporta la visión, genera percepciones de actitud erróneas que conducen con rapidez a la pérdida de control.

Un estudio de la Universidad de Illinois (Bryan et al., 1954), habitual en la formación aeronáutica, sometió a veinte pilotos sin instrucción por instrumentos a vuelo simulado sin referencia visual: la totalidad de ellos alcanzó una actitud peligrosa incipiente, con un tiempo medio de 178 segundos. El dato, divulgado bajo el rótulo de "178 segundos para vivir", requiere una precisión: el ensayo se realizó en condiciones deliberadamente exigentes —aeronave al peso máximo y con el centro de gravedad en su posición más retrasada, instrumentos de actitud, rumbo y velocidad vertical cubiertos, participantes sin experiencia previa alguna en vuelo por instrumentos— y midió el intervalo transcurrido hasta la pérdida incipiente de control, no un tiempo de supervivencia. De hecho, su conclusión principal fue la inversa de la que suele atribuírsele: tras una instrucción breve y específica, todos los participantes menos uno lograron mantener el control y ejecutar con éxito el viraje de 180 grados de escape. Leído en sus propios términos, el estudio no establece una fatalidad inevitable, sino la extrema rapidez con que se degrada el control cuando falta el entrenamiento y, con ello, la estrechez del margen disponible una vez producido el ingreso a IMC.

Los informes periódicos de seguridad de la aviación general confirman esa lectura con datos agregados de accidentología. El 28.º informe Joseph T. Nall del Air Safety Institute de la Aircraft Owners and Pilots Association, que analiza la aviación general no comercial de ala fija sobre la serie 2007-2016, permite dimensionar el fenómeno con precisión (AOPA Air Safety Institute, 2019).

Su frecuencia es acotada: en 2016 los accidentes atribuidos al estado del tiempo fueron 23 sobre 755 accidentes relacionados con el piloto, alrededor del 3 %. Su letalidad, en cambio, es desproporcionada: a lo largo de la década la categoría meteorológica acumuló 345 accidentes fatales sobre 473, esto es, una letalidad del **72,9 %**, con un rango anual comprendido entre el 52 % y el 79 %. Dentro de esa categoría, el ingreso involuntario a condiciones instrumentales es el tipo dominante: 13 de los 23 accidentes meteorológicos de 2016, 7 de ellos fatales.

El contraste con otra categoría del mismo informe termina de dimensionarlo. Los accidentes de aterrizaje —donde se manifiesta la pérdida de control direccional asociada al viento— fueron 3410 en la misma década, más de siete veces más frecuentes que los meteorológicos, pero produjeron 51 desenlaces fatales: una letalidad del **1,5 %**. Un accidente meteorológico tiene, por lo tanto, una probabilidad de resultar fatal casi cincuenta veces mayor que uno de aterrizaje.

Se trata, en consecuencia, de un evento de baja frecuencia y consecuencia máxima. Esta asimetría no es solo un argumento de la problematización: estos mismos datos son los que fundamentan cuantitativamente la ponderación de los criterios del modelo de riesgo (§ 3.1.4).

La conclusión operacional es inequívoca: el accidente por pérdida de referencia visual es, en la enorme mayoría de los casos, evitable, porque su prevención no ocurre en el aire —donde el margen de maniobra es escaso y el desenlace veloz— sino en tierra, en el momento previo a la decisión de despegar.

### 1.1.4. La decisión "ir / no ir" como problema semi-estructurado

La instancia en la que ese riesgo se acepta o se rechaza es la decisión de despacho o decisión "ir / no ir": la evaluación que el piloto al mando realiza, antes del vuelo, sobre si las condiciones meteorológicas reales y pronosticadas —en origen, en ruta y en destino— son compatibles con una operación segura.

En apariencia es una decisión binaria; en su fundamento es multicriterio y semi-estructurada. Es multicriterio porque depende de la interacción de muchas variables —visibilidad, techo de nubes, componente de viento cruzado, ráfagas, fenómenos presentes, tendencia pronosticada, luz diurna disponible, estado del aeródromo y capacidades de la aeronave—, cada una con su escala y su umbral de tolerancia. Y es semi-estructurada en el sentido de Gorry y Scott Morton (1971): combina componentes plenamente objetivables —los mínimos meteorológicos son valores normativos precisos— con componentes que tradicionalmente se resuelven por juicio experto no formalizado, como el modo en que esos factores se ponderan e integran cuando ninguno es, por sí solo, determinante. Esta naturaleza es la que la vuelve un candidato idóneo para el apoyo mediante un sistema de decisión.

### 1.1.5. Insuficiencia de las soluciones actuales

En la práctica corriente, la decisión "ir / no ir" se apoya en fuentes de información dispersas y en la interpretación individual del piloto, lo que da lugar a tres debilidades recurrentes:

1. **Integración informal de los factores.** En ausencia de un método explícito, la ponderación de las variables queda librada a la experiencia y al estado del decisor, con la consiguiente variabilidad e inconsistencia entre pilotos y situaciones.

2. **Sesgos de la decisión.** Los factores humanos documentan la incidencia de sesgos como la presión por completar la misión (*plan continuation bias* o *get-there-itis*) y el exceso de confianza, que empujan la evaluación hacia el lado optimista justo cuando las condiciones se deterioran (Goh y Wiegmann, 2002; Jensen, 1995).

3. **Cobertura desigual del territorio.** Los aeródromos sin estación meteorológica quedan, en la práctica, con información degradada, cuando son a menudo los de operación más vulnerable.

Las herramientas existentes no cubren adecuadamente este vacío. Los servicios oficiales (SMN, AIS de EANA/ANAC) proveen la información en crudo —METAR, TAF, NOTAM, cartas— pero exigen formación específica para su lectura y no la integran en un veredicto ligado al vuelo planeado; además, esa información se emite únicamente en los principales centros aeroportuarios, mientras que un porcentaje considerable de los vuelos de aviación general se desarrolla en aeródromos rurales. Las aplicaciones aeronáuticas comerciales de referencia (ForeFlight, Garmin Pilot y similares) están orientadas al marco regulatorio y a la cartografía de otros países —principalmente de los Estados Unidos—, son de pago y ofrecen una cobertura limitada del territorio argentino y de sus aeródromos rurales. Las aplicaciones meteorológicas de uso general (por ejemplo, Windy) presentan el estado del tiempo de forma accesible pero no aplican el criterio aeronáutico ni emiten una decisión operacional. En ninguno de los casos se combinan las cuatro condiciones que el problema exige: (i) aplicación del criterio normativo argentino (ANAC/OACI); (ii) emisión de un veredicto GO / CAUTION / NO GO ponderado por la aeronave concreta; (iii) cobertura de los aeródromos sin estación mediante modelo numérico de pronóstico; y (iv) gratuidad y disponibilidad en español para el piloto de aviación general argentino.

Es esta brecha —entre la información disponible y una decisión estructurada, trazable y adaptada al contexto argentino— la que el presente proyecto se propone cerrar.

---

## 1.2. Objetivos

### 1.2.1. Objetivo general

Desarrollar un sistema de apoyo a la decisión meteorológica para la operación de aviación general bajo reglas de vuelo visual (VFR) en el territorio argentino, que integre las fuentes de información oficiales en un modelo de riesgo explícito, trazable y reproducible, y que asista al piloto al mando en la decisión "ir / no ir" mediante un veredicto justificado GO / CAUTION / NO GO, con el fin de reducir su exposición al riesgo de ingreso inadvertido a condiciones meteorológicas instrumentales (VFR-into-IMC), sin sustituir su autoridad ni su responsabilidad.

### 1.2.2. Objetivos específicos

Los objetivos específicos se organizan en dos planos: las **metas técnicas**, que definen la construcción del sistema, y las **metas operacionales y de seguridad** —equivalentes a las "metas de negocio" de la plantilla, adaptadas a un contexto de seguridad operacional sin fin de lucro—, que definen el valor que el sistema debe entregar al usuario.

**Metas técnicas**

1. Integrar las fuentes de datos oficiales en un flujo de procesamiento homogéneo: observaciones METAR y pronósticos TAF (aviationweather.gov) para los aeródromos con estación, modelo numérico de pronóstico (Open-Meteo) para los que no la poseen, NOTAM del servicio AIS de ANAC y el registro nacional de aeródromos (ANAC/MADHEL), unificándolas en un contrato de datos único.
2. Diseñar un modelo de riesgo multicriterio que combine reglas categóricas de rechazo inmediato (*hard blockers*) con funciones de riesgo continuas por factor, integradas en una evaluación cuantitativa acotada.
3. Derivar la ponderación de los criterios mediante un método formal y reproducible —el Proceso Analítico Jerárquico (AHP)—, de modo que los pesos del modelo no sean arbitrarios sino auditables y fundamentados en la accidentología.
4. Incorporar una barrera no-compensatoria al modelo, que impida que un factor individualmente inhabilitante (por ejemplo, un viento cruzado superior al máximo demostrado de la aeronave) quede diluido por el promedio ponderado.
5. Calibrar los umbrales de decisión por anclaje normativo y analizar la robustez del sistema mediante un estudio de sensibilidad de sus parámetros.
6. Garantizar la escalabilidad del sistema a la totalidad del territorio —los 561 aeródromos del registro oficial— y a los cinco perfiles de aeronave contemplados, de modo que los límites de riesgo se deriven del perfil seleccionado y de los datos del aeródromo, y no de un caso particular.
7. Proveer una interfaz web usable que presente el veredicto con su justificación —factor dominante y factor limitante—, la cartografía de la ruta y el cierre del ciclo de decisión mediante la exportación de un borrador de plan de vuelo conforme al modelo OACI/ANAC.

**Metas operacionales y de seguridad**

1. Reducir la variabilidad e inconsistencia de la decisión de despacho, entregando un veredicto estructurado y comprensible que sustituya la integración informal de factores por un criterio explícito y homogéneo.
2. Extender la evaluación a los aeródromos sin estación meteorológica, que en la práctica actual quedan sin información aeronáutica útil, mediante el uso de modelo numérico de pronóstico punto a punto.
3. Alcanzar una concordancia igual o superior al 90 % con el criterio normativo ANAC/OACI sobre una batería de escenarios de referencia, sin incurrir en sub-avisos peligrosos —esto es, sin que el sistema emita un veredicto menos restrictivo que el que la norma exige—, adoptando así, de manera medible, el sesgo conservador propio de la decisión de seguridad.
4. Lograr la aceptación y el uso efectivo del sistema por parte de pilotos reales en su operación previa al vuelo, como evidencia de su utilidad y usabilidad (validación de campo).
5. Ofrecer una herramienta gratuita y en español, accesible desde un navegador, orientada específicamente a pilotos y estudiantes de piloto.

---

## 1.3. Justificación y Alcance

### 1.3.1. Justificación: relevancia y pertinencia del enfoque

**Relevancia del problema.** La justificación primera del proyecto es de seguridad operacional. Según se expuso, el ingreso inadvertido a condiciones instrumentales es una de las causas de accidente de mayor letalidad en la aviación general (Goh y Wiegmann, 2002), y su prevención es plenamente eficaz únicamente en tierra, en la decisión previa al vuelo. Un instrumento que estructure y mejore esa decisión actúa, por tanto, sobre el punto exacto donde la intervención tiene mayor rendimiento en términos de vidas. A ello se suma una relevancia de equidad en el acceso a la información: al cubrir los aeródromos sin estación meteorológica y ofrecerse de forma gratuita y en español, el sistema lleva una capacidad de evaluación que hoy está reservada a quien domina la interpretación de la información aeronáutica cruda a un grupo mucho más amplio de pilotos y estudiantes de piloto.

**Pertinencia del enfoque de inteligencia artificial.** El problema reúne las condiciones que hacen apropiado un abordaje de inteligencia artificial en su vertiente simbólica (Newell y Simon, 1976) —esto es, de sistemas basados en conocimiento y de análisis de decisión multicriterio (Roy, 1996)— y no en su vertiente de aprendizaje automático. Conviene precisarlo desde el inicio, dado que el uso corriente del término "inteligencia artificial" tiende a identificarlo con el aprendizaje automático: el presente trabajo no emplea redes neuronales ni aprendizaje supervisado, sino un motor de reglas y funciones de riesgo (Buchanan y Shortliffe, 1984) que codifica conocimiento normativo y experto, sostenido sobre un trabajo sustantivo de ciencia de datos —adquisición, integración y normalización de fuentes heterogéneas—. Esta elección es deliberada y se fundamenta en profundidad en la Sección 3.1; aquí basta con anticipar sus tres razones:

- el dominio exige explicabilidad total del veredicto, propiedad natural de un modelo simbólico y problemática en un modelo estadístico opaco (Rudin, 2019);
- no existen datos de casos reales etiquetados —pares de condiciones meteorológicas y decisión correcta validada por pilotos— que permitan un entrenamiento supervisado representativo, limitación que en un dominio con conocimiento ya formalizado desaconseja los enfoques puramente inductivos (Marcus, 2020), y
- ese conocimiento está disponible en la regulación y en la literatura de seguridad, de modo que el problema es de representación de conocimiento, no de inducción de patrones (Davis, Shrobe y Szolovits, 1993).

Es precisamente esta clase de problema —una decisión crítica, de base normativa clara pero de integración multicriterio no trivial, sin datos de entrenamiento pero con conocimiento experto disponible— la que constituye el terreno propio de la inteligencia artificial simbólica y de los sistemas de apoyo a la decisión (Belton y Stewart, 2002; Simon, 1977). El uso de IA no es aquí un fin ornamental sino el medio adecuado para transformar un juicio experto disperso e inconsistente en un razonamiento explícito, reproducible y escalable a la totalidad del territorio.

**Encuadre ético del uso de IA.** La pertinencia del enfoque incluye una delimitación de responsabilidad: el sistema asiste, no decide ni autoriza. El veredicto es un insumo estructurado para el piloto al mando, quien conserva íntegramente la autoridad y la responsabilidad de la decisión final y de la información que eventualmente presente ante la autoridad aeronáutica. Coherente con ello, el criterio del sistema es deliberadamente conservador: ante ambigüedad o ausencia de datos se inclina hacia la advertencia, porque en la decisión de despacho el costo de omitir una advertencia —habilitar un vuelo riesgoso— supera ampliamente al de advertir de más —desalentar un vuelo que hubiera sido seguro—.

### 1.3.2. Alcance del proyecto

**Funciones comprendidas en el desarrollo (dentro del alcance)**

- Evaluación meteorológica GO / CAUTION / NO GO para vuelos VFR de aviación general, sobre la totalidad de los 561 aeródromos del registro oficial ANAC/MADHEL y cinco perfiles de aeronave (Pipistrel Alpha Trainer, Cessna 152, Cessna 172, Piper PA-28 y Diamond DA40), representativos de los tipos de mayor difusión en la instrucción y la aviación general.
- Adquisición automática de datos oficiales: METAR y TAF cuando el aeródromo posee estación, modelo numérico de pronóstico (NWP) cuando no la posee, NOTAM del servicio AIS de ANAC y datos del registro de aeródromos.
- Motor de riesgo de tres capas: reglas de rechazo inmediato (*hard blockers*), puntuación blanda multicriterio con pesos derivados por AHP, y barrera no-compensatoria para los factores inhabilitantes por sí solos.
- Calibración de umbrales por anclaje normativo y análisis de sensibilidad de los pesos.
- Funciones de juicio operacional complementarias: selección automática de la pista más favorable, cálculo de la altitud de crucero VFR por regla hemisférica, advertencia de altitud-densidad, bloqueo por operación nocturna, evaluación de NOTAM que inhabilitan el aeródromo, mínimos personales según la experiencia del piloto y advertencia de capa de nubes por debajo del nivel de crucero.
- Ruteo VFR por corredores visuales publicados para las TMA de Buenos Aires y Córdoba, y un modo IFR complementario (aerovías inferiores con su altitud mínima de enrutamiento, MEA) como alternativa cuando el terreno lo aconseja.
- Interfaz web con cartografía interactiva, perfil vertical del terreno y exportación de un borrador de plan de vuelo conforme al modelo OACI (AIP ENR 1.10).

**Funciones excluidas del desarrollo actual (fuera del alcance)**

- El sistema no radica, transmite ni presenta planes de vuelo ante la autoridad; esa acción y la responsabilidad asociada corresponden exclusivamente al piloto al mando.
- No constituye un sistema de navegación en tiempo real ni un instrumento de a bordo; su uso es previo al vuelo y no sustituye a los instrumentos ni a la información oficial en vuelo.
- No genera pronóstico meteorológico propio: consume el de las fuentes oficiales y de modelos numéricos externos.
- No emplea aprendizaje automático, por las razones expuestas; en particular, no realiza entrenamiento sobre datos históricos.
- No consume la telemetría registrada por el instrumental de a bordo de la flota del comitente. Es el único activo de datos propio de la organización relevado (§ 2.2.5) y su exclusión es deliberada: un límite calibrado sobre un único tipo de aeronave en un único aeródromo no sería generalizable a los cinco perfiles y los 561 aeródromos que el sistema cubre. Se contempla como trabajo futuro (§ 6.3).
- No implementa la excepción OACI de mínimos reducidos para aeronaves de velocidad igual o menor a 140 kt (criterio conservador), ni utiliza la normativa FAA como referencia.
- No incorpora archivo histórico de datos (por ejemplo, Iowa State Mesonet), al no ser necesario para la evaluación en tiempo real.
- No es una herramienta de planificación IFR completa: el modo IFR es un complemento acotado, no un sustituto de la planificación por instrumentos.
- Quedan planteadas como trabajo futuro (§ 6.3), y por tanto fuera del desarrollo actual, la calibración empírica con casos reales etiquetados por pilotos, un módulo comunitario de correcciones tipo PIREP a la base de aeródromos, y el ruteo VFR con evitación automática de terreno.

---

## Referencias citadas en esta sección

> *Normas APA 7.ª edición. Todas las entradas fueron verificadas contra la fuente
> editorial. Se consolidan luego en la Sección 7.*

Administración Nacional de Aviación Civil. (2022). *Regulaciones Argentinas de Aviación Civil (RAAC), Parte 91: Reglas de vuelo y operación general*. ANAC. https://www.argentina.gob.ar/anac/resoluciones-disposiciones-y-otras-normas-aeronauticas/raac

AOPA Air Safety Institute. (2019). *28th Joseph T. Nall report: General aviation accidents in 2016*. Aircraft Owners and Pilots Association. https://www.aopa.org/-/media/Files/AOPA/Home/Training-and-Safety/Nall-Report/28th-nall-report/nallreport_2019_final2.pdf

Belton, V., & Stewart, T. J. (2002). *Multiple criteria decision analysis: An integrated approach*. Kluwer Academic Publishers. https://doi.org/10.1007/978-1-4615-1495-4

Bryan, L. A., Stonecipher, J. W., & Aron, K. (1954). *180-degree turn experiment* (Aeronautics Bulletin No. 11). University of Illinois.

Buchanan, B. G., & Shortliffe, E. H. (Eds.). (1984). *Rule-based expert systems: The MYCIN experiments of the Stanford Heuristic Programming Project*. Addison-Wesley.

Davis, R., Shrobe, H., & Szolovits, P. (1993). What is a knowledge representation? *AI Magazine, 14*(1), 17-33. https://doi.org/10.1609/aimag.v14i1.1029

Goh, J., & Wiegmann, D. A. (2002). Human factors analysis of accidents involving visual flight rules flight into adverse weather. *Aviation, Space, and Environmental Medicine, 73*(8), 817-822.

Gorry, G. A., & Scott Morton, M. S. (1971). A framework for management information systems. *Sloan Management Review, 13*(1), 55-70.

Jensen, R. S. (1995). *Pilot judgment and crew resource management*. Avebury Aviation.

Marcus, G. (2020). *The next decade in AI: Four steps towards robust artificial intelligence*. arXiv. https://arxiv.org/abs/2002.06177

Newell, A., & Simon, H. A. (1976). Computer science as empirical inquiry: Symbols and search. *Communications of the ACM, 19*(3), 113-126. https://doi.org/10.1145/360018.360022

Organización de Aviación Civil Internacional. (2005). *Anexo 2 al Convenio sobre Aviación Civil Internacional: Reglamento del aire* (10.ª ed.). OACI.

Roy, B. (1996). *Multicriteria methodology for decision aiding*. Kluwer Academic Publishers. https://doi.org/10.1007/978-1-4757-2500-1

Rudin, C. (2019). Stop explaining black box machine learning models for high stakes decisions and use interpretable models instead. *Nature Machine Intelligence, 1*(5), 206-215. https://doi.org/10.1038/s42256-019-0048-x

Simon, H. A. (1977). *The new science of management decision* (ed. rev.). Prentice-Hall.

> **Retiradas de esta sección** (la lista de referencias solo debe contener lo efectivamente citado):
> Haugeland (1985) y Jackson (1998), por redundancia; Doshi-Velez y Kim (2017), cubierta por Rudin.
> **Wiggins y O'Hare (1995)** —*Journal of Experimental Psychology: Applied, 1*(4), 305-320— y
> **Garcez y Lamb (2023)** —*Artificial Intelligence Review, 56*(11), 12387-12406— se reservan para
> los mínimos personales por experiencia y para el trabajo futuro (§ 6.3), respectivamente.

---

## ⬜ Pendientes de esta sección

1. **Denominación formal del comitente** (1.1.1): confirmar el nombre exacto que figura en la nota firmada y usar el mismo en la portada.
2. **Anexo N.º 1**: se unificó como "Acta de conformidad". Verificar que el documento efectivamente se titule así.
3. **Figura del requerimiento**: definir si la nota firmada se reproduce como figura dentro de 1.1.1 o queda únicamente en el Anexo N.º 1.
4. **Enmienda vigente de la RAAC Parte 91** al momento de la entrega (Resolución ANAC N.° 239/2022 y enmiendas posteriores).
5. **DOI faltantes** (opcional): solo restan Goh y Wiegmann (2002) y Wiggins y O'Hare (1995), cuyos DOI no se pudieron confirmar contra la fuente editorial. Si se agregan, deben tomarse de la página oficial del editor, no de repositorios secundarios.
