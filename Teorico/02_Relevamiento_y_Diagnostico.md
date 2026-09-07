# 2. RELEVAMIENTO ORGANIZACIONAL Y DIAGNÓSTICO

> **Nota de reestructuración.** Este capítulo reorganiza el borrador previo para
> ajustarlo a la estructura obligatoria. La numeración anterior se había separado de
> ella: el relevamiento tecnológico no existía como apartado propio —el equipamiento
> estaba descrito dentro del análisis de la organización— y el cuadro de diagnóstico
> figuraba como 2.2.1 en lugar de 2.3. Se conserva íntegramente el contenido original
> y se lo redistribuye. Los apartados agregados están señalados; los datos que solo
> puede aportar el autor están marcados con ⬜.

---

## 2.1. Análisis de la Organización

### 2.1.1. Descripción de la organización

Aeroatelier es una organización clasificada como **CIAC** (Centro de Instrucción
Aeronáutica Civil), dedicada a disciplinas aerodeportivas: paracaidismo, parapentismo,
vuelo a vela, vuelos de bautismo, escuela de vuelo, escuela de paracaidismo y exhibiciones
aéreas, entre otras.

Opera con base en el **Aeródromo de La Cumbre**, provincia de Córdoba, siendo uno de los
aeroclubes que lo conforman. Si bien no existe una fuente oficial que permita afirmar que
se trate de una escuela de prestigio establecido, la referencia del ambiente la ubica
entre las más innovadoras e influyentes del país. Estas características determinan su
posicionamiento: entusiastas de la aviación, alumnos piloto que buscan una formación
moderna por fuera del aeroclub tradicional, y turismo de aventura.

El presente trabajo no aborda la dimensión comercial de la organización, dado que la
solución desarrollada no está orientada a optimizar su actividad económica. El enfoque se
sitúa en la **seguridad operacional de su actividad principal**: la instrucción de vuelo y
la formación de pilotos.

Existe un punto de la operación que resulta determinante para comprender el origen del
proyecto. Una vez que un alumno obtiene su licencia de piloto privado, Aeroatelier pone
sus aeronaves a disposición para alquiler libre. Esto forma parte del negocio y
constituye una oportunidad cada vez que un piloto quiere volar, pero introduce
simultáneamente un riesgo: un piloto recién licenciado, con poca experiencia acumulada,
puede exponerse a sí mismo, a sus pasajeros y a la aeronave a las situaciones descritas en
el § 1.1.3. De esa constatación nace la idea de construir un motor de decisión VFR basado
en reglas que reduzca o mitigue ese riesgo.

⬜ *[Completar la estructura funcional que pide la consigna: cantidad de instructores,
alumnos activos y pilotos con acceso al alquiler libre; quién autoriza la salida de una
aeronave; si existe una figura de jefe de instrucción o responsable de seguridad
operacional.]*

### 2.1.2. Mercado e inserción territorial

La operación se inserta en la red de aeródromos de la República Argentina, muchos de los
cuales —especialmente en zonas no metropolitanas— carecen de servicio meteorológico local.
El propio aeródromo de base se encuentra en esa condición: no cuenta con estación emisora
de informes METAR, de modo que la planificación se realiza sin observación oficial en el
campo.

Esto configura un escenario donde la seguridad operacional —la mitigación del
VFR-into-IMC— compite permanentemente con la presión por completar la misión
(*get-there-itis*), sesgo humano crítico en la aviación general documentado en el § 1.1.5.

A ello se suma el emplazamiento en las Sierras Chicas, un entorno de relieve complejo donde
los fenómenos locales pueden diferir sensiblemente de las condiciones reportadas en la
llanura próxima. La organización opera, en consecuencia, en el escenario que concentra la
dificultad que este trabajo aborda, y esa condición no es incidental: es la que determinó
buena parte de las decisiones de diseño del sistema (§ 3.2.5).

### 2.1.3. Flujo de la información en la decisión de despacho *(apartado agregado)*

La consigna señala que resulta crucial comprender cómo circula la información dentro de la
entidad. En el caso que nos ocupa, el circuito relevante es el que precede a cada vuelo, y
su relevamiento revela que **la información existe pero no fluye: se recolecta**.

El piloto o el instructor que va a volar reúne, por su cuenta y antes de cada salida:

1. **Condiciones meteorológicas**, que al no haber estación en el campo obtiene de reportes
   de aeródromos distantes, de la apreciación visual directa o de servicios meteorológicos
   de uso general no aeronáutico.
2. **Estado del aeródromo de destino y de la ruta**, mediante consulta de NOTAM.
3. **Capacidades de la aeronave asignada**, del manual de vuelo.
4. **Su propia experiencia y estado**, sin instrumento formal que lo estructure.

Tres rasgos de ese circuito constituyen el diagnóstico:

