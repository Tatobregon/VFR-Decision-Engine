"""
airports.py
===========
Registro canonico de aerodromos del sistema VFR GO/NO GO.

Fuente unica: data/madhel_cache.json (ANAC MADHEL — 712 aerodromos AR)

Regla de identificador:
  - Si el aerodromo tiene codigo ICAO  → clave primaria = ICAO  (ej. "SACO")
  - Si no tiene ICAO                   → clave primaria = local_id MADHEL (ej. "ACB")

Para regenerar el cache ejecutar:
    python data/fetcher_madhel.py --force
"""

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_DATA_DIR     = os.path.dirname(os.path.abspath(__file__))
_MADHEL_CACHE = os.path.join(_DATA_DIR, "madhel_cache.json")

_NAME_RE = re.compile(r'^(.+?)\s+-\s+\(')
_RWY_RE  = re.compile(r'(\d+)/(\d+)\s+(\d+)x(\d+)\s+M\s*[-–]\s*([A-Z]+)', re.IGNORECASE)
_THR_RE  = re.compile(
    r'^(\d+)\s+([\d,]+[NS])\s+([\d,]+[EW])'
    r'(?:\s*[-–]\s*ELEV\s*[\d,. ]+\s*M\s*\((\d+)\s*FT\))?',
    re.IGNORECASE,
)


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RunwayInfo:
    label   : str            # descripcion para mostrar
    heading : int            # grados magneticos
    length_m: Optional[int] = None   # largo en metros
    width_m : Optional[int] = None   # ancho en metros
    surface : str = ""               # "ASPH", "CONC", "TIERRA", etc.
    thr_lat : Optional[float] = None # coordenada del umbral
    thr_lon : Optional[float] = None


@dataclass(frozen=True)
class AirportInfo:
    code          : str                    # clave primaria (ICAO o local_id)
    name          : str
    lat           : float
    lon           : float
    elev_ft       : int
    runways       : Tuple[RunwayInfo, ...]
    municipality  : str = ""
    province      : str = ""
    icao_code     : Optional[str] = None
    elev_estimated: bool = False
    local_id      : Optional[str] = None   # identificador MADHEL de 3 letras
    iata_code     : Optional[str] = None
    condition     : str = ""               # "PUBLICO" | "PRIVADO"
    control       : str = ""               # "CONTROLLED" | "NON-CONTROLLED"
    fuel          : str = ""
    schedule      : str = ""
    phones        : Tuple[str, ...] = ()
    norms_particular: str = ""
    is_public     : bool = True
    is_madhel     : bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Parseo de coordenadas DMS (formato MADHEL: '312611,45S', '0641539,28W')
# ─────────────────────────────────────────────────────────────────────────────

def _dms_to_decimal(s: str) -> Optional[float]:
    s = s.strip().upper()
    if not s:
        return None
    direction = s[-1]
    raw = s[:-1].replace(',', '.')

    dot_idx  = raw.find('.')
    int_part = raw[:dot_idx] if dot_idx >= 0 else raw
    frac_part = raw[dot_idx + 1:] if dot_idx >= 0 else ''

    try:
        if len(int_part) == 6:      # latitud DDMMSS
            deg  = int(int_part[:2])
            mins = int(int_part[2:4])
            secs = float(int_part[4:] + ('.' + frac_part if frac_part else '.0'))
        elif len(int_part) == 7:    # longitud DDDMMSS
            deg  = int(int_part[:3])
            mins = int(int_part[3:5])
            secs = float(int_part[5:] + ('.' + frac_part if frac_part else '.0'))
        else:
            return None
    except ValueError:
        return None

    val = deg + mins / 60.0 + secs / 3600.0
    if direction in ('S', 'W'):
        val = -val
    return val


# ─────────────────────────────────────────────────────────────────────────────
# Parseo de pistas MADHEL
# ─────────────────────────────────────────────────────────────────────────────

