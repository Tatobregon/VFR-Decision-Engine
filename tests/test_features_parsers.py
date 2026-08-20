"""
test_features_parsers.py
========================
Capa de features (crosswind, niebla, luz diurna, NOTAM, altitud VFR, densidad) y
capa de parsing (METAR, TAF, adaptador NWP).
"""

from datetime import datetime, timezone

import pytest

from features.crosswind import compute_crosswind, favored_runway
from features.fog_risk import compute_fog_risk
from features.daylight import daylight_status, is_daylight, sun_times_unix
from features.notam_impact import assess_notam_impact, runway_designators
from features.vfr_altitude import hemispheric_vfr_altitude, true_to_magnetic
from features.density_altitude import compute_density_altitude, advisory
from features.taf_window import nwp_trend_r_taf
from parsers.metar_parser import (
    _compute_flight_category, _parse_visibility_km, _extract_ceiling_ft,
)
from parsers.openmeteo_adapter import estimate_cloud_base_ft, _pct_to_sky_cover


# ── Viento cruzado ────────────────────────────────────────────────────────────

def test_viento_de_frente_no_tiene_componente_cruzada():
    r = compute_crosswind(360, 15.0, 360)
    assert r.crosswind_kt == pytest.approx(0.0, abs=0.01)
    assert r.headwind_kt == pytest.approx(15.0, abs=0.01)


def test_viento_perpendicular_es_todo_cruzado():
    r = compute_crosswind(90, 10.0, 360)
    assert r.crosswind_kt == pytest.approx(10.0, abs=0.01)
    assert r.headwind_kt == pytest.approx(0.0, abs=0.01)


def test_viento_de_cola_da_headwind_negativo():
    assert compute_crosswind(180, 10.0, 360).headwind_kt < 0


def test_viento_variable_asume_el_peor_caso():
    """Sin direccion confiable se asume viento de costado puro."""
    r = compute_crosswind(None, 12.0, 360, wind_variable=True)
    assert r.crosswind_kt == pytest.approx(12.0)
    assert r.is_worst_case


def test_pista_favorable_elige_la_mas_alineada():
    # Pista 18/36, viento del norte -> conviene la 36 (los rumbos se
    # normalizan con % 360, asi que la cabecera norte se expresa como 0)
    assert favored_runway([180], wind_dir=360, wind_spd_kt=12.0) % 360 == 0
    # mismo par de cabeceras, viento del sur -> conviene la 18
    assert favored_runway([180], wind_dir=180, wind_spd_kt=12.0) == 180


def test_pista_favorable_sin_datos_es_determinista():
    assert favored_runway([], None, None, default=180) == 180


# ── Niebla ────────────────────────────────────────────────────────────────────

def test_spread_bajo_es_riesgo_maximo_de_niebla():
    assert compute_fog_risk(spread_c=1.0, wx_codes=[]).r_fog == pytest.approx(1.0)


def test_spread_alto_sin_fenomenos_no_da_riesgo():
    assert compute_fog_risk(spread_c=12.0, wx_codes=[]).r_fog == pytest.approx(0.0)


def test_niebla_reportada_pesa_aunque_el_spread_sea_alto():
    assert compute_fog_risk(spread_c=10.0, wx_codes=["FG"]).r_fog > 0.5


# ── Luz diurna ────────────────────────────────────────────────────────────────

def test_mediodia_es_de_dia_y_medianoche_no():
    lat, lon = -34.6, -58.4        # Buenos Aires
    mediodia = int(datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc).timestamp())
    medianoche = int(datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc).timestamp())
    assert is_daylight(lat, lon, mediodia)
    assert not is_daylight(lat, lon, medianoche)


def test_el_dia_es_mas_corto_en_invierno():
    lat, lon = -34.6, -58.4
    invierno = int(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc).timestamp())
    verano   = int(datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc).timestamp())
    sr_i, ss_i = sun_times_unix(lat, lon, invierno)
    sr_v, ss_v = sun_times_unix(lat, lon, verano)
    assert (ss_i - sr_i) < (ss_v - sr_v)


def test_aviso_de_luz_ajustada_cerca_del_ocaso():
    lat, lon = -34.6, -58.4
    ref = int(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc).timestamp())
    _, ocaso = sun_times_unix(lat, lon, ref)
    st = daylight_status(lat, lon, ocaso - 30 * 60)
    assert st["is_day"] and st["tight"]


def test_la_latitud_cambia_la_duracion_del_dia():
    """Ushuaia en invierno tiene menos luz que Buenos Aires el mismo dia."""
    ref = int(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc).timestamp())
    sr_ba, ss_ba = sun_times_unix(-34.6, -58.4, ref)
    sr_us, ss_us = sun_times_unix(-54.8, -68.3, ref)
    assert (ss_us - sr_us) < (ss_ba - sr_ba)


# ── NOTAM ─────────────────────────────────────────────────────────────────────

class _Notam:
    def __init__(self, message="", q_code=""):
        self.message, self.q_code = message, q_code


def test_designadores_de_pista_incluyen_ambas_cabeceras():
    assert runway_designators([160]) == {"16", "34"}


def test_aerodromo_cerrado_bloquea():
    assert assess_notam_impact([_Notam("AD CLSD POR OBRAS")], {"16", "34"}).blocking


def test_una_sola_cabecera_cerrada_no_bloquea():
    impacto = assess_notam_impact([_Notam("RWY 16 CLSD", "QMRLC")], {"16", "34"})
    assert not impacto.blocking


