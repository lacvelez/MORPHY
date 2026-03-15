"""
MORPHY PACE — Capa de inteligencia contextual

Responsabilidad: decidir si una alerta candidata debe emitirse,
suprimirse, diferirse o si debe disparar una recalibración de targets.

No genera alertas. Solo evalúa si una alerta propuesta por alert_engine
tiene sentido dado el contexto actual del atleta.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import statistics


# ─── Modelos de estado ────────────────────────────────────────────────────────

@dataclass
class RacePoint:
    timestamp: datetime
    hr_bpm: int
    pace_min_km: float      # min/km
    altitude_m: float
    km_marker: float
    gradient_pct: float = 0.0   # calculado al insertar


@dataclass
class AlertRecord:
    alert_type: str         # "hr_high" | "hr_low" | "pace_slow" | "pace_fast" | "km_split" | "hydration"
    fired_at: datetime
    message: str
    corrective_action_detected: bool = False  # se actualiza si el atleta corrige


@dataclass
class RaceTargets:
    """
    Targets actuales. Pueden ser recalibrados durante la carrera.

    hr_zone5_min se calcula en pace_strategist.py usando Karvonen real:
        Z5_min = FCrep + 0.90 * (FCmax - FCrep)
    No se estima aquí como hr_zone_max + constante_arbitraria.
    """
    hr_zone_min: int        # FC mínima del rango objetivo (ej: 145)
    hr_zone_max: int        # FC máxima del rango objetivo (ej: 162)
    hr_zone5_min: int       # FC mínima de Z5 — umbral crítico personalizado
    pace_min: float         # pace mínimo aceptable en min/km (ej: 5.5)
    pace_max: float         # pace máximo aceptable en min/km (ej: 4.9)
    recalibration_count: int = 0
    last_recalibrated_at: Optional[datetime] = None


@dataclass
class AlertDecision:
    should_emit: bool
    reason: str                     # para logging interno
    recalibration: Optional[dict] = None   # si hay recalibración, incluye nuevos targets
    defer_until: Optional[datetime] = None


# ─── Parámetros de inteligencia ───────────────────────────────────────────────

FLOW_STATE_MIN_DURATION_SEC = 300       # 5 min para considerar flow state activo
FLOW_STATE_HR_VARIANCE_MAX = 4          # ±4 bpm para considerar HR estable
FLOW_STATE_PACE_VARIANCE_MAX_SEC = 8    # ±8 seg/km para considerar pace estable

RECALIBRATION_MIN_DURATION_SEC = 480   # 8 min sosteniendo HR fuera de zona
RECALIBRATION_PACE_TOLERANCE = 0.10    # pace puede estar hasta 10% más rápido que objetivo
RECALIBRATION_MAX_COUNT = 2            # máximo 2 recalibraciones por carrera
RECALIBRATION_HR_STEP = 5              # sube el techo de zona en 5 bpm por recalibración

ALERT_MIN_GAP_SEC = 45                 # mínimo entre cualquier alerta
ALERT_SAME_TYPE_SILENCE_MIN = 12       # min de silencio si mismo tipo sin corrección
ALERT_GRADIENT_SUPPRESSION_PCT = 4.0   # suprimir HR alert si gradiente > 4%

CORRECTION_DETECTION_WINDOW_SEC = 90   # ventana para detectar corrección tras alerta

# Detección de HR crítica — valores separados de la lógica de detección
CRITICAL_HR_WINDOW_SEC = 180           # ventana de evaluación (3 min)
CRITICAL_HR_MIN_POINTS = 4            # mínimo de puntos para considerar válida la ventana
# Nota: el umbral de HR crítica NO se define aquí — se deriva del perfil del atleta
# via RaceTargets.hr_zone5_min (calculado con Karvonen desde FCmax y FCrep reales)


# ─── Motor principal ──────────────────────────────────────────────────────────

class PaceIntelligence:
    """
    Evalúa el contexto del atleta antes de emitir cualquier alerta.

    Uso:
        intel = PaceIntelligence(targets)
        decision = intel.evaluate(candidate_alert_type, recent_points, alert_history)
        if decision.should_emit:
            alert_engine.push(alert)
        if decision.recalibration:
            targets = apply_recalibration(targets, decision.recalibration)
    """

    def __init__(self, targets: RaceTargets):
        self.targets = targets

    # ─── API pública ──────────────────────────────────────────────────────────

    def evaluate(
        self,
        candidate_type: str,
        recent_points: list[RacePoint],     # últimos ~10 min de puntos
        alert_history: list[AlertRecord],
    ) -> AlertDecision:
        """
        Evalúa si una alerta candidata debe emitirse.
        Retorna AlertDecision con la decisión y razón.
        """
        if len(recent_points) < 3:
            return AlertDecision(False, "insuficientes_puntos")

        now = recent_points[-1].timestamp

        # Filtro 1: ¿Hay desnivel activo?
        if candidate_type.startswith("hr_") and self._climbing_active(recent_points):
            return AlertDecision(False, "subida_activa_hr_suprimido")

        # Filtro 2: ¿Estado de flow activo?
        flow = self._detect_flow_state(recent_points)
        if flow and candidate_type not in ("hr_critical",):
            return AlertDecision(False, "flow_state_activo_alerta_no_critica")

        # Filtro 3: ¿Debería recalibrarse en lugar de alertar?
        if candidate_type == "hr_high":
            recal = self._should_recalibrate(recent_points)
            if recal:
                return AlertDecision(
                    should_emit=True,
                    reason="recalibracion_targets",
                    recalibration=recal,
                )

        # Filtro 4: ¿Misma alerta ya emitida sin corrección?
        if self._same_type_ignored(candidate_type, alert_history, now):
            defer = now + timedelta(minutes=ALERT_SAME_TYPE_SILENCE_MIN)
            return AlertDecision(False, "misma_alerta_ignorada", defer_until=defer)

        # Filtro 5: ¿Gap mínimo respecto a última alerta?
        if not self._min_gap_elapsed(alert_history, now):
            defer = now + timedelta(seconds=ALERT_MIN_GAP_SEC)
            return AlertDecision(False, "gap_minimo_no_cumplido", defer_until=defer)

        return AlertDecision(True, "todos_filtros_ok")

    def detect_correction(
        self,
        alert: AlertRecord,
        points_after: list[RacePoint],
    ) -> bool:
        """
        Evalúa si el atleta corrigió tras una alerta.
        Se llama desde el router /update cuando ha pasado la ventana de detección.
        """
        if not points_after:
            return False
        window = [
            p for p in points_after
            if (p.timestamp - alert.fired_at).total_seconds() <= CORRECTION_DETECTION_WINDOW_SEC
        ]
        if not window:
            return False

        if alert.alert_type == "hr_high":
            # Corrección: HR bajó al menos 5 bpm respecto al pico
            peak_hr = max(p.hr_bpm for p in points_after[:3])
            avg_after = statistics.mean(p.hr_bpm for p in window)
            return avg_after < (peak_hr - 5)

        if alert.alert_type == "pace_slow":
            # Corrección: pace mejoró al menos 8 seg/km
            initial_pace = points_after[0].pace_min_km
            avg_after = statistics.mean(p.pace_min_km for p in window)
            return avg_after < (initial_pace - 8/60)

        return False

    # ─── Detección de estado de flow ──────────────────────────────────────────

    def _detect_flow_state(self, points: list[RacePoint]) -> bool:
        """
        Flow state: HR y pace estables durante >= FLOW_STATE_MIN_DURATION_SEC.
        El atleta está concentrado y en ritmo — no interrumpir salvo crítico.
        """
        if len(points) < 5:
            return False

        window = self._last_n_seconds(points, FLOW_STATE_MIN_DURATION_SEC)
        if len(window) < 4:
            return False

        hrs = [p.hr_bpm for p in window]
        paces = [p.pace_min_km for p in window]

        hr_stable = (max(hrs) - min(hrs)) <= FLOW_STATE_HR_VARIANCE_MAX
        pace_stable_sec = (max(paces) - min(paces)) * 60  # convertir a segundos
        pace_stable = pace_stable_sec <= FLOW_STATE_PACE_VARIANCE_MAX_SEC

        return hr_stable and pace_stable

    # ─── Detección de recalibración ───────────────────────────────────────────

    def _should_recalibrate(self, points: list[RacePoint]) -> Optional[dict]:
        """
        Recalibra si el atleta lleva RECALIBRATION_MIN_DURATION_SEC con HR
        sobre el techo de zona pero pace igual o mejor al objetivo.

        Retorna dict con nuevos targets si aplica, None si no.
        """
        if self.targets.recalibration_count >= RECALIBRATION_MAX_COUNT:
            return None

        window = self._last_n_seconds(points, RECALIBRATION_MIN_DURATION_SEC)
        if len(window) < 5:
            return None

        hrs = [p.hr_bpm for p in window]
        paces = [p.pace_min_km for p in window]

        avg_hr = statistics.mean(hrs)
        avg_pace = statistics.mean(paces)

        hr_over_target = avg_hr > self.targets.hr_zone_max
        pace_acceptable = avg_pace <= self.targets.pace_max * (1 + RECALIBRATION_PACE_TOLERANCE)

        if hr_over_target and pace_acceptable:
            new_max = self.targets.hr_zone_max + RECALIBRATION_STEP
            new_min = self.targets.hr_zone_min + RECALIBRATION_STEP
            return {
                "new_hr_zone_min": new_min,
                "new_hr_zone_max": new_max,
                "reason": "atleta_sostuvo_ritmo_con_hr_alta",
                "message": f"Ritmo sostenido por encima del plan. Ajustando zona a {new_min}–{new_max} bpm.",
            }
        return None

    # ─── Cansancio de alertas ─────────────────────────────────────────────────

    def _same_type_ignored(
        self,
        alert_type: str,
        history: list[AlertRecord],
        now: datetime,
    ) -> bool:
        """
        True si el mismo tipo de alerta se emitió recientemente y el atleta
        no corrigió — indicando que la ignoró deliberadamente.
        """
        cutoff = now - timedelta(minutes=ALERT_SAME_TYPE_SILENCE_MIN)
        recent_same = [
            a for a in history
            if a.alert_type == alert_type
            and a.fired_at >= cutoff
            and not a.corrective_action_detected
        ]
        return len(recent_same) >= 1

    # ─── Gap mínimo entre alertas ─────────────────────────────────────────────

    def _min_gap_elapsed(self, history: list[AlertRecord], now: datetime) -> bool:
        if not history:
            return True
        last = max(history, key=lambda a: a.fired_at)
        elapsed = (now - last.fired_at).total_seconds()
        return elapsed >= ALERT_MIN_GAP_SEC

    # ─── Desnivel activo ──────────────────────────────────────────────────────

    def _climbing_active(self, points: list[RacePoint]) -> bool:
        """Gradiente promedio de los últimos 200m > umbral."""
        recent = self._last_n_seconds(points, 120)  # ~200m a 5 min/km
        if len(recent) < 2:
            return False
        avg_gradient = statistics.mean(p.gradient_pct for p in recent)
        return avg_gradient > ALERT_GRADIENT_SUPPRESSION_PCT

    # ─── Utilidades ───────────────────────────────────────────────────────────

    def _is_critical_hr(self, points: list[RacePoint]) -> bool:
        """
        True si el atleta lleva CRITICAL_HR_WINDOW_SEC en Z5 sostenida.
        Usa hr_zone5_min del perfil real (Karvonen), no una constante arbitraria.
        """
        window = self._last_n_seconds(points, CRITICAL_HR_WINDOW_SEC)
        if len(window) < CRITICAL_HR_MIN_POINTS:
            # Gap de datos — no asumir que todo está bien
            import logging
            logging.warning(
                "pace_intelligence: ventana crítica con solo %d puntos "
                "(esperados >= %d). Posible gap de sensor.",
                len(window), CRITICAL_HR_MIN_POINTS
            )
            return False
        avg_hr = statistics.mean(p.hr_bpm for p in window)
        return avg_hr >= self.targets.hr_zone5_min
        if not points:
            return []
        cutoff = points[-1].timestamp - timedelta(seconds=seconds)
        return [p for p in points if p.timestamp >= cutoff]


# ─── Función auxiliar para el router ─────────────────────────────────────────

def apply_recalibration(targets: RaceTargets, recal: dict) -> RaceTargets:
    """Aplica una recalibración a los targets actuales. Llamar desde pace.py."""
    targets.hr_zone_min = recal["new_hr_zone_min"]
    targets.hr_zone_max = recal["new_hr_zone_max"]
    targets.recalibration_count += 1
    targets.last_recalibrated_at = datetime.utcnow()
    return targets