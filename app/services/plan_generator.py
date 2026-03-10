"""
MORPHY PLAN — Plan Generator
Algoritmo de periodización para generación de planes de entrenamiento.
Soporta: running urbano, trail, ultra, ciclismo montaña, triatlón.
"""
from datetime import date, timedelta
from typing import Optional
from dataclasses import dataclass, field

from app.services.trimp_calculator import get_indoor_equivalents


# ─────────────────────────────────────────────
# Configuración por tipo de carrera
# ─────────────────────────────────────────────

RACE_CONFIG = {
    # (ctl_min, ctl_max, total_weeks_min, total_weeks_max,
    #  taper_weeks, peak_weeks, elevation_weekly_target_m)
    "10k":              (35, 55,  8, 12, 1, 1,    0),
    "10k_trail":        (40, 60,  8, 14, 1, 1,  800),
    "21k":              (50, 68, 10, 16, 2, 1,    0),
    "21k_trail":        (55, 72, 12, 16, 2, 1, 1500),
    "marathon":         (65, 85, 16, 20, 2, 2,    0),
    "ultra_50k":        (75, 95, 16, 24, 2, 2, 3000),
    "ultra_100k":       (90,115, 20, 28, 3, 2, 5000),
    "mtb_race":         (65, 90, 12, 18, 2, 1, 2000),
    "cycling_gran_fondo":(55, 80, 12, 18, 2, 1, 1500),
    "triathlon_sprint":  (55, 72, 10, 14, 1, 1,    0),
    "triathlon_olympic": (65, 85, 14, 18, 2, 1,    0),
    "ironman":           (85,115, 20, 28, 3, 2,    0),
}

# Distribución de sesiones por fase (sesiones/semana → tipos)
SESSION_DISTRIBUTION = {
    "base": {
        3: ["easy", "easy", "long_run"],
        4: ["easy", "easy", "strength", "long_run"],
        5: ["easy", "easy", "tempo", "strength", "long_run"],
        6: ["easy", "easy", "tempo", "easy", "strength", "long_run"],
    },
    "construccion": {
        3: ["easy", "tempo", "long_run"],
        4: ["easy", "tempo", "intervals", "long_run"],
        5: ["easy", "tempo", "intervals", "easy", "long_run"],
        6: ["easy", "tempo", "intervals", "easy", "strength", "long_run"],
    },
    "pico": {
        3: ["tempo", "intervals", "long_run"],
        4: ["easy", "tempo", "intervals", "long_run"],
        5: ["easy", "tempo", "intervals", "tempo", "long_run"],
        6: ["easy", "tempo", "intervals", "tempo", "easy", "long_run"],
    },
    "tapering": {
        3: ["easy", "tempo", "easy"],
        4: ["easy", "easy", "tempo", "easy"],
        5: ["easy", "easy", "tempo", "easy", "easy"],
        6: ["easy", "easy", "tempo", "easy", "easy", "rest"],
    },
    "descarga": {
        3: ["easy", "rest", "easy"],
        4: ["easy", "rest", "easy", "rest"],
        5: ["easy", "rest", "easy", "rest", "easy"],
        6: ["easy", "rest", "easy", "rest", "easy", "rest"],
    },
}

# Zona objetivo por tipo de sesión
SESSION_ZONES = {
    "easy":       ("Z1", "Z2"),
    "tempo":      ("Z3", "Z3"),
    "intervals":  ("Z4", "Z5"),
    "long_run":   ("Z1", "Z2"),
    "trail_climb":("Z2", "Z3"),
    "strength":   ("Z2", "Z3"),
    "brick_workout": ("Z2", "Z3"),
    "rest":       (None, None),
    "cross_training": ("Z1", "Z2"),
}

# Duración base por tipo de sesión (minutos)
SESSION_DURATION = {
    "easy":       45,
    "tempo":      50,
    "intervals":  45,
    "long_run":   75,
    "trail_climb":60,
    "strength":   45,
    "brick_workout": 90,
    "rest":        0,
    "cross_training": 50,
}

# TRIMP base por tipo de sesión (sin ajuste desnivel)
SESSION_TRIMP = {
    "easy":       35,
    "tempo":      58,
    "intervals":  65,
    "long_run":   70,
    "trail_climb":55,
    "strength":   28,
    "brick_workout": 85,
    "rest":         0,
    "cross_training": 38,
}


