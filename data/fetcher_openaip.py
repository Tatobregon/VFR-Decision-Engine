"""
fetcher_openaip.py
==================
Descarga datos de espacios aereos de Argentina desde OpenAIP (api.core.openaip.net).

Fuente   : https://www.openaip.net/  (API v1)
Costo    : Gratuito con registro (API key requerida)
Cobertura: Argentina (country=AR) — CTR, TMA, R, P, D y zonas especiales

Como obtener la API key
-----------------------
1. Registrarse en https://www.openaip.net/
2. Ir a perfil → API Keys → crear una nueva
3. Guardar la key en la variable de entorno OPENAIP_API_KEY
   o pasarla directamente a update_cache(api_key="...")

Uso tipico (actualizar el cache de espacios aereos)
----------------------------------------------------
    python data/fetcher_openaip.py --api-key TU_KEY

    # O desde Python:
    from data.fetcher_openaip import update_cache
    update_cache(api_key="TU_KEY")

El archivo resultante (data/ar-airspace.json) es leido automaticamente
por data/airspace.py al iniciar el sistema.

Nota sobre actualizaciones
--------------------------
Los espacios aereos raramente cambian. Se recomienda actualizar el cache
una vez al mes o cuando ANAC publique cambios en el AIP Argentina.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Rutas y configuracion
# ──────────────────────────────────────────────────────────────────────────────

_DATA_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH  = os.path.join(_DATA_DIR, "ar-airspace.json")
_META_PATH  = os.path.join(_DATA_DIR, "ar-airspace-meta.json")

_API_BASE    = "https://api.core.openaip.net/api"
_PAGE_SIZE   = 100
_MAX_RETRIES = 3
_RETRY_DELAY = 2   # segundos entre reintentos


# ──────────────────────────────────────────────────────────────────────────────
# Fetcher
# ──────────────────────────────────────────────────────────────────────────────

class OpenAIPFetcher:
    """
    Cliente para la API de OpenAIP.

    Parametros
    ----------
    api_key : API key de OpenAIP (https://www.openaip.net/).
              Si es None, intenta leer desde variable de entorno OPENAIP_API_KEY.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENAIP_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Se requiere una API key de OpenAIP. "
                "Pasarla como argumento o definir OPENAIP_API_KEY en el entorno."
            )
        self._session = requests.Session()
        self._session.headers.update({
            "x-openaip-api-key": self.api_key,
            "Accept":            "application/json",
        })

    # ── Peticiones ──────────────────────────────────────────────────────────

    def _get_page(self, endpoint: str, params: Dict) -> Dict[str, Any]:
        """Descarga una pagina con reintentos ante errores transitorios."""
        url = f"{_API_BASE}/{endpoint}"
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 5))
                    logger.warning(f"Rate limit OpenAIP, esperando {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout en intento {attempt}/{_MAX_RETRIES}")
            except requests.exceptions.ConnectionError:
                logger.warning(f"Error de conexion en intento {attempt}/{_MAX_RETRIES}")
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code >= 500:
                    logger.warning(f"Error servidor OpenAIP ({e.response.status_code}), "
                                   f"intento {attempt}/{_MAX_RETRIES}")
                else:
                    raise
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * attempt)
        raise RuntimeError(f"OpenAIP: no se pudo obtener {url} despues de {_MAX_RETRIES} intentos")

    def fetch_argentina_airspaces(self) -> List[Dict[str, Any]]:
        """
        Descarga todos los espacios aereos de Argentina desde OpenAIP.
        Maneja paginacion automaticamente.

        Retorna
        -------
        Lista de objetos JSON crudos de la API (sin transformar).
        """
        all_items: List[Dict] = []
        page = 1

        while True:
            logger.info(f"Descargando pagina {page} (OpenAIP AR)...")
            data = self._get_page("airspaces", {
                "country"  : "AR",
                "limit"    : _PAGE_SIZE,
                "page"     : page,
            })
            items = data.get("items", [])
            all_items.extend(items)

            total = data.get("totalCount", len(all_items))
            logger.info(f"  {len(all_items)}/{total} zonas descargadas")

            if len(items) < _PAGE_SIZE or len(all_items) >= total:
                break
            page += 1
            time.sleep(0.3)   # respetar rate limit

        return all_items


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

def update_cache(
    api_key   : Optional[str] = None,
    cache_path: str = CACHE_PATH,
) -> int:
    """
    Descarga los espacios aereos de Argentina desde OpenAIP y los guarda localmente.

    Parametros
    ----------
    api_key    : API key de OpenAIP. Si es None, usa OPENAIP_API_KEY del entorno.
    cache_path : Ruta donde guardar el JSON de cache.

    Retorna
    -------
    Cantidad de zonas guardadas.
    """
    fetcher = OpenAIPFetcher(api_key=api_key)
    items   = fetcher.fetch_argentina_airspaces()

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    meta = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count"     : len(items),
        "source"    : "OpenAIP",
        "country"   : "AR",
    }
    meta_path = cache_path.replace(".json", "-meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"Cache actualizado: {len(items)} zonas → {cache_path}")
    return len(items)


def is_cache_fresh(max_age_days: int = 30, cache_path: str = CACHE_PATH) -> bool:
    """Retorna True si el cache existe y tiene menos de max_age_days dias."""
    if not os.path.exists(cache_path):
        return False
    meta_path = cache_path.replace(".json", "-meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            updated = datetime.fromisoformat(meta["updated_at"])
            age = datetime.now(timezone.utc) - updated
            return age.days < max_age_days
        except Exception:
            pass
    # Fallback: fecha de modificacion del archivo
    age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
    return age_days < max_age_days


def load_raw_cache(cache_path: str = CACHE_PATH) -> List[Dict[str, Any]]:
    """Carga el cache raw de OpenAIP desde disco. Retorna [] si no existe."""
    if not os.path.exists(cache_path):
        return []
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Error al leer cache OpenAIP ({cache_path}): {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Script de actualizacion standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt = "%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Descarga espacios aereos de Argentina desde OpenAIP y actualiza el cache local."
    )
    parser.add_argument(
        "--api-key", default=None,
        help="API key de OpenAIP. Si se omite, usa la variable de entorno OPENAIP_API_KEY."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Forzar descarga aunque el cache sea reciente (menos de 30 dias)."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Solo verificar si el cache esta fresco, sin descargar."
    )
    args = parser.parse_args()

    if args.check:
        fresh = is_cache_fresh()
        raw   = load_raw_cache()
        print(f"Cache: {'FRESCO' if fresh else 'DESACTUALIZADO o INEXISTENTE'}")
        print(f"Zonas en cache: {len(raw)}")
        print(f"Ruta: {CACHE_PATH}")
    elif not args.force and is_cache_fresh():
        print("Cache ya actualizado (menos de 30 dias). Usar --force para refrescar.")
        raw = load_raw_cache()
        print(f"Zonas en cache: {len(raw)}")
    else:
        try:
            n = update_cache(api_key=args.api_key)
            print(f"\nDescarga completa: {n} zonas guardadas en {CACHE_PATH}")
        except ValueError as e:
            print(f"Error: {e}")
            print("\nOpciones para proveer la API key:")
            print("  1. python data/fetcher_openaip.py --api-key TU_KEY")
            print("  2. export OPENAIP_API_KEY=TU_KEY  (Linux/Mac)")
            print("     $env:OPENAIP_API_KEY='TU_KEY'  (PowerShell)")
