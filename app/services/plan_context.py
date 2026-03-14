"""
MORPHY — Plan Context Service
Provee contexto del plan activo al ENGINE para enriquecer decisiones.
El ENGINE decide el 'qué'. El PLAN decide el 'cuánto' y 'cómo'.
"""
from datetime import date
from typing import Optional
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


@dataclass
class TodayPlanContext:
    has_plan: bool
    # Sesión de hoy
    session_type: Optional[str]       # 'easy' | 'tempo' | 'long_run' | 'rest' | etc.
    zone_target: Optional[str]        # 'Z1' | 'Z2' | 'Z3' | 'Z4' | 'Z5'
    duration_min: Optional[int]
    trimp_target: Optional[float]
    elevation_m_target: Optional[int]
    indoor_equivalents: Optional[list]
    # Contexto de semana
    week_number: Optional[int]
    week_phase: Optional[str]         # 'base' | 'construccion' | 'pico' | 'tapering' | 'descarga'
    week_trimp_target: Optional[float]
    # Contexto de plan
    race_name: Optional[str]
    race_date: Optional[date]
    race_type: Optional[str]
    weeks_to_race: Optional[int]


PHASE_LABELS = {
    "base": "Base",
    "construccion": "Construcción",
    "pico": "Pico",
    "tapering": "Tapering",
    "descarga": "Descarga",
}

SESSION_LABELS = {
    "easy":          "Rodaje suave",
    "tempo":         "Tempo / Umbral",
    "intervals":     "Intervalos",
    "long_run":      "Fondo largo",
    "trail_climb":   "Subida trail",
    "strength":      "Fuerza",
    "brick_workout": "Brick (ciclo + carrera)",
    "rest":          "Descanso",
    "cross_training":"Cross training",
}