def _parse_madhel_runways(rwy_list: list, thr_list: list) -> List[RunwayInfo]:
    """
    rwy: ["17/35 1290x30 M - ASPH - AUW 11t/1 18t/2."]
    thr: ["17 312611,45S 0641539,28W - ELEV 479,75 M (1574 FT)", ...]
    """
    thr_by_hdg: Dict[int, Tuple[Optional[float], Optional[float]]] = {}
    for thr_str in (thr_list or []):
        m = _THR_RE.match(thr_str.strip())
        if not m:
            continue
        hdg_num = int(m.group(1))
        thr_by_hdg[hdg_num] = (_dms_to_decimal(m.group(2)), _dms_to_decimal(m.group(3)))

    result: List[RunwayInfo] = []
    for rwy_str in (rwy_list or []):
        m = _RWY_RE.search(rwy_str)
        if not m:
            continue
        hdg_le_num = int(m.group(1))
        hdg_he_num = int(m.group(2))
        length_m   = int(m.group(3))
        width_m    = int(m.group(4))
        surface    = m.group(5).upper()
        dims       = f"{length_m}m x {width_m}m  {surface}"

        for hdg_num, ident in ((hdg_le_num, str(hdg_le_num)),
                               (hdg_he_num, str(hdg_he_num))):
            hdg_deg         = hdg_num * 10
            thr_lat, thr_lon = thr_by_hdg.get(hdg_num, (None, None))
            result.append(RunwayInfo(
                label    = f"Pista {ident}  -  {hdg_deg:03d}°  ({dims})",
                heading  = hdg_deg,
                length_m = length_m,
                width_m  = width_m,
                surface  = surface,
                thr_lat  = thr_lat,
                thr_lon  = thr_lon,
            ))
    return result


def _extract_name(human_readable: str) -> str:
    """
    'GENERAL ACHA - (ACH / SAEA) - DRCE - ...' → 'GENERAL ACHA'
    """
    m = _NAME_RE.match(human_readable)
    return m.group(1).strip() if m else human_readable.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Cargador MADHEL
# ─────────────────────────────────────────────────────────────────────────────

def _load_from_madhel(cache_path: str) -> Dict[str, AirportInfo]:
    """Lee madhel_cache.json y construye {code: AirportInfo}."""
    if not os.path.exists(cache_path):
        return {}

    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)

    airports: Dict[str, AirportInfo] = {}

    for ap in cache.get("airports", []):
        # Excluir helipuertos — el sistema es exclusivo para aviones de ala fija
        if ap.get("type") == "HEL":
            continue

        meta       = ap.get("metadata", {}) or {}
        ids        = meta.get("identifiers", {}) or {}
        loc        = meta.get("localization", {}) or {}
        data       = ap.get("data", {}) or {}
        the_geom   = ap.get("the_geom", {}) or {}
        geom_geo   = the_geom.get("geometry", {}) if isinstance(the_geom, dict) else {}
        geom_props = the_geom.get("properties", {}) if isinstance(the_geom, dict) else {}

        local_id = (ids.get("local") or ap.get("local_identifier") or "").strip()
        icao     = ids.get("icao") or None
        iata     = ids.get("iata") or None

        if not local_id:
            continue

        code = icao if icao else local_id

        human_readable = (
            geom_props.get("name")
            or ap.get("human_readable_identifier")
            or local_id
        )
        name = _extract_name(human_readable)

        # Coordenadas
        coords = loc.get("coordinates", {}) or {}
        if coords.get("lat") is not None:
            try:
                lat = float(coords["lat"])
                lon = float(coords.get("lng", 0))
            except (TypeError, ValueError):
                lat = lon = None
        elif geom_geo and geom_geo.get("type") == "Point":
            gc = geom_geo.get("coordinates", [])
            try:
                lon = float(gc[0])
                lat = float(gc[1])
            except (TypeError, ValueError, IndexError):
                lat = lon = None
        else:
            lat = lon = None

        if lat is None or lon is None:
            continue

        # Elevacion (metros → pies)
        elev_m = loc.get("elevation")
        if elev_m is not None:
            try:
                elev_m_f = float(elev_m)
                elev_ft  = int(round(elev_m_f * 3.28084))
                # Elevacion 0 en un aeródromo terrestre es casi siempre un dato faltante en MADHEL
                elev_estimated = (elev_m_f == 0.0)
            except (TypeError, ValueError):
                elev_ft, elev_estimated = 0, True
        else:
            elev_ft, elev_estimated = 0, True

        province = loc.get("state", "") or ""

        runways = tuple(_parse_madhel_runways(
            data.get("rwy", []) or [],
            data.get("thr", []) or [],
        ))

        condition = meta.get("condition", "") or ""
        control   = meta.get("control", "") or ""
        is_public = condition.upper() in ("PUBLICO", "PÚBLICO")

        fuel     = (data.get("fuel", "") or "").strip()
        schedule = (data.get("service_schedule", "") or "").strip()
        phones   = tuple(str(p).strip() for p in (data.get("telephone", []) or []) if p)

        norms_part  = ""
        norms_block = data.get("norms", {}) or {}
        if isinstance(norms_block, dict):
            particular = norms_block.get("particular", {}) or {}
            if isinstance(particular, dict):
                norms_part = (particular.get("content", "") or "").strip()

        airports[code] = AirportInfo(
            code             = code,
            name             = name,
            lat              = lat,
            lon              = lon,
            elev_ft          = elev_ft,
            elev_estimated   = elev_estimated,
            runways          = runways,
            municipality     = "",
            province         = province,
            icao_code        = icao,
            local_id         = local_id,
            iata_code        = iata,
            condition        = condition,
            control          = control,
            fuel             = fuel,
            schedule         = schedule,
            phones           = phones,
            norms_particular = norms_part,
            is_public        = is_public,
            is_madhel        = True,
        )

    return airports


