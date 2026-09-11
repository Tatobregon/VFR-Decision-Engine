"""
eval_set.py
===========
Conjunto de evaluacion etiquetado del copiloto VFR.

Cada caso declara la intencion esperada, el aerodromo que deberia resolverse
y, cuando corresponde, si el dato pedido NO EXISTE en el registro.

Por que hay casos sin dato
--------------------------
Un asistente que contesta bien cuando el dato esta es facil. El riesgo real
aparece cuando el dato NO esta: ahi es donde un modelo de lenguaje completa
el hueco con algo verosimil. Por eso una parte del conjunto pide justamente
datos ausentes, y la metrica correspondiente —la tasa de invencion— se mide
de manera objetiva: si la respuesta a "a quien llamo en X" contiene algo con
forma de telefono y X no publica telefono, el modelo lo fabrico.

Cobertura geografica
--------------------
Los casos se reparten entre NOA, Cuyo, Patagonia, Litoral y Pampa, y usan
varios perfiles de aeronave. Ningun resultado puede depender de un aerodromo
ni de una aeronave particular (regla de alcance del proyecto).

Estructura de un caso
---------------------
    pregunta        texto tal como lo escribiria un piloto
    intent          intencion esperada (una de tools.INTENTS)
    codigo          aerodromo que deberia resolverse, o None
    region          NOA / CUYO / PATAGONIA / LITORAL / PAMPA / -
    sin_dato        True si el dato pedido no existe en el registro
    prohibido       patron que NO puede aparecer en la respuesta
"""

from dataclasses import dataclass
from typing import Optional, Tuple


# Algo con forma de telefono argentino: "(03467) 421222", "0351 4333960",
# "15-438878". Si aparece donde no hay telefono publicado, fue inventado.
RE_TELEFONO = r"\(\s*\d{3,5}\s*\)\s*[\d\s\-]{6,}|\b\d{2,4}\s*-\s*\d{6,}\b|\b\d{4}\s*-\s*\d{4}\b"

# Un tipo de combustible afirmado donde el registro no publica ninguno.
RE_COMBUSTIBLE = r"\bAVGAS\b|\bJET\s*A\b|\b100\s*LL\b"


@dataclass(frozen=True)
class EvalCase:
    pregunta  : str
    intent    : str
    codigo    : Optional[str] = None
    region    : str = "-"
    sin_dato  : bool = False
    prohibido : Optional[str] = None
    # Estado del formulario con el que se corre el caso. La mayoria no lo
    # necesita, pero hay intenciones que SOLO existen con un vuelo cargado:
    # "quiero pasar por Rosario" no significa nada sin una ruta que modificar.
    # Evaluarlas con la pantalla vacia mediria otra cosa.
    contexto  : Optional[dict] = None


# ──────────────────────────────────────────────────────────────────────────────
# 1. buscar_aerodromo
# ──────────────────────────────────────────────────────────────────────────────