**No queda registro.** La evaluación se realiza mentalmente y se descarta. No existe un
asiento de qué condiciones se consideraron ni con qué criterio se resolvió, de modo que la
organización no puede revisar sus propias decisiones ni detectar patrones.

**No es homogéneo.** Cada piloto integra los factores según su experiencia y su criterio,
por lo que dos personas pueden resolver de manera distinta ante la misma información.

**No se transmite.** El criterio del instructor experimentado no queda disponible para el
piloto recién licenciado que alquila la aeronave un sábado, que es precisamente el caso de
mayor exposición identificado en el § 2.1.1.

⬜ *[Confirmar con el comitente: qué fuentes meteorológicas consultan hoy efectivamente
(aplicaciones, sitios, radio); si existe algún registro escrito o planilla previa al vuelo;
si el alquiler libre requiere alguna autorización o el piloto dispone de la aeronave por
sí mismo.]*

---

## 2.2. Relevamiento Tecnológico *(apartado agregado)*

La consigna requiere detallar el hardware y software existente, las capacidades de
conectividad y las fuentes de datos disponibles que podrían servir como insumo del modelo.

### 2.2.1. Equipamiento de a bordo

La flota está compuesta por **cinco aeronaves Pipistrel Alpha Trainer**, equipadas con
instrumental digital avanzado (EFIS / *glass cockpit*). Estas pantallas registran
telemetría precisa de cada vuelo: régimen del motor, temperaturas, parámetros de vuelo,
consumo en tiempo real y trayectorias GPS.

⬜ *[Precisar marca y modelo del EFIS instalado, y confirmar si la telemetría se descarga
y almacena efectivamente o solo se presenta en vuelo.]*

### 2.2.2. Equipamiento en tierra

Computadoras administrativas, utilizadas entre otras funciones para la consulta
meteorológica previa al vuelo, y **simuladores de vuelo sintéticos** empleados en la
instrucción.

⬜ *[Detallar si existe software de gestión de la operación —programación de vuelos,
control de horas de célula y motor, facturación— y de qué tipo.]*

### 2.2.3. Conectividad

⬜ *[Dato requerido por la consigna y no relevado aún. Determinar si el aeródromo dispone
de conexión a internet fija, cuál es la cobertura de datos móviles en el campo, y si el
piloto puede acceder a información en línea desde el lugar o debe hacerlo antes de
trasladarse. **Es determinante para el sistema desarrollado**, que es una aplicación web y
requiere conexión: si la cobertura en el campo fuera deficiente, la herramienta debería
utilizarse antes del traslado al aeródromo, lo que constituye una restricción de uso a
declarar.]*

### 2.2.4. Fuentes de datos disponibles como insumo del modelo

El relevamiento distingue las fuentes que el sistema efectivamente consume de aquellas que
existen en la organización pero quedaron fuera del desarrollo actual. La distinción
importa: un relevamiento no inventaría únicamente lo que se aprovecha, sino también lo
disponible y la razón por la que no se utiliza.

| Fuente | Origen | Estado en el proyecto |
|---|---|---|
| METAR y TAF | aviationweather.gov | **Consumida.** Observación y pronóstico en aeródromos con estación. |
| Pronóstico numérico (NWP) | Open-Meteo | **Consumida.** Cubre los aeródromos sin estación, incluido el de base. |
| NOTAM | AIS de ANAC | **Consumida.** Estado operativo del aeródromo. |
| Registro de aeródromos y pistas | ANAC/MADHEL y OurAirports | **Consumida.** Coordenadas, elevación, cabeceras. |
| Elevación del terreno | SRTM vía Open-Topo-Data | **Consumida.** Perfil vertical y conflicto de terreno. |
| **Telemetría EFIS de la flota** | Instrumental de a bordo | **Relevada, no consumida.** Ver § 2.2.5. |

### 2.2.5. La telemetría de a bordo: por qué se releva y por qué no se utiliza

La telemetría registrada por los EFIS es el único activo de datos **propio de la
organización** identificado en el relevamiento, y merece un tratamiento explícito porque
su valor potencial es real y su exclusión es deliberada.

**Qué permitiría hacer.** El sistema evalúa el viento cruzado contra el límite del perfil
de aeronave; para el Alpha Trainer ese valor es de 12 nudos. Ese número no es un límite
operativo sino el **máximo demostrado** por el fabricante durante la certificación: la
componente cruzada más alta con la que un piloto de pruebas efectuó un aterrizaje
controlado en condiciones favorables. No describe lo que un alumno o un piloto recién
licenciado puede manejar con seguridad.

