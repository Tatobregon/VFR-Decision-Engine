# -*- coding: utf-8 -*-
"""
generar_figuras_cap5.py
=======================
Genera las figuras del § 5.1 a partir de los modulos y resultados reales del
sistema, sin transcribir ningun numero a mano, y guarda los datos de cada
figura en un JSON (la version en tabla de cada grafico).

Requiere matplotlib, que NO es una dependencia del sistema: se instala aparte,
solo para regenerar las figuras.

Uso (desde la raiz del repositorio):
    python Teorico/figuras/generar_figuras_cap5.py

Salida: Teorico/figuras/cap5/*.png (300 dpi, 16 cm de ancho) y datos_cap5.json
"""

import json
import os
import socket
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)


def _sin_red(*a, **k):
    raise RuntimeError("las figuras se generan sin red")


socket.socket.connect = _sin_red

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from risk import calibration as C  # noqa: E402
from risk import sensitivity as S  # noqa: E402
from risk.personal_minima import LEVEL_NAMES, LEVELS  # noqa: E402
from risk.scenarios import _RANK, evaluate_battery, system_verdict  # noqa: E402
from risk.weights import THRESHOLD_CAUTION, THRESHOLD_GO  # noqa: E402

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cap5")
os.makedirs(SALIDA, exist_ok=True)

# ── Paleta de referencia (modo claro) y tinta ────────────────────────────────
SUPERFICIE = "#ffffff"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
TENUE = "#898781"
GRILLA = "#e1e0d9"
EJE = "#c3c2b7"
SERIE_1 = "#2a78d6"
SERIE_2 = "#eb6834"
BANDA = "#f0efec"
VACIO = "#f7f7f5"
SECUENCIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

# El limite superior de t_go que explora risk/calibration.py (bucle "while t <= 45").
T_GO_MAX_GRILLA = 0.45

ANCHO_IN = 16 / 2.54
DPI = 300

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 9,
    "axes.edgecolor": EJE,
    "axes.labelcolor": TINTA_2,
    "axes.linewidth": 0.8,
    "xtick.color": TENUE,
    "ytick.color": TINTA_2,
    "xtick.labelcolor": TINTA_2,
    "ytick.labelcolor": TINTA_2,
    "figure.facecolor": SUPERFICIE,
    "axes.facecolor": SUPERFICIE,
    "savefig.facecolor": SUPERFICIE,
})

datos = {}


def coma(x, decimales=2):
    return f"{x:.{decimales}f}".replace(".", ",")


def ejes_limpios(ax, grilla="x"):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(length=0)
    if grilla:
        ax.grid(axis=grilla, color=GRILLA, linewidth=0.8, linestyle="-")
        ax.set_axisbelow(True)


