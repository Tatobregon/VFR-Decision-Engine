"""
aircraft_profiles.py
====================
Perfiles operacionales de aeronaves. Define los limites fisicos y regulatorios
que usa el risk engine para calcular hard blockers y el soft scoring,
y los parametros de performance que usa el route optimizer.

Aeronaves disponibles:
  - Pipistrel Alpha Trainer (LSA)
  - Cessna 172 Skyhawk (SEP)
  - Piper PA-28 Cherokee / Archer (SEP)
  - Cessna 152 (SEP)
  - Diamond DA40 (SEP)

Datos de performance basados en POH / AFM a 75% potencia, altitudes tipicas
de Cordoba (~5000-7000 ft MSL), condiciones ISA.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AircraftProfile:
    """
    Parametros operacionales de una aeronave para el motor de decision VFR.

    Todos los limites son los valores publicados en el POH / AFM del fabricante.
    El risk engine los usa como referencia: superar un limite no es
    automaticamente NO GO (depende del scoring), pero se usa como ancla
    para normalizar los scores.

    Los campos de performance (fuel_flow_lph, fuel_capacity_l, fuel_reserve_min)
    son utilizados por el route optimizer para calcular combustible y rango.
    """
    name               : str    # Nombre identificador del modelo

    # Limites de viento (kt)
    # NOTA: el "maximum demonstrated crosswind component" del POH NO es un limite
    # operacional legal, sino el mayor cruzado demostrado durante la certificacion.
    # Aqui se usa como ANCLA conservadora para normalizar el riesgo de cruzado
    # (r_xwind = xw / crosswind_max_kt), no como un techo prohibido.
    crosswind_max_kt   : float  # Componente cruzado maximo DEMOSTRADO (ancla, no limite)
    gust_max_kt        : float  # Rafaga maxima de referencia para operacion normal

    # Minimos meteorologicos VFR (ANAC Argentina)
    vis_min_km         : float  # Visibilidad minima en km
    ceiling_min_ft     : int    # Techo minimo en ft AGL

    # Velocidades de referencia (kt)
    vs0_kt             : float  # Velocidad de perdida, flaps extendidos
    cruise_kt          : float  # Velocidad de crucero tipica (75% potencia)
    cruise_alt_ft      : int    # Altitud tipica de crucero en ft MSL (para fetch NWP en ruta)
    service_ceiling_ft : int    # Techo de servicio del POH. Es un TOPE, no un crucero:
                                # acota la altitud que el piloto puede elegir a mano en VFR.

    # Performance de combustible
    fuel_flow_lph      : float  # Consumo de combustible en crucero (litros/hora)
    fuel_capacity_l    : float  # Capacidad total de combustible (litros)
    fuel_reserve_min   : int    # Reserva minima reglamentaria (minutos)

    # Clasificacion
    category           : str    # "LSA", "SEP", "MEP", etc.

    # Identificacion OACI (casilla 9 del plan de vuelo)
    icao_type          : str = "ZZZZ"   # designador OACI Doc 8643 ("ZZZZ" = no listado → va en RMK)
    wake_cat           : str = "L"       # estela turbulenta: L (ligera) / M / H / J

    @property
    def fuel_reserve_l(self) -> float:
        """Combustible de reserva en litros (calculado desde fuel_reserve_min)."""
        return self.fuel_flow_lph * (self.fuel_reserve_min / 60.0)

    @property
    def fuel_usable_l(self) -> float:
        """Combustible utilizable (capacidad total menos reserva)."""
        return self.fuel_capacity_l - self.fuel_reserve_l

    @property
    def range_km(self) -> float:
        """Alcance maximo con combustible utilizable (sin reserva) en km."""
        KT_TO_KMH = 1.852  # 1 kt = 1.852 km/h
        endurance_h = self.fuel_usable_l / self.fuel_flow_lph
        return endurance_h * self.cruise_kt * KT_TO_KMH


# ──────────────────────────────────────────────────────────────────────────────
# Perfiles disponibles
# ──────────────────────────────────────────────────────────────────────────────

ALPHA_TRAINER = AircraftProfile(
    name             = "Pipistrel Alpha Trainer",
    crosswind_max_kt = 18.0,
    gust_max_kt      = 20.0,
    vis_min_km       = 5.0,
    ceiling_min_ft   = 1000,
    vs0_kt           = 44.0,
    cruise_kt        = 97.0,
    cruise_alt_ft    = 6000,   # VFR bajo espacio controlado, planicie pampeana
    service_ceiling_ft = 18000,
    fuel_flow_lph    = 14.0,   # ~3.7 gal/hr (Rotax 912)
    fuel_capacity_l  = 50.0,   # 2 tanques x 25L
    fuel_reserve_min = 30,
    category         = "LSA",
    icao_type        = "PIAT",  # designador OACI Doc 8643 (Pipistrel Alpha Trainer)
)

CESSNA_172 = AircraftProfile(
    name             = "Cessna 172 Skyhawk",
    crosswind_max_kt = 15.0,
    gust_max_kt      = 25.0,
    vis_min_km       = 5.0,
    ceiling_min_ft   = 1000,
    vs0_kt           = 40.0,
    cruise_kt        = 110.0,
    cruise_alt_ft    = 8000,
    service_ceiling_ft = 14000,
    fuel_flow_lph    = 32.0,   # ~8.5 gal/hr (Lycoming O-360, 75% potencia)
    fuel_capacity_l  = 212.0,  # 56 gal (tanques estandar)
    fuel_reserve_min = 45,     # VFR diurno ANAC: 45 min de reserva
    category         = "SEP",
    icao_type        = "C172",
)

PIPER_PA28 = AircraftProfile(
    name             = "Piper PA-28 Cherokee",
    crosswind_max_kt = 17.0,
    gust_max_kt      = 25.0,
    vis_min_km       = 5.0,
    ceiling_min_ft   = 1000,
    vs0_kt           = 44.0,
    cruise_kt        = 108.0,
    cruise_alt_ft    = 7500,
    service_ceiling_ft = 14500,
    fuel_flow_lph    = 30.0,   # ~8.0 gal/hr (Lycoming O-360, 75% potencia)
    fuel_capacity_l  = 189.0,  # 50 gal (PA-28-181 Archer)
    fuel_reserve_min = 45,
    category         = "SEP",
    icao_type        = "P28A",
)

CESSNA_152 = AircraftProfile(
    name             = "Cessna 152",
    crosswind_max_kt = 12.0,
    gust_max_kt      = 20.0,
    vis_min_km       = 5.0,
    ceiling_min_ft   = 1000,
    vs0_kt           = 35.0,
    cruise_kt        = 90.0,
    cruise_alt_ft    = 5500,
    service_ceiling_ft = 14000,
    fuel_flow_lph    = 19.0,   # ~5.0 gal/hr (Lycoming O-235, 75% potencia)
    fuel_capacity_l  = 98.0,   # 26 gal (tanques estandar)
    fuel_reserve_min = 45,
    category         = "SEP",
    icao_type        = "C152",
)

DIAMOND_DA40 = AircraftProfile(
    name             = "Diamond DA40",
    crosswind_max_kt = 20.0,
    gust_max_kt      = 30.0,
    vis_min_km       = 5.0,
    ceiling_min_ft   = 1000,
    vs0_kt           = 48.0,
    cruise_kt        = 130.0,
    cruise_alt_ft    = 10500,
    service_ceiling_ft = 16500,
    fuel_flow_lph    = 20.0,   # ~5.3 gal/hr (Lycoming IO-360, 75% potencia)
    fuel_capacity_l  = 148.0,  # 39.1 gal (DA40-180)
    fuel_reserve_min = 45,
    category         = "SEP",
    icao_type        = "DA40",
)


# Registro central: mapeo nombre -> perfil para lookup por string
PROFILES = {
    "ALPHA_TRAINER"            : ALPHA_TRAINER,
    "Pipistrel Alpha Trainer"  : ALPHA_TRAINER,
    "CESSNA_172"               : CESSNA_172,
    "Cessna 172 Skyhawk"       : CESSNA_172,
    "PIPER_PA28"               : PIPER_PA28,
    "Piper PA-28 Cherokee"     : PIPER_PA28,
    "CESSNA_152"               : CESSNA_152,
    "Cessna 152"               : CESSNA_152,
    "DIAMOND_DA40"             : DIAMOND_DA40,
    "Diamond DA40"             : DIAMOND_DA40,
}

# Lista ordenada para la GUI (combobox)
PROFILE_NAMES = [
    "Pipistrel Alpha Trainer",
    "Cessna 172 Skyhawk",
    "Piper PA-28 Cherokee",
    "Cessna 152",
    "Diamond DA40",
]


def get_profile(name: str) -> AircraftProfile:
    """
    Devuelve el perfil de aeronave por nombre.
    Lanza KeyError si el perfil no existe.
    """
    profile = PROFILES.get(name)
    if profile is None:
        available = list(PROFILES.keys())
        raise KeyError(
            f"Perfil '{name}' no encontrado. Disponibles: {available}"
        )
    return profile


# ──────────────────────────────────────────────────────────────────────────────
# Script de prueba standalone
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TEST: aircraft_profiles.py")
    print("=" * 60)

    all_pass = True

    def check(desc, cond):
        global all_pass
        all_pass = all_pass and cond
        print(f"  [{'OK' if cond else 'FALLO'}] {desc}")

    all_profiles = [ALPHA_TRAINER, CESSNA_172, PIPER_PA28, CESSNA_152, DIAMOND_DA40]

    print()
    for p in all_profiles:
        reserve = p.fuel_reserve_l
        usable  = p.fuel_usable_l
        rng     = p.range_km
        print(f"  {p.name} ({p.category})")
        print(f"    Crucero  : {p.cruise_kt} kt   Xwind max: {p.crosswind_max_kt} kt")
        print(f"    Consumo  : {p.fuel_flow_lph} L/hr")
        print(f"    Tanques  : {p.fuel_capacity_l} L  Reserva: {reserve:.1f} L ({p.fuel_reserve_min} min)")
        print(f"    Utilizable: {usable:.1f} L   Alcance: {rng:.0f} km")
        print()

    # ── Tests Alpha Trainer (compatibilidad con v1.0) ──
    p = ALPHA_TRAINER
    check("Alpha Trainer: crosswind_max_kt = 12.0",  p.crosswind_max_kt == 12.0)
    check("Alpha Trainer: gust_max_kt = 20.0",       p.gust_max_kt == 20.0)
    check("Alpha Trainer: vis_min_km = 5.0",         p.vis_min_km == 5.0)
    check("Alpha Trainer: ceiling_min_ft = 1000",    p.ceiling_min_ft == 1000)
    check("Alpha Trainer: cruise_alt_ft = 6000",     p.cruise_alt_ft == 6000)
    check("Alpha Trainer: categoria = LSA",          p.category == "LSA")
    check("Alpha Trainer: fuel_flow_lph > 0",        p.fuel_flow_lph > 0)
    check("Alpha Trainer: fuel_capacity_l > 0",      p.fuel_capacity_l > 0)
    check("Alpha Trainer: fuel_usable_l < capacity", p.fuel_usable_l < p.fuel_capacity_l)
    check("Alpha Trainer: range_km > 0",             p.range_km > 0)

    # ── Tests nuevos perfiles ──
    check("C172: cruise_kt = 110",   CESSNA_172.cruise_kt == 110.0)
    check("C172: categoria = SEP",   CESSNA_172.category == "SEP")
    check("C172: fuel_capacity > C152", CESSNA_172.fuel_capacity_l > CESSNA_152.fuel_capacity_l)
    check("DA40: cruise_kt mas rapido que C172", DIAMOND_DA40.cruise_kt > CESSNA_172.cruise_kt)
    check("DA40: xwind_max mayor que Alpha",     DIAMOND_DA40.crosswind_max_kt > ALPHA_TRAINER.crosswind_max_kt)
    check("C152: xwind_max = 12",    CESSNA_152.crosswind_max_kt == 12.0)
    check("PA28: cruise ~108",       PIPER_PA28.cruise_kt == 108.0)

    # ── Propiedades calculadas ──
    for p in all_profiles:
        check(f"{p.name}: fuel_reserve_l > 0",       p.fuel_reserve_l > 0)
        check(f"{p.name}: fuel_usable_l > 0",        p.fuel_usable_l > 0)
        check(f"{p.name}: range_km razonable (>200)", p.range_km > 200)
        check(f"{p.name}: range_km < 3000",          p.range_km < 3000)

    # ── Lookup por nombre ──
    check("lookup ALPHA_TRAINER",           get_profile("ALPHA_TRAINER") is ALPHA_TRAINER)
    check("lookup nombre completo Alpha",   get_profile("Pipistrel Alpha Trainer") is ALPHA_TRAINER)
    check("lookup CESSNA_172",              get_profile("CESSNA_172") is CESSNA_172)
    check("lookup Cessna 172 Skyhawk",      get_profile("Cessna 172 Skyhawk") is CESSNA_172)
    check("lookup PIPER_PA28",              get_profile("PIPER_PA28") is PIPER_PA28)
    check("lookup CESSNA_152",              get_profile("CESSNA_152") is CESSNA_152)
    check("lookup DIAMOND_DA40",            get_profile("DIAMOND_DA40") is DIAMOND_DA40)

    try:
        get_profile("INEXISTENTE")
        check("KeyError para perfil inexistente", False)
    except KeyError:
        check("KeyError para perfil inexistente", True)

    # ── Inmutabilidad (frozen=True) ──
    try:
        ALPHA_TRAINER.crosswind_max_kt = 15.0  # type: ignore
        check("Perfil es inmutable (frozen)", False)
    except Exception:
        check("Perfil es inmutable (frozen)", True)

    # ── PROFILE_NAMES para GUI ──
    check("PROFILE_NAMES tiene 5 aeronaves",   len(PROFILE_NAMES) == 5)
    check("PROFILE_NAMES[0] = Alpha Trainer",  PROFILE_NAMES[0] == "Pipistrel Alpha Trainer")
    check("todos los nombres en PROFILES",
          all(n in PROFILES for n in PROFILE_NAMES))

    print("\n" + "=" * 60)
    print(f"  {'TODOS LOS TESTS PASARON' if all_pass else 'ALGUNOS TESTS FALLARON'}")
    print("=" * 60)
