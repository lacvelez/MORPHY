"""
MORPHY - Decision Engine
Motor de decisiones autónomas basado en el estado del atleta.
Sistema de reglas con confianza ponderada.
"""
from dataclasses import dataclass
from typing import List
from app.services.athlete_state import AthleteState

@dataclass
class Decision:
    action: str          # "reduce", "maintain", "increase", "rest"
    confidence: float    # 0.0 - 1.0
    headline: str        # Mensaje principal
    reasoning: str       # Explicación detallada
    suggestions: List[str]  # Recomendaciones específicas

class DecisionEngine:
    """
    Genera decisiones de entrenamiento basadas en el estado del atleta.
    Reglas basadas en literatura deportiva (Banister, Gabbett, Seiler).
    """
    
    def generate_decision(self, state: AthleteState) -> Decision:
        """Genera la decisión principal para hoy"""
        
        # Regla 1: Sin datos suficientes
        if state.activities_count < 3:
            return Decision(
                action="maintain",
                confidence=0.3,
                headline="📊 Necesito más datos para decidir bien",
                reasoning=f"Solo tengo {state.activities_count} actividades registradas. "
                          "Necesito al menos una semana de datos para hacer recomendaciones precisas.",
                suggestions=[
                    "Sigue entrenando como normalmente lo haces",
                    "Asegúrate de sincronizar todas tus actividades",
                    "En unos días podré darte recomendaciones personalizadas"
                ]
            )
        
        # Regla 2: Alto riesgo de lesión (ACWR > 1.5)
        if state.injury_risk == "high":
            return Decision(
                action="rest",
                confidence=0.9,
                headline="🔴 ALERTA: Riesgo alto de lesión — Descansa hoy",
                reasoning=f"Tu ratio de carga aguda/crónica es {state.acwr}, muy por encima "
                          "del rango seguro (0.8-1.3). Has aumentado tu carga de entrenamiento "
                          "demasiado rápido. El riesgo de lesión es significativamente elevado.",
                suggestions=[
                    "Hoy: descanso completo o caminata suave de 20 min",
                    "Mañana: si te sientes bien, actividad a intensidad muy baja",
                    "Esta semana: reduce tu volumen un 40-50%",
                    "No ignores dolores o molestias — tu cuerpo necesita recuperar"
                ]
            )
        
        # Regla 3: Riesgo moderado (ACWR 1.3-1.5)
        if state.injury_risk == "moderate":
            return Decision(
                action="reduce",
                confidence=0.8,
                headline="🟡 Cuidado: Carga elevada — Reduce la intensidad",
                reasoning=f"Tu ACWR está en {state.acwr}, acercándose a la zona de riesgo. "
                          f"Tu fatiga aguda ({state.acute_load}) supera significativamente "
                          f"tu fitness crónica ({state.chronic_load}). "
                          "Necesitas moderar para evitar sobreentrenamiento.",
                suggestions=[
                    "Hoy: entrena pero a intensidad baja (zona 1-2)",
                    "Reduce el volumen un 20-30% esta semana",
                    "Prioriza sueño y recuperación",
                    "Incluye movilidad y estiramientos"
                ]
            )
        
        # Regla 4: Muchos días sin entrenar
        if state.days_since_last >= 4:
            return Decision(
                action="increase",
                confidence=0.7,
                headline="🔵 Llevas varios días de descanso — Hora de moverse",
                reasoning=f"Han pasado {state.days_since_last} días desde tu última actividad. "
                          "Demasiado descanso puede reducir tu fitness acumulada. "
                          "Es buen momento para retomar con una sesión moderada.",
                suggestions=[
                    "Hoy: sesión moderada de 30-45 minutos",
                    "Empieza suave los primeros 10 minutos",
                    "No intentes compensar los días perdidos — progresa gradualmente",
                    "Escucha a tu cuerpo durante la sesión"
                ]
            )
        
        # Regla 5: TSB muy negativo (fatigado)
        if state.training_stress_balance < -15:
            return Decision(
                action="reduce",
                confidence=0.75,
                headline="🟡 Acumulación de fatiga — Sesión ligera hoy",
                reasoning=f"Tu balance de estrés es {state.training_stress_balance}, indicando "
                          "fatiga acumulada. Tu cuerpo necesita recuperar antes de poder "
                          "absorber más entrenamiento de calidad.",
                suggestions=[
                    "Hoy: recuperación activa — trote suave 20-30 min",
                    "Mantén la frecuencia cardíaca por debajo de zona 2",
                    "Considera una sesión de movilidad o yoga",
                    "Prioriza dormir 8+ horas esta noche"
                ]
            )
        
        # Regla 6: TSB positivo (descansado y en buena forma)
        if state.training_stress_balance > 10 and state.chronic_load > 5:
            return Decision(
                action="increase",
                confidence=0.8,
                headline="🟢 Estado óptimo — Aprovecha para entrenar fuerte",
                reasoning=f"Tu readiness es {state.readiness_score}/100. Estás descansado "
                          f"y tu fitness base es sólida (CTL: {state.chronic_load}). "
                          "Es un buen día para una sesión de calidad.",
                suggestions=[
                    "Hoy: sesión de calidad — intervalos o tempo run",
                    "Puedes aumentar intensidad o duración respecto a tu promedio",
                    "Aprovecha la frescura para trabajar velocidad",
                    "No olvides calentar bien antes de exigirte"
                ]
            )
        
        # Regla 7: Todo normal — mantener
        return Decision(
            action="maintain",
            confidence=0.7,
            headline="🟢 Todo en orden — Entrena según tu plan",
            reasoning=f"Tu estado es equilibrado. ACWR: {state.acwr} (rango óptimo), "
                      f"Readiness: {state.readiness_score}/100. "
                      "No hay señales de alarma ni oportunidades especiales.",
            suggestions=[
                "Sigue con tu plan de entrenamiento normal",
                "Escucha a tu cuerpo — si sientes fatiga inusual, reduce",
                f"Tu fitness crónica (CTL: {state.chronic_load}) va progresando bien"
            ]
        )