"""
MORPHY PLAN — TRIMP Calculator
Calcula TRIMP real (con HR) y TRIMP proxy (sin HR)
con ajuste por desnivel positivo para deportes de montaña.
"""
from typing import Optional


# ─────────────────────────────────────────────
# Coeficientes por tipo de actividad y terreno
# ─────────────────────────────────────────────
TRIMP_PROXY_COEF = {
    "running_flat":     0.85,
    "running_mixed":    0.95,
    "running_trail":    1.10,
    "running_ultra":    1.15,
    "cycling_road":     0.70,
    "cycling_mountain": 0.85,
    "swimming_pool":    0.95,   # natación piscina
    "swimming_open":    1.05,   # mar abierto — corrientes + traje
    "weight_training":  0.55,
    "elliptical":       0.75,
    "stairmaster":      0.90,
    "spin_bike":        0.72,
    "curved_treadmill": 0.88,
    "rowing":           0.78,   # equivalente indoor natación
    "default":          0.80,
}

# Factor de ajuste por cada 100m de desnivel positivo
ELEVATION_FACTOR_PER_100M = 0.08

# Zonas HR Karvonen → factor TRIMP Banister
# factor = e^(1.92 × intensidad_relativa)
BANISTER_FACTORS = {
    "Z1": 0.86,   # < 60% intensidad relativa
    "Z2": 1.09,   # 60-75%
    "Z3": 1.38,   # 75-85%
    "Z4": 1.75,   # 85-92%
    "Z5": 2.22,   # > 92%
}

# HR en agua es ~10-13 bpm menor que en tierra
# Se ajusta la intensidad relativa para no subestimar carga
SWIM_HR_CORRECTION = 12  # bpm a sumar antes de calcular zona


# ─────────────────────────────────────────────
# Funciones auxiliares
# ─────────────────────────────────────────────

def get_hr_zone(avg_hr: int, hr_max: int, hr_rest: int) -> str:
    """Determina zona Karvonen dado HR promedio."""
    if hr_max <= hr_rest:
        return "Z2"
    intensity = (avg_hr - hr_rest) / (hr_max - hr_rest)
    if intensity < 0.60: return "Z1"
    if intensity < 0.75: return "Z2"
    if intensity < 0.85: return "Z3"
    if intensity < 0.92: return "Z4"
    return "Z5"


def elevation_adjustment(elevation_m: float) -> float:
    """
    Factor multiplicador por desnivel acumulado.
    Cada 100m D+ añade 8% de carga adicional.
    """
    if not elevation_m or elevation_m <= 0:
        return 1.0
    return 1.0 + (elevation_m / 100) * ELEVATION_FACTOR_PER_100M


# ─────────────────────────────────────────────
# Calculadores TRIMP
# ─────────────────────────────────────────────

def calc_trimp_banister(
    duration_min: float,
    avg_hr: int,
    hr_max: int,
    hr_rest: int,
    elevation_m: float = 0.0
) -> dict:
    """
    TRIMP real usando fórmula Banister con zonas Karvonen.
    Incluye ajuste por desnivel positivo.
    """
    if hr_max <= hr_rest or hr_max == 0:
        return calc_trimp_proxy(duration_min, "running_mixed", elevation_m)

    intensity = (avg_hr - hr_rest) / (hr_max - hr_rest)
    intensity = max(0.0, min(1.0, intensity))

    zone = get_hr_zone(avg_hr, hr_max, hr_rest)
    banister_factor = BANISTER_FACTORS[zone]

    trimp_base = duration_min * intensity * banister_factor
    elev_factor = elevation_adjustment(elevation_m)
    trimp_adjusted = trimp_base * elev_factor

    return {
        "trimp_base":       round(trimp_base, 1),
        "trimp_adjusted":   round(trimp_adjusted, 1),
        "elevation_factor": round(elev_factor, 3),
        "hr_zone":          zone,
        "intensity":        round(intensity, 3),
        "method":           "banister",
    }


def calc_trimp_proxy(
    duration_min: float,
    activity_profile: str = "running_mixed",
    elevation_m: float = 0.0
) -> dict:
    """
    TRIMP proxy cuando no hay datos de HR.
    Usa coeficientes por tipo de actividad + ajuste desnivel.
    """
    coef = TRIMP_PROXY_COEF.get(activity_profile, TRIMP_PROXY_COEF["default"])
    elev_factor = elevation_adjustment(elevation_m)

    trimp_base = duration_min * coef
    trimp_adjusted = trimp_base * elev_factor

    return {
        "trimp_base":       round(trimp_base, 1),
        "trimp_adjusted":   round(trimp_adjusted, 1),
        "elevation_factor": round(elev_factor, 3),
        "hr_zone":          None,
        "intensity":        None,
        "method":           "proxy",
    }


def calc_trimp(
    duration_min: float,
    avg_hr: Optional[int],
    hr_max: int,
    hr_rest: int,
    elevation_m: float = 0.0,
    activity_type: str = "Run",
    terrain: str = "mixed"
) -> dict:
    """
    Entry point principal. Decide automáticamente
    si usar Banister (con HR) o proxy (sin HR).
    Aplica corrección HR para natación.
    """
    profile_map = {
        ("Run", "flat"):            "running_flat",
        ("Run", "mixed"):           "running_mixed",
        ("Run", "mountain"):        "running_trail",
        ("Ride", "flat"):           "cycling_road",
        ("Ride", "mixed"):          "cycling_road",
        ("Ride", "mountain"):       "cycling_mountain",
        ("Swim", "flat"):           "swimming_pool",
        ("Swim", "open"):           "swimming_open",
        ("WeightTraining", "flat"): "weight_training",
        ("Workout", "flat"):        "default",
    }
    profile = profile_map.get((activity_type, terrain), "running_mixed")

    # Corrección HR para natación — HR en agua ~12 bpm menor que en tierra
    corrected_hr = avg_hr
    if activity_type == "Swim" and avg_hr and avg_hr > 0:
        corrected_hr = avg_hr + SWIM_HR_CORRECTION

    if corrected_hr and corrected_hr > 0 and hr_max > 0:
        return calc_trimp_banister(
            duration_min, corrected_hr, hr_max, hr_rest, elevation_m
        )
    else:
        return calc_trimp_proxy(duration_min, profile, elevation_m)


