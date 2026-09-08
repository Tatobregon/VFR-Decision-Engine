"""
test_copilot.py
===============
Copiloto en lenguaje natural: herramientas deterministas, contrato de
honestidad ante datos ausentes, y las dos garantias que se hacen cumplir en
codigo (transcripcion del veredicto y codigos de aerodromo no fabricados).

Todo corre SIN RED: las herramientas son deterministas y el modelo de lenguaje
se inyecta simulado, como el resto de la suite.
"""

import pytest

from copilot import tools as T
from copilot.agent import (
    CopilotAgent,
    invented_codes,
    trim_history,
    verdicts_mentioned,
)
from copilot.client import LLMResponse, LLMUnavailable, ScriptedClient, ToolCall
from copilot.eval_set import CASES
from copilot.prompts import build_system_prompt, verdict_fallback
from data.airports import AIRPORTS


# ══════════════════════════════════════════════════════════════════════════════
# Resolutor de aerodromos
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("consulta,esperado", [
    ("SACO",       "SACO"),    # codigo exacto
    ("La Cumbre",  "SACC"),    # nombre exacto
    ("Cruz Alta",  "ALT"),     # identificador local, sin OACI
    ("Cordoba",    "SACO"),    # nombre de ciudad que ademas es provincia
    ("Rosario",    "SAAR"),    # varios homonimos, gana el principal
])
def test_el_resolutor_elige_el_aerodromo_correcto(consulta, esperado):
    ap, _ = T.resolve_airport(consulta)
    assert ap is not None, f"{consulta} no resolvio"
    assert ap.code == esperado


def test_un_aerodromo_inexistente_no_resuelve_ni_sugiere():
    """Preferible no encontrar nada que devolver algo parecido como si fuera."""
    ap, candidatos = T.resolve_airport("Aerodromo San Fantasma")
    assert ap is None
    assert candidatos == []


def test_la_ambiguedad_devuelve_candidatos_en_vez_de_elegir():
    r = T.contacto_aerodromo("San")
    if not r.get("ok"):
        assert r["motivo"] in ("ambiguo", "no_encontrado")
        if r["motivo"] == "ambiguo":
            assert len(r["candidatos"]) > 1


def test_el_resolutor_no_esta_atado_a_una_region():
    """Regla de alcance: tiene que funcionar en todo el pais."""
    por_region = {
        "NOA":       "Andalgala",
        "CUYO":      "Chilecito",
        "PATAGONIA": "Puerto Deseado",
        "LITORAL":   "Apostoles",
        "PAMPA":     "Cruz Alta",
    }
    for region, nombre in por_region.items():
        ap, _ = T.resolve_airport(nombre)
        assert ap is not None, f"{region}: no resolvio {nombre}"


# ══════════════════════════════════════════════════════════════════════════════
# Contrato de honestidad: ausencia de dato != ausencia de la cosa
# ══════════════════════════════════════════════════════════════════════════════

def test_un_dato_ausente_nunca_se_devuelve_como_vacio():
    """
    La cobertura del registro es inversa a la intuicion: los aerodromos
    controlados grandes tienen los campos vacios porque se publican en el AIP.
    Devolver "" haria que el asistente dijera "no tiene".
    """
    r = T.servicios_aerodromo("SACO")
    assert r["combustible"]["publicado"] is False
    assert r["combustible"]["valor"] is None
    assert "nota" in r["combustible"]
    assert "controlado" in r["combustible"]["nota"]


def test_un_dato_presente_se_marca_como_publicado():
    r = T.contacto_aerodromo("Cruz Alta")
    assert r["contactos"]["publicado"] is True
    assert len(r["contactos"]["telefonos"]) >= 1


def test_la_busqueda_de_combustible_siempre_advierte_la_cobertura():
    """
    Solo 1 de cada 5 aerodromos declara combustible. Una lista sin esa
    advertencia induce a planificar una etapa sobre un supuesto falso.
    """
    r = T.combustible_cercano("La Cumbre")
    assert "NO significa que no tenga" in r["advertencia_de_cobertura"]


def test_no_encontrado_instruye_a_no_inventar():
    r = T.contacto_aerodromo("Aerodromo Que No Existe")
    assert r["ok"] is False
    assert r["motivo"] == "no_encontrado"
    assert "inventes" in r["mensaje"]


