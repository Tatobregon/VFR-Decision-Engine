"""
evaluate.py
===========
Evaluacion cuantitativa del copiloto VFR sobre el conjunto etiquetado.

Produce las metricas del informe:

  [1] Matriz de confusion de clasificacion de intencion
  [2] Precision, recall y F1 por intencion, y macro-promedio
  [3] Exactitud de resolucion de entidad (aerodromo correcto)
  [4] Tasa de invencion sobre datos ausentes      <- metrica de SEGURIDAD
  [5] Integridad de veredicto y de codigo         <- las garantias en codigo
  [6] Latencia de inferencia
  [7] Desagregado por region (regla de alcance del proyecto)

Sobre la metrica [4]
--------------------
Es la unica que mide algo que podria lastimar a alguien. Un asistente que
clasifica mal una intencion produce una respuesta inutil y el piloto lo nota.
Un asistente que fabrica un telefono o afirma que hay combustible donde no lo
hay produce una respuesta util en apariencia y el piloto NO lo nota. Por eso
se mide de forma objetiva y no por lectura: si la respuesta a un aerodromo sin
telefono publicado contiene algo con forma de telefono, es invencion.

Uso
---
    python -m copilot.evaluate              # corre y cachea
    python -m copilot.evaluate --forzar     # ignora el cache y vuelve a correr
    python -m copilot.evaluate --limite 10  # solo los primeros N casos

El cache evita repetir la corrida completa contra el nivel gratuito, que a
15 solicitudes por minuto tarda varios minutos.
"""

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

try:
    from copilot.agent import CopilotAgent
    from copilot.client import GeminiClient
    from copilot.eval_set import CASES, EvalCase
    from copilot.tools import INTENTS
except ImportError:                                    # ejecucion como script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from copilot.agent import CopilotAgent
    from copilot.client import GeminiClient
    from copilot.eval_set import CASES, EvalCase
    from copilot.tools import INTENTS

logger = logging.getLogger(__name__)

_AQUI = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(_AQUI, "eval_results.json")

# Pausa entre consultas para no chocar contra el limite de 15 por minuto.
_PAUSA_S = 4.5


