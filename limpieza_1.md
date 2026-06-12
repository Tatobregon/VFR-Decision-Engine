# Limpieza #1 — Eliminación de código muerto

**Fecha:** 2026-06-12
**Alcance:** barrido completo del código para eliminar lo no usado y dejar que
todo lo que queda tenga una razón de estar. **No** se cambió funcionalidad ni
performance del sistema; el comportamiento es idéntico.

**Balance:** 25 archivos tocados · **−3.908 líneas** · +46 · **5 módulos eliminados**.

---

## Metodología

Análisis estático + grafo de imports, no a ojo:

1. **`pyflakes`** → imports y nombres sin uso, variables muertas, redefiniciones.
2. **`vulture`** → funciones/clases/variables nunca referenciadas.
3. **Grafo de imports (AST propio)** → módulos que nadie importa (huérfanos),
   con análisis de alcanzabilidad desde el entry point real (`web/app.py`).
4. **`autoflake`** → remoción automática y segura de los imports muertos.
5. **Verificación tras cada tanda:** import de los 33 módulos restantes, tests
   `__main__` standalone, `evaluate` VFR (corredores) e IFR (aerovías), y los
   endpoints GET. Árbol git limpio de base → red de seguridad para revertir.

Criterio aplicado:
- **Se eliminó sin preguntar:** imports muertos, funciones/variables sin uso,
  módulos huérfanos, ramas y vestigios muertos.
- **Se consultó antes de tocar:** interfaces completas (CLI, GUI).
- **Se respetó:** los bloques `__main__` de test standalone, el patrón
  `try/except ImportError`, y el código `mock` (infraestructura de test).

---

## 1. Módulos eliminados (5 archivos, 3.826 líneas)

| Archivo | Líneas | Motivo |
|---|---|---|
| `gui.py` | 2519 | GUI Tkinter. Interfaz alternativa superada por la web. **Decisión del usuario.** |
| `output/formatter.py` | 417 | `format_decision()` / `format_short()`. Solo lo usaban la CLI y la GUI. |
| `route/genetic.py` | 406 | Optimizador por **algoritmo genético**, reemplazado por A* en modo `suggested`. Nadie lo importaba. |
| `features/flight_category.py` | 276 | Categoría de vuelo ANAC. **Duplicaba** `parsers/metar_parser._compute_flight_category`, que es la que el sistema usa de verdad. Nadie importaba el módulo. |
| `main.py` | 208 | CLI entry point. Interfaz alternativa superada por la web. **Decisión del usuario.** |

> **Por qué se fueron CLI y GUI:** el sistema corre como web app
> (`uvicorn web.app:app`). `main.py` y `gui.py` eran entradas raíz que nadie
> importaba y que la interfaz web ya reemplaza. Al eliminarlos, `output/formatter.py`
> quedó huérfano (solo lo consumían ellos) y también se removió.

---

## 2. Código muerto eliminado dentro de los archivos

### Imports sin uso (55, en 20 archivos)
Removidos con `autoflake`. Ejemplos: `math` sin usar en `terrain.py`,
`vfr_altitude.py`, `fog_risk.py`, `density_altitude.py`; `dataclasses.field`
en varios; `typing.Optional` sin usar; `config.METAR_STATIONS` y
`risk.hard_blockers.HARD_BLOCKER_TOKENS` en `decision/engine.py`; etc.

### `data/airports.py`
- `get_by_name()` — función pública sin un solo uso en el código.
- `AIRPORTS_BY_NAME` — dict derivado que existía **solo** para `get_by_name`.
- `AIRPORT_NAMES` — lista derivada con cero usos.
- *(Se conservó `AIRPORTS_PUBLIC` porque lo usa el optimizer, y `get_by_code`/
  `search_airports` porque son API en uso.)*

### `route/optimizer.py`
- Parámetro **`ga_seed`** de `optimize()` — vestigio del algoritmo genético ya
  eliminado. Se sacó de la firma, del docstring y de sus 4 llamadas
  (3 en tests + 1 en `output/briefing.py`).
