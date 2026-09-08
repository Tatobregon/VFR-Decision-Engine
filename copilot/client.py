"""
client.py
=========
Cliente de modelo de lenguaje para el copiloto VFR.

Habla con la API de Google AI Studio (Gemini) por REST plano, sin SDK: el
protocolo de function calling es JSON y `requests` ya es dependencia del
proyecto. Esto mantiene `requirements.txt` en cuatro paquetes y hace que
cambiar de proveedor sea reescribir un solo archivo.

Estructura
----------
  LLMClient      : interfaz minima (Protocol). Todo el resto del paquete
                   depende de ESTO, no de Gemini.
  GeminiClient   : implementacion contra generativelanguage.googleapis.com
  ToolCall       : una llamada a herramienta pedida por el modelo
  LLMResponse    : respuesta normalizada, agnostica del proveedor

Hallazgos de la validacion contra la API real (paso 0 del desarrollo);
estan documentados aca porque no son obvios y costaron encontrarlos:

  1. `gemini-2.5-flash` y `gemini-2.5-flash-lite` devuelven 404 para cuentas
     nuevas: fueron retirados. No usarlos.

  2. El nivel gratuito sufre HTTP 503 ("high demand") de manera intermitente,
     y la tasa depende MUCHO del modelo: medida sobre 5-6 intentos, va de
     0/5 en `gemini-flash-latest` a 6/6 en `gemini-flash-lite-latest`. Por eso
     hay CADENA DE RESERVA entre modelos y no un modelo unico.

  3. `gemini-flash-lite-latest` RECHAZA `thinkingConfig.thinkingBudget = 0`
     con HTTP 400. Por eso no se envia `thinkingConfig` en absoluto.

  4. En la segunda vuelta del bucle hay que reenviar el `content` del modelo
     TAL CUAL vino, porque incluye `thoughtSignature` dentro de la parte
     `functionCall`. Si se reconstruye la parte a mano, la API responde
     HTTP 400 "Function call is missing a thought_signature".

Uso tipico
----------
    from copilot.client import GeminiClient

    client = GeminiClient()
    resp = client.generate(
        system_instruction="Sos un asistente VFR...",
        contents=[{"role": "user", "parts": [{"text": "hola"}]}],
        tools=[...],
    )
    print(resp.text, resp.tool_calls)
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Configuracion
# ──────────────────────────────────────────────────────────────────────────────

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Cadena de reserva, ordenada por disponibilidad medida en el nivel gratuito.
# El primero es el que se usa siempre que responda; los siguientes cubren el
# 503 intermitente. Ver hallazgo 2 del encabezado.
DEFAULT_MODELS: Tuple[str, ...] = (
    "gemini-flash-lite-latest",   # 6/6 disponible, ~1.3 s
    "gemini-3.1-flash-lite",      # 4/5 disponible, ~5.9 s
    "gemini-3.5-flash",           # 3/5 disponible, ~10.2 s
)

# Codigos que justifican reintentar con OTRO modelo (saturacion o cuota),
# a diferencia de un 400/404 que es un error nuestro y no se reintenta.
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)

# Timeout corto a proposito. Medido en vivo: el nivel gratuito a veces deja la
# conexion colgada, y esperar 30 s a un modelo que no va a responder produjo
# consultas de 72 s. Cortar a los 15 s y pasar al siguiente modelo de la cadena
# da una latencia peor-caso mucho mas baja que insistir con el primero.
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_ROUNDS    = 2       # vueltas completas sobre la cadena de modelos
_BACKOFF_S        = 1.5     # espera entre vueltas


# ──────────────────────────────────────────────────────────────────────────────
# Carga de .env sin dependencias
# ──────────────────────────────────────────────────────────────────────────────

def load_env(path: Optional[str] = None) -> None:
    """
    Carga pares CLAVE=valor de un archivo .env al entorno del proceso.

    No pisa variables ya definidas: en produccion (Render) la variable la
    provee la plataforma y el archivo no existe. Se evita depender de
    python-dotenv para no sumar un paquete por diez lineas.
    """
    if path is None:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(raiz, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, valor = linea.split("=", 1)
                clave, valor = clave.strip(), valor.strip().strip('"').strip("'")
                if clave and clave not in os.environ:
                    os.environ[clave] = valor
    except OSError as e:
        logger.warning(f"No se pudo leer {path}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Tipos normalizados (agnosticos del proveedor)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCall:
    """Una llamada a herramienta solicitada por el modelo."""
    name    : str
    args    : Dict[str, Any]
    call_id : Optional[str] = None


@dataclass(frozen=True)
class LLMResponse:
    """
    Respuesta normalizada del modelo.

    `raw_content` es el objeto `content` tal cual lo devolvio la API. Hay que
    reenviarlo sin tocar en la vuelta siguiente del bucle de herramientas
    (ver hallazgo 4 del encabezado). Es opaco a proposito: el agente lo
    transporta, no lo interpreta.
    """
    text          : str
    tool_calls    : Tuple[ToolCall, ...] = ()
    raw_content   : Optional[Dict[str, Any]] = None
    model_version : str = ""
    latency_s     : float = 0.0

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


class LLMUnavailable(RuntimeError):
    """El proveedor no respondio tras agotar la cadena de reserva."""


# ──────────────────────────────────────────────────────────────────────────────
# Interfaz
# ──────────────────────────────────────────────────────────────────────────────

class LLMClient(Protocol):
    """
    Contrato minimo que necesita el agente.

    Existe para que el resto del paquete NO dependa de Gemini: los tests
    inyectan un cliente simulado y la suite corre sin red, como el resto
    del proyecto. Si Google cambia el nivel gratuito, se implementa esta
    interfaz contra otro proveedor y no se toca nada mas.
    """

    def generate(
        self,
        system_instruction: str,
        contents: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        ...


# ──────────────────────────────────────────────────────────────────────────────
# Implementacion: Google AI Studio (Gemini)
# ──────────────────────────────────────────────────────────────────────────────

class GeminiClient:
    """Cliente REST con cadena de reserva entre modelos."""

    def __init__(
        self,
        api_key   : Optional[str] = None,
        models    : Optional[Sequence[str]] = None,
        timeout_s : float = DEFAULT_TIMEOUT_S,
        rounds    : int = DEFAULT_ROUNDS,
    ):
        if api_key is None:
            load_env()
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "Falta GEMINI_API_KEY. Definila en .env (desarrollo local) o "
                "como variable de entorno del servicio (Render). Nunca en codigo."
            )
        self._api_key = api_key
        self.models   = tuple(models) if models else DEFAULT_MODELS
        self.timeout_s = timeout_s
        self.rounds    = rounds
        # Ultimo modelo que respondio bien; se registra para poder declarar en
        # el informe con que version concreta se corrieron las metricas.
        self.last_model_used: str = ""

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _post(self, model: str, body: Dict[str, Any]) -> Tuple[float, Any]:
        url = f"{_API_BASE}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self._api_key,
                   "Content-Type": "application/json"}
        t0 = time.time()
        resp = requests.post(url, headers=headers, json=body,
                             timeout=self.timeout_s)
        return time.time() - t0, resp

    def generate(
        self,
        system_instruction: str,
        contents: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """
        Una vuelta de conversacion. Recorre la cadena de modelos ante 503.

        No se envia `thinkingConfig`: `gemini-flash-lite-latest` lo rechaza
        con HTTP 400 (hallazgo 3 del encabezado).
        """
        body: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            body["tools"] = tools

        ultimo_error = "sin intentos"
        for ronda in range(self.rounds):
            for model in self.models:
                try:
                    latencia, resp = self._post(model, body)
                except requests.RequestException as e:
                    ultimo_error = f"{model}: {type(e).__name__}"
                    logger.warning(f"Copiloto: {ultimo_error}, sigo con el proximo modelo")
                    continue

                if resp.status_code == 200:
                    self.last_model_used = model
                    if ronda > 0 or model != self.models[0]:
                        logger.info(f"Copiloto: respondio {model} (reserva)")
                    return self._parse(resp.json(), latencia)

                if resp.status_code in _RETRYABLE_STATUS:
                    ultimo_error = f"{model}: HTTP {resp.status_code}"
                    logger.warning(f"Copiloto: {ultimo_error}, sigo con el proximo modelo")
                    continue

                # 400 / 404 / 403: error nuestro o de credencial. No se reintenta.
                detalle = self._error_message(resp)
                raise LLMUnavailable(f"{model}: HTTP {resp.status_code} - {detalle}")

            if ronda + 1 < self.rounds:
                time.sleep(_BACKOFF_S)

        raise LLMUnavailable(
            f"Ningun modelo respondio tras {self.rounds} vueltas "
            f"sobre {len(self.models)} modelos. Ultimo: {ultimo_error}"
        )

    # ── Normalizacion ─────────────────────────────────────────────────────────

    @staticmethod
    def _error_message(resp) -> str:
        try:
            return str(resp.json()["error"]["message"])[:200]
        except Exception:
            return resp.text[:200]

    @staticmethod
    def _parse(data: Dict[str, Any], latencia: float) -> LLMResponse:
        """Convierte la respuesta cruda de Gemini en LLMResponse."""
        version = str(data.get("modelVersion", ""))
        try:
            content = data["candidates"][0]["content"]
        except (KeyError, IndexError):
            # Puede pasar con filtros de seguridad o respuesta vacia.
            logger.warning("Copiloto: respuesta sin content utilizable")
            return LLMResponse(text="", model_version=version, latency_s=latencia)

        partes = content.get("parts", []) or []
        textos: List[str] = []
        llamadas: List[ToolCall] = []
        for parte in partes:
            if "functionCall" in parte:
                fc = parte["functionCall"]
                llamadas.append(ToolCall(
                    name=fc.get("name", ""),
                    args=dict(fc.get("args") or {}),
                    call_id=fc.get("id"),
                ))
            elif "text" in parte:
                textos.append(parte["text"])

        return LLMResponse(
            text="".join(textos).strip(),
            tool_calls=tuple(llamadas),
            raw_content=content,
            model_version=version,
            latency_s=latencia,
        )

    # ── Construccion del turno de resultado de herramienta ────────────────────

    @staticmethod
    def build_tool_result_content(
        calls_and_results: Sequence[Tuple[ToolCall, Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        Arma el turno que devuelve al modelo lo que produjeron las herramientas.

        Se emite con rol "user" porque es lo que acepta la API de Gemini para
        `functionResponse`. El `id` se reenvia cuando existe, para que el
        modelo pueda aparear llamadas en paralelo.
        """
        partes = []
        for call, resultado in calls_and_results:
            fr: Dict[str, Any] = {"name": call.name, "response": resultado}
            if call.call_id:
                fr["id"] = call.call_id
            partes.append({"functionResponse": fr})
        return {"role": "user", "parts": partes}