def tinta_sobre(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#ffffff" if lum < 0.5 else TINTA


def matriz(ax, m, filas, columnas, etiqueta_x, etiqueta_y):
    maximo = max(1, int(m.max()))
    cmap = LinearSegmentedColormap.from_list("azul", SECUENCIAL)
    enmascarada = np.ma.masked_where(m == 0, m)
    cmap.set_bad(VACIO)
    ax.imshow(enmascarada, cmap=cmap, vmin=0, vmax=maximo, aspect="equal")
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            if m[i, j]:
                color = cmap(m[i, j] / maximo)
                hex_color = "#{:02x}{:02x}{:02x}".format(*(int(c * 255) for c in color[:3]))
                ax.text(j, i, str(int(m[i, j])), ha="center", va="center",
                        fontsize=9, color=tinta_sobre(hex_color))
    ax.set_xticks(range(len(columnas)), columnas)
    ax.set_yticks(range(len(filas)), filas)
    ax.set_xticks(np.arange(-0.5, len(columnas), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(filas), 1), minor=True)
    ax.grid(which="minor", color=SUPERFICIE, linewidth=2)
    ax.tick_params(which="both", length=0)
    for lado in ax.spines.values():
        lado.set_visible(False)
    ax.set_xlabel(etiqueta_x)
    ax.set_ylabel(etiqueta_y)


def guardar(fig, nombre):
    fig.savefig(os.path.join(SALIDA, nombre), dpi=DPI, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print("  ", nombre)


print("Figuras del capitulo 5:")

# ══════════════════════════════════════════════════════════════════════════════
# 5.1 Matriz de confusion de la bateria
# ══════════════════════════════════════════════════════════════════════════════
filas_bat = C._precompute()
orden = ["GO", "CAUTION", "NO GO"]
conf = C._confusion(filas_bat, THRESHOLD_GO, THRESHOLD_CAUTION)
m_bat = np.array([[conf[(p, a)] for a in orden] for p in orden])
datos["bateria_matriz"] = {"filas_sistema": orden, "columnas_referencia": orden,
                           "valores": m_bat.tolist(),
                           "umbrales": [THRESHOLD_GO, THRESHOLD_CAUTION]}

fig, ax = plt.subplots(figsize=(ANCHO_IN * 0.55, ANCHO_IN * 0.5))
matriz(ax, m_bat, orden, orden, "Veredicto de referencia", "Veredicto del sistema")
guardar(fig, "fig_5_1_matriz_bateria.png")

# ══════════════════════════════════════════════════════════════════════════════
# 5.2 Curva de operacion del umbral inferior
# ══════════════════════════════════════════════════════════════════════════════
# Se recorre mas alla del limite de la grilla de calibracion (0.45) a proposito:
# muestra que exprimir la bateria vaciaria la franja de CAUTION del puntaje.
t_gos = [round(0.05 + i * 0.01, 2) for i in range(54)]            # 0.05 .. 0.58
curva = [C._score_thresholds(filas_bat, t, THRESHOLD_CAUTION) for t in t_gos]
mejor, candidatos = C.grid_search(filas_bat)
equivalentes = [c for c in candidatos if c["cost"] == mejor["cost"]]
rango = (min(c["t_go"] for c in equivalentes), max(c["t_go"] for c in equivalentes))
datos["curva_umbral"] = {"t_caution": THRESHOLD_CAUTION, "t_go": t_gos,
                         "sobre_avisos": [c["over"] for c in curva],
                         "sub_avisos": [c["dangerous"] for c in curva],
                         "concordancia": [c["agree"] for c in curva],
                         "t_go_adoptado": THRESHOLD_GO,
                         "rango_optimo_grilla": list(rango),
                         "t_go_max_grilla": T_GO_MAX_GRILLA}

fig, ax = plt.subplots(figsize=(ANCHO_IN, ANCHO_IN * 0.46))
ejes_limpios(ax, grilla="y")
ax.axvspan(rango[0], rango[1], color=BANDA, zorder=0, linewidth=0)
ax.axvline(THRESHOLD_GO, color=TENUE, linewidth=0.8, zorder=1)
ax.plot(t_gos, [c["over"] for c in curva], color=SERIE_1, linewidth=2,
        solid_joinstyle="round", solid_capstyle="round", label="Sobre-avisos", zorder=3)
ax.plot(t_gos, [c["dangerous"] for c in curva], color=SERIE_2, linewidth=2,
        solid_joinstyle="round", solid_capstyle="round", label="Sub-avisos", zorder=3)
tope = max(max(c["over"] for c in curva), max(c["dangerous"] for c in curva))
ax.set_ylim(-0.3, tope + 1.2)
ax.yaxis.set_major_locator(MaxNLocator(integer=True))
ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: coma(x)))
ax.set_xlim(t_gos[0], t_gos[-1])
ax.set_xlabel(f"Umbral inferior t_go (con t_caution = {coma(THRESHOLD_CAUTION)})")
ax.set_ylabel("Escenarios (de 38)")
ax.text(THRESHOLD_GO - 0.004, tope + 0.9, f"adoptado: {coma(THRESHOLD_GO)}",
        color=TINTA_2, fontsize=8, va="top", ha="right")