# ─────────────────────────────────────────────
# Dataclasses de salida
# ─────────────────────────────────────────────

@dataclass
class PlannedSession:
    day_of_week: int          # 0=lunes, 6=domingo
    session_date: date
    session_type: str
    zone_target: Optional[str]
    zone_target_secondary: Optional[str]
    duration_min: int
    distance_km_target: Optional[float]
    elevation_m_target: int
    trimp_target: float
    trimp_target_adjusted: float
    indoor_equivalents: list = field(default_factory=list)


@dataclass
class PlannedWeek:
    week_number: int
    week_start_date: date
    phase: str
    trimp_target: float
    trimp_target_adjusted: float
    elevation_target_m: int
    volume_km_target: float
    volume_hours_target: float
    sessions_planned: int
    sessions: list[PlannedSession] = field(default_factory=list)


@dataclass
class GeneratedPlan:
    race_type: str
    race_date: date
    race_distance_km: float
    race_elevation_m: int
    ctl_start: float
    ctl_target: float
    total_weeks: int
    build_weeks: int
    peak_weeks: int
    taper_weeks: int
    terrain: str
    discipline: str
    weeks: list[PlannedWeek] = field(default_factory=list)


# ─────────────────────────────────────────────
# Funciones auxiliares
# ─────────────────────────────────────────────

def _calc_total_weeks(race_date: date, today: date, race_type: str) -> int:
    """Calcula semanas disponibles, respetando min/max por tipo de carrera."""
    cfg = RACE_CONFIG.get(race_type, RACE_CONFIG["21k"])
    weeks_available = (race_date - today).days // 7
    return max(cfg[2], min(cfg[3], weeks_available))


def _calc_ctl_target(race_type: str, ctl_start: float) -> float:
    """CTL objetivo basado en tipo de carrera y baseline del atleta."""
    cfg = RACE_CONFIG.get(race_type, RACE_CONFIG["21k"])
    ctl_min, ctl_max = cfg[0], cfg[1]
    # Si ya está cerca del objetivo, subimos al máximo
    if ctl_start >= ctl_min:
        return min(ctl_start * 1.15, ctl_max)
    return ctl_min + (ctl_max - ctl_min) * 0.6


def _assign_phases(total_weeks: int, peak_weeks: int, taper_weeks: int) -> list[str]:
    """
    Distribuye fases en el plan completo.
    Estructura: base → construccion → pico → tapering
    Con descargas automáticas cada 3-4 semanas de carga.
    """
    build_weeks = total_weeks - peak_weeks - taper_weeks
    phases = []
    build_count = 0

    for i in range(build_weeks):
        build_count += 1
        if build_count % 4 == 0:
            phases.append("descarga")
        elif i < build_weeks * 0.5:
            phases.append("base")
        else:
            phases.append("construccion")

    for _ in range(peak_weeks):
        phases.append("pico")

    for _ in range(taper_weeks):
        phases.append("tapering")

    return phases


def _trimp_for_week(
    phase: str,
    week_number: int,
    total_weeks: int,
    ctl_start: float,
    ctl_target: float
) -> float:
    """
    TRIMP semanal objetivo usando progresión lineal hacia CTL target.
    Las semanas de descarga = 60% del pico anterior.
    CTL_semanal ≈ TRIMP_diario × 7 / constante_decay
    """
    progress = week_number / total_weeks
    trimp_daily_target = ctl_start + (ctl_target - ctl_start) * progress

    # TRIMP semanal = promedio diario × 7 × factor de fase
    phase_factors = {
        "base":         0.75,
        "construccion": 0.90,
        "pico":         1.00,
        "tapering":     0.55,
        "descarga":     0.60,
    }
    factor = phase_factors.get(phase, 0.80)
    return round(trimp_daily_target * 7 * factor, 1)


def _elevation_for_week(
    phase: str,
    week_number: int,
    total_weeks: int,
    race_type: str,
    terrain: str
) -> int:
    """D+ objetivo semanal según fase y tipo de carrera."""
    cfg = RACE_CONFIG.get(race_type, RACE_CONFIG["21k"])
    elevation_weekly_max = cfg[6]

    if elevation_weekly_max == 0 or terrain == "flat":
        return 0

    progress = week_number / total_weeks
    phase_factors = {
        "base":         0.50,
        "construccion": 0.75,
        "pico":         1.00,
        "tapering":     0.40,
        "descarga":     0.30,
    }
    factor = phase_factors.get(phase, 0.60)
    return int(elevation_weekly_max * progress * factor)


