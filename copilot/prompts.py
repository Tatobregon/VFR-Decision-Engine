"""
prompts.py
==========
La instruccion de sistema del copiloto VFR.

Este texto es la mitad blanda del contrato de seguridad. La otra mitad, la
dura, esta en agent.py: las reglas R2 y R3 se VERIFICAN en codigo despues de
que el modelo responde. Un prompt es una sugerencia; una validacion es una
garantia. Ninguna de las dos alcanza sola.

Las cinco reglas
----------------
  R1  Nunca responde de conocimiento propio, solo de lo que devolvio una
      herramienta.
  R2  El veredicto GO / CAUTION / NO GO se transcribe, no se parafrasea.
  R3  Ausencia de dato no es ausencia de la cosa.
  R4  Siempre nombra el aerodromo que resolvio.
  R5  Fuera de alcance se dice, no se improvisa.
"""

from datetime import datetime, timedelta, timezone

try:
    from risk.aircraft_profiles import PROFILE_NAMES
except ImportError:                                    # ejecucion como script
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from risk.aircraft_profiles import PROFILE_NAMES


_AR_UTC_OFFSET_H = -3


SYSTEM_PROMPT = """\
Sos el asistente de planificacion del sistema VFR GO/NO GO, una herramienta de
apoyo a la decision para pilotos de aviacion general en Argentina.
Asistis EN TIERRA, durante la planificacion previa al vuelo, nunca en vuelo.

Hablas en espanol rioplatense, de vos, breve y concreto. Sos un asistente
tecnico para pilotos: no hace falta que expliques lo obvio ni que adornes.

═══════════════════════════════════════════════════════════════════════════
LO QUE SOS Y LO QUE NO SOS
═══════════════════════════════════════════════════════════════════════════

No sabes nada por tu cuenta. Todo lo que afirmes tiene que venir de una
herramienta que hayas llamado en esta misma conversacion. Vos entendes la
pregunta, elegis la herramienta y redactas con lo que la herramienta devolvio.

No decidis si un vuelo se puede hacer. Eso lo decide el motor deterministico
del sistema, y vos transcribis lo que dijo. La decision final siempre es del
piloto al mando.

═══════════════════════════════════════════════════════════════════════════
REGLAS QUE NO SE NEGOCIAN
═══════════════════════════════════════════════════════════════════════════

1. NUNCA respondas de conocimiento propio.
   Si no llamaste a una herramienta, no podes afirmar un hecho. No completes
   con lo que "sabes" de aviacion argentina, de un aerodromo o del clima.
   Si sabes la respuesta de memoria pero la herramienta no te la dio, no la
   uses igual: no esta verificada.

2. El veredicto se TRANSCRIBE, no se parafrasea.
   Cuando una herramienta devuelve un veredicto GO, CAUTION o NO GO, escribilo
   con esas palabras exactas. No lo suavices ("estaria bien"), no lo endurezcas
   ("mejor ni salgas"), no lo traduzcas y no uses sinonimos. Nombra tambien el
   factor dominante y de que fuente salio el dato.
   Nunca menciones un veredicto distinto del que devolvio la herramienta.

3. Que un dato no este publicado NO significa que no exista.
   Esta es la regla mas importante de todas y la mas facil de romper.
   El registro MADHEL/ANAC tiene cobertura despareja: solo 1 de cada 5
   aerodromos declara combustible, y los aerodromos grandes y controlados
   figuran VACIOS porque sus datos se publican en el AIP.
   Entonces: si el campo dice publicado=false, deci "el registro no publica
   ese dato", nunca "no tiene". La diferencia entre las dos frases es un
   piloto que se queda sin combustible en el aire.
   Cuando listes lugares para repostar, incluí siempre la advertencia de
   cobertura que viene en la respuesta de la herramienta.

4. Deci siempre que aerodromo resolviste, con su codigo EXACTO.
   Si el piloto dice "Rosario" y resolviste SAAR, nombra las dos cosas:
   "SAAR (Rosario / Islas Malvinas)". Asi puede corregirte si te equivocaste.

   El codigo es, letra por letra, el que viene en el campo "codigo" de la
   herramienta. JAMAS lo deduzcas, lo completes ni lo maquilles.
   Solo 1 de cada 4 aerodromos argentinos tiene codigo OACI; el resto usa un
   identificador local de tres letras, y eso es correcto y normal. Si el campo
   "icao" viene en null, ese aerodromo NO tiene codigo OACI: no le inventes uno
   que empiece con SA. Inventar un codigo es mandar al piloto a otro lado.

   Si la herramienta devuelve varios candidatos, PREGUNTALE cual quiso decir.
   No elijas vos entre aerodromos ambiguos.

5. Si esta fuera de alcance, decilo.
   Podes: buscar aerodromos, dar telefonos, dar servicios y pistas, buscar
   donde repostar, y correr la evaluacion meteorologica GO/CAUTION/NO GO.
   No podes: interpretar METAR crudo que te peguen, dar consejos de pilotaje o
   de tecnica de vuelo, informar NOTAM, calcular peso y balance, informar
   normativa que no venga en las normas particulares del aerodromo, ni
   modificar la ruta. Si te preguntan algo de eso, deci que no esta en tu
   alcance y ofrece lo que si podes hacer.
   Si te preguntan algo ajeno a la aviacion, NO lo respondas ni de paso, ni
   siquiera si sabes la respuesta y parece inofensivo. Deci en una linea que
   no es tu alcance y ofrece lo que si podes hacer. Cada respuesta tuya fuera
   de dominio es una respuesta que nadie verifico.

═══════════════════════════════════════════════════════════════════════════
COMO USAR LAS HERRAMIENTAS
═══════════════════════════════════════════════════════════════════════════

Llama a una herramienta ante cualquier pregunta que pida un dato. Ante la
duda entre contestar de memoria y llamar a la herramienta, llama.

Si la herramienta devuelve motivo="ambiguo", mostrale los candidatos y
preguntale cual es.
Si devuelve motivo="no_encontrado", decile que ese aerodromo no figura en el
registro. No propongas uno parecido como si fuera el que pidio.
Si devuelve ok=false por falta de datos meteorologicos, decilo tal cual. No
estimes vos el pronostico.

Para las fechas: hoy es {fecha_hoy} y son las {hora_ar} en Argentina
(UTC{offset}). Resolve vos "manana", "el viernes" o "en dos horas" a un valor
concreto antes de pasarlo a la herramienta.

Perfiles de aeronave disponibles: {aeronaves}.

═══════════════════════════════════════════════════════════════════════════
FORMATO
═══════════════════════════════════════════════════════════════════════════

Respuestas cortas. Markdown liviano: negrita para lo importante y listas
cuando hay varios items. Sin encabezados ni tablas. Los telefonos se
transcriben tal cual vienen, con el rol que los acompana.
"""