# ─────────────────────────────────────────────────────────────────────────────
# Carga al importar
# ─────────────────────────────────────────────────────────────────────────────

AIRPORTS: Dict[str, AirportInfo] = _load_from_madhel(_MADHEL_CACHE)

# Solo aerodromos publicos — para grafo de rutas (waypoints)
AIRPORTS_PUBLIC: Dict[str, AirportInfo] = {
    code: info for code, info in AIRPORTS.items() if info.is_public
}

AIRPORTS_BY_NAME: Dict[str, AirportInfo] = {
    info.name: info for info in AIRPORTS.values()
}

AIRPORT_NAMES: List[str] = sorted(info.name for info in AIRPORTS.values())


# ─────────────────────────────────────────────────────────────────────────────
# API publica
# ─────────────────────────────────────────────────────────────────────────────

def get_by_name(name: str) -> Optional[AirportInfo]:
    return AIRPORTS_BY_NAME.get(name)


def get_by_code(code: str) -> Optional[AirportInfo]:
    return AIRPORTS.get(code)


def search_airports(query: str, province: Optional[str] = None) -> List[AirportInfo]:
    """
    Busqueda tolerante sobre nombre, provincia, codigo ICAO y local_id.
    Retorna lista ordenada por nombre.
    """
    q = _normalize(query.strip())
    results = []
    for info in AIRPORTS.values():
        if province and _normalize(province) not in _normalize(info.province):
            continue
        if q and not (
            q in _normalize(info.name)
            or q in _normalize(info.province)
            or q in info.code.lower()
            or (info.local_id  and q in info.local_id.lower())
            or (info.icao_code and q in info.icao_code.lower())
        ):
            continue
        results.append(info)
    return sorted(results, key=lambda a: a.name)


# ─────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 65)
    print("  TEST: data/airports.py  (fuente unica: MADHEL)")
    print("=" * 65)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    N        = len(AIRPORTS)
    N_public = len(AIRPORTS_PUBLIC)

    check(f"Total aerodromos cargados   : {N}  (minimo 700)", N >= 700)
    check(f"Aerodromos publicos         : {N_public}  (minimo 200)", N_public >= 200)
    check("Todos son is_madhel=True", all(a.is_madhel for a in AIRPORTS.values()))

    for icao, desc in [("SACO", "Cordoba"), ("SABE", "Ezeiza"), ("SACC", "La Cumbre")]:
        check(f"{icao} ({desc}) presente", icao in AIRPORTS)

    check("AIRPORTS_PUBLIC subconjunto de AIRPORTS",
          all(c in AIRPORTS for c in AIRPORTS_PUBLIC))

    with_rwys = [a for a in AIRPORTS.values() if a.runways]
    check(f"Con datos de pista          : {len(with_rwys)}", len(with_rwys) > 0)

    with_fuel = sum(1 for a in AIRPORTS.values() if a.fuel)
    with_sch  = sum(1 for a in AIRPORTS.values() if a.schedule)
    with_icao = sum(1 for a in AIRPORTS.values() if a.icao_code)
    with_dim  = sum(1 for a in AIRPORTS.values() for r in a.runways if r.length_m)
    print(f"\n  Con combustible             : {with_fuel}")
    print(f"  Con horario                 : {with_sch}")
    print(f"  Con codigo ICAO             : {with_icao}")
    print(f"  Pistas con dimensiones      : {with_dim}")

    saco = get_by_code("SACO")
    if saco:
        check("SACO: local_id presente",                   saco.local_id is not None)
        check("SACO: province contiene CORDOBA",           "RDOBA" in saco.province.upper())

    res = search_airports("cordoba")
    check(f"search('cordoba'): {len(res)} resultados", len(res) > 0)
    check("search('xyzzy'): lista vacia", search_airports("nada_xyzzy") == [])

    print("\n  Muestra (primeros 5 por nombre):")
    for a in sorted(AIRPORTS.values(), key=lambda x: x.name)[:5]:
        rwys = ", ".join(f"{r.heading}°" for r in a.runways) if a.runways else "sin pista"
        print(f"    {a.code:<8}  {a.name:<40}  [{rwys}]")

    print("\n" + "=" * 65)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 65)