def _build_session(
    session_type: str,
    session_date: date,
    day_of_week: int,
    phase: str,
    week_number: int,
    total_weeks: int,
    terrain: str,
    race_type: str,
    weekly_elevation_m: int,
    available_equipment: list[str],
    sessions_in_week: int,
    session_index: int,
) -> PlannedSession:
    """Construye una sesión individual con todos sus parámetros."""

    # Adaptar tipo de sesión para trail/montaña
    if terrain in ("mountain", "mixed") and session_type == "long_run":
        if weekly_elevation_m > 500 and phase in ("construccion", "pico"):
            session_type = "trail_climb"

    # Para triatlón, convertir algunos easy en brick en fase pico
    if race_type in ("triathlon_olympic", "ironman") and phase == "pico":
        if session_type == "long_run":
            session_type = "brick_workout"

    zone_primary, zone_secondary = SESSION_ZONES.get(session_type, ("Z2", "Z2"))
    duration = SESSION_DURATION.get(session_type, 45)
    trimp_base = SESSION_TRIMP.get(session_type, 35)

    # Progresión de carga: sesiones crecen hasta semana pico
    progress = min(1.0, week_number / (total_weeks * 0.8))
    phase_multipliers = {
        "base":         0.75 + progress * 0.15,
        "construccion": 0.90 + progress * 0.10,
        "pico":         1.00,
        "tapering":     0.60,
        "descarga":     0.55,
    }
    multiplier = phase_multipliers.get(phase, 0.85)
    duration = round(duration * multiplier)
    trimp_target = round(trimp_base * multiplier, 1)

    # D+ por sesión: distribuido entre sesiones de carga
    elevation_per_session = 0
    if session_type in ("long_run", "trail_climb", "easy") and weekly_elevation_m > 0:
        load_sessions = max(1, sessions_in_week - 1)  # excluye rest/strength
        elevation_per_session = weekly_elevation_m // load_sessions

    # TRIMP ajustado por desnivel
    from app.services.trimp_calculator import elevation_adjustment
    elev_factor = elevation_adjustment(elevation_per_session)
    trimp_adjusted = round(trimp_target * elev_factor, 1)

    # Distancia estimada (solo para running)
    distance_km = None
    if session_type not in ("strength", "rest", "brick_workout"):
        pace_factor = {"Z1": 7.5, "Z2": 6.5, "Z3": 5.5, "Z4": 4.5}.get(zone_primary, 6.5)
        distance_km = round(duration / pace_factor, 1) if pace_factor > 0 else None

    # Equivalencias indoor
    indoor_eq = get_indoor_equivalents(
        session_type, duration, elevation_per_session,
        available_equipment, "Run"
    )

    return PlannedSession(
        day_of_week=day_of_week,
        session_date=session_date,
        session_type=session_type,
        zone_target=zone_primary,
        zone_target_secondary=zone_secondary,
        duration_min=duration,
        distance_km_target=distance_km,
        elevation_m_target=elevation_per_session,
        trimp_target=trimp_target,
        trimp_target_adjusted=trimp_adjusted,
        indoor_equivalents=indoor_eq,
    )


# ─────────────────────────────────────────────
# Generador principal
# ─────────────────────────────────────────────