def test_notam_informativo_no_bloquea():
    assert not assess_notam_impact([_Notam("VOR U/S", "QNVAS")], {"16", "34"}).blocking


# ── Altitud VFR ───────────────────────────────────────────────────────────────

def test_regla_de_semicirculos_da_niveles_terminados_en_500():
    for rumbo in (0, 45, 90, 135, 180, 225, 270, 315):
        alt = hemispheric_vfr_altitude(rumbo, -34.0, -60.0, 9500)
        assert alt % 1000 == 500


def test_rumbos_opuestos_usan_semicirculos_distintos():
    este  = hemispheric_vfr_altitude(90, -34.0, -60.0, 7500)
    oeste = hemispheric_vfr_altitude(270, -34.0, -60.0, 7500)
    assert (este // 1000) % 2 != (oeste // 1000) % 2


def test_por_debajo_de_3000_no_aplica_la_regla():
    assert hemispheric_vfr_altitude(90, -34.0, -60.0, 2500) == 2500


def test_declinacion_magnetica_desplaza_el_rumbo():
    assert true_to_magnetic(360, -34.0, -60.0) != 360.0


# ── Altitud de densidad ───────────────────────────────────────────────────────

def test_mas_calor_implica_mayor_altitud_de_densidad():
    fria = compute_density_altitude(temp_c=5.0, elevation_ft=3000)
    calida = compute_density_altitude(temp_c=35.0, elevation_ft=3000)
    assert calida.density_alt_ft > fria.density_alt_ft


def test_niveles_de_advertencia_de_densidad():
    assert advisory(compute_density_altitude(15.0, 0)) == "NORMAL"
    assert advisory(compute_density_altitude(40.0, 6000)) in {"ELEVATED", "HIGH"}


# ── Categoria de vuelo (ANAC/OACI) ────────────────────────────────────────────

@pytest.mark.parametrize("vis,ceil,esperado", [
    (10.0, 3000, "VFR"),
    (4.0, 3000, "VFR marginal"),
    (10.0, 800, "VFR marginal"),
    (2.0, 3000, "IFR"),
    (0.5, 3000, "IFR bajo mínimos"),
    (10.0, 100, "IFR bajo mínimos"),
])
def test_categorias_de_vuelo(vis, ceil, esperado):
    assert _compute_flight_category(vis, ceil) == esperado


def test_no_se_usan_las_siglas_de_la_faa():
    """El proyecto usa etiquetas OACI en espanol, no MVFR/LIFR."""
    todas = {_compute_flight_category(v, c)
             for v in (0.5, 2.0, 4.0, 10.0) for c in (100, 800, 3000)}
    assert not ({"MVFR", "LIFR"} & todas)


# ── Parsing ───────────────────────────────────────────────────────────────────

def test_visibilidad_metar():
    assert _parse_visibility_km("9999") == 10.0        # CAVOK
    assert _parse_visibility_km("6000") == 6.0
    assert _parse_visibility_km("10SM") == pytest.approx(18.52, abs=0.01)
    assert _parse_visibility_km(None) is None


def test_solo_bkn_ovc_constituyen_techo():
    assert _extract_ceiling_ft([{"cover": "FEW", "base_ft": 1000},
                                {"cover": "SCT", "base_ft": 2000}]) is None
    assert _extract_ceiling_ft([{"cover": "SCT", "base_ft": 1000},
                                {"cover": "BKN", "base_ft": 2500}]) == 2500


# ── Base de nubes NWP (regla de Espy) ─────────────────────────────────────────

def test_aire_saturado_da_nubes_al_ras_del_suelo():
    """El caso que la constante fija de 2000 ft representaba mal."""
    assert estimate_cloud_base_ft(12.0, 12.0) <= 200


def test_la_base_crece_con_el_spread():
    bases = [estimate_cloud_base_ft(20.0, 20.0 - s) for s in (0, 2, 5, 10)]
    assert bases == sorted(bases)
    assert bases[0] < bases[-1]


def test_regla_de_espy_400_ft_por_grado():
    assert estimate_cloud_base_ft(20.0, 15.0) == pytest.approx(2000, abs=1)   # 5 C


def test_sin_temperatura_usa_el_fallback():
    assert estimate_cloud_base_ft(None, None, fallback_ft=2000) == 2000


@pytest.mark.parametrize("pct,esperado", [
    (5, None), (25, "FEW"), (50, "SCT"), (70, "BKN"), (95, "OVC"),
])
def test_cobertura_a_codigo_metar(pct, esperado):
    assert _pct_to_sky_cover(pct) == esperado


# ── Tendencia sintetizada desde NWP ───────────────────────────────────────────

class _Cat:
    def __init__(self, cat):
        self.flight_category = cat


def test_condicion_estable_no_puntua_tendencia():
    serie = [_Cat("VFR"), _Cat("VFR"), _Cat("VFR")]
    assert nwp_trend_r_taf(serie, _Cat("VFR")) == 0.0


def test_mejora_no_puntua_tendencia():
    serie = [_Cat("IFR"), _Cat("VFR marginal"), _Cat("VFR")]
    assert nwp_trend_r_taf(serie, _Cat("IFR")) == 0.0


def test_deterioro_puntua_segun_severidad():
    leve = nwp_trend_r_taf([_Cat("VFR"), _Cat("VFR marginal")], _Cat("VFR"))
    grave = nwp_trend_r_taf([_Cat("VFR"), _Cat("IFR bajo mínimos")], _Cat("VFR"))
    assert 0.0 < leve < grave <= 1.0


def test_serie_vacia_no_rompe():
    assert nwp_trend_r_taf([], None) == 0.0