- Import local redundante `from route.graph import RouteGraph` (ya estaba a
  nivel de módulo).

### `route/airway_router.py`
- Variables locales muertas `best_entry_dist` / `best_exit_dist` (asignadas,
  nunca leídas).

---

## 3. Corrección de convención

- **`route/airway_router.py`** — se agregó el bootstrap de `sys.path`
  (`try/except ImportError`) para que el módulo corra como script standalone
  (`python route/airway_router.py`), igual que el resto. Antes fallaba con
  `ModuleNotFoundError: No module named 'data'`. (Pre-existente, no era código
  muerto, pero rompía la convención de tests `__main__` del proyecto.)

---

## 4. Documentación actualizada (`CLAUDE.md`)

- Tabla de integración: se quitaron las filas de `output/formatter.py`,
  `main.py` y `gui.py`; se agregaron `output/briefing.py`, `web/app.py` y
  `route/vfr_corridors.py` (la integración y salida reales de hoy).
- Tabla de features: se quitó `flight_category.py`; se agregó `vfr_altitude.py`.
- `data/airports.py`: `AIRPORTS_BY_NAME`/`AIRPORT_NAMES` → `AIRPORTS_PUBLIC`.
- "Orden de construcción": Output + CLI → Output + Web; comando de arranque
  `python main.py …` → `uvicorn web.app:app …`.

---

## 5. Lo que se CONSERVÓ a propósito (no es código muerto)

| Elemento | Por qué se queda |
|---|---|
| `data/fetcher_madhel.py` | Herramienta que **regenera** `madhel_cache.json` (documentada en `airports.py`). |
| `data/fetcher_openaip.py` | Herramienta que **regenera** el cache de espacio aéreo (documentada en `airspace.py`). |
| `risk/ahp_weights.py` | Script reproducible de la derivación AHP de los pesos — artefacto/apéndice de la tesis. |
| `route/weather_sampler.py` | Lo usa el optimizer (camino `evaluate_intermediate`). |
| Código `mock` de los fetchers | Infraestructura de test (`__main__` y `mock=True`). La web ya usa `mock=False`. |

---

## 6. Verificación final

- ✅ Los **33 módulos** restantes importan sin error.
- ✅ `evaluate` **VFR** (SACO→SAAR): ruta directa + 3 waypoints de corredor.
- ✅ `evaluate` **IFR** (SASA→SAWH): 5 waypoints de aerovía.
- ✅ Endpoints `GET` (`/api/vfr_corridors`, `/api/aircraft`, `/api/airspace`): 200.
- ✅ `pyflakes`: **0** issues reales (solo quedan f-strings sin placeholder y
  re-imports dentro de bloques `__main__`, ambos inofensivos).
- ✅ `airway_router.py` ahora corre standalone.

---

## Detalle por archivo

```
 CLAUDE.md                       |   18 +-
 data/airports.py                |   11 -
 data/airspace.py                |    6 +-
 data/fetcher_aviationweather.py |    5 +-
 data/fetcher_openmeteo.py       |    2 +-
 data/terrain.py                 |    1 -
 decision/engine.py              |    7 +-
 features/density_altitude.py    |    1 -
 features/flight_category.py     |  276 -----   (ELIMINADO)
 features/fog_risk.py            |    1 -
 features/orographic.py          |    1 -
 features/taf_window.py          |    4 +-
 features/vfr_altitude.py        |    2 -
 gui.py                          | 2519 ---------   (ELIMINADO)
 main.py                         |  208 ----   (ELIMINADO)
 output/briefing.py              |   10 +-
 output/formatter.py             |  417 -----   (ELIMINADO)
 parsers/taf_parser.py           |    2 -
 risk/soft_scoring.py            |    1 -
 route/airway_router.py          |    6 +-   (+ bootstrap standalone)
 route/astar.py                  |   12 +-
 route/genetic.py                |  406 -----   (ELIMINADO)
 route/graph.py                  |    4 +-
 route/optimizer.py              |   30 +-
 web/app.py                      |    4 +-
```