async def get_today_plan_context(
    user_id: str,
    db: AsyncSession,
) -> TodayPlanContext:
    """
    Obtiene el contexto del plan para hoy.
    Retorna TodayPlanContext con has_plan=False si no hay plan activo.
    """
    today = date.today()

    # 1. Plan activo
    plan_result = await db.execute(text("""
        SELECT id, race_name, race_type, race_date, total_weeks
        FROM training_plans
        WHERE user_id = :uid AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
    """), {"uid": user_id})
    plan = plan_result.fetchone()

    if not plan:
        return TodayPlanContext(has_plan=False, **{k: None for k in [
            "session_type","zone_target","duration_min","trimp_target",
            "elevation_m_target","indoor_equivalents","week_number",
            "week_phase","week_trimp_target","race_name","race_date",
            "race_type","weeks_to_race"
        ]})

    # 2. Semana actual
    week_result = await db.execute(text("""
        SELECT id, week_number, phase, trimp_target_adjusted
        FROM plan_weeks
        WHERE plan_id = :pid
          AND week_start_date <= :today
          AND week_start_date + INTERVAL '6 days' >= :today
        LIMIT 1
    """), {"pid": str(plan.id), "today": today})
    week = week_result.fetchone()

    # 3. Sesión de hoy
    session = None
    if week:
        session_result = await db.execute(text("""
            SELECT session_type, zone_target, duration_min,
                   trimp_target_adjusted, elevation_m_target,
                   indoor_equivalents
            FROM plan_sessions
            WHERE week_id = :wid
              AND session_date = :today
            LIMIT 1
        """), {"wid": str(week.id), "today": today})
        session = session_result.fetchone()

    # Semanas hasta la carrera
    weeks_to_race = None
    if plan.race_date:
        delta = (plan.race_date - today).days
        weeks_to_race = max(0, delta // 7)

    return TodayPlanContext(
        has_plan=True,
        session_type=session.session_type if session else None,
        zone_target=session.zone_target if session else None,
        duration_min=session.duration_min if session else None,
        trimp_target=float(session.trimp_target_adjusted) if session and session.trimp_target_adjusted else None,
        elevation_m_target=session.elevation_m_target if session else None,
        indoor_equivalents=session.indoor_equivalents if session else [],
        week_number=week.week_number if week else None,
        week_phase=week.phase if week else None,
        week_trimp_target=float(week.trimp_target_adjusted) if week and week.trimp_target_adjusted else None,
        race_name=plan.race_name,
        race_date=plan.race_date,
        race_type=plan.race_type,
        weeks_to_race=weeks_to_race,
    )


def enrich_decision_with_plan(
    decision: dict,
    ctx: TodayPlanContext,
    state: dict,
) -> dict:
    """
    Enriquece la decisión del ENGINE con contexto del PLAN.
    NO modifica el action. Solo enriquece suggestions y reasoning.
    """
    if not ctx.has_plan or not ctx.session_type:
        return decision

    action = decision["action"]
    phase_label = PHASE_LABELS.get(ctx.week_phase, ctx.week_phase or "")
    session_label = SESSION_LABELS.get(ctx.session_type, ctx.session_type or "")
    tsb = state.get("stress_balance_tsb", 0)
    acwr = state.get("acwr", 1.0)

    # ── Construir contexto de plan para el reasoning ──────────
    plan_context_line = ""
    if ctx.race_name and ctx.weeks_to_race is not None:
        plan_context_line = (
            f"Según tu plan para **{ctx.race_name}** "
            f"({ctx.weeks_to_race} semanas restantes, fase {phase_label}), "
        )
    elif ctx.week_phase:
        plan_context_line = f"Estás en semana {ctx.week_number} — fase {phase_label}. "

    # ── Sugerencias específicas según acción + sesión planeada ──
    new_suggestions = []

    if action in ("maintain", "increase"):
        # Estado OK → ejecuta lo planeado
        new_suggestions.append(
            f"📋 Sesión planeada: {session_label} "
            f"{'en ' + ctx.zone_target if ctx.zone_target else ''} "
            f"— {ctx.duration_min} min "
            f"(TRIMP objetivo: {ctx.trimp_target})"
            + (f" · {ctx.elevation_m_target}m D+" if ctx.elevation_m_target else "")
        )
        if ctx.zone_target in ("Z3", "Z4", "Z5"):
            new_suggestions.append(
                f"Tu TSB es {tsb} — condiciones óptimas para ejecutar {session_label.lower()} completo"
            )
        else:
            new_suggestions.append(
                f"Mantén FC dentro de {ctx.zone_target} durante toda la sesión"
            )

    elif action == "reduce":
        # Carga elevada → ejecutar versión reducida
        reduced_duration = round((ctx.duration_min or 45) * 0.70)
        reduced_trimp = round((ctx.trimp_target or 40) * 0.70, 1)
        new_suggestions.append(
            f"📋 Plan original: {session_label} {ctx.duration_min} min "
            f"(TRIMP {ctx.trimp_target}) — ejecuta versión reducida: "
            f"{reduced_duration} min (TRIMP ~{reduced_trimp})"
        )
        if ctx.zone_target in ("Z3", "Z4", "Z5"):
            # Si era sesión de calidad, bajarla a Z2
            new_suggestions.append(
                f"Baja la intensidad a Z2 hoy — la sesión de {session_label.lower()} "
                f"queda para cuando tu ACWR baje de 1.3 (actual: {acwr})"
            )
        else:
            new_suggestions.append(
                f"Reduce duración al 70% — {reduced_duration} min máximo a {ctx.zone_target}"
            )

    elif action == "rest":
        # Descanso → postponer sesión
        new_suggestions.append(
            f"📋 Sesión de {session_label} postponida — "
            f"tu ACWR {acwr} requiere recuperación hoy"
        )
        new_suggestions.append(
            "Movilidad suave 15 min máximo — sin carga aeróbica"
        )

    # ── Equivalencias indoor si las hay ──────────────────────
    if ctx.indoor_equivalents and len(ctx.indoor_equivalents) > 0:
        eq = ctx.indoor_equivalents[0]
        equipment = eq.get("equipment", "").replace("_", " ")
        eq_duration = eq.get("duration_min", ctx.duration_min)
        eq_setting = eq.get("setting", "")
        new_suggestions.append(
            f"🏋️ Indoor: {equipment} {eq_duration} min — {eq_setting}"
        )

    # ── Contexto de fase en el reasoning ─────────────────────
    if plan_context_line:
        decision["reasoning"] = plan_context_line + decision["reasoning"]

    # Reemplazar suggestions originales con las enriquecidas + originales
    # Las del plan van primero, luego las genéricas originales
    original = decision.get("suggestions", [])
    decision["suggestions"] = new_suggestions + original
    decision["plan_context"] = {
        "session_type":   ctx.session_type,
        "session_label":  session_label,
        "zone_target":    ctx.zone_target,
        "duration_min":   ctx.duration_min,
        "trimp_target":   ctx.trimp_target,
        "week_phase":     ctx.week_phase,
        "phase_label":    phase_label,
        "week_number":    ctx.week_number,
        "weeks_to_race":  ctx.weeks_to_race,
        "race_name":      ctx.race_name,
    }

    return decision