ax.text((rango[0] + rango[1]) / 2, tope + 0.9,
        f"óptimo de la calibración: {coma(rango[0])} a {coma(rango[1])}\n"
        f"(la grilla llega hasta {coma(T_GO_MAX_GRILLA)})",
        color=TINTA_2, fontsize=7.5, va="top", ha="center", linespacing=1.3)
ax.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 0.72), fontsize=8,
          labelcolor=TINTA_2)
guardar(fig, "fig_5_2_curva_umbral.png")

# ══════════════════════════════════════════════════════════════════════════════
# 5.3 Concordancia por nivel de experiencia
# ══════════════════════════════════════════════════════════════════════════════
niveles = []
for nombre in ["(sin minimos)"] + LEVEL_NAMES:
    pm = None if nombre.startswith("(") else LEVELS[nombre]
    ok = sub = over = 0
    for sc, comp, etiqueta in evaluate_battery(personal_minima=pm):
        v = system_verdict(sc, comp, comp.r_total, THRESHOLD_GO, THRESHOLD_CAUTION)
        if v == etiqueta:
            ok += 1
        elif _RANK[v] < _RANK[etiqueta]:
            sub += 1
        else:
            over += 1
    niveles.append({"nivel": nombre, "acuerdos": ok, "sobre_avisos": over, "sub_avisos": sub})
datos["concordancia_por_nivel"] = niveles

nombres_nivel = {"(sin minimos)": "Sin mínimos personales", "Avanzado": "Avanzado",
                 "PPL": "PPL", "Alumno": "Alumno"}
orden_nivel = ["(sin minimos)", "Avanzado", "PPL", "Alumno"]
filas_nivel = [next(x for x in niveles if x["nivel"] == k) for k in orden_nivel]

fig, ax = plt.subplots(figsize=(ANCHO_IN, ANCHO_IN * 0.36))
ejes_limpios(ax, grilla="x")
y = np.arange(len(filas_nivel))[::-1]
alto = 0.5
ac = [f["acuerdos"] for f in filas_nivel]
so = [f["sobre_avisos"] for f in filas_nivel]
ax.barh(y, ac, height=alto, color=SERIE_1, edgecolor=SUPERFICIE, linewidth=1.5,
        label="Coincide con la referencia")
ax.barh(y, so, left=ac, height=alto, color=SERIE_2, edgecolor=SUPERFICIE, linewidth=1.5,
        label="Sobre-aviso")
for yi, a, s in zip(y, ac, so):
    ax.text(a / 2, yi, str(a), ha="center", va="center", color="#ffffff", fontsize=8.5)
    ax.text(a + s + 0.4, yi, f"{s} sobre-avisos", ha="left", va="center", color=TINTA_2, fontsize=8)
ax.set_yticks(y, [nombres_nivel[f["nivel"]] for f in filas_nivel])
ax.set_xlim(0, 45)
ax.set_xticks(range(0, 39, 5))
sub_total = sum(f["sub_avisos"] for f in filas_nivel)
ax.set_xlabel(f"Escenarios de la batería (38)  ·  sub-avisos en todos los niveles: {sub_total}")
ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, fontsize=8,
          labelcolor=TINTA_2, borderaxespad=0.2)
guardar(fig, "fig_5_3_concordancia_por_nivel.png")

# ══════════════════════════════════════════════════════════════════════════════
# 5.4 Sensibilidad
# ══════════════════════════════════════════════════════════════════════════════
bateria = evaluate_battery()
n = len(bateria)
base_w = S.default_weights()
base_v = S._verdicts(bateria, base_w, THRESHOLD_GO, THRESHOLD_CAUTION)

oat = S.oat_analysis(bateria, base_w, base_v)
mc = S.monte_carlo(bateria, base_w, base_v)
umbrales = S.threshold_sensitivity(bateria, base_w, base_v)
forma = S.shape_analysis(bateria, base_w, base_v)
barrera = S.barrier_sensitivity(bateria, base_w, base_v)

