#!/usr/bin/env python3
"""
fetcher_madhel.py
=================
Descarga y cachea todos los aerodromos del sistema MADHEL de ANAC Argentina.

API oficial (sin autenticacion):
  GET https://datos.anac.gob.ar/madhel/api/v2/airports/?limit=1000
  GET https://datos.anac.gob.ar/madhel/api/v2/airports/{local_id}/

Uso:
    python data/fetcher_madhel.py
    python data/fetcher_madhel.py --force   # regenerar aunque exista el cache

Genera:
    data/madhel_cache.json  (~3-5 MB)

Tiempo estimado: 5-10 minutos (712 requests con rate limiting suave).
"""

import json
import os
import sys
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone

_BASE_URL   = "https://datos.anac.gob.ar/madhel/api/v2/airports"
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "madhel_cache.json")
_HEADERS    = {
    "User-Agent" : "VFR-GONOGO/1.0 (Tesis academica, contacto: lautarobregon@gmail.com)",
    "Accept"     : "application/json",
}

_RETRY_DELAYS = (1.0, 2.5, 5.0)


def _get_json(url: str) -> dict:
    """GET con reintentos. Retorna {} en 404, lanza en otros errores persistentes."""
    last_exc = None
    for delay in (None, *_RETRY_DELAYS):
        if delay is not None:
            time.sleep(delay)
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            last_exc = e
        except Exception as e:
            last_exc = e
    raise RuntimeError(f"Fallo GET {url}: {last_exc}")


def fetch_all(force: bool = False) -> str:
    """
    Descarga lista + detalles individuales de todos los aerodromos.
    Guarda resultado en madhel_cache.json y retorna la ruta al archivo.
    """
    if not force and os.path.exists(_CACHE_FILE):
        print(f"Cache ya existe: {_CACHE_FILE}")
        print("Ejecutar con --force para regenerar.")
        return _CACHE_FILE

    # ── Paso 1: lista completa ───────────────────────────────────────────────
    print("Descargando lista de aerodromos MADHEL (ANAC Argentina)...")
    list_data = _get_json(f"{_BASE_URL}/?limit=1000&format=json")
    airports_list = list_data.get("results", [])
    total = len(airports_list)
    if not total:
        print("ERROR: la API no devolvio aerodromos. Verificar conectividad.")
        sys.exit(1)
    print(f"  {total} aerodromos en la lista.")

    # ── Paso 2: detalle individual ───────────────────────────────────────────
    print(f"Descargando fichas individuales (puede tardar ~{total // 100 + 5} min)...")
    airports_detail = []
    errors          = []

    for i, ap in enumerate(airports_list, 1):
        local_id = ap.get("local_identifier", "").strip()
        if not local_id:
            continue

        if i == 1 or i % 100 == 0 or i == total:
            pct = i / total * 100
            print(f"  [{i:3d}/{total}] {pct:5.1f}%  — ultimo: {local_id}")

        try:
            detail = _get_json(f"{_BASE_URL}/{local_id}/?format=json")
            if detail:
                airports_detail.append(detail)
            else:
                airports_detail.append({**ap, "_source": "list_only"})
        except Exception as exc:
            errors.append(local_id)
            print(f"    [WARN] {local_id}: {exc} — usando datos de lista")
            airports_detail.append({**ap, "_source": "list_only"})

        time.sleep(0.07)

    # ── Paso 3: guardar cache ────────────────────────────────────────────────
    cache = {
        "_meta": {
            "fetched_at" : datetime.now(timezone.utc).isoformat(),
            "count"      : len(airports_detail),
            "errors"     : errors,
            "source"     : _BASE_URL,
            "api_version": "v2",
        },
        "airports": airports_detail,
    }

    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(_CACHE_FILE) // 1024
    print(f"\nCache guardado: {_CACHE_FILE}  ({size_kb} KB)")
    if errors:
        print(f"Errores ({len(errors)}): {errors}")
    else:
        print("Sin errores.")
    return _CACHE_FILE


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetcher ANAC MADHEL — cache de aerodromos AR")
    parser.add_argument("--force", action="store_true",
                        help="Regenerar cache aunque ya exista")
    args = parser.parse_args()
    fetch_all(force=args.force)
