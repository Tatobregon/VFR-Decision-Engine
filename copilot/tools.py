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
    evaluar_meteo         -> DecisionEngine.evaluate                    (red)
    atmosfera_en_punto    -> OpenMeteoFetcher.get_upper_air             (red)
    mejor_hora_para_salir -> decision.enroute.nwp_series_at_coord       (red)

Superficie y altura no se mezclan
---------------------------------
`evaluar_meteo` y `mejor_hora_para_salir` son de SUPERFICIE: miden despegue y
aterrizaje contra una pista, y de ahi que devuelvan veredicto.

`atmosfera_en_punto` es de ALTURA y no devuelve veredicto. Usa
`get_upper_air()`, que trae temperatura, punto de rocio, humedad y nubosidad
DEL NIVEL DE PRESION. No alcanza con pedir el viento en altura y dejar el resto
en superficie: a 15.000 ft la diferencia de temperatura contra el suelo es de
decenas de grados, y presentarla como dato de altura es un error grosero.
"""

import inspect
import logging
import math
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from data.airports import AIRPORTS, AirportInfo, get_by_code, search_airports
    from route.performance import haversine_km
    from route.optimizer import (ViaPoint, detour_cost, eta_por_aerodromo,
                                 optimize)
    from risk.aircraft_profiles import PROFILE_NAMES, get_profile
except ImportError:                                    # ejecucion como script
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.airports import AIRPORTS, AirportInfo, get_by_code, search_airports
    from route.performance import haversine_km
    from route.optimizer import (ViaPoint, detour_cost, eta_por_aerodromo,
                                 optimize)
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


def _compacto(s: str) -> str:
    """Sin espacios ni guiones: 'bellville' tiene que encontrar 'BELL VILLE'."""
    return s.replace(" ", "").replace("-", "").replace(".", "")


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
    # Nombre escrito sin separar: "bellville" por "BELL VILLE", "riocuarto"
    # por "RIO CUARTO". Es una forma corriente de tipear y no deberia fallar.
    # Puntua por debajo del match exacto para que no le gane a uno bien escrito.
    elif _compacto(q) and _compacto(q) in _compacto(nombre):
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


def _etiqueta_momento(unix_ts: int) -> str:
    """Momento Unix -> etiqueta legible en hora local argentina."""
    tz_ar = timezone(timedelta(hours=_AR_UTC_OFFSET_H))
    return (datetime.fromtimestamp(unix_ts, tz_ar).strftime("%d/%m/%Y %H:%M")
            + " hora local")


def _fuente_explicada(source: str) -> str:
    """
    Como se le nombra al piloto la fuente de las condiciones evaluadas.

    Son TRES, no dos. Decir "pronostico numerico NWP" sobre condiciones que
    salieron del TAF seria nombrar mal la fuente, que es justo lo que el resto
    del sistema dejo de hacer.
    """
    if source == "metar":
        return "observacion METAR real"
    if source == "metar+taf":
        return ("pronostico TAF del aerodromo para ese momento, con temperatura "
                "y punto de rocio del modelo numerico")
    return "pronostico numerico NWP (no es una observacion directa)"


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
        # Contribucion ponderada de cada criterio: w_i * r_i. Es lo que permite
        # responder "por que da CAUTION?" con el desglose real del motor en vez
        # de una explicacion inventada. Se ordena de mayor a menor para que el
        # modelo pueda nombrar los que mandan sin tener que decidir el orden.
        from risk.weights import (
            W_CEIL, W_FOG, W_GUST, W_TAF, W_VIS, W_WX, W_XWIND,
        )
        criterios = [
            ("visibilidad",     sb.r_vis,   W_VIS),
            ("techo de nubes",  sb.r_ceil,  W_CEIL),
            ("viento cruzado",  sb.r_xwind, W_XWIND),
            ("riesgo de niebla", sb.r_fog,  W_FOG),
            ("rafagas",         sb.r_gust,  W_GUST),
            ("fenomenos",       sb.r_wx,    W_WX),
            ("tendencia",       sb.r_taf,   W_TAF),
        ]
        componentes = sorted(
            ({"criterio": nombre,
              "riesgo_0_a_1": round(r, 3),
              "peso": round(w, 3),
              "aporte_al_puntaje": round(r * w, 4)}
             for nombre, r, w in criterios),
            key=lambda c: -c["aporte_al_puntaje"],
        )
        desglose = {
            "factor_dominante": getattr(sb, "dominant_factor", None),
            "barrera_activada": getattr(sb, "guardrail_floor", None),
            "motivo_barrera": getattr(sb, "guardrail_reason", None) or None,
            "componentes": componentes,
            "como_leerlo": (
                "El puntaje R es la suma de los aportes. Un criterio con riesgo "
                "alto pero peso bajo aporta poco; por eso existe ademas la "
                "barrera no-compensatoria, que impone un piso al veredicto sin "
                "promediar. Si 'barrera_activada' no es GO, el veredicto lo fijo "
                "esa barrera y no el puntaje."
            ),
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
        "fuente_explicada": _fuente_explicada(resultado.weather_source),
        "instruccion": (
            "Transcribi el veredicto EXACTAMENTE como figura en el campo "
            "'veredicto'. No lo suavices, no lo endurezcas y no uses sinonimos. "
            "Aclarale al piloto de que fuente salio."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 6: atmosfera_en_punto
# ──────────────────────────────────────────────────────────────────────────────
# Responde "como esta el aire sobre tal lugar, a tal altura, para pasar por ahi".
#
# NO DEVUELVE VEREDICTO, y eso es deliberado. GO / CAUTION / NO GO es un concepto
# de AERODROMO: mide despegue y aterrizaje contra una pista concreta. Si una
# consulta sobre el aire a 7500 ft devolviera tambien un veredicto, la etiqueta
# pasaria a significar dos cosas distintas y dejaria de ser el objeto unico y
# bien definido sobre el que se apoya todo el motor. Para un veredicto esta
# `evaluar_meteo` sobre un aerodromo.

# Grilla 3x3 centrada en el punto: 9 muestras de terreno en UNA sola peticion.
# El radio es el mismo que usa el muestreo en anillo del motor (~una celda de
# modelo global), asi que el criterio de "entorno del punto" es el mismo en
# todo el sistema.
_TERRENO_RADIO_KM = 10.0
_TERRENO_PASOS    = (-1, 0, 1)


def _terreno_alrededor(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Elevacion maxima del terreno en una grilla alrededor del punto.

    Una sola peticion a Open-Topo-Data. Si falla devuelve None: el informe de
    atmosfera se entrega igual, sin la parte de terreno, en lugar de fallar
    entero por un dato accesorio.
    """
    try:
        from data.terrain import M_TO_FT, get_elevations_m
    except ImportError:                                    # pragma: no cover
        return None

    grados_lat = _TERRENO_RADIO_KM / 111.0
    grados_lon = _TERRENO_RADIO_KM / (111.0 * max(0.2, math.cos(math.radians(lat))))
    puntos = [(lat + i * grados_lat, lon + j * grados_lon)
              for i in _TERRENO_PASOS for j in _TERRENO_PASOS]
    try:
        elevaciones = [e for e in get_elevations_m(puntos) if e is not None]
    except Exception as e:                                 # pragma: no cover
        logger.warning(f"Copiloto: no se pudo consultar el terreno: {e}")
        return None
    if not elevaciones:
        return None
    return {
        "elevacion_maxima_ft": int(max(elevaciones) * M_TO_FT),
        "radio_km": _TERRENO_RADIO_KM,
    }