nombres_forma = {
    "vis  8 -> 10 km  (margen 2.0x)": "Visibilidad, riesgo nulo 8 → 10 km",
    "vis  8 ->  6 km  (margen 1.2x)": "Visibilidad, riesgo nulo 8 → 6 km",
    "vis  3 -> 3.6 km (riesgo max +20%)": "Visibilidad, riesgo máximo 3 → 3,6 km",
    "vis  3 -> 2.4 km (riesgo max -20%)": "Visibilidad, riesgo máximo 3 → 2,4 km",
    "techo 2000 -> 3000 ft (margen 3x)": "Techo, riesgo nulo 2000 → 3000 ft",
    "techo 2000 -> 1500 ft (margen 1.5x)": "Techo, riesgo nulo 2000 → 1500 ft",
    "techo 500 -> 600 ft (riesgo max +20%)": "Techo, riesgo máximo 500 → 600 ft",
    "techo 500 -> 400 ft (riesgo max -20%)": "Techo, riesgo máximo 500 → 400 ft",
    "spread  5 ->  7 C": "Niebla, riesgo nulo 5 → 7 °C",
    "spread  5 ->  4 C": "Niebla, riesgo nulo 5 → 4 °C",
}
nombres_umbral = {"t_go -0.05": "Umbral inferior −0,05", "t_go +0.05": "Umbral inferior +0,05",
                  "t_caution -0.05": "Umbral superior −0,05",
                  "t_caution +0.05": "Umbral superior +0,05"}

barras = []
barras.append(("Pesos", "Un peso por vez, ±20 % (peor caso)",
               max(max(r["flips_down"], r["flips_up"]) for r in oat)))
barras.append(("Pesos", "Siete pesos a la vez, ±20 % (peor sorteo)",
               n - round(mc["worst_trial"] * n)))
for nombre, flips in umbrales:
    barras.append(("Umbrales", nombres_umbral.get(nombre, nombre), flips))
for fila in forma:
    barras.append(("Rampas", nombres_forma.get(fila["label"], fila["label"]), fila["flips"]))
for fila in barrera:
    barras.append(("Barrera", f"Piso de cruzado {coma(fila['base'])} → {coma(fila['valor'])}",
                   fila["flips"]))
datos["sensibilidad"] = {
    "escenarios": n,
    "barras": [{"grupo": g, "perturbacion": e, "cambios": v} for g, e, v in barras],
    "oat_cambios_totales": sum(r["flips_total"] for r in oat),
    "oat_evaluaciones": 14 * n,
    "monte_carlo": {"sorteos": mc["trials"], "estabilidad": mc["stability"],
                    "peor_sorteo_sin_cambio": mc["worst_trial"],
                    "escenarios_que_nunca_cambian": mc["never_flip"]},
}

fig, ax = plt.subplots(figsize=(ANCHO_IN, ANCHO_IN * 0.78))
ejes_limpios(ax, grilla="x")
etiquetas, valores, posiciones, grupos_y = [], [], [], []
pos, grupo_previo = 0, None
for grupo, etiqueta, valor in barras:
    if grupo != grupo_previo:
        if grupo_previo is not None:
            pos += 0.6
        grupos_y.append((grupo, pos))
        grupo_previo = grupo
    posiciones.append(pos)
    etiquetas.append(etiqueta)
    valores.append(valor)
    pos += 1
posiciones = [max(posiciones) - p for p in posiciones]
ax.barh(posiciones, valores, height=0.55, color=SERIE_1)
for p, v in zip(posiciones, valores):
    ax.text(v + 0.05, p, str(v), va="center", ha="left", color=TINTA_2, fontsize=8)
ax.set_yticks(posiciones, etiquetas, fontsize=8)
for grupo, p in grupos_y:
    ax.text(-0.02, max(posiciones) - p + 0.72, grupo.upper(), transform=ax.get_yaxis_transform(),
            ha="right", va="center", color=TENUE, fontsize=7.5, fontweight="bold")