# ──────────────────────────────────────────────────────────────────────────────
# Cliente simulado (para tests sin red)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScriptedClient:
    """
    Cliente que devuelve respuestas prefijadas, en orden.

    Permite testear el agente completo sin tocar la red, que es la convencion
    de toda la suite del proyecto. Registra las llamadas recibidas para poder
    verificar QUE se le mando al modelo, no solo que respondio.
    """
    respuestas : List[LLMResponse] = field(default_factory=list)
    recibidas  : List[Dict[str, Any]] = field(default_factory=list)
    _i         : int = 0

    def generate(
        self,
        system_instruction: str,
        contents: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        self.recibidas.append({
            "system": system_instruction,
            "contents": [dict(c) for c in contents],
            "tools": tools,
        })
        if self._i >= len(self.respuestas):
            raise AssertionError(
                f"ScriptedClient sin respuestas: se pidieron {self._i + 1}, "
                f"hay {len(self.respuestas)} cargadas"
            )
        r = self.respuestas[self._i]
        self._i += 1
        return r

    @staticmethod
    def build_tool_result_content(calls_and_results):
        return GeminiClient.build_tool_result_content(calls_and_results)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=" * 68)
    print("  TEST: copilot/client.py")
    print("=" * 68)

    load_env()
    if not os.environ.get("GEMINI_API_KEY"):
        print("\n  SIN GEMINI_API_KEY: definila en .env para probar contra la API.")
        raise SystemExit(0)

    client = GeminiClient()
    print(f"\n  cadena de modelos: {', '.join(client.models)}")

    # 1. respuesta de texto simple
    r = client.generate(
        system_instruction="Respondes en una sola linea, en espanol.",
        contents=[{"role": "user", "parts": [{"text": "Decime 'listo' y nada mas."}]}],
    )
    print(f"\n  [1] texto        : {r.text!r}")
    print(f"      modelo usado : {client.last_model_used} ({r.model_version})")
    print(f"      latencia     : {r.latency_s:.2f}s")

    # 2. function calling
    tools = [{"functionDeclarations": [{
        "name": "contacto_aerodromo",
        "description": "Telefonos publicados de un aerodromo argentino.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]},
    }]}]
    contents = [{"role": "user", "parts": [{"text": "a quien llamo en Cruz Alta?"}]}]
    r2 = client.generate("Siempre usas las herramientas disponibles.", contents, tools)
    print(f"\n  [2] tool_calls   : {[ (c.name, c.args) for c in r2.tool_calls ]}")
    print(f"      latencia     : {r2.latency_s:.2f}s")

    # 3. segunda vuelta reenviando el content original
    if r2.wants_tool:
        call = r2.tool_calls[0]
        contents.append(r2.raw_content)
        contents.append(client.build_tool_result_content([
            (call, {"resultado": {"codigo": "CRA", "nombre": "Cruz Alta",
                                  "telefonos": ["(03467) 15-438878 - Jefe de Aerodromo"]}}),
        ]))
        r3 = client.generate("Respondes breve, en espanol rioplatense.", contents, tools)
        print(f"\n  [3] respuesta    : {r3.text}")
        print(f"      latencia     : {r3.latency_s:.2f}s")
        print(f"      TOTAL bucle  : {r2.latency_s + r3.latency_s:.2f}s")

    print("\n" + "=" * 68)