Las trayectorias GPS registradas por los EFIS, cruzadas con el viento observado en cada
aterrizaje, permitirían construir la **distribución empírica de la componente cruzada
efectivamente volada** por los pilotos de la escuela. Con ella sería posible contrastar
ese umbral contra la operación real y, sobre todo, calibrar con datos los mínimos
personales por nivel de experiencia (§ 3.2.4), que en el estado actual del sistema se
derivan de criterio y no de medición.

**Por qué queda fuera del desarrollo actual.** Tres razones, en orden de peso.

La primera es de **alcance**, y es la decisiva. El sistema es nacional y multi-aeronave:
cubre 561 aeródromos y cinco perfiles de aeronave. Un umbral calibrado sobre cinco
Pipistrel Alpha Trainer operando en un único aeródromo no es generalizable a esa cobertura.
Incorporarlo produciría exactamente el sesgo que el proyecto se propone evitar: una
solución ajustada a un caso particular presentada como si fuera general.

La segunda es de **acceso al dato**. Su utilización requiere un acuerdo de cesión con el
comitente, distinto del acta de conformidad que enmarca este trabajo.

La tercera es de **privacidad**. Los registros identifican vuelos de pilotos individuales
y permiten reconstruir su desempeño. Su tratamiento exige consentimiento informado y
anonimización, consideración que corresponde desarrollar en el análisis de impacto
(§ 5.3).

**Dónde queda.** Se incorpora a la hoja de ruta como línea de trabajo futuro (§ 6.3). Vale
señalar su valor potencial: en un trabajo cuya validación es de constructo —el sistema
reproduce la norma, pero no ha sido contrastado contra resultados reales— la telemetría es
el único insumo relevado con capacidad de aportar **validación empírica**. Es, por lo
tanto, el activo más valioso que el relevamiento identifica, aun cuando no se utilice en
esta etapa.

---

## 2.3. Cuadro de Diagnóstico y Propuesta

| Punto débil detectado | Propuesta de solución |
|---|---|
| **Integración informal de las variables.** El piloto evalúa las condiciones de manera subjetiva, combinando mentalmente techo, visibilidad y viento, lo que produce decisiones inconsistentes entre personas y situaciones. | **Motor de riesgo multicriterio con ponderación AHP.** El juicio subjetivo se reemplaza por un modelo explícito cuyos pesos se derivan de accidentología mediante una operación documentada, auditable y reproducible (§ 3.1.4). |
| **Puntos ciegos territoriales.** Los aeródromos sin estación meteorológica oficial —incluido el de base— obligan a operar con información degradada o con estimaciones visuales. | **Modelo numérico de pronóstico punto a punto.** Cobertura del 100 % de los aeródromos del registro nacional, con muestreo en anillo para tratar la incertidumbre orográfica en terreno complejo (§ 3.2.5). |
| **Dilución de factores inhabilitantes.** Un factor individualmente crítico —viento cruzado fuera de límite— puede quedar compensado mentalmente por un clima general favorable. | **Barrera no compensatoria.** Reglas estrictas por perfil de aeronave: un solo factor que exceda el límite impone un piso de veredicto que el resto de las condiciones no puede mejorar (§ 3.2.4). |
| **La decisión no deja registro** (§ 2.1.3). La evaluación se realiza mentalmente y se descarta, de modo que la organización no puede revisarla ni aprender de ella. | **Veredicto trazable con su justificación.** El sistema informa el factor dominante y el factor limitante de cada evaluación, y exporta un borrador de plan de vuelo, dejando constancia de la información considerada. |
| **El criterio experto no se transmite** (§ 2.1.3). El juicio del instructor no está disponible para el piloto recién licenciado que alquila la aeronave, que es el caso de mayor exposición. | **Codificación del conocimiento normativo y experto.** El sistema pone a disposición de cualquier piloto, en cualquier momento, el mismo criterio estructurado, con mínimos personales ajustables según el nivel de experiencia. |

El pedido del usuario y la propuesta general se especifican en el § 1.1.1 y el § 1.2.

---

## ⬜ Pendientes de esta sección

1. **Estructura funcional** (§ 2.1.1): instructores, alumnos activos, pilotos con acceso al
   alquiler libre, y quién autoriza la salida de una aeronave.
2. **Fuentes meteorológicas actuales** (§ 2.1.3): qué consultan hoy efectivamente, y si
   existe alguna planilla o registro previo al vuelo.
3. **EFIS** (§ 2.2.1): marca y modelo; si la telemetría se descarga y almacena o solo se
   presenta en vuelo.
4. **Software de gestión** (§ 2.2.2): si existe y de qué tipo.
5. **Conectividad** (§ 2.2.3): dato que la consigna pide expresamente y que aún no está
   relevado. Es el más importante de esta lista, porque condiciona el modo de uso del
   sistema.
6. **Verificar las remisiones** a §§ 5.3 y 6.3 cuando esas secciones estén redactadas.