# ══════════════════════════════════════════════════════════════════════════════
# Interpretacion de la hora
# ══════════════════════════════════════════════════════════════════════════════

def test_la_hora_local_argentina_se_convierte_a_utc():
    """Argentina es UTC-3 fijo: las 09:00 locales son las 12:00 UTC."""
    from datetime import datetime, timezone
    ts, _ = T._parse_cuando("2026-09-09 09:00")
    assert datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M") == "12:00"


def test_una_hora_incomprensible_degrada_y_lo_declara():
    ts, etiqueta = T._parse_cuando("cuando pinte")
    assert ts > 0
    assert "no se entendio" in etiqueta


# ══════════════════════════════════════════════════════════════════════════════
# Esquema de herramientas
# ══════════════════════════════════════════════════════════════════════════════

def test_el_esquema_coincide_con_el_registro():
    decls = T.tool_declarations()[0]["functionDeclarations"]
    assert {d["name"] for d in decls} == set(T.REGISTRY)


def test_existe_una_intencion_de_fuera_de_alcance():
    """
    Sin una clase explicita para 'no puedo contestar esto', el clasificador
    queda obligado a elegir una herramienta y el modelo inventa para encajar.
    """
    assert T.INTENT_OUT_OF_SCOPE in T.INTENTS
    assert T.INTENT_OUT_OF_SCOPE not in T.REGISTRY
    assert len(T.INTENTS) == len(T.REGISTRY) + 1


def test_los_argumentos_inventados_se_descartan_sin_romper():
    r = T.execute("contacto_aerodromo",
                  {"query": "Cruz Alta", "parametro_que_no_existe": 123})
    assert r["ok"] is True