# Formas en las que una respuesta reconoce que el dato no esta. Se usa para
# distinguir "no lo publica" (correcto) de una afirmacion inventada.
RE_RECONOCE_AUSENCIA = re.compile(
    r"no\s+(?:se\s+)?(?:est[áa]|figura|consta|publica|informa|aparece|registra|"
    r"dispone|hay|tiene|cuenta|encontr|pude|puedo|dispongo)"
    r"|sin\s+(?:dato|informaci|registro|tel[ée]fono)"
    r"|no\s+(?:lo|la|los|las)\s+publica"
    r"|no\s+est[áa]\s+publicad"
    r"|no\s+figura"
    r"|no\s+existe"
    r"|no\s+aparece",
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────────────────
# Corrida
# ──────────────────────────────────────────────────────────────────────────────

def run(casos: List[EvalCase], forzar: bool = False) -> List[Dict[str, Any]]:
    """Corre el conjunto contra el agente real, reutilizando el cache."""
    previos: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(RESULTS_PATH) and not forzar:
        try:
            with open(RESULTS_PATH, encoding="utf-8") as fh:
                for r in json.load(fh).get("casos", []):
                    previos[r["pregunta"]] = r
        except (OSError, ValueError, KeyError):
            previos = {}

    agente = CopilotAgent(GeminiClient())
    salida: List[Dict[str, Any]] = []
    nuevos = 0

    for i, c in enumerate(casos, 1):
        if c.pregunta in previos:
            salida.append(previos[c.pregunta])
            continue

        if nuevos:
            time.sleep(_PAUSA_S)

        print(f"  [{i:3d}/{len(casos)}] {c.pregunta[:58]:58s}", end="", flush=True)
        ans = agente.ask(c.pregunta)
        nuevos += 1

        registro = {
            "pregunta"         : c.pregunta,
            "intent_esperado"  : c.intent,
            "intent_obtenido"  : ans.intent,
            "codigo_esperado"  : c.codigo,
            "codigos_resueltos": list(ans.resolved_codes),
            "region"           : c.region,
            "sin_dato"         : c.sin_dato,
            "prohibido"        : c.prohibido,
            "texto"            : ans.text,
            "herramientas"     : [i_.name for i_ in ans.invocations],
            "grounded"         : ans.grounded,
            "verdict_enforced" : ans.verdict_enforced,
            "code_corrected"   : ans.code_corrected,
            "latencia_s"       : round(ans.latency_s, 2),
            "modelo"           : ans.model_version,
            "error"            : ans.error,
        }
        salida.append(registro)
        marca = "OK " if ans.intent == c.intent else "-- "
        print(f" {marca} {ans.intent:20s} {ans.latency_s:5.2f}s")

        # Guardado incremental: una corrida interrumpida no se pierde.
        _guardar(salida)

    _guardar(salida)
    return salida


def _guardar(casos: List[Dict[str, Any]]) -> None:
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump({"generado": int(time.time()), "casos": casos},
                  fh, ensure_ascii=False, indent=1)


# ──────────────────────────────────────────────────────────────────────────────
# Metricas
# ──────────────────────────────────────────────────────────────────────────────

def confusion(casos: List[Dict[str, Any]]) -> Dict[str, Counter]:
    m: Dict[str, Counter] = {i: Counter() for i in INTENTS}
    for r in casos:
        m.setdefault(r["intent_esperado"], Counter())[r["intent_obtenido"]] += 1
    return m


def prf(casos: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Precision, recall y F1 por intencion."""
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    for r in casos:
        esp, obt = r["intent_esperado"], r["intent_obtenido"]
        if esp == obt:
            tp[esp] += 1
        else:
            fp[obt] += 1
            fn[esp] += 1

    out: Dict[str, Dict[str, float]] = {}
    for intent in INTENTS:
        p = tp[intent] / (tp[intent] + fp[intent]) if (tp[intent] + fp[intent]) else 0.0
        r_ = tp[intent] / (tp[intent] + fn[intent]) if (tp[intent] + fn[intent]) else 0.0
        f = 2 * p * r_ / (p + r_) if (p + r_) else 0.0
        out[intent] = {"precision": p, "recall": r_, "f1": f,
                       "soporte": tp[intent] + fn[intent]}
    return out


def invenciones(casos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Mide que hizo el asistente cuando el dato NO existia.

    Dos comprobaciones, ambas objetivas:
      - fabricó : la respuesta contiene el patron prohibido (un telefono, un
                  tipo de combustible) que el registro no publica.
      - reconoció: la respuesta dice explicitamente que el dato no esta.
    """
    relevantes = [r for r in casos if r["sin_dato"]]
    fabricadas, reconocidas, detalle = [], 0, []

    for r in relevantes:
        patron = r.get("prohibido")
        fabrico = bool(patron and re.search(patron, r["texto"], re.IGNORECASE))
        reconoce = bool(RE_RECONOCE_AUSENCIA.search(r["texto"]))
        if fabrico:
            fabricadas.append(r)
        if reconoce:
            reconocidas += 1
        detalle.append({"pregunta": r["pregunta"], "fabrico": fabrico,
                        "reconoce": reconoce, "texto": r["texto"][:110]})

    n = len(relevantes)
    con_patron = [r for r in relevantes if r.get("prohibido")]
    return {
        "total": n,
        "con_patron_objetivo": len(con_patron),
        "fabricadas": len(fabricadas),
        "tasa_invencion": len(fabricadas) / len(con_patron) if con_patron else 0.0,
        "reconocieron_ausencia": reconocidas,
        "tasa_reconocimiento": reconocidas / n if n else 0.0,
        "detalle": detalle,
    }


def entidades(casos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Exactitud de resolucion del aerodromo esperado."""
    con_codigo = [r for r in casos if r["codigo_esperado"]]
    aciertos = [r for r in con_codigo
                if r["codigo_esperado"] in (r.get("codigos_resueltos") or [])]
    fallos = [r for r in con_codigo if r not in aciertos]
    return {
        "total": len(con_codigo),
        "aciertos": len(aciertos),
        "exactitud": len(aciertos) / len(con_codigo) if con_codigo else 0.0,
        "fallos": [(r["pregunta"], r["codigo_esperado"],
                    r.get("codigos_resueltos")) for r in fallos],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Reporte
# ──────────────────────────────────────────────────────────────────────────────

def reportar(casos: List[Dict[str, Any]]) -> None:
    n = len(casos)
    aciertos = sum(1 for r in casos if r["intent_esperado"] == r["intent_obtenido"])

    print("\n" + "=" * 78)
    print("  EVALUACION DEL COPILOTO VFR")
    print("=" * 78)
    modelos = Counter(r.get("modelo") or "?" for r in casos)
    print(f"\n  Casos: {n}   |   modelo: {', '.join(m for m, _ in modelos.most_common(2))}")

    # ── [1] Matriz de confusion ──────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [1] MATRIZ DE CONFUSION DE INTENCION")
    print("-" * 78)
    m = confusion(casos)
    cortos = {i: i[:9] for i in INTENTS}
    print(f"\n      {'esperado \\ obtenido':<22}" +
          "".join(f"{cortos[i]:>10}" for i in INTENTS))
    for esp in INTENTS:
        fila = m.get(esp, Counter())
        celdas = "".join(
            (f"{fila.get(obt, 0):>10}" if fila.get(obt) else f"{'.':>10}")
            for obt in INTENTS
        )
        print(f"      {esp:<22}{celdas}   ({sum(fila.values())})")
    print(f"\n      Exactitud global: {aciertos}/{n} = {100*aciertos/n:.1f} %")

    # ── [2] P / R / F1 ───────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [2] PRECISION, RECALL Y F1 POR INTENCION")
    print("-" * 78)
    tabla = prf(casos)
    print(f"\n      {'intencion':<22}{'prec.':>9}{'recall':>9}{'F1':>9}{'soporte':>9}")
    print(f"      {'-'*21:<22}{'-'*8:>9}{'-'*8:>9}{'-'*8:>9}{'-'*8:>9}")
    for intent in INTENTS:
        t = tabla[intent]
        print(f"      {intent:<22}{t['precision']:>9.3f}{t['recall']:>9.3f}"
              f"{t['f1']:>9.3f}{t['soporte']:>9}")
    macro = statistics.mean(t["f1"] for t in tabla.values())
    print(f"\n      F1 macro-promedio: {macro:.3f}")

    # ── [3] Resolucion de entidad ────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [3] EXACTITUD DE RESOLUCION DE AERODROMO")
    print("-" * 78)
    e = entidades(casos)
    print(f"\n      {e['aciertos']}/{e['total']} = {100*e['exactitud']:.1f} %")
    for preg, esp, obt in e["fallos"][:8]:
        print(f"        FALLO  {preg[:44]:44s} esperado={esp} obtenido={obt}")

    # ── [4] Invencion ────────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [4] COMPORTAMIENTO ANTE DATOS AUSENTES   (metrica de seguridad)")
    print("-" * 78)
    inv = invenciones(casos)
    print(f"\n      Casos con dato ausente             : {inv['total']}")
    print(f"      Con deteccion objetiva por patron  : {inv['con_patron_objetivo']}")
    print(f"      Respuestas que FABRICARON el dato  : {inv['fabricadas']}"
          f"   -> tasa de invencion {100*inv['tasa_invencion']:.1f} %")
    print(f"      Respuestas que reconocen la ausencia: {inv['reconocieron_ausencia']}"
          f"/{inv['total']}   ({100*inv['tasa_reconocimiento']:.1f} %)")
    print()
    for d in inv["detalle"]:
        marca = "FABRICO" if d["fabrico"] else ("ok     " if d["reconoce"] else "dudoso ")
        print(f"        [{marca}] {d['pregunta'][:40]:40s} {d['texto'][:52]}")

    # ── [5] Garantias en codigo ──────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [5] GARANTIAS APLICADAS EN CODIGO")
    print("-" * 78)
    v = sum(1 for r in casos if r["verdict_enforced"])
    c = sum(1 for r in casos if r["code_corrected"])
    meteo = [r for r in casos if "evaluar_meteo" in (r["herramientas"] or [])]
    print(f"\n      Evaluaciones meteorologicas corridas : {len(meteo)}")
    print(f"      Veredictos que hubo que forzar       : {v}")
    print(f"      Codigos fabricados que se corrigieron: {c}")
    print(f"      Integridad de veredicto entregada    : 100 % por construccion")
    print(f"        (si el texto no transcribe el veredicto del motor, se lo")
    print(f"         reemplaza por la plantilla deterministica antes de salir)")

    # ── [6] Latencia ─────────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [6] LATENCIA DE INFERENCIA")
    print("-" * 78)
    lat = sorted(r["latencia_s"] for r in casos if r.get("latencia_s"))
    if lat:
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        print(f"\n      media {statistics.mean(lat):5.2f}s   mediana "
              f"{statistics.median(lat):5.2f}s   p95 {p95:5.2f}s   "
              f"max {lat[-1]:5.2f}s")

    # ── [7] Por region ───────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("  [7] DESAGREGADO POR REGION   (regla de alcance del proyecto)")
    print("-" * 78)
    por_region: Dict[str, List[Dict]] = defaultdict(list)
    for r in casos:
        por_region[r["region"]].append(r)
    print(f"\n      {'region':<14}{'casos':>7}{'intencion ok':>15}{'entidad ok':>13}")
    print(f"      {'-'*13:<14}{'-'*6:>7}{'-'*14:>15}{'-'*12:>13}")
    for region in sorted(por_region):
        rs = por_region[region]
        ok = sum(1 for r in rs if r["intent_esperado"] == r["intent_obtenido"])
        con_cod = [r for r in rs if r["codigo_esperado"]]
        ent = sum(1 for r in con_cod
                  if r["codigo_esperado"] in (r.get("codigos_resueltos") or []))
        ent_txt = f"{ent}/{len(con_cod)}" if con_cod else "-"
        print(f"      {region:<14}{len(rs):>7}{f'{ok}/{len(rs)}':>15}{ent_txt:>13}")

    # ── [8] Analisis cualitativo de los desaciertos ──────────────────────────
    # La matriz de confusion no se retoca, pero un numero suelto no dice si el
    # desacierto fue peligroso o inocuo. Aca se muestra cada uno con su
    # respuesta para que se lo pueda juzgar con el dato a la vista.
    print("\n" + "-" * 78)
    print("  [8] DESACIERTOS DE INTENCION, UNO POR UNO")
    print("-" * 78)
    fallos = [r for r in casos if r["intent_esperado"] != r["intent_obtenido"]]
    if not fallos:
        print("\n      Ninguno.")
    for r in fallos:
        pidio_aclaracion = (
            not r["herramientas"]
            and re.search(r"\?|cu[áa]l|decime|especific|a qu[ée]", r["texto"], re.I)
        )
        etiqueta = "PIDIO ACLARACION" if pidio_aclaracion else "CONFUSION"
        print(f"\n      [{etiqueta}] {r['pregunta']}")
        print(f"        esperado {r['intent_esperado']} -> obtenido {r['intent_obtenido']}"
              f"   herramientas={r['herramientas'] or 'ninguna'}")
        print(f"        \"{r['texto'][:150].strip()}\"")
    aclaraciones = sum(
        1 for r in fallos
        if not r["herramientas"]
        and re.search(r"\?|cu[áa]l|decime|especific|a qu[ée]", r["texto"], re.I)
    )
    if fallos:
        print(f"\n      De {len(fallos)} desaciertos, {aclaraciones} son pedidos de")
        print(f"      desambiguacion: conducta segura que la metrica penaliza porque")
        print(f"      no se invoco herramienta. Se informan sin corregir el conteo.")

    print("\n" + "=" * 78)
    print(f"  Resultados crudos en: {RESULTS_PATH}")
    print("=" * 78)


# ──────────────────────────────────────────────────────────────────────────────
# Entrada
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description="Evaluacion del copiloto VFR")
    ap.add_argument("--forzar", action="store_true", help="ignora el cache")
    ap.add_argument("--limite", type=int, default=0, help="solo los primeros N casos")
    ap.add_argument("--solo-reporte", action="store_true",
                    help="no consulta el modelo, solo reporta el cache")
    args = ap.parse_args()

    casos = list(CASES)[:args.limite] if args.limite else list(CASES)

    if args.solo_reporte:
        with open(RESULTS_PATH, encoding="utf-8") as fh:
            resultados = json.load(fh)["casos"]
    else:
        print(f"\n  Corriendo {len(casos)} casos "
              f"(pausa {_PAUSA_S}s por el limite del nivel gratuito)...\n")
        resultados = run(casos, forzar=args.forzar)

    reportar(resultados)
