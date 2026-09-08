"""
tools.py
========
Las herramientas deterministas del copiloto VFR.

Cada herramienta es un envoltorio fino sobre modulos que ya existen en el
sistema. NINGUNA agrega una fuente de datos nueva: el asistente no amplia lo
que el sistema sabe, hace utilizable lo que ya releva.

Contrato de honestidad (regla R3 del diseno)
--------------------------------------------
Ausencia de dato NO es ausencia de la cosa. Ninguna herramienta devuelve ""
ni una lista vacia a secas: devuelve una estructura que dice explicitamente
que el registro no publica ese dato.

Esto no es cosmetico. La cobertura del registro MADHEL es despareja y, sobre
todo, es INVERSA a la intuicion: los aerodromos grandes y controlados
(SACO, SAAR) tienen combustible, telefonos y normas VACIOS, porque esos datos
se publican en el AIP; los rurales chicos los tienen completos. Un piloto que
pregunta "tiene combustible Cordoba?" y recibe "no" quedaria peor informado
que antes de preguntar. La respuesta correcta es "el registro no lo publica
para ese aerodromo".

Cobertura medida sobre los 561 aerodromos:
    province           100.0 %      icao_code    24.6 %
    runways             98.6 %      fuel         19.6 %
    phones              70.4 %      schedule      7.8 %
    norms_particular    67.0 %      municipality  0.0 %

Herramientas
------------
    buscar_aerodromo      -> data.airports.search_airports
    contacto_aerodromo    -> AirportInfo.phones
    servicios_aerodromo   -> fuel / schedule / runways / condition / norms
    combustible_cercano   -> los 110 con fuel + haversine
    evaluar_meteo         -> DecisionEngine.evaluate   (unica que sale a la red)
"""

import inspect
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from data.airports import AIRPORTS, AirportInfo, get_by_code, search_airports
    from route.performance import haversine_km
    from risk.aircraft_profiles import PROFILE_NAMES, get_profile
except ImportError:                                    # ejecucion como script
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS, AirportInfo, get_by_code, search_airports
    from route.performance import haversine_km
    from risk.aircraft_profiles import PROFILE_NAMES, get_profile

logger = logging.getLogger(__name__)


# Argentina no aplica horario de verano desde 2009: el desfase es fijo.
_AR_UTC_OFFSET_H = -3

# Tope de aerodromos que se devuelven en un listado, para no inundar el
# contexto del modelo ni la pantalla del piloto.
_MAX_CANDIDATOS = 8

_NOTA_SIN_DATO = "el registro MADHEL/ANAC no publica este dato para el aerodromo"
_NOTA_CONTROLADO = (
    "es un aerodromo controlado: estos datos suelen publicarse en el AIP/AIS, "
    "no en el registro MADHEL. Que no figure aca NO significa que no exista"
)


# ──────────────────────────────────────────────────────────────────────────────
# Utilidades internas
# ──────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Minusculas sin acentos ni marcas invisibles, para comparar nombres."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.encode("ascii", "ignore").decode().lower().strip()


def _campo(valor: str, es_controlado: bool = False) -> Dict[str, Any]:
    """
    Envuelve un campo de texto en la estructura honesta de disponibilidad.

    Nunca devuelve "" pelado: el modelo tiene que poder distinguir entre
    "no hay" y "no se publica".
    """
    if valor and valor.strip():
        return {"publicado": True, "valor": valor.strip()}
    nota = _NOTA_CONTROLADO if es_controlado else _NOTA_SIN_DATO
    return {"publicado": False, "valor": None, "nota": nota}