def test_una_herramienta_desconocida_no_lanza():
    r = T.execute("herramienta_inexistente", {})
    assert r["ok"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Garantia R2: el veredicto se transcribe, no se parafrasea
# ══════════════════════════════════════════════════════════════════════════════

_METEO_NOGO = {
    "ok": True, "veredicto": "NO GO", "r_total": 0.71,
    "aerodromo": {"codigo": "SACC", "nombre": "LA CUMBRE"},
    "momento_evaluado": "manana 09:00 hora local",
    "fuente_explicada": "pronostico numerico NWP",
    "bloqueo_normativo": {"activo": False, "detalle": None},
    "desglose": {"factor_dominante": "visibilidad", "motivo_barrera": None},
}


@pytest.mark.parametrize("texto,esperado", [
    ("El resultado es NO GO",        {"NO GO"}),
    ("veredicto: NO-GO",             {"NO GO"}),
    ("Da **GO**, podes salir",       {"GO"}),
    ("Da CAUTION por viento",        {"CAUTION"}),
    ("no es GO, es CAUTION",         {"GO", "CAUTION"}),
    ("mejor no salgas, esta feo",    set()),
])
def test_no_go_no_deja_un_go_suelto(texto, esperado):
    """'GO' es subcadena de 'NO GO': el orden de la alternancia importa."""
    assert verdicts_mentioned(texto) == esperado


def test_una_transcripcion_correcta_no_se_altera():
    texto, forzado = CopilotAgent._enforce_verdict(
        "Da **NO GO** por visibilidad.", [_METEO_NOGO])
    assert forzado is False


@pytest.mark.parametrize("generado", [
    "Yo diria que mejor no salgas.",     # parafrasis
    "Esta todo bien, da GO.",            # veredicto invertido
    "Da CAUTION, ojo con el viento.",    # veredicto suavizado
])
def test_un_veredicto_falseado_se_reemplaza_por_la_plantilla(generado):
    """
    Es el error mas grave posible del asistente: un piloto que sale a volar
    porque el texto suavizo un NO GO. Por eso no se confia en el prompt.
    """
    texto, forzado = CopilotAgent._enforce_verdict(generado, [_METEO_NOGO])
    assert forzado is True
    assert "NO GO" in texto
    assert verdicts_mentioned(texto) == {"NO GO"}


def test_sin_evaluacion_meteorologica_la_barrera_no_interviene():
    texto, forzado = CopilotAgent._enforce_verdict("cualquier cosa", [])
    assert forzado is False


def test_la_plantilla_determinista_lleva_veredicto_factor_y_fuente():
    texto = verdict_fallback(_METEO_NOGO)
    assert "NO GO" in texto
    assert "visibilidad" in texto
    assert "piloto al mando" in texto


# ══════════════════════════════════════════════════════════════════════════════
# Garantia R4: no se fabrican codigos de aerodromo
# ══════════════════════════════════════════════════════════════════════════════

_CONTACTO_ALT = {
    "ok": True,
    "aerodromo": {"codigo": "ALT", "nombre": "CRUZ ALTA", "icao": None},
    "contactos": {"publicado": True, "telefonos": ["(03467) 421222"]},
}


def test_se_detecta_el_codigo_fabricado():
    """Caso real observado en vivo: la herramienta dio ALT y el modelo
    escribio 'SAAL', un codigo OACI que no existe."""
    assert "SAAL" not in AIRPORTS
    assert invented_codes("Para **SAAL / Cruz Alta** llama al ...",
                          [_CONTACTO_ALT]) == ["SAAL"]


def test_el_codigo_legitimo_no_se_marca():
    assert invented_codes("En ALT (Cruz Alta) llama al ...", [_CONTACTO_ALT]) == []


def test_un_codigo_real_de_otro_turno_no_es_una_invencion():
    """
    En conversacion multi-turno el modelo puede nombrar un aerodromo consultado
    antes. Marcarlo como fabricado seria un falso positivo, y una correccion
    equivocada destruye la confianza en el unico mecanismo que si sirve.
    """
    res = [{"ok": True, "aerodromo": {"codigo": "SAAR", "nombre": "ROSARIO",
                                      "icao": "SAAR"}}]
    assert "SACC" in AIRPORTS
    assert invented_codes("En SAAR da GO, mejor que en SACC de antes.", res) == []
    assert invented_codes("En SAAR da GO, mejor que en SAZZ.", res) == ["SAZZ"]


def test_la_correccion_de_codigo_conserva_el_texto_util():
    texto, corregido = CopilotAgent._enforce_codes(
        "Para **SAAL / Cruz Alta** llama al (03467) 421222.", [_CONTACTO_ALT])
    assert corregido is True
    assert "ALT" in texto
    assert "(03467) 421222" in texto      # no se tira la respuesta entera


# ══════════════════════════════════════════════════════════════════════════════
# Historial de conversacion
# ══════════════════════════════════════════════════════════════════════════════

def _turno(i, n_tools=1):
    e = [{"role": "user", "parts": [{"text": f"pregunta {i}"}]}]
    for _ in range(n_tools):
        e.append({"role": "model", "parts": [{"functionCall": {"name": "x"}}]})
        e.append({"role": "user", "parts": [{"functionResponse": {"name": "x"}}]})
    e.append({"role": "model", "parts": [{"text": f"respuesta {i}"}]})
    return e


def test_el_recorte_del_historial_cae_en_limite_de_turno():
    """
    Un functionResponse sin su functionCall previo invalida la conversacion
    ante la API. Recortar por cantidad de entradas funciona solo mientras todos
    los turnos midan lo mismo; en cuanto uno usa dos herramientas, se desalinea.
    """
    largo = []
    for i in range(4):
        largo += _turno(i, n_tools=(i % 2) + 1)
    recortado = trim_history(largo)
    assert recortado
    primero = recortado[0]
    assert primero["role"] == "user"
    assert any("text" in p for p in primero["parts"])


def test_un_historial_corto_no_se_toca():
    h = _turno(0)
    assert trim_history(h) == h


def test_un_historial_sin_limite_seguro_se_descarta():
    patologico = [
        {"role": "model", "parts": [{"functionCall": {"name": "x"}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "x"}}]},
    ] * 20
    assert trim_history(patologico) == []


def test_un_historial_vacio_no_rompe():
    assert trim_history(None) == []
    assert trim_history([]) == []


# ══════════════════════════════════════════════════════════════════════════════
# Bucle completo con modelo simulado
# ══════════════════════════════════════════════════════════════════════════════

def _cliente_con_herramienta(nombre, args, respuesta):
    return ScriptedClient(respuestas=[
        LLMResponse(
            text="",
            tool_calls=(ToolCall(nombre, args),),
            raw_content={"role": "model",
                         "parts": [{"functionCall": {"name": nombre, "args": args}}]},
        ),
        LLMResponse(text=respuesta),
    ])


