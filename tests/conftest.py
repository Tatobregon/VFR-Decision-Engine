"""
conftest.py
===========
Configuracion comun de la suite. Agrega la raiz del proyecto al sys.path para
que los tests importen los modulos igual que lo hace la aplicacion.

Los tests NO deben salir a la red: los que necesitan datos usan `mock=True` o
construyen los objetos a mano. Asi la suite corre en cualquier lado y siempre
da el mismo resultado.
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Cada test arranca con las caches de datos vacias."""
    from data.cache import clear_all
    clear_all()
    yield
    clear_all()


@pytest.fixture
def weather():
    """
    Fabrica de ParsedWeather para los tests: `weather(vis_km=..., ceiling_ft=...)`.
    Los valores por defecto describen un dia VFR de aire calmo.
    """
    from parsers.metar_parser import ParsedWeather, _compute_flight_category

    def _make(**kwargs):
        vis  = kwargs.pop("visibility_km", 10.0)
        ceil = kwargs.pop("ceiling_ft", None)
        base = dict(
            source="metar", station_id="TEST", obs_time=0,
            visibility_km=vis, ceiling_ft=ceil,
            wind_dir=360, wind_spd_kt=0.0, spread_c=10.0,
            flight_category=_compute_flight_category(vis, ceil),
        )
        base.update(kwargs)
        return ParsedWeather(**base)

    return _make