def _ficha_breve(ap: AirportInfo) -> Dict[str, Any]:
    """Identificacion minima de un aerodromo, para listados y desambiguacion."""
    return {
        "codigo": ap.code,
        "nombre": ap.name,
        "provincia": ap.province,
        "icao": ap.icao_code or None,
        "condicion": ap.condition or None,
        "elevacion_ft": ap.elev_ft,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Resolutor de aerodromos
# ──────────────────────────────────────────────────────────────────────────────
# El piloto escribe "Cordoba", "La Cumbre" o "SACO", no la clave primaria del
# registro. Y la busqueda cruda es demasiado laxa: "Cordoba" devuelve 69
# resultados porque tambien matchea la PROVINCIA. Hace falta ordenar.
#
# La jerarquia es deliberada: un codigo exacto gana siempre, despues el nombre
# exacto, despues el prefijo, y una coincidencia solo por provincia es la mas
# debil de todas. Los desempates prefieren el aerodromo mas probable como
# destino (con ICAO, publico, controlado), que es lo que alguien quiere decir
# cuando nombra una ciudad grande.

_PUNTAJE_CODIGO_EXACTO = 1000
_PUNTAJE_NOMBRE_EXACTO = 500
_PUNTAJE_NOMBRE_PREFIJO = 300
_PUNTAJE_PALABRA_PREFIJO = 200
_PUNTAJE_NOMBRE_CONTIENE = 100
_PUNTAJE_SOLO_PROVINCIA = 10


def _puntuar(ap: AirportInfo, q: str) -> int:
    """Puntaje de afinidad entre un aerodromo y la consulta ya normalizada."""
    nombre = _norm(ap.name)
    codigos = {_norm(ap.code)}
    if ap.icao_code:
        codigos.add(_norm(ap.icao_code))
    if ap.local_id:
        codigos.add(_norm(ap.local_id))

    if q in codigos:
        base = _PUNTAJE_CODIGO_EXACTO
    elif nombre == q:
        base = _PUNTAJE_NOMBRE_EXACTO
    elif nombre.startswith(q):
        base = _PUNTAJE_NOMBRE_PREFIJO
    elif any(palabra.startswith(q) for palabra in nombre.replace("/", " ").split()):
        base = _PUNTAJE_PALABRA_PREFIJO
    elif q in nombre:
        base = _PUNTAJE_NOMBRE_CONTIENE
    elif q in _norm(ap.province):
        base = _PUNTAJE_SOLO_PROVINCIA
    else:
        return 0

    # Desempates: el destino mas probable cuando alguien nombra una ciudad.
    if ap.icao_code:
        base += 5
    if ap.is_public:
        base += 3
    if ap.control == "CONTROLLED":
        base += 2
    return base


def resolve_airport(
    query: str,
    provincia: Optional[str] = None,
) -> Tuple[Optional[AirportInfo], List[AirportInfo]]:
    """
    Resuelve un texto libre a un aerodromo del registro.

    Returns
    -------
    (aerodromo, candidatos)
        Si se resolvio sin ambiguedad, `aerodromo` es el elegido.
        Si no, `aerodromo` es None y `candidatos` trae las opciones para que
        el asistente REPREGUNTE. Nunca elige a la fuerza entre empatados:
        preguntar es barato, mandar a un piloto al aerodromo equivocado no.
    """
    q = _norm(query)
    if not q:
        return None, []

    universo = AIRPORTS.values()
    if provincia:
        pq = _norm(provincia)
        universo = [a for a in universo if pq in _norm(a.province)]

    puntuados = [(_puntuar(a, q), a) for a in universo]
    puntuados = [(p, a) for p, a in puntuados if p > 0]
    if not puntuados:
        return None, []

    puntuados.sort(key=lambda t: (-t[0], t[1].name))
    mejor = puntuados[0][0]

    # Una coincidencia solo por provincia no resuelve nada: es un listado.
    if mejor <= _PUNTAJE_SOLO_PROVINCIA + 10:
        return None, [a for _, a in puntuados[:_MAX_CANDIDATOS]]

    empatados = [a for p, a in puntuados if p == mejor]
    if len(empatados) == 1:
        return empatados[0], []
    return None, empatados[:_MAX_CANDIDATOS]


def _resolver_o_error(query: str, provincia: Optional[str] = None):
    """
    Envoltorio comun: devuelve (aerodromo, None) o (None, respuesta_de_error).

    Centraliza los dos modos de falla de todas las herramientas para que la
    forma del error sea siempre la misma y el modelo la aprenda una sola vez.
    """
    ap, candidatos = resolve_airport(query, provincia)
    if ap is not None:
        return ap, None
    if candidatos:
        return None, {
            "ok": False,
            "motivo": "ambiguo",
            "consulta": query,
            "mensaje": "Hay varios aerodromos que coinciden. Preguntale al "
                       "piloto cual de estos quiso decir; no elijas vos.",
            "candidatos": [_ficha_breve(a) for a in candidatos],
        }
    return None, {
        "ok": False,
        "motivo": "no_encontrado",
        "consulta": query,
        "mensaje": f"No hay ningun aerodromo llamado '{query}' en el registro "
                   f"MADHEL/ANAC de {len(AIRPORTS)} aerodromos. No inventes uno: "
                   f"decile al piloto que no figura y sugerile revisar el nombre.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 1: buscar_aerodromo
# ──────────────────────────────────────────────────────────────────────────────

def buscar_aerodromo(query: str = "", provincia: Optional[str] = None) -> Dict[str, Any]:
    """Busca aerodromos por nombre, ciudad, provincia o codigo."""
    q = (query or "").strip()
    if not q and not provincia:
        return {"ok": False, "motivo": "consulta_vacia",
                "mensaje": "Pedile al piloto un nombre, ciudad o codigo."}

    if q:
        ap, candidatos = resolve_airport(q, provincia)
        encontrados = [ap] if ap else candidatos
    else:
        pq = _norm(provincia or "")
        encontrados = sorted(
            [a for a in AIRPORTS.values() if pq in _norm(a.province)],
            key=lambda a: a.name,
        )

    if not encontrados:
        if not q:
            return {
                "ok": False,
                "motivo": "no_encontrado",
                "consulta": provincia,
                "mensaje": f"No hay aerodromos registrados en '{provincia}'. "
                           f"Puede que la provincia este mal escrita. No "
                           f"inventes aerodromos ni provincias.",
            }
        return _resolver_o_error(q, provincia)[1]

    return {
        "ok": True,
        "consulta": q or provincia,
        "total": len(encontrados),
        "resultados": [_ficha_breve(a) for a in encontrados[:_MAX_CANDIDATOS]],
        "truncado": len(encontrados) > _MAX_CANDIDATOS,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 2: contacto_aerodromo
# ──────────────────────────────────────────────────────────────────────────────

def contacto_aerodromo(query: str, provincia: Optional[str] = None) -> Dict[str, Any]:
    """Telefonos publicados de un aerodromo. Cobertura: 70,4 %."""
    ap, error = _resolver_o_error(query, provincia)
    if error:
        return error

    controlado = ap.control == "CONTROLLED"
    if ap.phones:
        contactos = {"publicado": True, "telefonos": list(ap.phones)}
    else:
        contactos = {
            "publicado": False,
            "telefonos": [],
            "nota": _NOTA_CONTROLADO if controlado else _NOTA_SIN_DATO,
        }

    return {
        "ok": True,
        "aerodromo": _ficha_breve(ap),
        "contactos": contactos,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 3: servicios_aerodromo
# ──────────────────────────────────────────────────────────────────────────────

def servicios_aerodromo(query: str, provincia: Optional[str] = None) -> Dict[str, Any]:
    """Ficha operativa: combustible, horario, pistas, condicion y normas."""
    ap, error = _resolver_o_error(query, provincia)
    if error:
        return error

    controlado = ap.control == "CONTROLLED"
    return {
        "ok": True,
        "aerodromo": _ficha_breve(ap),
        "control": ap.control or None,
        "condicion": ap.condition or None,
        "combustible": _campo(ap.fuel, controlado),
        "horario": _campo(ap.schedule, controlado),
        "normas_particulares": _campo(ap.norms_particular, controlado),
        "pistas": [
            {
                "designacion": r.label,
                "rumbo_magnetico": r.heading,
                "largo_m": r.length_m,
                "ancho_m": r.width_m,
                "superficie": r.surface or None,
            }
            for r in ap.runways
        ] or None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 4: combustible_cercano
# ──────────────────────────────────────────────────────────────────────────────

def combustible_cercano(
    query: str,
    radio_km: float = 150.0,
    provincia: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aerodromos con combustible PUBLICADO cerca de un punto de referencia.

    La advertencia de cobertura no es opcional: solo 110 de 561 aerodromos
    declaran combustible, y los grandes controlados no lo declaran aunque
    obviamente lo tengan. Una lista sin esa aclaracion induce a error.
    """
    ap, error = _resolver_o_error(query, provincia)
    if error:
        return error

    radio = max(10.0, min(float(radio_km or 150.0), 800.0))
    cercanos = []
    for otro in AIRPORTS.values():
        if otro.code == ap.code or not otro.fuel.strip():
            continue
        d = haversine_km(ap.lat, ap.lon, otro.lat, otro.lon)
        if d <= radio:
            cercanos.append((d, otro))
    cercanos.sort(key=lambda t: t[0])

    con_fuel_total = sum(1 for a in AIRPORTS.values() if a.fuel.strip())
    return {
        "ok": True,
        "referencia": _ficha_breve(ap),
        "radio_km": radio,
        "combustible_en_la_referencia": _campo(ap.fuel, ap.control == "CONTROLLED"),
        "total_encontrados": len(cercanos),
        "resultados": [
            {**_ficha_breve(o), "distancia_km": round(d, 1), "combustible": o.fuel}
            for d, o in cercanos[:_MAX_CANDIDATOS]
        ],
        "advertencia_de_cobertura": (
            f"Esta lista sale del registro MADHEL/ANAC, donde solo "
            f"{con_fuel_total} de {len(AIRPORTS)} aerodromos declaran combustible. "
            f"Que un aerodromo no aparezca NO significa que no tenga: puede ser "
            f"que no lo publique. Hay que confirmar por telefono antes de "
            f"planificar la etapa. Decile esto al piloto siempre."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 5: evaluar_meteo
# ──────────────────────────────────────────────────────────────────────────────

def _parse_cuando(cuando: Optional[str]) -> Tuple[int, str]:
    """
    Convierte una hora local argentina a Unix UTC.

    Acepta 'YYYY-MM-DD HH:MM' y 'HH:MM' (proxima ocurrencia). Sin argumento,
    dentro de una hora. Devuelve tambien la etiqueta legible que se le muestra
    al piloto, para que pueda detectar si el asistente entendio mal la fecha.
    """
    ahora_utc = datetime.now(timezone.utc)
    tz_ar = timezone(timedelta(hours=_AR_UTC_OFFSET_H))

    texto = (cuando or "").strip().replace("T", " ")
    if not texto:
        dep = ahora_utc + timedelta(hours=1)
        return int(dep.timestamp()), "dentro de una hora"

    for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%H:%M"):
        try:
            crudo = datetime.strptime(texto, formato)
        except ValueError:
            continue
        if formato == "%H:%M":
            local_ahora = ahora_utc.astimezone(tz_ar)
            dep_local = local_ahora.replace(hour=crudo.hour, minute=crudo.minute,
                                            second=0, microsecond=0)
            if dep_local < local_ahora:
                dep_local += timedelta(days=1)
        else:
            dep_local = crudo.replace(tzinfo=tz_ar)
        return (int(dep_local.astimezone(timezone.utc).timestamp()),
                dep_local.strftime("%d/%m/%Y %H:%M") + " hora local")

    dep = ahora_utc + timedelta(hours=1)
    return int(dep.timestamp()), "dentro de una hora (no se entendio la fecha pedida)"


def evaluar_meteo(
    query: str,
    cuando: Optional[str] = None,
    duracion_h: float = 1.0,
    aeronave: Optional[str] = None,
    provincia: Optional[str] = None,
    engine_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """
    Corre el motor determinista y devuelve el veredicto TAL CUAL.

    Esta herramienta NO interpreta ni suaviza nada. El veredicto
    GO / CAUTION / NO GO sale de `DecisionEngine.evaluate()` y el agente lo
    transcribe literalmente; la validacion posterior en agent.py verifica que
    el texto final no lo contradiga.

    No se pasa `runway_heading`: se deja en None para que el motor use la pista
    favorable. La eleccion de pista es del piloto, no del asistente.

    `engine_factory` existe para inyectar un motor simulado en los tests: es la
    unica herramienta que sale a la red.
    """
    ap, error = _resolver_o_error(query, provincia)
    if error:
        return error

    dep_ts, etiqueta = _parse_cuando(cuando)
    try:
        duracion = max(0.5, min(float(duracion_h or 1.0), 12.0))
    except (TypeError, ValueError):
        duracion = 1.0

    perfil_nombre = None
    if aeronave:
        for nombre in PROFILE_NAMES:
            if _norm(aeronave) in _norm(nombre):
                perfil_nombre = nombre
                break

    if engine_factory is None:
        from decision.engine import DecisionEngine
        engine_factory = DecisionEngine

    kwargs: Dict[str, Any] = {}
    if perfil_nombre:
        kwargs["aircraft"] = get_profile(perfil_nombre)

    try:
        engine = engine_factory(**kwargs)
        resultado = engine.evaluate(
            station_id=ap.code,
            runway_heading=None,
            departure_time=dep_ts,
            flight_duration_h=duracion,
        )
    except Exception as e:                                   # pragma: no cover
        logger.warning(f"Copiloto: fallo la evaluacion de {ap.code}: {e}")
        return {
            "ok": False,
            "motivo": "motor_no_disponible",
            "aerodromo": _ficha_breve(ap),
            "mensaje": "No se pudo obtener el pronostico. Decile al piloto que "
                       "el dato no esta disponible ahora; no estimes vos.",
        }

    if not resultado.fetch_ok:
        return {
            "ok": False,
            "motivo": "sin_datos_meteorologicos",
            "aerodromo": _ficha_breve(ap),
            "detalle": resultado.error_message or "",
            "mensaje": "No hay datos meteorologicos para ese aerodromo y esa "
                       "hora. No inventes un pronostico.",
        }

    desglose = None
    if resultado.score_breakdown is not None:
        sb = resultado.score_breakdown
        desglose = {
            "factor_dominante": getattr(sb, "dominant_factor", None),
            "barrera_activada": getattr(sb, "guardrail_floor", None),
            "motivo_barrera": getattr(sb, "guardrail_reason", None) or None,
        }

    return {
        "ok": True,
        "aerodromo": _ficha_breve(ap),
        "momento_evaluado": etiqueta,
        "duracion_h": duracion,
        "aeronave": perfil_nombre or "perfil por defecto del sistema",
        # ── Estos tres campos son el nucleo: se transcriben, no se parafrasean.
        "veredicto": resultado.decision,
        "r_total": round(resultado.r_total, 3),
        "bloqueo_normativo": {
            "activo": resultado.hard_blocked,
            "detalle": resultado.blocker_summary or None,
        },
        "desglose": desglose,
        "fuente": resultado.weather_source,
        "fuente_explicada": (
            "observacion METAR real" if resultado.weather_source == "metar"
            else "pronostico numerico NWP (no es una observacion directa)"
        ),
        "instruccion": (
            "Transcribi el veredicto EXACTAMENTE como figura en el campo "
            "'veredicto'. No lo suavices, no lo endurezcas y no uses sinonimos. "
            "Aclarale al piloto de que fuente salio."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Registro de herramientas
# ──────────────────────────────────────────────────────────────────────────────
# El nombre de cada entrada es la intencion medible en la matriz de confusion.

REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "buscar_aerodromo": buscar_aerodromo,
    "contacto_aerodromo": contacto_aerodromo,
    "servicios_aerodromo": servicios_aerodromo,
    "combustible_cercano": combustible_cercano,
    "evaluar_meteo": evaluar_meteo,
}

# Intencion adicional que no ejecuta nada. Existe para que el clasificador
# tenga a donde ir cuando la pregunta esta fuera de alcance: sin esta clase el
# modelo esta obligado a elegir una de las cinco y termina inventando.
INTENT_OUT_OF_SCOPE = "fuera_de_alcance"

INTENTS: Tuple[str, ...] = tuple(REGISTRY.keys()) + (INTENT_OUT_OF_SCOPE,)


_P_QUERY = {
    "type": "string",
    "description": "Nombre, ciudad o codigo del aerodromo, tal como lo dijo el piloto.",
}
_P_PROV = {
    "type": "string",
    "description": "Provincia, solo si el piloto la menciono para desambiguar.",
}


def tool_declarations() -> List[Dict[str, Any]]:
    """Esquema de las herramientas en el formato de function calling de Gemini."""
    return [{"functionDeclarations": [
        {
            "name": "buscar_aerodromo",
            "description": (
                "Busca aerodromos argentinos por nombre, ciudad, provincia o "
                "codigo. Usala cuando el piloto quiere ubicar un aerodromo o "
                "saber su codigo, o cuando no sabes a cual se refiere."
            ),
            "parameters": {"type": "object",
                           "properties": {"query": _P_QUERY, "provincia": _P_PROV},
                           "required": ["query"]},
        },
        {
            "name": "contacto_aerodromo",
            "description": (
                "Telefonos publicados de un aerodromo, incluido el del jefe de "
                "aerodromo cuando figura. Usala para cualquier pregunta sobre a "
                "quien llamar o como contactar un aerodromo."
            ),
            "parameters": {"type": "object",
                           "properties": {"query": _P_QUERY, "provincia": _P_PROV},
                           "required": ["query"]},
        },
        {
            "name": "servicios_aerodromo",
            "description": (
                "Ficha operativa de un aerodromo: combustible, horario de "
                "atencion, pistas con rumbo y superficie, elevacion, si es "
                "publico o privado, controlado o no, y normas particulares. "
                "Usala para preguntas sobre las caracteristicas de UN aerodromo."
            ),
            "parameters": {"type": "object",
                           "properties": {"query": _P_QUERY, "provincia": _P_PROV},
                           "required": ["query"]},
        },
        {
            "name": "combustible_cercano",
            "description": (
                "Aerodromos con combustible publicado cerca de una referencia. "
                "Usala cuando el piloto pregunta DONDE puede repostar o cargar "
                "combustible, no cuando pregunta por un aerodromo puntual."
            ),
            "parameters": {"type": "object", "properties": {
                "query": _P_QUERY,
                "radio_km": {"type": "number",
                             "description": "Radio de busqueda en km (por defecto 150)."},
                "provincia": _P_PROV,
            }, "required": ["query"]},
        },
        {
            "name": "evaluar_meteo",
            "description": (
                "Corre el motor de decision VFR y devuelve el veredicto "
                "GO / CAUTION / NO GO para volar desde un aerodromo. Usala "
                "para cualquier pregunta sobre si se puede volar, como esta el "
                "tiempo o si las condiciones son aptas."
            ),
            "parameters": {"type": "object", "properties": {
                "query": _P_QUERY,
                "cuando": {
                    "type": "string",
                    "description": (
                        "Hora local argentina de despegue, como 'YYYY-MM-DD HH:MM' "
                        "o 'HH:MM'. Resolve vos las expresiones relativas "
                        "('manana', 'el viernes') usando la fecha de hoy que "
                        "figura en tus instrucciones. Omitila si no la dijo."
                    ),
                },
                "duracion_h": {"type": "number",
                               "description": "Duracion estimada del vuelo en horas."},
                "aeronave": {"type": "string",
                             "description": f"Una de: {', '.join(PROFILE_NAMES)}."},
                "provincia": _P_PROV,
            }, "required": ["query"]},
        },
    ]}]


def execute(name: str, args: Dict[str, Any], **extra) -> Dict[str, Any]:
    """
    Ejecuta una herramienta por nombre, filtrando argumentos desconocidos.

    El modelo a veces inventa parametros. Se descartan en silencio en lugar de
    romper: la herramienta corre igual con los que si entiende.
    """
    fn = REGISTRY.get(name)
    if fn is None:
        return {"ok": False, "motivo": "herramienta_desconocida",
                "mensaje": f"No existe la herramienta '{name}'."}

    validos = set(inspect.signature(fn).parameters)
    limpios = {k: v for k, v in (args or {}).items() if k in validos}
    limpios.update({k: v for k, v in extra.items() if k in validos})

    try:
        return fn(**limpios)
    except Exception as e:                                   # pragma: no cover
        logger.warning(f"Copiloto: fallo la herramienta {name}: {e}")
        return {"ok": False, "motivo": "error_interno",
                "mensaje": "La herramienta fallo. Decile al piloto que el dato "
                           "no esta disponible; no lo estimes."}


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("=" * 74)
    print("  TEST: copilot/tools.py")
    print("=" * 74)

    todo_ok = True

    def check(desc, cond):
        global todo_ok
        todo_ok = todo_ok and bool(cond)
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    print("\n  ── Resolutor ──")
    for consulta, esperado in [("SACO", "SACO"), ("La Cumbre", "SACC"),
                               ("Cruz Alta", "ALT"), ("Cordoba", "SACO"),
                               ("Rosario", "SAAR")]:
        ap, cand = resolve_airport(consulta)
        obtenido = ap.code if ap else f"ambiguo({len(cand)})"
        check(f"{consulta!r:14s} -> {obtenido}", obtenido == esperado)

    ap, cand = resolve_airport("Los Cardales")
    check("'Los Cardales' no existe en el registro", ap is None and not cand)

    print("\n  ── Contacto: dato presente vs ausente ──")
    r = contacto_aerodromo("Cruz Alta")
    check("Cruz Alta publica telefonos", r["contactos"]["publicado"] is True)
    r = contacto_aerodromo("SACO")
    check("SACO no publica y lo declara", r["contactos"]["publicado"] is False)
    check("y aclara que es controlado", "controlado" in r["contactos"]["nota"])

    print("\n  ── Servicios ──")
    r = servicios_aerodromo("La Cumbre")
    check("SACC trae normas particulares", r["normas_particulares"]["publicado"])
    check("SACC no publica combustible", r["combustible"]["publicado"] is False)
    check("SACC trae 2 pistas", len(r["pistas"]) == 2)

    print("\n  ── Combustible cercano ──")
    r = combustible_cercano("La Cumbre", radio_km=150)
    check("encuentra alguno cerca de SACC", r["total_encontrados"] > 0)
    check("siempre advierte la cobertura", "NO significa que no tenga"
          in r["advertencia_de_cobertura"])

    print("\n  ── Errores ──")
    r = contacto_aerodromo("Aerodromo Inexistente XYZ")
    check("no encontrado no inventa", r["motivo"] == "no_encontrado")
    r = execute("herramienta_falsa", {})
    check("herramienta desconocida se maneja", r["motivo"] == "herramienta_desconocida")
    r = execute("contacto_aerodromo", {"query": "Cruz Alta", "parametro_inventado": 1})
    check("descarta argumentos inventados", r["ok"] is True)

    print("\n  ── Esquema ──")
    decls = tool_declarations()[0]["functionDeclarations"]
    check(f"{len(decls)} herramientas declaradas", len(decls) == len(REGISTRY))
    check("los nombres coinciden con el registro",
          {d["name"] for d in decls} == set(REGISTRY))
    check("6 intenciones (5 + fuera de alcance)", len(INTENTS) == 6)

    print("\n  ── Ejemplo de salida ──")
    print(json.dumps(contacto_aerodromo("Cruz Alta"), indent=2, ensure_ascii=False)[:520])

    print("\n" + "=" * 74)
    print(f"  {'TODOS LOS TESTS PASARON' if todo_ok else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 74)