def _altitud_para_el_punto(
    ap: AirportInfo,
    altura_ft: Optional[int],
    perfil,
    regimen: Optional[str],
) -> Dict[str, Any]:
    """
    Decide a que altitud se evalua el aire, y de donde salio esa altitud.

    Que la respuesta DIGA su procedencia no es adorno: es lo que le permite al
    piloto verificar que el asistente no se la invento.
    """
    # 1. La eligio el piloto: manda la suya, acotada al techo de servicio.
    if altura_ft is not None:
        try:
            pedida = int(altura_ft)
        except (TypeError, ValueError):
            pedida = 0
        if pedida > 0:
            techo = getattr(perfil, "service_ceiling_ft", 0) or 0
            usada = max(1000, min(pedida, techo) if techo else pedida)
            origen = "elegida por el piloto"
            if techo and pedida > techo:
                origen = (f"elegida por el piloto ({pedida} ft) pero acotada al "
                          f"techo de servicio del {perfil.name} ({techo} ft)")
            return {"altitud_ft": usada, "origen": origen}

    # 2. IFR: la fija la MEA de la aerovia, no el piloto.
    if (regimen or "").upper() == "IFR":
        try:
            from route.airway_router import nearest_airway_segment
            seg = nearest_airway_segment(ap.lat, ap.lon)
        except Exception:                                  # pragma: no cover
            seg = None
        if seg and seg.get("mea_ft"):
            return {
                "altitud_ft": int(seg["mea_ft"]),
                "origen": (f"MEA de la aerovia {seg['ruta']} en el tramo "
                           f"{seg['desde']}-{seg['hasta']}, a {seg['dist_km']} km "
                           f"del punto"),
                "aerovia": seg,
            }
        return {
            "altitud_ft": None,
            "origen": None,
            "sin_aerovia": True,
        }

    # 3. VFR sin altitud: no se inventa un rumbo para aplicar semicirculos.
    return {"altitud_ft": None, "origen": None}