def test_el_bucle_llama_la_herramienta_y_redacta_con_su_resultado():
    cliente = _cliente_con_herramienta(
        "contacto_aerodromo", {"query": "Cruz Alta"},
        "En **ALT (Cruz Alta)** llamas al (03467) 15-438878.")
    ans = CopilotAgent(cliente).ask("a quien llamo en Cruz Alta?")

    assert ans.intent == "contacto_aerodromo"
    assert ans.grounded is True
    assert ans.invocations[0].ok is True
    assert "ALT" in ans.resolved_codes


def test_sin_herramienta_la_intencion_es_fuera_de_alcance():
    cliente = ScriptedClient(respuestas=[LLMResponse(text="Eso no esta en mi alcance.")])
    ans = CopilotAgent(cliente).ask("cual es la capital de Francia?")
    assert ans.intent == T.INTENT_OUT_OF_SCOPE
    assert ans.grounded is False


def test_si_el_proveedor_no_responde_el_sistema_degrada_con_aviso():
    """El copiloto es accesorio: su caida no puede arrastrar al resto."""

    class Caido:
        def generate(self, *a, **k):
            raise LLMUnavailable("todos los modelos dieron 503")

        @staticmethod
        def build_tool_result_content(x):
            return {}

    ans = CopilotAgent(Caido()).ask("a quien llamo en Cruz Alta?")
    assert ans.error is not None
    assert "no esta disponible" in ans.text


def test_una_pregunta_vacia_no_consume_cuota():
    cliente = ScriptedClient(respuestas=[])       # cualquier llamada explotaria
    ans = CopilotAgent(cliente).ask("   ")
    assert ans.intent == T.INTENT_OUT_OF_SCOPE
    assert cliente.recibidas == []


def test_la_pregunta_se_recorta_para_acotar_el_consumo():
    cliente = ScriptedClient(respuestas=[LLMResponse(text="ok")])
    CopilotAgent(cliente).ask("x" * 5000)
    enviado = cliente.recibidas[0]["contents"][-1]["parts"][0]["text"]
    assert len(enviado) <= 600


# ══════════════════════════════════════════════════════════════════════════════
# Instruccion de sistema
# ══════════════════════════════════════════════════════════════════════════════

def test_el_prompt_resuelve_la_fecha_y_las_aeronaves():
    p = build_system_prompt()
    assert "{fecha_hoy}" not in p and "{aeronaves}" not in p
    assert "Pipistrel" in p


def test_el_prompt_declara_las_reglas_criticas():
    p = build_system_prompt()
    assert "EN TIERRA" in p                       # alcance previo al vuelo
    assert "TRANSCRIBE" in p                      # R2
    assert "NO significa que no exista" in p      # R3
    assert "no le inventes uno" in p              # R4


# ══════════════════════════════════════════════════════════════════════════════
# Conjunto de evaluacion
# ══════════════════════════════════════════════════════════════════════════════

def test_el_conjunto_de_evaluacion_cubre_todas_las_intenciones():
    """Si se agrega una herramienta y no sus casos, la metrica deja de medirla."""
    assert {c.intent for c in CASES} == set(T.INTENTS)


def test_cada_intencion_tiene_suficientes_casos_para_medir():
    """Con menos de cinco casos, la precision por intencion no dice nada."""
    from collections import Counter
    for intent, n in Counter(c.intent for c in CASES).items():
        assert n >= 5, f"{intent} solo tiene {n} casos"


def test_el_conjunto_cubre_las_cinco_regiones():
    """Regla de alcance: ningun resultado puede depender de una sola region."""
    regiones = {c.region for c in CASES} - {"-"}
    assert regiones == {"NOA", "CUYO", "PATAGONIA", "LITORAL", "PAMPA"}


def test_los_codigos_esperados_existen_en_el_registro():
    for c in CASES:
        if c.codigo:
            assert c.codigo in AIRPORTS, f"{c.codigo} ({c.pregunta})"


def test_hay_casos_con_dato_ausente_y_deteccion_objetiva():
    """Es la metrica de seguridad: sin estos casos no se mide la invencion."""
    sin_dato = [c for c in CASES if c.sin_dato]
    assert len(sin_dato) >= 8
    assert sum(1 for c in sin_dato if c.prohibido) >= 6


