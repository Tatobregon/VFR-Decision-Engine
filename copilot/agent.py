"""
agent.py
========
Orquestador del copiloto VFR.

Ejecuta el bucle pregunta -> herramienta -> datos -> redaccion, y despues
VERIFICA que la respuesta redactada no contradiga lo que dijo el motor.

Por que hay verificacion y no solo un prompt
--------------------------------------------
El modelo de lenguaje es el unico componente no interpretable del sistema.
Pedirle por prompt que no falsee un veredicto reduce la probabilidad de que
lo haga, pero no la anula, y el error seria del tipo mas grave posible: un
piloto que sale a volar porque el asistente le suavizo un NO GO.

Por eso la regla R2 se hace cumplir en codigo. Si el texto generado no
contiene el veredicto del motor, o contiene uno distinto, se DESCARTA el
texto y se emite la plantilla determinista de prompts.verdict_fallback().
La integridad del veredicto queda garantizada por construccion, no por
confianza en el modelo, y esa garantia es medible.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from copilot import tools as T
    from copilot.client import LLMClient, LLMResponse, LLMUnavailable, ToolCall
    from copilot.prompts import build_system_prompt, verdict_fallback
    from data.airports import AIRPORTS
except ImportError:                                    # ejecucion como script
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from copilot import tools as T
    from copilot.client import LLMClient, LLMResponse, LLMUnavailable, ToolCall
    from copilot.prompts import build_system_prompt, verdict_fallback
    from data.airports import AIRPORTS

logger = logging.getLogger(__name__)


# Tope de vueltas de herramienta por consulta. Acota latencia y consumo de
# cuota, y evita que el modelo entre en un ciclo llamando siempre lo mismo.
MAX_TOOL_STEPS = 3

# Topes de entrada. El endpoint queda expuesto en internet: sin cotas,
# cualquiera puede agotar la cuota gratuita con un mensaje enorme.
MAX_QUESTION_CHARS = 600
MAX_HISTORY_TURNS  = 12

_MSG_SIN_MODELO = (
    "No puedo responder ahora: el servicio de lenguaje natural no esta "
    "disponible. El resto del sistema funciona igual — podes usar la "
    "evaluacion meteorologica y el planificador de ruta directamente."
)


# ──────────────────────────────────────────────────────────────────────────────
# Deteccion de veredictos en texto libre
# ──────────────────────────────────────────────────────────────────────────────
# El orden de la alternancia importa: "NO GO" tiene que ir ANTES que "GO",
# porque lo contiene como subcadena. `re` es leftmost-first y no solapa, asi
# que con este orden "NO GO" se consume entero y no deja un "GO" suelto.
#
# A proposito NO se reconocen sinonimos ("precaucion", "no despegar"): si el
# modelo parafraseo en vez de transcribir, eso ES la violacion de R2 que
# queremos detectar, no una forma alternativa valida de decirlo.

_VERDICT_RE = re.compile(r"NO\s*[-–—]?\s*GO|CAUTION|\bGO\b", re.IGNORECASE)


def verdicts_mentioned(texto: str) -> set:
    """Veredictos canonicos mencionados en un texto."""
    encontrados = set()
    for m in _VERDICT_RE.finditer(texto or ""):
        crudo = m.group(0).upper()
        if crudo.startswith("NO"):
            encontrados.add("NO GO")
        elif crudo == "CAUTION":
            encontrados.add("CAUTION")
        else:
            encontrados.add("GO")
    return encontrados


# ──────────────────────────────────────────────────────────────────────────────
# Deteccion de codigos de aerodromo inventados
# ──────────────────────────────────────────────────────────────────────────────
# Modo de falla observado en la prueba en vivo: la herramienta devolvio
# {"codigo": "ALT", "icao": null} y el modelo redacto "SAAL / Cruz Alta",
# fabricando un codigo OACI que no existe. Es el error mas peligroso de la
# familia, porque un codigo mal escrito manda al piloto a otro aerodromo.
#
# Se detecta solo el patron OACI argentino (cuatro letras que empiezan con SA),
# que es la forma concreta que toma la invencion: los identificadores locales
# de tres letras no se validan porque colisionarian con siglas legitimas del
# texto (VFR, AIP, GO). Es una red angosta y precisa, no una red general.

_ICAO_AR_RE = re.compile(r"\b(SA[A-Z]{2})\b")

# Todos los codigos que existen de verdad. Un codigo REAL nombrado fuera de los
# resultados de este turno no es una fabricacion: normalmente viene del
# historial de la conversacion ("mejor que en SACC, que consultamos antes").
# Sin esta salvedad el guardia produce falsos positivos en multi-turno, y una
# "correccion automatica" equivocada es peor que no corregir: destruye la
# confianza en el unico mecanismo que si detecta las invenciones de verdad.
_CODIGOS_REALES = frozenset(AIRPORTS) | frozenset(
    a.icao_code for a in AIRPORTS.values() if a.icao_code
)


def _codigos_en_resultados(resultados: List[Dict[str, Any]]) -> Dict[str, str]:
    """Recorre las respuestas de las herramientas y junta los codigos legitimos."""
    encontrados: Dict[str, str] = {}

    def caminar(nodo: Any, nombre_ctx: str = "") -> None:
        if isinstance(nodo, dict):
            nombre = str(nodo.get("nombre") or nombre_ctx or "")
            for clave in ("codigo", "icao"):
                valor = nodo.get(clave)
                if isinstance(valor, str) and valor.strip():
                    encontrados.setdefault(valor.strip().upper(), nombre)
            for valor in nodo.values():
                if isinstance(valor, (dict, list)):
                    caminar(valor, nombre)
        elif isinstance(nodo, list):
            for item in nodo:
                caminar(item, nombre_ctx)

    for r in resultados:
        caminar(r)
    return encontrados


def invented_codes(texto: str, resultados: List[Dict[str, Any]]) -> List[str]:
    """
    Codigos con forma OACI argentina que el modelo FABRICO.

    Un codigo cuenta como fabricado solo si no salio de las herramientas de
    este turno Y ademas no existe en el registro. Se detecta la invencion,
    no la mera mencion.
    """
    legitimos = set(_codigos_en_resultados(resultados))
    vistos = {m.group(1).upper() for m in _ICAO_AR_RE.finditer(texto or "")}
    return sorted(c for c in (vistos - legitimos) if c not in _CODIGOS_REALES)


# ──────────────────────────────────────────────────────────────────────────────
# Resultado
# ──────────────────────────────────────────────────────────────────────────────

def trim_history(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Recorta el historial a los ultimos turnos COMPLETOS.

    No se puede cortar en cualquier punto: una entrada `functionResponse` sin
    su `functionCall` inmediatamente anterior deja la conversacion incoherente
    y la API la rechaza. Por eso, despues de recortar por longitud, se avanza
    hasta el primer mensaje de texto del piloto, que es el unico limite
    seguro de turno.

    Cortar a secas por cantidad de entradas funciona solo mientras cada turno
    ocupe siempre la misma cantidad de entradas; en cuanto un turno usa dos
    herramientas en vez de una, deja de alinearse. La correccion no depende
    de esa aritmetica.
    """
    h = [e for e in (history or []) if isinstance(e, dict)]
    if len(h) <= MAX_HISTORY_TURNS:
        return h

    recorte = h[-MAX_HISTORY_TURNS:]
    for i, entrada in enumerate(recorte):
        partes = entrada.get("parts") or []
        if entrada.get("role") == "user" and any("text" in p for p in partes):
            return recorte[i:]

    # No hay ningun limite seguro en la ventana: se descarta el historial.
    # Perder contexto es molesto; mandar una conversacion invalida es un error.
    logger.debug("Copiloto: no se hallo limite seguro de turno, se descarta el historial")
    return []