def atmosfera_en_punto(
    lugar: str,
    altura_ft: Optional[int] = None,
    cuando: Optional[str] = None,
    aeronave: Optional[str] = None,
    regimen: Optional[str] = None,
    provincia: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Informe del estado de la atmosfera sobre un lugar, a una altitud dada.

    Sirve para decidir si conviene pasar por ahi. No es un veredicto de vuelo.
    """
    ap, error = _resolver_o_error(lugar, provincia)
    if error:
        return error

    perfil = None
    if aeronave:
        for nombre in PROFILE_NAMES:
            if _norm(aeronave) in _norm(nombre):
                perfil = get_profile(nombre)
                break
    if perfil is None:
        perfil = get_profile(PROFILE_NAMES[0])

    alt = _altitud_para_el_punto(ap, altura_ft, perfil, regimen)

    # Sin altitud no hay informe posible: se pregunta, no se supone.
    if alt["altitud_ft"] is None:
        sugerencias = [perfil.cruise_alt_ft]
        try:
            from route.airway_router import nearest_airway_segment
            seg = nearest_airway_segment(ap.lat, ap.lon)
            if seg and seg.get("mea_ft"):
                sugerencias.append(int(seg["mea_ft"]))
        except Exception:                                  # pragma: no cover
            seg = None
        return {
            "ok": False,
            "motivo": "falta_altitud",
            "lugar": _ficha_breve(ap),
            "mensaje": (
                "Para informar el estado del aire hace falta saber A QUE ALTURA "
                "va a pasar. PREGUNTASELO al piloto antes de volver a llamar a "
                "esta herramienta. No elijas vos una altitud."
                + (" No hay aerovia cerca de ese punto, asi que en IFR tampoco "
                   "hay MEA de la cual tomarla." if alt.get("sin_aerovia") else "")
            ),
            "sugerencias_ft": sorted(set(sugerencias)),
            "techo_de_servicio_ft": perfil.service_ceiling_ft,
        }

    dep_ts, etiqueta = _parse_cuando(cuando)

    try:
        from data.fetcher_openmeteo import OpenMeteoFetcher
        aire = OpenMeteoFetcher().get_upper_air(
            lat=ap.lat, lon=ap.lon, alt_ft=alt["altitud_ft"], hours_ahead=24,
        )
    except Exception as e:                                 # pragma: no cover
        logger.warning(f"Copiloto: fallo el aire en altura sobre {ap.code}: {e}")
        aire = None

    if aire is None or not aire.hours:
        return {
            "ok": False,
            "motivo": "sin_datos_meteorologicos",
            "lugar": _ficha_breve(ap),
            "mensaje": "No hay pronostico en altura para ese punto y esa hora. "
                       "No lo estimes vos.",
        }

    h = min(aire.hours, key=lambda x: abs(x.valid_time_utc - dep_ts))

    # El nivel de presion es una aproximacion de la altitud pedida, y su altura
    # real varia con la masa de aire. Informar las dos evita presentar una
    # aproximacion como si fuera una medicion en el punto exacto.
    desvio = (h.level_altitude_ft - alt["altitud_ft"]) if h.level_altitude_ft else None

    informe: Dict[str, Any] = {
        "ok": True,
        "lugar": _ficha_breve(ap),
        "altitud_consultada_ft": alt["altitud_ft"],
        "origen_de_la_altitud": alt["origen"],
        "nivel_de_presion_hpa": aire.pressure_level_hpa,
        "altitud_real_del_nivel_ft": h.level_altitude_ft,
        "desvio_respecto_de_lo_pedido_ft": desvio,
        "momento_evaluado": etiqueta,
        "regimen": (regimen or "VFR").upper(),
        "aire_en_ese_nivel": {
            "temperatura_c": h.temperature_c,
            "punto_de_rocio_c": h.dewpoint_c,
            "humedad_relativa_pct": h.relative_humidity_pct,
            "bajo_cero": (h.temperature_c is not None and h.temperature_c < 0),
            "viento_direccion_grados": h.wind_dir,
            "viento_kt": h.wind_spd_kt,
            "nubosidad_en_el_nivel_pct": h.cloud_cover_pct,
        },
        "visibilidad": {
            "disponible": False,
            "nota": "Open-Meteo no publica visibilidad por nivel de presion. La "
                    "de superficie NO sirve como sustituto y por eso no se "
                    "informa. Si el piloto la pide, decile esto.",
        },
        "fuente": "pronostico numerico NWP en el nivel de presion indicado "
                  "(no es una observacion directa ni un sondeo real)",
        "instruccion": (
            "Es un INFORME DE ATMOSFERA en altura, no un veredicto de vuelo. NO "
            "digas GO, CAUTION ni NO GO: esas etiquetas son de aerodromo y "
            "salen de evaluar_meteo. Deci a que altitud corresponde el dato, de "
            "donde salio esa altitud, y a que altura esta realmente el nivel de "
            "presion si difiere de lo pedido. Si la temperatura esta bajo cero, "
            "nombralo explicitamente."
        ),
    }
    if "aerovia" in alt:
        informe["aerovia"] = alt["aerovia"]

    terreno = _terreno_alrededor(ap.lat, ap.lon)
    if terreno:
        margen = alt["altitud_ft"] - terreno["elevacion_maxima_ft"]
        terreno["margen_ft"] = margen
        if margen < 1000:
            terreno["advertencia"] = (
                f"la altitud consultada deja solo {margen} ft sobre el terreno "
                f"mas alto del entorno; avisale al piloto"
            )
        informe["terreno"] = terreno

    return informe


# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 7: mejor_hora_para_salir
# ──────────────────────────────────────────────────────────────────────────────

_MAX_HORAS_TIMELINE = 24


def mejor_hora_para_salir(
    query: str,
    horas: int = 12,
    aeronave: Optional[str] = None,
    provincia: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Serie horaria del veredicto de un aerodromo, para elegir a que hora salir.

    Es el mismo veredicto de superficie que produce `evaluar_meteo`, calculado
    hora por hora. Se evalua cada EJE de pista del aerodromo y se toma el mejor:
    la componente de cruzado de una pista y su reciproca es la misma, asi que
    alcanza con un rumbo por eje.
    """
    ap, error = _resolver_o_error(query, provincia)
    if error:
        return error

    perfil = None
    if aeronave:
        for nombre in PROFILE_NAMES:
            if _norm(aeronave) in _norm(nombre):
                perfil = get_profile(nombre)
                break
    if perfil is None:
        perfil = get_profile(PROFILE_NAMES[0])

    try:
        ventana = max(3, min(int(horas or 12), _MAX_HORAS_TIMELINE))
    except (TypeError, ValueError):
        ventana = 12

    ejes = sorted({r.heading % 180 for r in ap.runways}) or [0]
    inicio = ((int(datetime.now(timezone.utc).timestamp()) // 3600) + 1) * 3600

    try:
        from decision.enroute import nwp_series_at_coord
        por_eje = [
            nwp_series_at_coord(
                lat=ap.lat, lon=ap.lon, elev_m=ap.elev_ft * 0.3048,
                runway_heading=eje, aircraft=perfil, mock=False,
                start_utc=inicio, hours=ventana,
            )
            for eje in ejes
        ]
    except Exception as e:                                 # pragma: no cover
        logger.warning(f"Copiloto: fallo la serie horaria de {ap.code}: {e}")
        por_eje = []

    series = [s for s in por_eje if s]
    if not series:
        return {
            "ok": False,
            "motivo": "sin_datos_meteorologicos",
            "aerodromo": _ficha_breve(ap),
            "mensaje": "No hay pronostico horario para ese aerodromo. No lo "
                       "estimes vos.",
        }

    # Mejor pista por hora: el piloto elige cabecera, no se queda con la peor.
    mejor_por_hora: Dict[int, Dict[str, Any]] = {}
    for serie in series:
        for punto in serie:
            actual = mejor_por_hora.get(punto["t"])
            if actual is None or punto["r"] < actual["r"]:
                mejor_por_hora[punto["t"]] = punto

    tz_ar = timezone(timedelta(hours=_AR_UTC_OFFSET_H))
    horas_ordenadas = sorted(mejor_por_hora.values(), key=lambda p: p["t"])

    def _local(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz_ar).strftime("%d/%m %H:%M")

    detalle = [
        {"hora_local": _local(p["t"]), "veredicto": p["dec"], "r": p["r"]}
        for p in horas_ordenadas
    ]
    primeras_go = [p for p in horas_ordenadas if p["dec"] == "GO"]
    mejor = min(horas_ordenadas, key=lambda p: p["r"])

    return {
        "ok": True,
        "aerodromo": _ficha_breve(ap),
        "aeronave": perfil.name,
        "horas_analizadas": len(detalle),
        "ejes_de_pista_evaluados": ejes,
        "primera_hora_go": _local(primeras_go[0]["t"]) if primeras_go else None,
        "horas_go": len(primeras_go),
        "mejor_hora": {"hora_local": _local(mejor["t"]), "r": mejor["r"],
                       "veredicto": mejor["dec"]},
        "serie": detalle,
        "instruccion": (
            "Es la MISMA escala GO / CAUTION / NO GO de la evaluacion de "
            "aerodromo, hora por hora. Transcribi los veredictos tal cual. "
            "Resumí: deci desde cuando mejora y cuando conviene salir, sin "
            "recitar las 12 horas una por una."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Registro de herramientas
# ──────────────────────────────────────────────────────────────────────────────
# El nombre de cada entrada es la intencion medible en la matriz de confusion.

# ──────────────────────────────────────────────────────────────────────────────
# Herramienta 8: proponer_cambio_de_ruta
# ──────────────────────────────────────────────────────────────────────────────
#
# Es la primera herramienta que no RESPONDE sino que PROPONE, y eso cambia el
# contrato en dos puntos que no son negociables:
#
#   * NO APLICA NADA. Devuelve una propuesta; el cambio lo aplica la pantalla
#     despues de que el piloto lo confirme con un click. El modelo de lenguaje
#     no puede modificar el vuelo de nadie, ni aunque se lo pidan bien.
#
#   * LA RUTA ACTUAL LA APORTA EL CODIGO, no el modelo. `ruta_actual` se inyecta
#     desde el estado de la pantalla igual que `engine_factory`. Hacer que el
#     modelo transcriba origen, destino y puntos ya cargados es exactamente como
#     nacio el bug de las tres horas: el formulario decia una cosa, el modelo
#     copiaba otra, y nadie lo notaba.

# Lo que la herramienta entiende como intencion del piloto. Se declara explicito
# porque "pasar por" y "hacer escala en" son dos vuelos distintos y la
# herramienta NO adivina: si no esta claro, devuelve `falta_tipo` y el modelo
# pregunta.
TIPO_SOBREVUELO = "sobrevuelo"
TIPO_ESCALA     = "escala"
TIPO_QUITAR     = "quitar"
TIPOS_VALIDOS   = (TIPO_SOBREVUELO, TIPO_ESCALA, TIPO_QUITAR)

# Mismo tope que la capa web (`web.app.MAX_VIA_POINTS`). Se repite como
# constante propia y no se importa para no invertir la dependencia: el copiloto
# no puede depender de `web/`.
MAX_PUNTOS_DE_PASO = 5


def _via_actual(ruta_actual: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Puntos de paso ya cargados en la pantalla, normalizados."""
    crudos = (ruta_actual or {}).get("via") or []
    salida = []
    for v in crudos:
        if not isinstance(v, dict):
            continue
        code = str(v.get("code") or "").strip().upper()
        if code and code in AIRPORTS:
            salida.append({"code": code, "is_stop": bool(v.get("is_stop"))})
    return salida


def _salida_del_formulario(ruta_actual: Dict[str, Any]) -> int:
    """
    Hora de salida del vuelo cargado en la pantalla, en Unix UTC.

    OJO CON LA ZONA: el campo `departure_time` del formulario esta en **UTC**,
    mientras que el parametro `cuando` de las otras herramientas esta en hora
    LOCAL. Pasarlo por `_parse_cuando` correria la consulta tres horas — que es
    exactamente el bug que ya costo caro en esta misma capa. Por eso se
    interpreta aca, en codigo, y no se reusa el parser de hora local.

    Sin hora cargada se usa dentro de una hora, que es lo que hace la web.
    """
    texto = str((ruta_actual or {}).get("departure_time") or "").strip()
    ahora = datetime.now(timezone.utc)
    if texto:
        try:
            h, m = (int(x) for x in texto.split(":"))
            dep = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
            if dep < ahora:
                dep += timedelta(days=1)
            return int(dep.timestamp())
        except (TypeError, ValueError):
            logger.warning(f"Copiloto: hora de salida ilegible {texto!r}")
    return int((ahora + timedelta(hours=1)).timestamp())


def proponer_cambio_de_ruta(
    lugar          : str,
    tipo           : str = "",
    ruta_actual    : Optional[Dict[str, Any]] = None,
    engine_factory : Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """
    Arma una PROPUESTA de cambio de ruta para que el piloto la confirme.

    `tipo` es "sobrevuelo" (pasar por encima), "escala" (aterrizar ahi) o
    "quitar" (sacar un punto ya cargado). Si el piloto no dejo claro cual, la
    herramienta NO elige: devuelve `falta_tipo` para que el modelo pregunte.
    Pasar por encima de Rosario y aterrizar en Rosario son dos vuelos distintos.

    Devuelve el costo del desvio —km, minutos y litros contra la ruta que hay
    ahora— y, si es escala, el veredicto del aerodromo a la hora en que se
    aterriza. Proponer una escala sin decir si se puede aterrizar ahi seria
    ofrecer un boton a ciegas.
    """
    ruta_actual = ruta_actual or {}
    origen  = str(ruta_actual.get("origin") or "").strip().upper()
    destino = str(ruta_actual.get("dest") or "").strip().upper()

    # El lugar se resuelve PRIMERO, aunque todavia no haya vuelo cargado. La
    # regla R4 pide nombrar el codigo exacto que se resolvio, y "cargá el vuelo
    # para poder agregar SAAR (Rosario)" es mucho mas util que un "no hay vuelo
    # cargado" a secas. Ademas, si el lugar no existe, eso se dice igual.
    ap, error = _resolver_o_error(lugar)
    if error:
        return error

    # ── Sin ruta cargada no hay nada que modificar ────────────────────────────
    if not origen or not destino or origen not in AIRPORTS or destino not in AIRPORTS:
        return {
            "ok": False,
            "motivo": "sin_ruta_cargada",
            "aerodromo": _ficha_breve(ap),
            "mensaje": f"El piloto quiere tocar la ruta en {ap.code} "
                       f"({ap.name}), pero todavia no tiene un vuelo cargado en "
                       f"la pantalla. Pedile que cargue origen y destino; no "
                       f"inventes un vuelo.",
        }

    via = _via_actual(ruta_actual)
    ya_esta = next((v for v in via if v["code"] == ap.code), None)

    # ── La intencion tiene que ser explicita ──────────────────────────────────
    tipo_norm = _norm(str(tipo or ""))
    if "quitar" in tipo_norm or "sacar" in tipo_norm or "elimin" in tipo_norm:
        accion = TIPO_QUITAR
    elif "escala" in tipo_norm or "aterriz" in tipo_norm or "parar" in tipo_norm:
        accion = TIPO_ESCALA
    elif "sobrevuelo" in tipo_norm or "sobrevolar" in tipo_norm or "pasar" in tipo_norm:
        accion = TIPO_SOBREVUELO
    else:
        return {
            "ok": False,
            "motivo": "falta_tipo",
            "aerodromo": _ficha_breve(ap),
            "mensaje": f"No esta claro que quiere hacer el piloto en "
                       f"{ap.code}. Preguntale si quiere SOBREVOLARLO (pasar "
                       f"por encima, vuelo continuo) o hacer ESCALA (aterrizar "
                       f"ahi). No elijas vos: son dos vuelos distintos — la "
                       f"escala exige que el aerodromo sea operable y parte el "
                       f"calculo de combustible en etapas.",
        }

    # ── Casos que no tienen sentido, dichos con nombre ────────────────────────
    if accion == TIPO_QUITAR and ya_esta is None:
        return {
            "ok": False,
            "motivo": "no_esta_en_la_ruta",
            "aerodromo": _ficha_breve(ap),
            "mensaje": f"{ap.code} no es un punto de paso del vuelo actual, "
                       f"asi que no hay nada que quitar. Decibelo al piloto.",
        }
    if accion != TIPO_QUITAR:
        if ap.code in (origen, destino):
            return {
                "ok": False,
                "motivo": "es_origen_o_destino",
                "aerodromo": _ficha_breve(ap),
                "mensaje": f"{ap.code} ya es el origen o el destino del vuelo. "
                           f"No puede ser ademas un punto de paso.",
            }
        if ya_esta is None and len(via) >= MAX_PUNTOS_DE_PASO:
            return {
                "ok": False,
                "motivo": "demasiados_puntos",
                "mensaje": f"El vuelo ya tiene {len(via)} puntos de paso, que es "
                           f"el maximo. Pedile al piloto que quite alguno antes "
                           f"de agregar otro.",
            }

    # ── Ruta resultante si el piloto acepta ───────────────────────────────────
    if accion == TIPO_QUITAR:
        via_nueva = [v for v in via if v["code"] != ap.code]
    else:
        quiere_escala = (accion == TIPO_ESCALA)
        if ya_esta is not None:
            if ya_esta["is_stop"] == quiere_escala:
                return {
                    "ok": False,
                    "motivo": "sin_cambios",
                    "aerodromo": _ficha_breve(ap),
                    "mensaje": f"{ap.code} ya esta en la ruta como "
                               f"{'escala' if quiere_escala else 'sobrevuelo'}. "
                               f"No hay nada que cambiar.",
                }
            via_nueva = [{**v, "is_stop": quiere_escala} if v["code"] == ap.code else v
                         for v in via]
        else:
            via_nueva = via + [{"code": ap.code, "is_stop": quiere_escala}]

    # ── Costo del cambio, contra la ruta que hay AHORA ────────────────────────
    # No contra la ruta directa: si el piloto ya tiene dos puntos cargados, lo
    # que necesita saber es cuanto agrega ESTE cambio, no cuanto agregan todos.
    ac = _perfil_de(ruta_actual)
    # La hora del vuelo la pone el PILOTO en el formulario. Calcular el desvio
    # y el veredicto de la escala para "dentro de una hora" daria numeros de un
    # momento que no es el suyo.
    dep_ts = _salida_del_formulario(ruta_actual)

    # El ruteo tiene que correr con las MISMAS reglas que va a usar la pantalla
    # cuando el piloto aplique el cambio. Con el switch de espacios aereos
    # prendido la ruta puede alargarse muchisimo —el rodeo solo puede apoyarse
    # en aerodromos—, asi que ignorarlo aca daria un costo que despues no
    # coincide con el que se muestra en la ficha de ruta.
    evitar_zonas = bool(ruta_actual.get("avoid_airspace"))

    def _ruta(puntos):
        return optimize(
            origin=origen, dest=destino, mode="suggested",
            aircraft=ac, suggest_alternate=False, evaluate_intermediate=False,
            avoid_restricted_zones=evitar_zonas,
            mock=False, dep_time=dep_ts,
            via=[ViaPoint(p["code"], p["is_stop"]) for p in puntos] or None,
        )

    try:
        antes   = _ruta(via)
        despues = _ruta(via_nueva)
    except Exception as e:                                   # pragma: no cover
        logger.warning(f"Copiloto: fallo el calculo de ruta: {e}")
        return {
            "ok": False,
            "motivo": "error_interno",
            "mensaje": "No se pudo calcular la ruta con ese cambio. Decile al "
                       "piloto que no se pudo; no estimes el desvio vos.",
        }

    if not despues.found:
        return {
            "ok": False,
            "motivo": "sin_ruta",
            "aerodromo": _ficha_breve(ap),
            "mensaje": f"No se pudo armar una ruta que pase por {ap.code}: "
                       f"{despues.error or 'sin detalle'}.",
        }

    costo = detour_cost(antes, despues)

    # ── Si es escala, hay que poder aterrizar ahi ─────────────────────────────
    # Se evalua a la hora en que se LLEGA, derivada de los tramos reales de la
    # ruta propuesta. Es el mismo criterio que usa la pantalla.
    escala = None
    if accion == TIPO_ESCALA:
        etas = eta_por_aerodromo(despues.legs, dep_ts)
        cuando = etas.get(ap.code, dep_ts)
        escala = _evaluar_para_propuesta(ap, cuando, ruta_actual, engine_factory)

    return {
        "ok": True,
        "propuesta": {
            "accion":      accion,
            "aerodromo":   _ficha_breve(ap),
            "origen":      origen,
            "destino":     destino,
            # Lo que la pantalla tiene que dejar cargado si el piloto acepta.
            # Se entrega la lista COMPLETA y no el delta: aplicar pasa a ser una
            # sola asignacion, sin que el frontend tenga que replicar la logica.
            "via_resultante": via_nueva,
            "via_anterior":   via,
        },
        "costo": {
            "dist_km_extra":      costo.dist_km_extra,
            "time_min_extra":     costo.time_min_extra,
            "fuel_l_extra":       costo.fuel_l_extra,
            "fuel_ok_despues":    costo.fuel_ok_despues,
            "rompe_la_autonomia": costo.rompe_la_autonomia,
            "ruta_despues":       list(despues.path),
        },
        "escala": escala,
        "mensaje": "Es una PROPUESTA: no se aplico nada. Contale al piloto que "
                   "cambia y cuanto cuesta, y decile que la confirme con el "
                   "boton. No afirmes que la ruta ya cambio.",
    }


def _perfil_de(ruta_actual: Dict[str, Any]):
    """Perfil de aeronave del vuelo cargado; el de referencia si no figura."""
    nombre = str(ruta_actual.get("aircraft") or "")
    for n in PROFILE_NAMES:
        if _norm(nombre) == _norm(n):
            return get_profile(n)
    return None


def _evaluar_para_propuesta(ap, cuando, ruta_actual, engine_factory):
    """
    Veredicto del aerodromo de la escala, a la hora en que se aterriza.

    Devuelve `None` si no se pudo evaluar: una escala sin veredicto se informa
    como tal, nunca como una escala buena.
    """
    if engine_factory is None:
        from decision.engine import DecisionEngine
        engine_factory = DecisionEngine

    kwargs: Dict[str, Any] = {}
    perfil = _perfil_de(ruta_actual)
    if perfil is not None:
        kwargs["aircraft"] = perfil

    try:
        resultado = engine_factory(**kwargs).evaluate(
            station_id=ap.code, runway_heading=None,
            departure_time=cuando, flight_duration_h=1.0,
        )
    except Exception as e:                                   # pragma: no cover
        logger.warning(f"Copiloto: fallo la evaluacion de la escala {ap.code}: {e}")
        return None

    if not resultado.fetch_ok:
        return {
            "ok": False,
            "aerodromo": ap.code,
            "mensaje": "No hay datos meteorologicos para esa escala a esa hora. "
                       "Decilo; no supongas que se puede aterrizar.",
        }

    # La forma es la MISMA que la de `evaluar_meteo` a proposito: asi el agente
    # puede meter este veredicto en la barrera R2 sin casos especiales, y un
    # veredicto de escala queda tan protegido como cualquier otro.
    return {
        "ok": True,
        "aerodromo": _ficha_breve(ap),
        "momento_evaluado": _etiqueta_momento(cuando),
        "veredicto": resultado.decision,
        "r_total": round(resultado.r_total, 3),
        "bloqueo_normativo": {
            "activo": resultado.hard_blocked,
            "detalle": resultado.blocker_summary or None,
        },
        "fuente": resultado.weather_source,
        "fuente_explicada": _fuente_explicada(resultado.weather_source),
        "procedencia": getattr(resultado, "conditions_source", ""),
        "instruccion": (
            "Transcribi el veredicto EXACTAMENTE como figura en 'veredicto'. "
            "Es el veredicto DE LA ESCALA a la hora en que se aterriza ahi, no "
            "el del vuelo entero."
        ),
    }


REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "buscar_aerodromo": buscar_aerodromo,
    "contacto_aerodromo": contacto_aerodromo,
    "servicios_aerodromo": servicios_aerodromo,
    "combustible_cercano": combustible_cercano,
    "evaluar_meteo": evaluar_meteo,
    "atmosfera_en_punto": atmosfera_en_punto,
    "mejor_hora_para_salir": mejor_hora_para_salir,
    "proponer_cambio_de_ruta": proponer_cambio_de_ruta,
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
                "combustible, no cuando pregunta por un aerodromo puntual. "
                "OJO, NO CONFUNDIR: esta solo INFORMA donde hay combustible. Si "
                "el piloto quiere BAJAR A CARGAR en un lugar concreto —'bajar a "
                "cargar nafta en X', 'parar a repostar en X'— eso es aterrizar "
                "en el camino, o sea un cambio de ruta: va por "
                "proponer_cambio_de_ruta con tipo='escala'."
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
                "Veredicto GO / CAUTION / NO GO para DESPEGAR O ATERRIZAR en un "
                "aerodromo. Mide condiciones de SUPERFICIE contra una pista: "
                "viento cruzado, visibilidad y techo. Usala cuando la pregunta "
                "es si se puede volar DESDE o HACIA un aerodromo, o si esta "
                "apto para operar. NO la uses si la pregunta es por el aire EN "
                "ALTURA sobre un punto de paso: para eso esta atmosfera_en_punto."
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
        {
            "name": "atmosfera_en_punto",
            "description": (
                "Estado del AIRE sobre un lugar, a altitud de crucero. "
                "INFORMA, no toca la ruta. Es para responder COMO ESTA el aire "
                "ahi arriba: 'como esta el aire sobre X', 'si paso por X que me "
                "encuentro', 'que viento hay sobre X a 8000 pies'. Señales de "
                "que va esta y no evaluar_meteo: 'por arriba de', 'en altura', "
                "'en ruta', 'a X pies', 'sobre'. "
                "OJO, NO CONFUNDIR: si el piloto quiere AGREGAR ese punto al "
                "vuelo —'quiero pasar por X', 'podriamos pasar por arriba de "
                "X?', 'meteme X'— eso es un cambio de ruta y va por "
                "proponer_cambio_de_ruta. La diferencia es si pregunta por las "
                "CONDICIONES o pide MODIFICAR por donde vuela. "
                "Devuelve viento, temperatura, visibilidad y nubes en el nivel, "
                "SIN veredicto GO/CAUTION/NO GO, porque el veredicto es de "
                "aerodromo y esto no lo es. Si no sabes la altitud, llamala "
                "igual sin ella: te va a pedir que se la preguntes al piloto."
            ),
            "parameters": {"type": "object", "properties": {
                "lugar": {"type": "string",
                          "description": "Ciudad, aerodromo o punto por el que se "
                                         "quiere pasar."},
                "altura_ft": {"type": "integer",
                              "description": "Altitud en pies MSL. Omitila si el "
                                             "piloto no la dijo."},
                "cuando": {"type": "string",
                           "description": "Hora local argentina, 'YYYY-MM-DD HH:MM' "
                                          "o 'HH:MM'."},
                "aeronave": {"type": "string",
                             "description": f"Una de: {', '.join(PROFILE_NAMES)}."},
                "regimen": {"type": "string",
                            "description": "VFR o IFR. En IFR la altitud sale de la "
                                           "MEA de la aerovia si no se especifica."},
                "provincia": _P_PROV,
            }, "required": ["lugar"]},
        },
        {
            "name": "mejor_hora_para_salir",
            "description": (
                "Serie horaria del veredicto de un aerodromo para las proximas "
                "horas. Usala cuando el piloto pregunta A QUE HORA conviene "
                "salir, cuando mejora o cuando empeora. Devuelve la misma escala "
                "GO/CAUTION/NO GO pero hora por hora."
            ),
            "parameters": {"type": "object", "properties": {
                "query": _P_QUERY,
                "horas": {"type": "integer",
                          "description": "Cuantas horas mirar hacia adelante "
                                         "(3 a 24, por defecto 12)."},
                "aeronave": {"type": "string",
                             "description": f"Una de: {', '.join(PROFILE_NAMES)}."},
                "provincia": _P_PROV,
            }, "required": ["query"]},
        },
        {
            "name": "proponer_cambio_de_ruta",
            "description": (
                "CAMBIA LA RUTA: agrega, convierte o quita un punto de paso "
                "del vuelo que el piloto tiene cargado. Es la herramienta "
                "cuando el piloto quiere MODIFICAR POR DONDE VA, no cuando "
                "pregunta como esta algo. NO aplica el cambio: devuelve una "
                "propuesta con su costo y el piloto la confirma con un boton. "
                "Nunca le digas que la ruta ya cambio. "
                "SOBREVUELO y ESCALA son dos vuelos distintos aunque dibujen la "
                "misma linea: en el sobrevuelo se pasa por encima y el vuelo es "
                "continuo; en la escala se ATERRIZA, asi que el aerodromo tiene "
                "que ser operable, su veredicto pesa sobre el vuelo y el "
                "combustible se calcula por etapa. "
                "COMO SACAR EL TIPO DE LO QUE DIJO EL PILOTO — mandalo siempre "
                "que se pueda, no lo dejes vacio por las dudas: "
                "'pasar por X', 'pasar por arriba de X', 'sobrevolar X', 'ir "
                "por X', 'de paso por X' -> tipo='sobrevuelo'. "
                "'escala en X', 'bajar en X', 'aterrizar en X', 'parar en X', "
                "'bajar a cargar en X', 'hacer noche en X' -> tipo='escala'. "
                "'sacar X', 'quitar X', 'sin pasar por X', 'volver a la ruta "
                "directa' -> tipo='quitar'. "
                "Dejalo vacio SOLO si de verdad no se puede saber (por ejemplo "
                "'meteme X en la ruta'): ahi la herramienta te devuelve la "
                "repregunta y se la haces al piloto. No elijas vos."
            ),
            "parameters": {"type": "object", "properties": {
                "lugar": {"type": "string",
                          "description": "Aerodromo o ciudad del punto de paso, "
                                         "como lo nombro el piloto."},
                "tipo": {"type": "string",
                         "enum": ["sobrevuelo", "escala", "quitar"],
                         "description": "'sobrevuelo' = pasar por encima. "
                                        "'escala' = aterrizar ahi. "
                                        "'quitar' = sacar un punto ya cargado. "
                                        "Dejalo vacio solo si el piloto no lo "
                                        "aclaro y queres que te repregunten."},
            }, "required": ["lugar"]},
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