def generate_plan(
    race_type: str,
    race_date: date,
    race_distance_km: float,
    race_elevation_m: int,
    ctl_start: float,
    weekly_days_available: int,
    terrain: str,
    discipline: str,
    available_equipment: list[str],
    today: Optional[date] = None,
    long_run_day: int = 6,       # 6 = domingo por defecto
    target_time_minutes: Optional[int] = None,
) -> GeneratedPlan:
    """
    Genera un plan de entrenamiento completo.

    Args:
        race_type:            tipo de carrera (ver RACE_CONFIG)
        race_date:            fecha de competencia
        race_distance_km:     distancia total de la carrera
        race_elevation_m:     D+ total de la carrera
        ctl_start:            CTL actual del atleta (de ENGINE)
        weekly_days_available: días disponibles por semana (3-6)
        terrain:              'flat' | 'mixed' | 'mountain'
        discipline:           'running' | 'cycling' | 'triathlon' | etc.
        available_equipment:  lista de equipos de gimnasio disponibles
        today:                fecha de inicio (default: hoy)
        long_run_day:         día del fondo (0=lun...6=dom)
        target_time_minutes:  tiempo objetivo en carrera (opcional)
    """
    if today is None:
        today = date.today()

    # Parámetros del plan
    total_weeks = _calc_total_weeks(race_date, today, race_type)
    ctl_target = _calc_ctl_target(race_type, ctl_start)
    cfg = RACE_CONFIG.get(race_type, RACE_CONFIG["21k"])
    taper_weeks = cfg[4]
    peak_weeks = cfg[5]
    build_weeks = total_weeks - peak_weeks - taper_weeks

    phases = _assign_phases(total_weeks, peak_weeks, taper_weeks)
    days_available = max(3, min(6, weekly_days_available))

    plan = GeneratedPlan(
        race_type=race_type,
        race_date=race_date,
        race_distance_km=race_distance_km,
        race_elevation_m=race_elevation_m,
        ctl_start=ctl_start,
        ctl_target=round(ctl_target, 1),
        total_weeks=total_weeks,
        build_weeks=build_weeks,
        peak_weeks=peak_weeks,
        taper_weeks=taper_weeks,
        terrain=terrain,
        discipline=discipline,
    )

    # ── Construir semanas ────────────────────────────────────
    for w_idx, phase in enumerate(phases):
        week_num = w_idx + 1
        week_start = today + timedelta(weeks=w_idx)

        trimp_weekly = _trimp_for_week(
            phase, week_num, total_weeks, ctl_start, ctl_target
        )
        elevation_weekly = _elevation_for_week(
            phase, week_num, total_weeks, race_type, terrain
        )

        # Tipos de sesión para esta semana
        dist = SESSION_DISTRIBUTION.get(phase, SESSION_DISTRIBUTION["base"])
        session_types = dist.get(days_available, dist[4])

        # Distribuir sesiones en la semana
        # Long run siempre en long_run_day, resto distribuido
        sessions = []
        available_days = list(range(7))
        assigned_days = []

        # Primero asignar el día largo (domingo por defecto)
        long_idx = session_types.index("long_run") if "long_run" in session_types else -1
        if long_idx >= 0:
            assigned_days.append(long_run_day)
        
        # Resto de días, evitando el día largo y días consecutivos cuando sea posible
        other_days = [d for d in available_days if d != long_run_day]
        step = max(1, len(other_days) // max(1, len(session_types) - 1))
        day_cursor = 0
        for i, stype in enumerate(session_types):
            if stype == "long_run":
                continue
            day = other_days[min(day_cursor, len(other_days) - 1)]
            assigned_days.insert(i, day)
            day_cursor += step

        # Ordenar días y crear sesiones
        assigned_days_sorted = sorted(set(assigned_days[:len(session_types)]))
        # Rellenar si faltan días
        while len(assigned_days_sorted) < len(session_types):
            for d in range(7):
                if d not in assigned_days_sorted:
                    assigned_days_sorted.append(d)
                    break
            assigned_days_sorted.sort()

        for s_idx, stype in enumerate(session_types):
            day_of_week = assigned_days_sorted[s_idx] if s_idx < len(assigned_days_sorted) else s_idx
            session_date = week_start + timedelta(days=day_of_week)

            session = _build_session(
                session_type=stype,
                session_date=session_date,
                day_of_week=day_of_week,
                phase=phase,
                week_number=week_num,
                total_weeks=total_weeks,
                terrain=terrain,
                race_type=race_type,
                weekly_elevation_m=elevation_weekly,
                available_equipment=available_equipment,
                sessions_in_week=len(session_types),
                session_index=s_idx,
            )
            sessions.append(session)

        # Calcular totales reales de la semana
        total_trimp = sum(s.trimp_target for s in sessions)
        total_trimp_adj = sum(s.trimp_target_adjusted for s in sessions)
        total_km = sum(s.distance_km_target or 0 for s in sessions)
        total_hours = sum(s.duration_min for s in sessions) / 60

        week = PlannedWeek(
            week_number=week_num,
            week_start_date=week_start,
            phase=phase,
            trimp_target=round(total_trimp, 1),
            trimp_target_adjusted=round(total_trimp_adj, 1),
            elevation_target_m=elevation_weekly,
            volume_km_target=round(total_km, 1),
            volume_hours_target=round(total_hours, 1),
            sessions_planned=len([s for s in sessions if s.session_type != "rest"]),
            sessions=sessions,
        )
        plan.weeks.append(week)

    return plan