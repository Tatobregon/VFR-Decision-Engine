# -*- coding: utf-8 -*-
"""
medir_latencia_cap5.py
======================
Mide la latencia de extremo a extremo de POST /api/evaluate contra las fuentes
reales, para el § 5.1.5. A diferencia del resto de las figuras, SALE A LA RED.

Uso (desde la raiz del repositorio):
    python Teorico/figuras/medir_latencia_cap5.py http://127.0.0.1:8765 local
    python Teorico/figuras/medir_latencia_cap5.py https://vfr-decision-engine.onrender.com render

Para cada ruta se evalua primero con una aeronave y despues con otra, y cada
evaluacion se repite una vez. La cache del servidor guarda cada dato por separado
(una observacion, un pronostico en una coordenada), no la ruta entera, asi que:
  - la PRIMERA evaluacion de una ruta encuentra vacia la cache de esa ruta;
  - la de la OTRA AERONAVE reutiliza los datos de superficie, pero no los del
    nivel de crucero, que depende de la aeronave;
  - la REPETIDA encuentra todo cargado.
Las rutas no comparten aerodromos entre si, para que la primera evaluacion de
cada una no se beneficie de la anterior.

Salida: Teorico/figuras/cap5/latencia_<etiqueta>.json
"""

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

from data.airports import AIRPORTS  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
ETIQUETA = sys.argv[2] if len(sys.argv) > 2 else "local"
SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cap5",
                      f"latencia_{ETIQUETA}.json")

CANDIDATAS = [
    ("PAMPA", "SACC", "JES"),
    ("PAMPA", "SADP", "SAAG"),
    ("LITORAL", "SAAR", "SAAP"),
    ("LITORAL", "SAAV", "SARE"),
    ("NOA", "SASA", "SANT"),
    ("NOA", "SANC", "SANE"),
    ("CUYO", "SAME", "SAOU"),
    ("CUYO", "SANU", "SAMR"),
    ("PATAGONIA", "SAZN", "SAZS"),
    ("PATAGONIA", "SAVT", "SAVC"),
]
AVIONES = ["Cessna 152", "Diamond DA40"]

rutas = [r for r in CANDIDATAS if r[1] in AIRPORTS and r[2] in AIRPORTS]
omitidas = [r for r in CANDIDATAS if r not in rutas]


def esperar_servidor():
    """Tiempo hasta la primera respuesta: en Render incluye el arranque en frio."""
    t0 = time.perf_counter()
    for _ in range(120):
        try:
            r = requests.get(BASE + "/api/aircraft", timeout=180)
            if r.ok:
                return time.perf_counter() - t0
        except requests.RequestException:
            pass
        time.sleep(2)
    raise SystemExit("el servidor no respondio")


def resumen(valores):
    valores = sorted(valores)
    if not valores:
        return {"n": 0}
    p95 = valores[min(len(valores) - 1, int(round(0.95 * (len(valores) - 1))))]
    return {"n": len(valores), "media": round(statistics.mean(valores), 2),
            "mediana": round(statistics.median(valores), 2), "p95": round(p95, 2),
            "min": round(valores[0], 2), "max": round(valores[-1], 2)}


print(f"[{ETIQUETA}] rutas: {len(rutas)} | omitidas por no estar en el registro: {omitidas}")
arranque_s = esperar_servidor()
print(f"[{ETIQUETA}] primera respuesta del servidor: {arranque_s:.1f} s")

filas = []
for region, origen, destino in rutas:
    for i, avion in enumerate(AVIONES):
        for repeticion in (1, 2):
            if repeticion == 2:
                categoria = "repetida"
            elif i == 0:
                categoria = "primera de la ruta"
            else:
                categoria = "otra aeronave"
            cuerpo = {"origin": origen, "dest": destino, "aircraft": avion, "flight_rules": "VFR"}
            t0 = time.perf_counter()
            try:
                r = requests.post(BASE + "/api/evaluate", json=cuerpo, timeout=300)
                estado = r.status_code
            except requests.RequestException as e:
                estado = f"error: {type(e).__name__}"
            dt = time.perf_counter() - t0
            filas.append({"region": region, "origen": origen, "destino": destino, "avion": avion,
                          "categoria": categoria, "estado": estado, "segundos": round(dt, 2)})
            print(f"  {region:10} {origen}-{destino:5} {avion:13} {categoria:19} {estado}  {dt:6.2f} s")

ok = [f for f in filas if f["estado"] == 200]
categorias = ("primera de la ruta", "otra aeronave", "repetida")
res = {c: resumen([f["segundos"] for f in ok if f["categoria"] == c]) for c in categorias}
print(f"\n[{ETIQUETA}] respuestas 200: {len(ok)}/{len(filas)}")
for c, r in res.items():
    print(f"  {c:19} {r}")

os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
with open(SALIDA, "w", encoding="utf-8") as f:
    json.dump({"etiqueta": ETIQUETA, "base": BASE,
               "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
               "primera_respuesta_s": round(arranque_s, 2), "rutas": rutas, "omitidas": omitidas,
               "filas": filas, "resumen": res}, f, ensure_ascii=False, indent=1)
print(f"  guardado en {SALIDA}")