@dataclass(frozen=True)
class ToolInvocation:
    """Una herramienta efectivamente ejecutada, para auditoria y metricas."""
    name   : str
    args   : Dict[str, Any]
    ok     : bool
    motivo : Optional[str] = None


@dataclass
class CopilotAnswer:
    """
    Respuesta del copiloto mas todo lo necesario para auditarla.

    Los campos de auditoria no son decorativos: alimentan directamente las
    metricas del informe (matriz de confusion sobre `intent`, tasa de
    correccion sobre `verdict_enforced`, latencia, y anclaje sobre
    `grounded`).
    """
    text             : str
    intent           : str
    invocations      : Tuple[ToolInvocation, ...] = ()
    verdict_enforced : bool = False
    code_corrected   : bool = False
    grounded         : bool = False
    # Codigos que las herramientas resolvieron efectivamente. Permite medir la
    # exactitud de resolucion de entidad sin tener que adivinarla del texto.
    resolved_codes   : Tuple[str, ...] = ()
    latency_s        : float = 0.0
    model_version    : str = ""
    error            : Optional[str] = None
    history          : List[Dict[str, Any]] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Agente
# ──────────────────────────────────────────────────────────────────────────────

class CopilotAgent:
    """
    Une el modelo de lenguaje con las herramientas deterministas.

    El cliente se inyecta: en produccion es GeminiClient, en los tests es
    ScriptedClient y la suite corre sin red.
    """

    def __init__(
        self,
        client         : LLMClient,
        engine_factory : Optional[Callable[..., Any]] = None,
        max_tool_steps : int = MAX_TOOL_STEPS,
    ):
        self.client = client
        self.engine_factory = engine_factory
        self.max_tool_steps = max_tool_steps

    # ── Bucle principal ───────────────────────────────────────────────────────

    def ask(
        self,
        pregunta : str,
        history  : Optional[List[Dict[str, Any]]] = None,
        ahora    : Optional[Any] = None,
        context  : Optional[Dict[str, Any]] = None,
    ) -> CopilotAnswer:
        """
        Responde una consulta del piloto.

        `context` es el estado del formulario de la pantalla (origen, destino,
        aeronave, regimen, altitud). Se inyecta en la instruccion de sistema
        para que "como esta el destino?" no obligue a repreguntar datos que el
        piloto ya cargo.
        """
        t0 = time.time()

        pregunta = (pregunta or "").strip()[:MAX_QUESTION_CHARS]
        if not pregunta:
            return CopilotAnswer(
                text="Contame que necesitas: puedo buscar aerodromos, darte "
                     "telefonos y servicios, o evaluar si podes volar.",
                intent=T.INTENT_OUT_OF_SCOPE,
            )

        system = build_system_prompt(ahora, context)
        declaraciones = T.tool_declarations()

        contents: List[Dict[str, Any]] = trim_history(history)
        contents.append({"role": "user", "parts": [{"text": pregunta}]})

        invocaciones: List[ToolInvocation] = []
        resultados_meteo: List[Dict[str, Any]] = []
        resultados_todos: List[Dict[str, Any]] = []
        texto = ""
        version = ""

        try:
            for paso in range(self.max_tool_steps):
                resp: LLMResponse = self.client.generate(system, contents, declaraciones)
                version = resp.model_version or version

                if not resp.wants_tool:
                    texto = resp.text
                    break

                # Reenviar el content del modelo TAL CUAL: lleva thoughtSignature.
                if resp.raw_content:
                    contents.append(resp.raw_content)

                pares: List[Tuple[ToolCall, Dict[str, Any]]] = []
                for call in resp.tool_calls:
                    datos = T.execute(call.name, call.args,
                                      engine_factory=self.engine_factory)
                    invocaciones.append(ToolInvocation(
                        name=call.name,
                        args=dict(call.args),
                        ok=bool(datos.get("ok")),
                        motivo=datos.get("motivo"),
                    ))
                    resultados_todos.append(datos)
                    if call.name == "evaluar_meteo" and datos.get("ok"):
                        resultados_meteo.append(datos)
                    pares.append((call, {"resultado": datos}))

                contents.append(self.client.build_tool_result_content(pares))
            else:
                # Se agoto el tope de vueltas sin que el modelo cerrara.
                logger.warning("Copiloto: se agoto el tope de vueltas de herramienta")
                texto = ("Me quedé dando vueltas con esa consulta. Probá "
                         "preguntándomelo de otra forma, o más puntual.")

        except LLMUnavailable as e:
            logger.warning(f"Copiloto: proveedor no disponible: {e}")
            return CopilotAnswer(
                text=_MSG_SIN_MODELO,
                intent=T.INTENT_OUT_OF_SCOPE,
                invocations=tuple(invocaciones),
                latency_s=time.time() - t0,
                error=str(e),
            )

        # ── Garantias duras: R2 (veredicto) y R4 (codigo) ─────────────────────
        hay_serie = any(i.name == "mejor_hora_para_salir" for i in invocaciones)
        texto, forzado = self._enforce_verdict(texto, resultados_meteo, hay_serie)
        texto, corregido = self._enforce_codes(texto, resultados_todos)

        intent = invocaciones[-1].name if invocaciones else T.INTENT_OUT_OF_SCOPE

        contents.append({"role": "model", "parts": [{"text": texto}]})

        return CopilotAnswer(
            text=texto,
            intent=intent,
            invocations=tuple(invocaciones),
            verdict_enforced=forzado,
            code_corrected=corregido,
            grounded=bool(invocaciones),
            resolved_codes=tuple(_codigos_en_resultados(resultados_todos)),
            latency_s=time.time() - t0,
            model_version=version,
            history=contents,
        )

    # ── Validacion ────────────────────────────────────────────────────────────

    @staticmethod
    def _enforce_verdict(
        texto: str,
        resultados_meteo: List[Dict[str, Any]],
        hay_serie_de_veredictos: bool = False,
    ) -> Tuple[str, bool]:
        """
        Verifica que el texto transcriba el veredicto del motor.

        Se descarta el texto generado y se reemplaza por la plantilla
        determinista si el veredicto correcto no aparece, o si aparece
        cualquier otro. Devuelve (texto_final, se_forzo).

        `hay_serie_de_veredictos` relaja la exclusividad. Cuando en el mismo
        turno corrio una herramienta que devuelve un veredicto POR HORA (la de
        mejor hora para salir), el texto va a mencionar legitimamente varios:
        "ahora da CAUTION, desde las 15 pasa a GO". Exigir que aparezca uno
        solo produciria una correccion falsa. En ese caso se exige unicamente
        que el veredicto del motor ESTE presente, que es lo que la regla R2
        protege de verdad: que no se lo omita ni se lo reemplace.
        """
        if not resultados_meteo:
            return texto, False

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

        logger.warning(
            f"Copiloto: se forzo el veredicto. motor={canonico!r} "
            f"texto_mencionaba={sorted(mencionados) or 'nada'}"
        )
        return verdict_fallback(datos), True

    @staticmethod
    def _enforce_codes(
        texto: str,
        resultados: List[Dict[str, Any]],
    ) -> Tuple[str, bool]:
        """
        Agrega una correccion si el texto nombra un codigo OACI inventado.

        No se reescribe la respuesta entera, como si se hace con el veredicto:
        el resto del contenido suele ser correcto y util. Se anexa una linea
        deterministica que nombra el codigo verdadero, de modo que el piloto
        nunca se quede solo con el codigo falso.
        """
        if not resultados:
            return texto, False

        inventados = invented_codes(texto, resultados)
        if not inventados:
            return texto, False

        legitimos = _codigos_en_resultados(resultados)
        reales = ", ".join(
            f"{c} ({n})" if n else c
            for c, n in list(legitimos.items())[:4]
        ) or "ninguno en esta consulta"

        logger.warning(f"Copiloto: codigo inventado {inventados} -> corregido")
        aviso = (
            f"\n\n---\n"
            f"**Corrección automática del sistema.** El código "
            f"{', '.join(inventados)} no corresponde a ningún aeródromo de esta "
            f"consulta y fue generado por error. El código correcto es: {reales}."
        )
        return texto + aviso, True


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    print("=" * 74)
    print("  TEST: copilot/agent.py")
    print("=" * 74)

    todo_ok = True

    def check(desc, cond):
        global todo_ok
        todo_ok = todo_ok and bool(cond)
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    # ── 1. deteccion de veredictos, sin red ───────────────────────────────────
    print("\n  ── Deteccion de veredictos en texto ──")
    check("'NO GO' no deja un GO suelto",
          verdicts_mentioned("El resultado es NO GO por visibilidad") == {"NO GO"})
    check("'NO-GO' con guion se reconoce",
          verdicts_mentioned("veredicto: NO-GO") == {"NO GO"})
    check("GO solo",
          verdicts_mentioned("Da **GO**, podes salir") == {"GO"})
    check("CAUTION solo",
          verdicts_mentioned("Da CAUTION por viento") == {"CAUTION"})
    check("dos veredictos se detectan los dos",
          verdicts_mentioned("no es GO, es CAUTION") == {"GO", "CAUTION"})
    check("parafrasis no cuenta como veredicto",
          verdicts_mentioned("mejor no salgas, esta feo") == set())

    # ── 2. la barrera R2 con cliente simulado ────────────────────────────────
    print("\n  ── Garantia R2 (se fuerza el veredicto) ──")
    from copilot.client import ScriptedClient

    METEO = {
        "ok": True, "veredicto": "NO GO", "r_total": 0.71,
        "aerodromo": {"codigo": "SACC", "nombre": "LA CUMBRE"},
        "momento_evaluado": "manana 09:00 hora local",
        "fuente_explicada": "pronostico numerico NWP (no es una observacion directa)",
        "bloqueo_normativo": {"activo": False, "detalle": None},
        "desglose": {"factor_dominante": "visibilidad", "motivo_barrera": None},
    }

    texto, forzado = CopilotAgent._enforce_verdict(
        "Da **NO GO** por visibilidad.", [METEO])
    check("transcripcion correcta NO se toca", forzado is False)

    texto, forzado = CopilotAgent._enforce_verdict(
        "Yo diria que mejor no salgas.", [METEO])
    check("parafrasis se corrige", forzado is True and "NO GO" in texto)

    texto, forzado = CopilotAgent._enforce_verdict(
        "Esta todo bien, da GO.", [METEO])
    check("veredicto invertido se corrige", forzado is True and "NO GO" in texto)

    texto, forzado = CopilotAgent._enforce_verdict("cualquier cosa", [])
    check("sin evaluacion meteo no interviene", forzado is False)

    # ── 2b. la barrera R4 con el caso real observado en vivo ─────────────────
    print("\n  ── Garantia R4 (codigo de aerodromo inventado) ──")
    CONTACTO = {
        "ok": True,
        "aerodromo": {"codigo": "ALT", "nombre": "CRUZ ALTA",
                      "provincia": "CORDOBA", "icao": None},
        "contactos": {"publicado": True, "telefonos": ["(03467) 421222"]},
    }
    check("detecta el codigo inventado del caso real",
          invented_codes("Para **SAAL / Cruz Alta** llama al ...", [CONTACTO]) == ["SAAL"])
    check("no marca el codigo legitimo",
          invented_codes("En ALT (Cruz Alta) llama al ...", [CONTACTO]) == [])

    METEO_SACC = {"ok": True, "aerodromo": {"codigo": "SACC", "nombre": "LA CUMBRE",
                                            "icao": "SACC"}}
    check("un SAxx legitimo no se marca",
          invented_codes("En SACC da NO GO", [METEO_SACC]) == [])

    texto, corregido = CopilotAgent._enforce_codes(
        "Para **SAAL / Cruz Alta** llama al (03467) 421222.", [CONTACTO])
    check("agrega la correccion", corregido is True)
    check("y nombra el codigo verdadero", "ALT" in texto.split("---")[-1])
    check("sin conservar el texto util no serviria", "(03467) 421222" in texto)

    # Un codigo REAL nombrado desde el historial no es una fabricacion.
    # Sin esta salvedad el guardia da falsos positivos en multi-turno.
    RES_ROSARIO = [{"ok": True, "aerodromo": {"codigo": "SAAR", "nombre": "ROSARIO",
                                              "icao": "SAAR"}}]
    check("un codigo real de otro turno NO se marca",
          invented_codes("En SAAR da GO, mejor que en SACC de antes.",
                         RES_ROSARIO) == [])
    check("pero uno fabricado si, aunque haya reales alrededor",
          invented_codes("En SAAR da GO, mejor que en SAZZ.",
                         RES_ROSARIO) == ["SAZZ"])

    # ── 2c. recorte seguro del historial ─────────────────────────────────────
    print("\n  ── Recorte de historial en limites de turno ──")

    def turno(i, n_tools=1):
        e = [{"role": "user", "parts": [{"text": f"pregunta {i}"}]}]
        for _ in range(n_tools):
            e.append({"role": "model", "parts": [{"functionCall": {"name": "x", "args": {}}}]})
            e.append({"role": "user", "parts": [{"functionResponse": {"name": "x", "response": {}}}]})
        e.append({"role": "model", "parts": [{"text": f"respuesta {i}"}]})
        return e

    def primero_es_seguro(h):
        if not h:
            return True
        partes = h[0].get("parts", [])
        return h[0].get("role") == "user" and any("text" in p for p in partes)

    # Turnos de largo variable: es el caso que rompia el recorte por cantidad.
    largo = []
    for i in range(4):
        largo += turno(i, n_tools=(i % 2) + 1)
    check(f"historial irregular de {len(largo)} entradas queda en limite seguro",
          primero_es_seguro(trim_history(largo)))
    check("historial corto no se toca",
          trim_history(turno(0)) == turno(0))
    check("historial vacio no rompe", trim_history(None) == [])

    # Caso patologico: ventana sin ningun mensaje de texto del piloto.
    patologico = [{"role": "model", "parts": [{"functionCall": {"name": "x"}}]},
                  {"role": "user", "parts": [{"functionResponse": {"name": "x"}}]}] * 20
    check("ventana sin limite seguro descarta el historial",
          trim_history(patologico) == [])

    # ── 3. bucle completo con cliente simulado ───────────────────────────────
    print("\n  ── Bucle completo sin red ──")
    from copilot.client import LLMResponse as R

    scripted = ScriptedClient(respuestas=[
        R(text="", tool_calls=(ToolCall("contacto_aerodromo", {"query": "Cruz Alta"}),),
          raw_content={"role": "model", "parts": [{"functionCall": {
              "name": "contacto_aerodromo", "args": {"query": "Cruz Alta"}}}]}),
        R(text="En **ALT (Cruz Alta)** podes llamar al (03467) 15-438878, "
               "Jefe de Aerodromo."),
    ])
    ans = CopilotAgent(scripted).ask("a quien llamo en Cruz Alta?")
    check("clasifico la intencion", ans.intent == "contacto_aerodromo")
    check("la herramienta corrio bien", ans.invocations[0].ok is True)
    check("la respuesta esta anclada en datos", ans.grounded is True)
    check("no hubo que forzar veredicto", ans.verdict_enforced is False)
    print(f"       respuesta: {ans.text[:70]}...")

    # ── 4. fuera de alcance ──────────────────────────────────────────────────
    scripted2 = ScriptedClient(respuestas=[
        R(text="Eso esta fuera de mi alcance. Puedo buscarte aerodromos, "
               "telefonos, servicios o evaluar la meteo."),
    ])
    ans2 = CopilotAgent(scripted2).ask("cual es la capital de Francia?")
    check("fuera de alcance sin herramienta", ans2.intent == T.INTENT_OUT_OF_SCOPE)
    check("y queda marcado como no anclado", ans2.grounded is False)

    # ── 5. proveedor caido ───────────────────────────────────────────────────
    class Caido:
        def generate(self, *a, **k):
            raise LLMUnavailable("todos los modelos dieron 503")

        @staticmethod
        def build_tool_result_content(x):
            return {}

    ans3 = CopilotAgent(Caido()).ask("a quien llamo en Cruz Alta?")
    check("proveedor caido degrada con aviso", "no esta disponible" in ans3.text)
    check("y lo registra como error", ans3.error is not None)

    print("\n" + "=" * 74)
    print(f"  {'TODOS LOS TESTS PASARON' if todo_ok else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 74)