tope = max(valores)
ax.set_xlim(0, tope + 1)
ax.xaxis.set_major_locator(MaxNLocator(integer=True))
ax.set_xlabel(f"Escenarios que cambian de veredicto (de {n})")
guardar(fig, "fig_5_4_sensibilidad.png")

# ══════════════════════════════════════════════════════════════════════════════
# 5.5 y 5.6 Asistente: matriz de confusion y F1 por intencion
# ══════════════════════════════════════════════════════════════════════════════
res = json.load(open(os.path.join(RAIZ, "copilot", "eval_results.json"), encoding="utf-8"))
casos = res["casos"]
cortos = {
    "buscar_aerodromo": "Búsqueda",
    "contacto_aerodromo": "Contacto",
    "servicios_aerodromo": "Servicios",
    "combustible_cercano": "Combustible cercano",
    "evaluar_meteo": "Evaluación meteorológica",
    "atmosfera_en_punto": "Aire en un punto",
    "mejor_hora_para_salir": "Mejor hora de salida",
    "proponer_cambio_de_ruta": "Cambio de ruta",
    "fuera_de_alcance": "Fuera de alcance",
}
intenciones = list(cortos)
indice = {it: i for i, it in enumerate(intenciones)}
m_asis = np.zeros((len(intenciones), len(intenciones)), dtype=int)
for c in casos:
    m_asis[indice[c["intent_esperado"]], indice[c["intent_obtenido"]]] += 1

metricas = []
for it in intenciones:
    tp = int(m_asis[indice[it], indice[it]])
    fn = int(m_asis[indice[it]].sum()) - tp
    fp = int(m_asis[:, indice[it]].sum()) - tp
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    metricas.append({"intencion": it, "casos": tp + fn, "precision": p, "exhaustividad": r, "f1": f1})
macro = sum(x["f1"] for x in metricas) / len(metricas)
datos["asistente"] = {"generado_unix": res["generado"], "casos": len(casos),
                      "aciertos": int(np.trace(m_asis)), "matriz": m_asis.tolist(),
                      "intenciones": intenciones, "metricas": metricas, "f1_macro": macro}

fig, ax = plt.subplots(figsize=(ANCHO_IN, ANCHO_IN * 0.82))
matriz(ax, m_asis, [cortos[i] for i in intenciones], [cortos[i] for i in intenciones],
       "Intención clasificada por el asistente", "Intención esperada")
plt.setp(ax.get_xticklabels(), rotation=35, ha="right", rotation_mode="anchor")
guardar(fig, "fig_5_5_matriz_asistente.png")

ordenadas = sorted(metricas, key=lambda x: x["f1"])
fig, ax = plt.subplots(figsize=(ANCHO_IN, ANCHO_IN * 0.5))
ejes_limpios(ax, grilla="x")
ys = np.arange(len(ordenadas))
ax.axvline(macro, color=TENUE, linewidth=0.8, zorder=1)
ax.scatter([x["f1"] for x in ordenadas], ys, s=46, color=SERIE_1, edgecolors=SUPERFICIE,
           linewidths=2, zorder=3)
for yi, x in zip(ys, ordenadas):
    ax.text(x["f1"] - 0.004, yi, coma(x["f1"], 3), va="center", ha="right", color=TINTA_2, fontsize=8)
ax.set_yticks(ys, [f"{cortos[x['intencion']]} (n = {x['casos']})" for x in ordenadas])
ax.set_xlim(0.88, 1.005)
ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: coma(v)))
ax.set_xlabel("F1 por intención")
ax.text(macro - 0.002, len(ordenadas) - 0.35, f"promedio macro: {coma(macro, 3)}", color=TINTA_2,
        fontsize=8, ha="right", va="bottom")
ax.set_ylim(-0.6, len(ordenadas) + 0.2)
guardar(fig, "fig_5_6_f1_asistente.png")

# ══════════════════════════════════════════════════════════════════════════════
# 5.7 Tiempos de respuesta
# ══════════════════════════════════════════════════════════════════════════════
# Lee las mediciones de medir_latencia_cap5.py (que si sale a la red; se corre
# aparte) y la latencia registrada en la evaluacion del asistente.
import statistics  # noqa: E402