# ══════════════════════════════════════════════════════════════════════════════
# Informe de atmosfera en altura
# ══════════════════════════════════════════════════════════════════════════════
# Estos tests no salen a la red: cubren la decision de ALTITUD y el contrato de
# la respuesta, que es donde estan las reglas. El fetch meteorologico en si ya
# esta cubierto por los tests del motor.

def test_sin_altitud_el_informe_la_pide_en_vez_de_elegirla():
    """
    Elegir una altitud por el piloto seria inventar la premisa de la respuesta:
    el aire a 3000 ft y a 12000 ft sobre el mismo punto no se parecen en nada.
    """
    r = T.atmosfera_en_punto("Junin")
    assert r["ok"] is False
    assert r["motivo"] == "falta_altitud"
    assert "PREGUNTASELO" in r["mensaje"]
    assert r["sugerencias_ft"]
    assert r["techo_de_servicio_ft"] > 0


def test_en_ifr_la_altitud_sale_de_la_mea_de_la_aerovia():
    """En IFR el piloto no elige altitud: la fija la MEA publicada del tramo."""
    from risk.aircraft_profiles import get_profile

    ap = AIRPORTS["SAAJ"]                       # Junin, sobre la aerovia W9
    alt = T._altitud_para_el_punto(ap, None, get_profile("Cessna 172 Skyhawk"), "IFR")
    assert alt["altitud_ft"] and alt["altitud_ft"] > 0
    assert "MEA de la aerovia" in alt["origen"]
    assert alt["aerovia"]["mea_ft"] == alt["altitud_ft"]


def test_la_altitud_elegida_se_acota_al_techo_de_servicio():
    from risk.aircraft_profiles import get_profile

    perfil = get_profile("Cessna 152")          # techo 14000 ft
    ap = AIRPORTS["SAAJ"]
    alt = T._altitud_para_el_punto(ap, 30000, perfil, "VFR")
    assert alt["altitud_ft"] == perfil.service_ceiling_ft
    assert "techo de servicio" in alt["origen"]


def test_la_altitud_elegida_por_el_piloto_se_respeta():
    from risk.aircraft_profiles import get_profile

    ap = AIRPORTS["SAAJ"]
    alt = T._altitud_para_el_punto(ap, 7500, get_profile("Cessna 152"), "VFR")
    assert alt["altitud_ft"] == 7500
    assert alt["origen"] == "elegida por el piloto"


def test_el_informe_de_atmosfera_no_declara_ningun_veredicto():
    """
    GO / CAUTION / NO GO es un concepto de aerodromo, medido contra una pista.
    Si el informe de altura tambien devolviera un veredicto, la etiqueta
    significaria dos cosas distintas y dejaria de ser el objeto unico sobre el
    que se apoya el motor.
    """
    import inspect
    fuente = inspect.getsource(T.atmosfera_en_punto)
    assert '"veredicto"' not in fuente
    assert '"decision"' not in fuente


# ══════════════════════════════════════════════════════════════════════════════
# La barrera R2 convive con una serie de veredictos por hora
# ══════════════════════════════════════════════════════════════════════════════

def test_una_serie_horaria_no_dispara_una_correccion_falsa():
    """
    Al preguntar "puedo salir y a que hora conviene?", la respuesta menciona
    legitimamente varios veredictos: "ahora CAUTION, desde las 15 GO". Exigir
    exclusividad ahi produciria una correccion equivocada, y una correccion
    equivocada destruye la confianza en el mecanismo.
    """
    texto = "Ahora da CAUTION, pero desde las 15:00 pasa a GO."
    _, forzado = CopilotAgent._enforce_verdict(
        texto, [dict(_METEO_NOGO, veredicto="CAUTION")],
        hay_serie_de_veredictos=True,
    )
    assert forzado is False


def test_pero_omitir_el_veredicto_se_corrige_igual_con_serie():
    """La relajacion afloja la exclusividad, no la presencia."""
    texto, forzado = CopilotAgent._enforce_verdict(
        "Fijate que mas tarde mejora.", [_METEO_NOGO],
        hay_serie_de_veredictos=True,
    )
    assert forzado is True
    assert "NO GO" in texto