# ─────────────────────────────────────────────
# Equivalencias indoor
# ─────────────────────────────────────────────

INDOOR_EQUIVALENTS = {
    "easy_flat": [
        {"equipment": "treadmill",         "duration_factor": 1.00, "setting": "0-2% incline",          "zone": "Z1-Z2"},
        {"equipment": "elliptical",        "duration_factor": 1.10, "setting": "resistencia media",     "zone": "Z1-Z2"},
        {"equipment": "spin_bike",         "duration_factor": 1.15, "setting": "cadencia 80-90 rpm",    "zone": "Z1-Z2"},
    ],
    "easy_climb": [
        {"equipment": "treadmill_incline", "duration_factor": 0.85, "setting": "8-12% incline",         "zone": "Z2"},
        {"equipment": "stairmaster",       "duration_factor": 0.70, "setting": "velocidad moderada",    "zone": "Z2"},
        {"equipment": "elliptical",        "duration_factor": 1.05, "setting": "resistencia alta",      "zone": "Z2"},
    ],
    "tempo": [
        {"equipment": "curved_treadmill",  "duration_factor": 0.85, "setting": "resistencia 3-4",       "zone": "Z3"},
        {"equipment": "treadmill",         "duration_factor": 0.90, "setting": "1% incline, umbral",    "zone": "Z3"},
        {"equipment": "spin_bike",         "duration_factor": 0.95, "setting": "intervalos 3:1",        "zone": "Z3"},
    ],
    "intervals": [
        {"equipment": "curved_treadmill",  "duration_factor": 0.80, "setting": "sprints máximos",       "zone": "Z4-Z5"},
        {"equipment": "spin_bike",         "duration_factor": 0.85, "setting": "tabata potencia max",   "zone": "Z4-Z5"},
        {"equipment": "stairmaster",       "duration_factor": 0.75, "setting": "velocidad alta",        "zone": "Z4"},
    ],
    "long_run": [
        {"equipment": "treadmill",         "duration_factor": 1.00, "setting": "0-3% incline",          "zone": "Z1-Z2"},
        {"equipment": "elliptical",        "duration_factor": 1.10, "setting": "resistencia media",     "zone": "Z1-Z2"},
    ],
    "trail_climb": [
        {"equipment": "stairmaster",       "duration_factor": 0.65, "setting": "velocidad alta",        "zone": "Z2-Z3"},
        {"equipment": "treadmill_incline", "duration_factor": 0.80, "setting": "12-15% incline",        "zone": "Z2-Z3"},
        {"equipment": "elliptical",        "duration_factor": 1.00, "setting": "resistencia máxima",    "zone": "Z2-Z3"},
    ],
    "swimming_pool": [
        {"equipment": "pool_swim",         "duration_factor": 1.00, "setting": "ritmo moderado Z2",     "zone": "Z2"},
        {"equipment": "rowing",            "duration_factor": 0.90, "setting": "resistencia media",     "zone": "Z2"},
    ],
    "swimming_open": [
        {"equipment": "pool_swim",         "duration_factor": 1.10, "setting": "ritmo sostenido + técnica", "zone": "Z2-Z3"},
        {"equipment": "rowing",            "duration_factor": 0.95, "setting": "resistencia alta",      "zone": "Z2-Z3"},
    ],
    "brick_workout": [
        {"equipment": "spin_bike",         "duration_factor": 1.00, "setting": "cadencia 85-95 rpm Z2", "zone": "Z2"},
        {"equipment": "treadmill",         "duration_factor": 0.50, "setting": "0-1% ritmo carrera",    "zone": "Z2"},
    ],
}


def get_indoor_equivalents(
    session_type: str,
    duration_min: int,
    elevation_m: int,
    available_equipment: list[str],
    activity_type: str = "Run"
) -> list[dict]:
    """
    Retorna equivalencias indoor filtradas por equipo disponible.
    Calcula duración ajustada por factor de equivalencia.
    """
    if activity_type == "Swim":
        key = "swimming_open" if session_type == "open_water" else "swimming_pool"
    elif session_type == "brick_workout":
        key = "brick_workout"
    elif elevation_m > 300:
        key = "trail_climb"
    elif elevation_m > 200 and session_type in ("easy", "long_run"):
        key = "easy_climb"
    else:
        key = session_type if session_type in INDOOR_EQUIVALENTS else "easy_flat"

    equivalents = INDOOR_EQUIVALENTS.get(key, INDOOR_EQUIVALENTS["easy_flat"])

    result = []
    for eq in equivalents:
        if available_equipment and eq["equipment"] not in available_equipment:
            continue
        result.append({
            "equipment":    eq["equipment"],
            "duration_min": round(duration_min * eq["duration_factor"]),
            "setting":      eq["setting"],
            "zone":         eq["zone"],
        })

    return result if result else equivalents[:2]  # fallback sin filtro