def _leer_medicion(nombre):
    ruta = os.path.join(SALIDA, nombre)
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh)


mediciones = [("local", _leer_medicion("latencia_local.json")),
              ("Render", _leer_medicion("latencia_render.json"))]
nombres_categoria = {"primera de la ruta": "Primera evaluación de la ruta",
                     "otra aeronave": "Misma ruta, otra aeronave",
                     "repetida": "Evaluación repetida"}
filas_lat = []
for entorno, med in mediciones:
    if not med:
        continue
    validas = [f for f in med["filas"] if f["estado"] == 200]
    for categoria, etiqueta in nombres_categoria.items():
        valores = [f["segundos"] for f in validas if f["categoria"] == categoria]
        if valores:
            filas_lat.append((f"Motor en {entorno}", f"{etiqueta} (n = {len(valores)})", valores))
lat_asistente = [c["latencia_s"] for c in casos if c.get("latencia_s") is not None]
filas_lat.append(("Asistente", f"Consulta (n = {len(lat_asistente)})", lat_asistente))
datos["latencia"] = {
    "mediciones": {e: {"fecha_utc": m["fecha_utc"], "base": m["base"],
                       "primera_respuesta_s": m["primera_respuesta_s"], "resumen": m["resumen"]}
                   for e, m in mediciones if m},
    "asistente": {"n": len(lat_asistente), "media": round(statistics.mean(lat_asistente), 2),
                  "mediana": round(statistics.median(lat_asistente), 2)},
}

fig, ax = plt.subplots(figsize=(ANCHO_IN, ANCHO_IN * 0.66))
ejes_limpios(ax, grilla="x")
posiciones, etiquetas, grupos_y = [], [], []
pos, grupo_previo = 0.0, None
for grupo, etiqueta, _ in filas_lat:
    if grupo != grupo_previo:
        if grupo_previo is not None:
            pos += 0.7
        grupos_y.append((grupo, pos))
        grupo_previo = grupo
    posiciones.append(pos)
    etiquetas.append(etiqueta)
    pos += 1
tope_y = max(posiciones)
azar = np.random.default_rng(7)
for (_, _, valores), p in zip(filas_lat, posiciones):
    y0 = tope_y - p
    ax.scatter(valores, y0 + azar.uniform(-0.15, 0.15, size=len(valores)), s=22, color=SERIE_1,
               alpha=0.85, edgecolors=SUPERFICIE, linewidths=1, zorder=3)
    mediana = float(np.median(valores))
    ax.plot([mediana, mediana], [y0 - 0.3, y0 + 0.3], color=TINTA, linewidth=1.6, zorder=4)
    ax.text(mediana, y0 + 0.33, f"mediana {coma(mediana, 1)} s", ha="center", va="bottom",
            color=TINTA_2, fontsize=7)
ax.set_yticks([tope_y - p for p in posiciones], etiquetas, fontsize=8)
for grupo, p in grupos_y:
    ax.text(-0.02, tope_y - p + 0.62, grupo.upper(), transform=ax.get_yaxis_transform(),
            ha="right", va="center", color=TENUE, fontsize=7.5, fontweight="bold")
ax.set_xscale("log")
ax.set_xticks([0.5, 1, 2, 5, 10, 20, 50, 100])
ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: coma(v, 1) if v < 1 else f"{v:g}"))
ax.xaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
ax.tick_params(axis="x", which="minor", length=0)
ax.set_xlim(0.4, 120)
ax.set_ylim(-0.6, tope_y + 0.95)
ax.set_xlabel("Segundos (escala logarítmica)")
guardar(fig, "fig_5_7_tiempos_de_respuesta.png")

with open(os.path.join(SALIDA, "datos_cap5.json"), "w", encoding="utf-8") as f:
    json.dump(datos, f, ensure_ascii=False, indent=1)
print("   datos_cap5.json")