def test_sin_serie_la_exclusividad_sigue_siendo_estricta():
    _, forzado = CopilotAgent._enforce_verdict(
        "No es GO exactamente, es NO GO.", [_METEO_NOGO],
        hay_serie_de_veredictos=False,
    )
    assert forzado is True


# ══════════════════════════════════════════════════════════════════════════════
# Aire en altura: consistencia fisica
# ══════════════════════════════════════════════════════════════════════════════
# Estos tests existen por un bug real, encontrado por el piloto usando la
# herramienta: informaba 19 C tanto a 6.000 como a 15.000 ft. La causa era que
# el fetcher pedia del nivel de presion SOLO el viento y dejaba temperatura,
# rocio y nubes en superficie. Un informe de altura construido con datos de
# suelo es peor que no tener informe: se equivoca con la misma confianza con la
# que acierta.

def test_la_temperatura_baja_con_la_altura():
    """
    La consistencia fisica basica del informe. El modo mock usa gradiente ISA
    justamente para que este test NO pueda pasar con el bug puesto: si el
    fetcher devolviera superficie, las tres altitudes darian lo mismo.
    """
    from data.fetcher_openmeteo import OpenMeteoFetcher

    f = OpenMeteoFetcher(mock=True)
    temps = [
        f.get_upper_air(-32.63, -62.68, alt, hours_ahead=2).hours[0].temperature_c
        for alt in (3000, 6000, 15000)
    ]
    assert temps[0] > temps[1] > temps[2], f"la temperatura no baja: {temps}"


def test_a_mayor_altura_el_nivel_de_presion_es_menor():
    from data.fetcher_openmeteo import _pressure_level_for_alt

    niveles = [int(_pressure_level_for_alt(a))
               for a in (2000, 5000, 7000, 11000, 15000, 20000, 25000)]
    assert niveles == sorted(niveles, reverse=True), niveles


def test_el_informe_declara_la_altura_real_del_nivel():
    """
    El nivel de presion es una aproximacion: 600 hPa estuvo a 14.091 ft el dia
    en que se probo, no a los 15.000 pedidos. Informar solo lo pedido seria
    presentar una aproximacion como una medicion en el punto exacto.
    """
    import inspect
    fuente = inspect.getsource(T.atmosfera_en_punto)
    assert "altitud_real_del_nivel_ft" in fuente
    assert "desvio_respecto_de_lo_pedido_ft" in fuente


def test_no_se_informa_visibilidad_en_altura():
    """
    Open-Meteo no publica visibilidad por nivel de presion. Sustituirla por la
    de superficie seria repetir el error que este modulo corrige.
    """
    import inspect
    fuente = inspect.getsource(T.atmosfera_en_punto)
    assert '"disponible": False' in fuente


def test_el_aire_en_altura_no_pasa_por_parsedweather():
    """
    ParsedWeather es un contrato de SUPERFICIE: visibilidad, techo AGL, spread
    para niebla. Forzar ahi los datos de un nivel de presion obligaria a
    rellenar campos que en altura no significan nada, que es como nacio el bug.
    """
    from data.fetcher_openmeteo import UpperAir, UpperAirHour

    campos = set(UpperAirHour.__dataclass_fields__)
    assert "level_altitude_ft" in campos
    assert "visibility_km" not in campos
    assert "ceiling_ft" not in campos


@pytest.mark.parametrize("consulta,esperado", [
    ("bellville",     "BEL"),     # BELL VILLE
    ("lacumbre",      "SACC"),    # LA CUMBRE
    ("cruzalta",      "ALT"),     # CRUZ ALTA
    ("venadotuerto",  "VNO"),     # VENADO TUERTO
    ("riocuarto",     "SAOC"),    # RIO CUARTO / AREA DE MATERIAL
])
def test_el_resolutor_tolera_nombres_escritos_sin_separar(consulta, esperado):
    """Escribir el nombre sin espacios es corriente y no deberia fallar."""
    ap, _ = T.resolve_airport(consulta)
    assert ap is not None, f"{consulta} no resolvio"
    assert ap.code == esperado


def test_el_nombre_bien_escrito_le_gana_al_compacto():
    """La tolerancia no puede desplazar a una coincidencia exacta."""
    ap, _ = T.resolve_airport("La Cumbre")
    assert ap.code == "SACC"