_BUSCAR = [
    EvalCase("cual es el codigo de La Cumbre?", "buscar_aerodromo", "SACC", "PAMPA"),
    EvalCase("donde queda Apostoles?", "buscar_aerodromo", "APO", "LITORAL"),
    EvalCase("que aerodromos hay en Misiones?", "buscar_aerodromo", None, "LITORAL"),
    EvalCase("necesito el codigo de Puerto Deseado", "buscar_aerodromo", "SAWD", "PATAGONIA"),
    EvalCase("aerodromos en la provincia de Salta", "buscar_aerodromo", None, "NOA"),
    EvalCase("como se identifica Andalgala?", "buscar_aerodromo", "AND", "NOA"),
    EvalCase("busco el aerodromo de Zarate", "buscar_aerodromo", "ATE", "PAMPA"),
    EvalCase("que aerodromos hay en Tierra del Fuego?", "buscar_aerodromo", None, "PATAGONIA"),
    EvalCase("codigo de Alto Rio Senguer", "buscar_aerodromo", "SAVR", "PATAGONIA"),
    EvalCase("donde esta Ameghino?", "buscar_aerodromo", "AMG", "PAMPA"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 2. contacto_aerodromo  (incluye los casos SIN dato, que son la prueba dura)
# ──────────────────────────────────────────────────────────────────────────────

_CONTACTO = [
    EvalCase("a quien llamo en Cruz Alta?", "contacto_aerodromo", "ALT", "PAMPA"),
    EvalCase("telefono del jefe de aerodromo de La Cumbre", "contacto_aerodromo", "SACC", "PAMPA"),
    EvalCase("como contacto Puerto Deseado?", "contacto_aerodromo", "SAWD", "PATAGONIA"),
    EvalCase("numero de Alta Gracia", "contacto_aerodromo", "AGR", "PAMPA"),
    EvalCase("tenes el telefono de Apostoles?", "contacto_aerodromo", "APO", "LITORAL"),
    EvalCase("a quien aviso antes de aterrizar en Andalgala?", "contacto_aerodromo", "AND", "NOA"),
    EvalCase("contacto de Piedra del Aguila", "contacto_aerodromo", "SAVA", "PATAGONIA"),
    EvalCase("telefono de Arroyito", "contacto_aerodromo", "ARY", "PAMPA"),

    # ── Sin telefono publicado: no puede inventar un numero ──
    EvalCase("a quien llamo en Cordoba?", "contacto_aerodromo", "SACO", "PAMPA",
             sin_dato=True, prohibido=RE_TELEFONO),
    EvalCase("telefono de Bariloche", "contacto_aerodromo", "SAZS", "PATAGONIA",
             sin_dato=True, prohibido=RE_TELEFONO),
    EvalCase("como contacto Catamarca?", "contacto_aerodromo", "SANC", "NOA",
             sin_dato=True, prohibido=RE_TELEFONO),
    EvalCase("numero de Curuzu Cuatia", "contacto_aerodromo", "SATU", "LITORAL",
             sin_dato=True, prohibido=RE_TELEFONO),
]

# ──────────────────────────────────────────────────────────────────────────────
# 3. servicios_aerodromo
# ──────────────────────────────────────────────────────────────────────────────

_SERVICIOS = [
    EvalCase("que pista tiene La Cumbre?", "servicios_aerodromo", "SACC", "PAMPA"),
    EvalCase("de que material es la pista de Cruz Alta?", "servicios_aerodromo", "ALT", "PAMPA"),
    EvalCase("a que elevacion esta Andalgala?", "servicios_aerodromo", "AND", "NOA"),
    EvalCase("Apostoles es publico o privado?", "servicios_aerodromo", "APO", "LITORAL"),
    EvalCase("que largo tiene la pista de Puerto Deseado?", "servicios_aerodromo", "SAWD", "PATAGONIA"),
    EvalCase("hay normas particulares en La Cumbre?", "servicios_aerodromo", "SACC", "PAMPA"),
    EvalCase("Piedra del Aguila es controlado?", "servicios_aerodromo", "SAVA", "PATAGONIA"),
    EvalCase("rumbo de las pistas de Zarate", "servicios_aerodromo", "ATE", "PAMPA"),
    EvalCase("que superficie tiene Alto Rio Senguer?", "servicios_aerodromo", "SAVR", "PATAGONIA"),

    # ── Sin combustible publicado: no puede afirmar que lo tiene ──
    EvalCase("tiene combustible La Cumbre?", "servicios_aerodromo", "SACC", "PAMPA",
             sin_dato=True, prohibido=RE_COMBUSTIBLE),
    EvalCase("hay nafta en Andalgala?", "servicios_aerodromo", "AND", "NOA",
             sin_dato=True, prohibido=RE_COMBUSTIBLE),
    EvalCase("cual es el horario de atencion de Apostoles?", "servicios_aerodromo",
             "APO", "LITORAL", sin_dato=True),
]

# ──────────────────────────────────────────────────────────────────────────────
# 4. combustible_cercano
# ──────────────────────────────────────────────────────────────────────────────

_COMBUSTIBLE = [
    EvalCase("donde puedo cargar combustible cerca de La Cumbre?",
             "combustible_cercano", "SACC", "PAMPA"),
    EvalCase("donde reposto si salgo de Cruz Alta?", "combustible_cercano", "ALT", "PAMPA"),
    EvalCase("hay algun lugar para cargar nafta cerca de Puerto Deseado?",
             "combustible_cercano", "SAWD", "PATAGONIA"),
    EvalCase("aerodromos con combustible cerca de Apostoles",
             "combustible_cercano", "APO", "LITORAL"),
    EvalCase("necesito repostar cerca de Andalgala", "combustible_cercano", "AND", "NOA"),
    EvalCase("donde cargo AVGAS cerca de Zarate?", "combustible_cercano", "ATE", "PAMPA"),
    EvalCase("lugares para combustible en 200 km de Piedra del Aguila",
             "combustible_cercano", "SAVA", "PATAGONIA"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 5. evaluar_meteo
# ──────────────────────────────────────────────────────────────────────────────

_METEO = [
    EvalCase("puedo volar manana a las 9 desde La Cumbre?", "evaluar_meteo", "SACC", "PAMPA"),
    EvalCase("como esta el tiempo para salir de Cruz Alta?", "evaluar_meteo", "ALT", "PAMPA"),
    EvalCase("se puede volar hoy desde Apostoles?", "evaluar_meteo", "APO", "LITORAL"),
    EvalCase("condiciones para despegar de Puerto Deseado esta tarde",
             "evaluar_meteo", "SAWD", "PATAGONIA"),
    EvalCase("da GO volar desde Andalgala manana temprano?", "evaluar_meteo", "AND", "NOA"),
    EvalCase("puedo salir de Zarate con un Cessna 172?", "evaluar_meteo", "ATE", "PAMPA"),
    EvalCase("evalua la meteo de Piedra del Aguila para un vuelo de 2 horas",
             "evaluar_meteo", "SAVA", "PATAGONIA"),
    EvalCase("esta apto Alta Gracia para volar ahora?", "evaluar_meteo", "AGR", "PAMPA"),
    EvalCase("me sirve el clima en Alto Rio Senguer para un Diamond DA40?",
             "evaluar_meteo", "SAVR", "PATAGONIA"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 6. fuera_de_alcance
# ──────────────────────────────────────────────────────────────────────────────

_FUERA = [
    EvalCase("cual es la capital de Francia?", "fuera_de_alcance"),
    EvalCase("hay NOTAM activos en Cordoba?", "fuera_de_alcance"),
    EvalCase("calculame el peso y balance de un PA-28", "fuera_de_alcance"),
    EvalCase("interpretame este METAR: SACO 121200Z 18010KT 9999 SCT030 24/12 Q1013",
             "fuera_de_alcance"),
    EvalCase("como hago un aterrizaje con viento cruzado fuerte?", "fuera_de_alcance"),
    EvalCase("que dice la RAAC parte 91 sobre altura minima?", "fuera_de_alcance"),
    EvalCase("escribime un poema sobre volar", "fuera_de_alcance"),
    EvalCase("cuanto sale un Cessna 152 usado?", "fuera_de_alcance"),
    EvalCase("necesito el plan de vuelo presentado ante ANAC", "fuera_de_alcance"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 7. Casos limite: el aerodromo no existe o el nombre es ambiguo
# ──────────────────────────────────────────────────────────────────────────────
# No se evalua la intencion sino la honestidad: no puede inventar un aerodromo
# ni elegir a la fuerza entre varios homonimos.

_LIMITE = [
    EvalCase("a quien llamo en Aerodromo San Fantasma?", "contacto_aerodromo",
             None, "-", sin_dato=True, prohibido=RE_TELEFONO),
    EvalCase("que pista tiene el aeropuerto de Wakanda?", "servicios_aerodromo",
             None, "-", sin_dato=True),
    EvalCase("telefono de Los Cardales", "contacto_aerodromo",
             None, "PAMPA", sin_dato=True, prohibido=RE_TELEFONO),
]


# ──────────────────────────────────────────────────────────────────────────────
# 8. Cuyo
# ──────────────────────────────────────────────────────────────────────────────
# Bloque aparte solo por trazabilidad: la regla de alcance del proyecto exige
# probar en las cinco regiones, y sin estos casos Cuyo quedaba sin cubrir.

_CUYO = [
    EvalCase("cual es el codigo de Chilecito?", "buscar_aerodromo", "SANO", "CUYO"),
    EvalCase("a quien llamo en Chamical?", "contacto_aerodromo", "SACT", "CUYO"),
    EvalCase("telefono de Tilisarao", "contacto_aerodromo", "TLS", "CUYO"),
    EvalCase("que pista tiene Chilecito?", "servicios_aerodromo", "SANO", "CUYO"),
    EvalCase("hay normas particulares en Chamical?", "servicios_aerodromo", "SACT", "CUYO"),
    EvalCase("donde reposto cerca de Chamical?", "combustible_cercano", "SACT", "CUYO"),
    EvalCase("puedo volar manana desde Chilecito?", "evaluar_meteo", "SANO", "CUYO"),

    # Sin telefono publicado
    EvalCase("telefono de San Rafael", "contacto_aerodromo", "SAMR", "CUYO",
             sin_dato=True, prohibido=RE_TELEFONO),
]


# ──────────────────────────────────────────────────────────────────────────────
# 9. atmosfera_en_punto
# ──────────────────────────────────────────────────────────────────────────────
# El estado del AIRE sobre un punto, a una altitud. Es la pregunta que se hace
# quien evalua desviarse por algun lado. No produce veredicto: la distincion
# entre esto y evaluar_meteo es justamente lo que el clasificador tiene que
# aprender, y la matriz de confusion es donde se ve si lo logro.

_ALTURA = [
    EvalCase("como esta el aire sobre Junin a 7500 pies?",
             "atmosfera_en_punto", "SAAJ", "PAMPA"),
    EvalCase("si paso por arriba de Pergamino a 6500 ft, como esta?",
             "atmosfera_en_punto", "SAAN", "PAMPA"),
    EvalCase("como esta la meteorologia en altura sobre Venado Tuerto?",
             "atmosfera_en_punto", "VNO", "PAMPA"),
    EvalCase("condiciones atmosfericas sobre Chilecito a 10000 pies",
             "atmosfera_en_punto", "SANO", "CUYO"),
    EvalCase("voy IFR, como esta el aire sobre Catamarca?",
             "atmosfera_en_punto", "SANC", "NOA"),
    EvalCase("me conviene pasar por arriba de Apostoles a 8000 ft?",
             "atmosfera_en_punto", "APO", "LITORAL"),
    EvalCase("que viento hay a 9000 pies sobre Piedra del Aguila?",
             "atmosfera_en_punto", "SAVA", "PATAGONIA"),
    EvalCase("como esta el aire en ruta sobre Chamical a 8500 ft?",
             "atmosfera_en_punto", "SACT", "CUYO"),
    EvalCase("temperatura y viento a 7000 pies sobre Puerto Deseado",
             "atmosfera_en_punto", "SAWD", "PATAGONIA"),
    EvalCase("como esta la atmosfera sobre Andalgala a 12000 pies?",
             "atmosfera_en_punto", "AND", "NOA"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 10. mejor_hora_para_salir
# ──────────────────────────────────────────────────────────────────────────────
# Vecina de evaluar_meteo: las dos hablan del veredicto de un aerodromo, pero
# una pregunta por UN momento y la otra por la evolucion del dia.

_HORARIO = [
    EvalCase("a que hora me conviene salir de La Cumbre?",
             "mejor_hora_para_salir", "SACC", "PAMPA"),
    EvalCase("cuando mejora el tiempo en Cruz Alta?",
             "mejor_hora_para_salir", "ALT", "PAMPA"),
    EvalCase("a que hora del dia esta mejor Apostoles?",
             "mejor_hora_para_salir", "APO", "LITORAL"),
    EvalCase("mostrame como evoluciona la meteo de Puerto Deseado en el dia",
             "mejor_hora_para_salir", "SAWD", "PATAGONIA"),
    EvalCase("hasta que hora puedo salir de Andalgala hoy?",
             "mejor_hora_para_salir", "AND", "NOA"),
    EvalCase("cuando se pone feo en Chilecito?",
             "mejor_hora_para_salir", "SANO", "CUYO"),
    EvalCase("en las proximas 6 horas, cuando conviene despegar de Zarate?",
             "mejor_hora_para_salir", "ATE", "PAMPA"),
    EvalCase("como viene el dia en Piedra del Aguila?",
             "mejor_hora_para_salir", "SAVA", "PATAGONIA"),
]


# ──────────────────────────────────────────────────────────────────────────────
# 11. proponer_cambio_de_ruta
# ──────────────────────────────────────────────────────────────────────────────
# La unica intencion que PROPONE en vez de responder. Los casos cubren las tres
# acciones —sobrevolar, hacer escala y quitar— y, sobre todo, la distincion que
# el modelo tiene que aprender: "pasar por" y "hacer escala en" dan la misma
# linea en el mapa y son dos vuelos distintos.
#
# Se incluyen a proposito casos SIN la intencion explicita ("meteme Rosario en
# la ruta"): ahi la conducta correcta NO es adivinar, es repreguntar. La
# herramienta devuelve `falta_tipo` justamente para eso.

# Vuelo de referencia para los casos de ruta. Cordoba -> Ezeiza cruza el pais de
# oeste a este por el centro, asi que cualquiera de los puntos que se piden abajo
# implica un desvio real y medible.
_VUELO_CARGADO = {
    "origin": "SACO", "dest": "SAEZ", "aircraft": "Cessna 172 Skyhawk",
    "flight_rules": "VFR", "departure_time": "13:00", "experience": "PPL",
    "via": [],
}
_VUELO_CON_PUNTO = dict(_VUELO_CARGADO, via=[{"code": "SAAR", "is_stop": False}])

_RUTA = [
    EvalCase("quiero pasar por Rosario",
             "proponer_cambio_de_ruta", "SAAR", "LITORAL",
             contexto=_VUELO_CARGADO),
    EvalCase("agregame una escala en Villa Dolores",
             "proponer_cambio_de_ruta", "SAOD", "PAMPA",
             contexto=_VUELO_CARGADO),
    EvalCase("podriamos sobrevolar Rio Cuarto en el camino?",
             "proponer_cambio_de_ruta", "SAOC", "PAMPA",
             contexto=_VUELO_CARGADO),
    EvalCase("sacame Rosario de la ruta",
             "proponer_cambio_de_ruta", "SAAR", "LITORAL",
             contexto=_VUELO_CON_PUNTO),
    EvalCase("quiero hacer escala en San Luis para cargar combustible",
             "proponer_cambio_de_ruta", "SAOU", "CUYO",
             contexto=_VUELO_CARGADO),
    EvalCase("meteme Santa Rosa en el medio del vuelo",
             "proponer_cambio_de_ruta", "SAZR", "PAMPA",
             contexto=_VUELO_CARGADO),
    EvalCase("prefiero hacer escala en Neuquen antes de seguir",
             "proponer_cambio_de_ruta", "SAZN", "PATAGONIA",
             contexto=_VUELO_CARGADO),
    # Sin codigo esperado A PROPOSITO: la frase no nombra ningun aerodromo, asi
    # que no prueba resolucion de entidad. Exigirle un codigo medira si el
    # modelo INFIERE del contexto, que es otra cosa y no es lo que reporta esa
    # metrica. (Se etiqueto mal una vez, y produjo una falla de resolucion que
    # no era tal.)
    EvalCase("volvamos a la ruta directa, sacale el punto de paso",
             "proponer_cambio_de_ruta", None, "LITORAL",
             contexto=_VUELO_CON_PUNTO),
    EvalCase("puedo pasar por arriba de Tucuman sin aterrizar?",
             "proponer_cambio_de_ruta", "SANT", "NOA",
             contexto=_VUELO_CARGADO),
    # Este caso vivia en `_FUERA`: era fuera de alcance cuando el copiloto no
    # podia tocar la ruta. Ahora puede, asi que la etiqueta quedo vieja — el
    # modelo la clasificaba "mal" contra una verdad de referencia caduca. Se
    # deja anotado porque es un riesgo real de todo conjunto etiquetado: cuando
    # el sistema gana una capacidad, parte del ground truth deja de valer.
    EvalCase("cambiame la ruta para pasar por arriba de Rosario",
             "proponer_cambio_de_ruta", "SAAR", "LITORAL",
             contexto=_VUELO_CARGADO),
]


CASES: Tuple[EvalCase, ...] = tuple(
    _BUSCAR + _CONTACTO + _SERVICIOS + _COMBUSTIBLE + _METEO + _FUERA
    + _LIMITE + _CUYO + _ALTURA + _HORARIO + _RUTA
)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from collections import Counter

    print("=" * 74)
    print("  CONJUNTO DE EVALUACION DEL COPILOTO")
    print("=" * 74)

    print(f"\n  Total de casos: {len(CASES)}")

    print("\n  Por intencion esperada:")
    for intent, n in Counter(c.intent for c in CASES).most_common():
        print(f"    {intent:22s} {n:3d}")

    print("\n  Por region:")
    for region, n in Counter(c.region for c in CASES).most_common():
        print(f"    {region:22s} {n:3d}")

    sin_dato = [c for c in CASES if c.sin_dato]
    print(f"\n  Casos con dato AUSENTE (prueba de invencion): {len(sin_dato)}")
    con_patron = [c for c in sin_dato if c.prohibido]
    print(f"    de ellos, con deteccion automatica objetiva : {len(con_patron)}")

    # Verificar que los codigos esperados existen y que el resolutor los alcanza
    print("\n  ── Verificacion del resolutor sobre los casos etiquetados ──")
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS

    fallas = 0
    for c in CASES:
        if not c.codigo:
            continue
        if c.codigo not in AIRPORTS:
            print(f"    [FALLO] {c.codigo} no existe en el registro ({c.pregunta!r})")
            fallas += 1
    print(f"    codigos esperados validos: {'todos' if fallas == 0 else f'{fallas} invalidos'}")

    # Verificar que los patrones prohibidos compilan
    import re
    for c in CASES:
        if c.prohibido:
            re.compile(c.prohibido, re.IGNORECASE)
    print("    patrones prohibidos: compilan correctamente")

    print("\n" + "=" * 74)
    print(f"  {'CONJUNTO VALIDO' if fallas == 0 else 'HAY CASOS MAL ETIQUETADOS'}")
    print("=" * 74)
