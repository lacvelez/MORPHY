"""
MORPHY PACE — Generador de estrategia precarrera

Responsabilidad única: dado un atleta y una configuración de carrera,
generar una estrategia km a km que integra:
  - Estado fisiológico actual  (ENGINE: ATL, CTL, TSB)
  - Objetivo y contexto        (PLAN: fase, distancia, terreno, CTL target)
  - Perfil cardiovascular real (User: FCmax, FCrep → Karvonen)

No asume ningún valor. Todo viene del atleta concreto.
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

# ── Dependencias internas MORPHY ──────────────────────────────────────────────
from app.models.models import User
from app.services.trimp_calculator import (
    calc_karvonen_zones,      # fuente de verdad para zonas HR
    elevation_adjustment,     # ajuste de esfuerzo por desnivel
)
from app.services.plan_context import get_today_plan_context  # PLAN
from app.services.pace_intelligence import RaceTargets         # PACE

log = logging.getLogger(__name__)


# ─── Modelos de salida ────────────────────────────────────────────────────────

@dataclass
class KmTarget:
    km: int
    pace_min_km: float          # pace objetivo en min/km
    hr_zone: int                # zona HR objetivo (1–5)
    hr_min: int                 # FC mínima de esa zona para este atleta
    hr_max: int                 # FC máxima de esa zona para este atleta
    notes: str = ""             # ej: "subida técnica", "acelerar aquí"


@dataclass
class RaceStrategy:
    """
    Estrategia completa generada para una carrera.
    Se almacena en race_sessions.strategy (JSONB).
    """
    # Contexto del atleta en este momento
    atl: float
    ctl: float
    tsb: float
    readiness_score: float

    # Zonas personalizadas (Karvonen real)
    hr_zones: dict[int, tuple[int, int]]   # {1: (117, 132), 2: (132, 148), ...}

    # Estrategia km a km
    km_targets: list[KmTarget]

    # Pacing global
    pacing_type: str            # "negativo" | "uniforme" | "conservador"
    estimated_finish_sec: int   # tiempo estimado en segundos

    # Alertas de hidratación/nutrición
    hydration_plan: list[dict]  # [{km: 10, message: "Primera hidratación"}, ...]

    # Mensaje de apertura para TTS
    opening_message: str

    # Diagnóstico para logging
    strategy_source: str        # "plan_activo" | "config_manual" | "sin_plan"


# ─── Constantes de estrategia ─────────────────────────────────────────────────

# TSB que define el estado del atleta hoy
TSB_RESTED_THRESHOLD   =  10   # TSB > 10 → descansado, estrategia negativa posible
TSB_FATIGUED_THRESHOLD = -10   # TSB < -10 → fatigado, estrategia conservadora

# Factor de ajuste de pace según estado (multiplicador sobre pace base)
PACE_FACTOR_RESTED    = 0.97   # 3% más rápido que pace base
PACE_FACTOR_NORMAL    = 1.00
PACE_FACTOR_FATIGUED  = 1.04   # 4% más lento que pace base

# Zona HR objetivo por defecto según tipo de carrera
DEFAULT_ZONE_BY_RACE = {
    "trail_21k":  2,    # mayormente Z2 con picos en Z3
    "trail_50k":  2,
    "trail_100k": 1,
    "road_10k":   3,
    "road_21k":   3,
    "road_42k":   2,
    "urban_5k":   4,
}

# Hidratación base (km) — ajustable por temperatura en versiones futuras
HYDRATION_KM_INTERVAL = 10
NUTRITION_KM_THRESHOLD = 15    # gel cada 15 km en distancias > 21K


# ─── Función principal ────────────────────────────────────────────────────────

async def generate_race_strategy(
    athlete: User,
    race_type: str,
    distance_km: float,
    terrain: str,                     # "flat" | "mixed" | "mountain"
    time_goal_sec: Optional[int],     # None = MORPHY estima el tiempo
    db: AsyncSession,
) -> RaceStrategy:
    """
    Genera la estrategia de carrera personalizada para este atleta hoy.

    Consume ENGINE (estado fisiológico) y PLAN (contexto del macrociclo).
    Todos los cálculos de HR usan FCmax y FCrep reales del atleta.
    """

    # ── Validación de perfil ──────────────────────────────────────────────────
    _validate_athlete_profile(athlete)

    # ── 1. Zonas HR reales (Karvonen) ─────────────────────────────────────────
    hr_zones = calc_karvonen_zones(
        max_hr=athlete.max_hr,
        rest_hr=athlete.rest_hr,
    )
    log.info(
        "Zonas calculadas para %s: FCmax=%d FCrep=%d → Z2=%s Z4=%s Z5=%s",
        athlete.name, athlete.max_hr, athlete.rest_hr,
        hr_zones[2], hr_zones[4], hr_zones[5],
    )

    # ── 2. Estado fisiológico actual (ENGINE) ─────────────────────────────────
    atl, ctl, tsb = await _get_athlete_state(athlete.id, db)
    readiness = _calc_readiness(tsb, atl, ctl)

    # ── 3. Contexto del plan activo (PLAN) ────────────────────────────────────
    plan_ctx = await get_today_plan_context(str(user_id), db)
    strategy_source = "plan_activo" if plan_ctx else "sin_plan"

    # ── 4. Pace base ──────────────────────────────────────────────────────────
    if time_goal_sec:
        base_pace = time_goal_sec / 60 / distance_km   # min/km
    elif plan_ctx and plan_ctx.get("target_pace_min_km"):
        base_pace = plan_ctx["target_pace_min_km"]
        strategy_source = "plan_activo"
    else:
        # Sin objetivo: estimar desde CTL del atleta
        base_pace = _estimate_pace_from_ctl(ctl, distance_km, terrain)
        strategy_source = "estimado_ctl"

    # ── 5. Ajuste de pace por estado fisiológico (TSB) ────────────────────────
    pace_factor = _pace_factor_from_tsb(tsb)
    adjusted_pace = base_pace * pace_factor

    # ── 6. Tipo de pacing según TSB ───────────────────────────────────────────
    pacing_type = _select_pacing_type(tsb, plan_ctx)

    # ── 7. Zona HR objetivo según tipo de carrera ─────────────────────────────
    target_zone = DEFAULT_ZONE_BY_RACE.get(race_type, 2)
    # Si el atleta está fatigado, bajar un nivel la zona objetivo
    if tsb < TSB_FATIGUED_THRESHOLD and target_zone > 1:
        target_zone -= 1
        log.info("TSB bajo (%.1f) — zona objetivo bajada a Z%d", tsb, target_zone)

    # ── 8. Estrategia km a km ─────────────────────────────────────────────────
    km_targets = _build_km_targets(
        distance_km=distance_km,
        adjusted_pace=adjusted_pace,
        pacing_type=pacing_type,
        target_zone=target_zone,
        hr_zones=hr_zones,
        terrain=terrain,
        plan_ctx=plan_ctx,
    )

    # ── 9. Plan de hidratación/nutrición ──────────────────────────────────────
    hydration_plan = _build_hydration_plan(distance_km, terrain)

    # ── 10. Tiempo estimado de llegada ────────────────────────────────────────
    estimated_finish_sec = int(
        sum(t.pace_min_km * 60 for t in km_targets) / len(km_targets) * distance_km
    )

    # ── 11. Mensaje de apertura (TTS) ─────────────────────────────────────────
    opening_message = _build_opening_message(
        tsb=tsb,
        readiness=readiness,
        pacing_type=pacing_type,
        adjusted_pace=adjusted_pace,
        target_zone=target_zone,
        hr_zones=hr_zones,
        plan_ctx=plan_ctx,
    )

    # ── 12. RaceTargets para pace_intelligence ────────────────────────────────
    # (se retorna en strategy para que el router lo construya al iniciar sesión)
    zone_lo, zone_hi = hr_zones[target_zone]
    z5_min, _ = hr_zones[5]

    return RaceStrategy(
        atl=atl,
        ctl=ctl,
        tsb=tsb,
        readiness_score=readiness,
        hr_zones=hr_zones,
        km_targets=km_targets,
        pacing_type=pacing_type,
        estimated_finish_sec=estimated_finish_sec,
        hydration_plan=hydration_plan,
        opening_message=opening_message,
        strategy_source=strategy_source,
    )


# ─── Helpers internos ─────────────────────────────────────────────────────────

def _validate_athlete_profile(athlete: User) -> None:
    """
    Falla rápido si faltan datos fisiológicos del atleta.
    No usar 220-edad como fallback silencioso — eso produce zonas incorrectas.
    """
    if not athlete.max_hr or not athlete.rest_hr:
        raise ValueError(
            "FCmax y FCrep son requeridas para generar una estrategia personalizada. "
            f"Atleta {athlete.id}: max_hr={athlete.max_hr}, rest_hr={athlete.rest_hr}. "
            "Completa tu perfil antes de iniciar MORPHY PACE."
        )
    if athlete.max_hr <= athlete.rest_hr:
        raise ValueError(
            f"FCmax ({athlete.max_hr}) debe ser mayor que FCrep ({athlete.rest_hr})."
        )
    if athlete.rest_hr < 30 or athlete.rest_hr > 100:
        log.warning(
            "FCrep fuera de rango típico: %d bpm. Verificar perfil del atleta %d.",
            athlete.rest_hr, athlete.id
        )


async def _get_athlete_state(user_id: int, db: AsyncSession) -> tuple[float, float, float]:
    """
    Lee ATL, CTL, TSB del ENGINE.
    Reutiliza calculate_athlete_state() de decision.py — no duplicar cálculo.
    """
    from sqlalchemy import select
    from app.models.models import Activity
    from app.routers.decision import calculate_athlete_state

    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    # Obtener max_hr y rest_hr del atleta para pasarlos al ENGINE
    from app.models.models import User
    user_result = await db.execute(select(User).where(User.id == user_id))
    athlete = user_result.scalar_one()

    state = calculate_athlete_state(
        activities=activities,
        max_hr=float(athlete.max_hr or 182),
        rest_hr=float(athlete.rest_hr or 50),
    )
    return state["acute_load_atl"], state["chronic_load_ctl"], state["stress_balance_tsb"]


def _calc_readiness(tsb: float, atl: float, ctl: float) -> float:
    """
    Misma fórmula que engine — readiness 0-100.
    TSB positivo + ATL baja + CTL alta = readiness alta.
    """
    tsb_score    = min(100, max(0, 50 + tsb * 1.5))
    fatigue_pen  = min(30, atl * 0.5)
    fitness_bon  = min(20, ctl * 0.3)
    return round(min(100, tsb_score - fatigue_pen + fitness_bon), 1)


def _pace_factor_from_tsb(tsb: float) -> float:
    if tsb >= TSB_RESTED_THRESHOLD:
        return PACE_FACTOR_RESTED
    elif tsb <= TSB_FATIGUED_THRESHOLD:
        return PACE_FACTOR_FATIGUED
    else:
        # Interpolación lineal entre fatigado y descansado
        t = (tsb - TSB_FATIGUED_THRESHOLD) / (TSB_RESTED_THRESHOLD - TSB_FATIGUED_THRESHOLD)
        return PACE_FACTOR_FATIGUED + t * (PACE_FACTOR_RESTED - PACE_FACTOR_FATIGUED)


def _select_pacing_type(tsb: float, plan_ctx: Optional[dict]) -> str:
    """
    Negativo: segunda mitad más rápida (atleta descansado, plan lo permite).
    Uniforme: ritmo constante (estado normal).
    Conservador: primera mitad más lenta (atleta fatigado o distancia > 42K).
    """
    if plan_ctx and plan_ctx.get("phase") in ("tapering", "descarga"):
        return "negativo"   # peaking → el atleta está fresco para una carrera
    if tsb >= TSB_RESTED_THRESHOLD:
        return "negativo"
    elif tsb <= TSB_FATIGUED_THRESHOLD:
        return "conservador"
    return "uniforme"


def _estimate_pace_from_ctl(ctl: float, distance_km: float, terrain: str) -> float:
    """
    Estima pace base desde CTL del atleta cuando no hay tiempo objetivo.
    CTL alto → atleta más en forma → pace más rápido.
    Escala inversa: CTL 80 ≈ pace 4:30/km, CTL 30 ≈ pace 6:00/km para 21K.

    Es una estimación — pace_strategist prioriza siempre el time_goal del atleta.
    """
    base = 7.5 - (ctl / 80) * 2.5      # rango aprox 5.0–7.5 min/km
    terrain_factor = {"flat": 1.0, "mixed": 1.05, "mountain": 1.15}
    distance_factor = 1.0 + (distance_km / 100) * 0.3   # distancia más larga → pace más conservador
    return round(base * terrain_factor.get(terrain, 1.05) * distance_factor, 2)


def _build_km_targets(
    distance_km: float,
    adjusted_pace: float,
    pacing_type: str,
    target_zone: int,
    hr_zones: dict,
    terrain: str,
    plan_ctx: Optional[dict],
) -> list[KmTarget]:
    """
    Genera target de pace y zona HR por cada km de la carrera.

    Pacing negativo: primeros 40% del recorrido a pace_factor 1.03,
                     últimos 30% a pace_factor 0.97 (más rápido).
    Conservador:     primeros 50% a 1.04, segunda mitad a 1.00.
    Uniforme:        pace constante todo el recorrido.
    """
    total_km = math.ceil(distance_km)
    hr_min, hr_max = hr_zones[target_zone]
    targets = []

    for km in range(1, total_km + 1):
        progress = km / total_km

        # Pace ajustado según estrategia de pacing
        if pacing_type == "negativo":
            if progress <= 0.40:
                km_pace = adjusted_pace * 1.03
                notes = "salida conservadora"
            elif progress <= 0.70:
                km_pace = adjusted_pace * 1.00
                notes = ""
            else:
                km_pace = adjusted_pace * 0.97
                notes = "puedes acelerar"
        elif pacing_type == "conservador":
            if progress <= 0.50:
                km_pace = adjusted_pace * 1.04
                notes = "primera mitad controlada"
            else:
                km_pace = adjusted_pace * 1.00
                notes = ""
        else:   # uniforme
            km_pace = adjusted_pace
            notes = ""

        # Ajuste de zona por terreno en segmentos de montaña
        # (en versiones futuras esto usará el perfil de elevación real de la ruta)
        km_zone = target_zone
        if terrain == "mountain" and 0.30 < progress < 0.70:
            km_zone = min(target_zone + 1, 4)   # zona +1 en zona central de montaña
            hr_min, hr_max = hr_zones[km_zone]
            notes = notes or "segmento de montaña"

        # Último km: zona puede subir si hay margen
        if progress > 0.95 and pacing_type in ("negativo", "uniforme"):
            km_zone = min(target_zone + 1, 5)
            hr_min, hr_max = hr_zones[km_zone]
            notes = "último km, puedes dar todo"

        targets.append(KmTarget(
            km=km,
            pace_min_km=round(km_pace, 2),
            hr_zone=km_zone,
            hr_min=hr_min,
            hr_max=hr_max,
            notes=notes,
        ))

    return targets


def _build_hydration_plan(distance_km: float, terrain: str) -> list[dict]:
    """
    Plan base de hidratación/nutrición.
    En versiones futuras: ajustar por temperatura y tasa de sudoración del atleta.
    """
    plan = []
    km = HYDRATION_KM_INTERVAL
    while km < distance_km:
        plan.append({"km": km, "type": "hidratacion", "message": f"Km {km}. Momento de hidratarte."})
        km += HYDRATION_KM_INTERVAL

    if distance_km > NUTRITION_KM_THRESHOLD:
        gel_km = 15
        while gel_km < distance_km - 5:
            plan.append({"km": gel_km, "type": "nutricion", "message": f"Km {gel_km}. Gel o carbohidrato."})
            gel_km += 15

    return sorted(plan, key=lambda x: x["km"])


def _build_opening_message(
    tsb: float,
    readiness: float,
    pacing_type: str,
    adjusted_pace: float,
    target_zone: int,
    hr_zones: dict,
    plan_ctx: Optional[dict],
) -> str:
    """
    Mensaje TTS al inicio de la carrera.
    Tono neutro-informativo. Sin motivación, sin alarmas.
    """
    pace_str = _format_pace(adjusted_pace)
    hr_min, hr_max = hr_zones[target_zone]

    state_desc = (
        "Estado: descansado."   if tsb >= TSB_RESTED_THRESHOLD else
        "Estado: fatigado."     if tsb <= TSB_FATIGUED_THRESHOLD else
        "Estado: normal."
    )

    pacing_desc = {
        "negativo":    f"Estrategia negativa. Primeros kilómetros a {pace_str}, aceleras en la segunda mitad.",
        "conservador": f"Estrategia conservadora. Sal a {pace_str} y mantén.",
        "uniforme":    f"Estrategia uniforme. Pace objetivo {pace_str} todo el recorrido.",
    }.get(pacing_type, f"Pace objetivo {pace_str}.")

    zone_desc = f"Zona {target_zone} objetivo. {hr_min} a {hr_max} pulsaciones."

    plan_desc = ""
    if plan_ctx:
        phase = plan_ctx.get("phase", "")
        phase_labels = {
            "base": "Fase base.",
            "construccion": "Fase construcción.",
            "pico": "Fase pico. Estás listo.",
            "tapering": "Tapering activo. Piernas frescas.",
            "descarga": "Semana de descarga.",
        }
        plan_desc = phase_labels.get(phase, "")

    return " ".join(filter(None, [state_desc, plan_desc, pacing_desc, zone_desc]))


def _format_pace(pace_min_km: float) -> str:
    """Convierte 5.25 min/km a '5:15 por kilómetro'."""
    minutes = int(pace_min_km)
    seconds = int((pace_min_km - minutes) * 60)
    return f"{minutes}:{seconds:02d} por kilómetro"


# ─── Helper para construir RaceTargets desde la estrategia ───────────────────

def build_race_targets(strategy: RaceStrategy, target_zone: int) -> RaceTargets:
    """
    Construye el objeto RaceTargets que pace_intelligence necesita.
    Llamar desde el router después de generate_race_strategy().
    """
    hr_min, hr_max = strategy.hr_zones[target_zone]
    z5_min, _      = strategy.hr_zones[5]

    pace_vals = [t.pace_min_km for t in strategy.km_targets]

    return RaceTargets(
        hr_zone_min=hr_min,
        hr_zone_max=hr_max,
        hr_zone5_min=z5_min,
        pace_min=max(pace_vals),    # pace más lento permitido
        pace_max=min(pace_vals),    # pace más rápido permitido
    )