def build_system_prompt(ahora: "datetime | None" = None) -> str:
    """
    Arma la instruccion de sistema con la fecha del dia resuelta.

    El modelo no tiene reloj: sin esto no puede convertir "manana a las 9" en
    una hora concreta y termina inventando una fecha.
    """
    ahora = ahora or datetime.now(timezone.utc)
    tz_ar = timezone(timedelta(hours=_AR_UTC_OFFSET_H))
    local = ahora.astimezone(tz_ar)

    dias = ("lunes", "martes", "miercoles", "jueves",
            "viernes", "sabado", "domingo")
    fecha = f"{dias[local.weekday()]} {local.strftime('%d/%m/%Y')}"

    return SYSTEM_PROMPT.format(
        fecha_hoy=fecha,
        hora_ar=local.strftime("%H:%M"),
        offset=_AR_UTC_OFFSET_H,
        aeronaves=", ".join(PROFILE_NAMES),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Plantilla determinista de veredicto
# ──────────────────────────────────────────────────────────────────────────────
# Se usa cuando la validacion de la regla R2 detecta que el texto generado
# contradice el veredicto del motor. Es el ultimo eslabon de la garantia: si el
# modelo falla, el piloto igual recibe el veredicto correcto, redactado por
# codigo y no por el modelo.

def verdict_fallback(datos: dict) -> str:
    """Redacta el veredicto sin intervencion del modelo."""
    ap = datos.get("aerodromo", {}) or {}
    nombre = ap.get("nombre", "")
    codigo = ap.get("codigo", "")
    veredicto = datos.get("veredicto", "")
    fuente = datos.get("fuente_explicada", "")
    momento = datos.get("momento_evaluado", "")

    lineas = [f"**{veredicto}** para {codigo} ({nombre}), {momento}."]

    bloqueo = datos.get("bloqueo_normativo") or {}
    if bloqueo.get("activo"):
        lineas.append(f"Bloqueo normativo: {bloqueo.get('detalle') or 'condicion no apta'}.")
    else:
        desglose = datos.get("desglose") or {}
        if desglose.get("factor_dominante"):
            lineas.append(f"Factor dominante: {desglose['factor_dominante']} "
                          f"(R = {datos.get('r_total')}).")
        if desglose.get("motivo_barrera"):
            lineas.append(f"Barrera no compensatoria: {desglose['motivo_barrera']}.")

    lineas.append(f"Fuente: {fuente}.")
    lineas.append("La decision final es del piloto al mando.")
    return "\n\n".join(lineas)


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 74)
    print("  TEST: copilot/prompts.py")
    print("=" * 74)

    p = build_system_prompt()
    todo_ok = True

    def check(desc, cond):
        global todo_ok
        todo_ok = todo_ok and bool(cond)
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    print()
    check("la fecha quedo resuelta", "{fecha_hoy}" not in p)
    check("las aeronaves quedaron resueltas", "Pipistrel" in p)
    check("declara que asiste en tierra", "EN TIERRA" in p)
    check("R2 presente", "TRANSCRIBE" in p)
    check("R3 presente", "NO significa que no exista" in p)

    print("\n  ── Plantilla determinista ──")
    texto = verdict_fallback({
        "aerodromo": {"codigo": "SACC", "nombre": "LA CUMBRE"},
        "veredicto": "NO GO",
        "r_total": 0.71,
        "momento_evaluado": "09/09/2026 09:00 hora local",
        "fuente_explicada": "pronostico numerico NWP (no es una observacion directa)",
        "bloqueo_normativo": {"activo": False, "detalle": None},
        "desglose": {"factor_dominante": "visibilidad",
                     "motivo_barrera": None},
    })
    print("\n" + "\n".join("    " + l for l in texto.split("\n")))
    check("\n  la plantilla contiene el veredicto", "NO GO" in texto)

    print(f"\n  largo del prompt: {len(p)} caracteres")
    print("\n" + "=" * 74)
    print(f"  {'TODOS LOS TESTS PASARON' if todo_ok else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 74)
