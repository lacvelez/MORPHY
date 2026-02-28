"""
MORPHY - Decision Engine v2.0
Filosofía: No decido SI entrenas. Decido CÓMO entrenas hoy.

Un atleta serio entrena todos los días. Las recomendaciones de "descansa 90 horas"
son ridículas y se ignoran. MORPHY adapta la sesión al estado real del atleta,
combinando datos fisiológicos con percepción subjetiva.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from app.services.athlete_state import AthleteState

@dataclass
class WorkoutSuggestion:
    title: str
    description: str
    duration_min: int
    intensity_zone: str       # "Z1", "Z2", "Z3", "Z4", "Z5"
    intensity_label: str      # "Recuperación", "Base", "Tempo", "Umbral", "VO2max"
    hr_range: Optional[str]   # "120-140 bpm"
    examples: List[str]       # Ejemplos concretos de sesión

@dataclass
class Decision:
    action: str              # "easy", "moderate", "hard", "quality", "active_recovery"
    confidence: float        # 0.0 - 1.0
    headline: str
    reasoning: str
    risk_note: Optional[str]  # Advertencia si hay riesgo, pero sin prohibir
    primary_workout: WorkoutSuggestion
    alternative_workout: WorkoutSuggestion  # Siempre dar opción B
    nutrition_tip: Optional[str]
    recovery_tip: Optional[str]

class DecisionEngine:
    """
    Motor de decisiones v2.0
    
    Principios:
    1. NUNCA decir "no entrenes" (excepto lesión confirmada)
    2. Siempre dar una sesión primaria + alternativa
    3. Adaptar intensidad, no prohibir actividad
    4. Reconocer que el atleta conoce su cuerpo
    5. Dar valor que Garmin no da: el CÓMO específico
    """
    
    def __init__(self, rest_hr: int = 60, max_hr: int = 190):
        self.rest_hr = rest_hr
        self.max_hr = max_hr
    
    def _hr_zone(self, zone: int) -> str:
        """Calcula rango de HR para una zona"""
        hr_reserve = self.max_hr - self.rest_hr
        zones = {
            1: (0.50, 0.60),
            2: (0.60, 0.70),
            3: (0.70, 0.80),
            4: (0.80, 0.90),
            5: (0.90, 1.00),
        }
        low_pct, high_pct = zones.get(zone, (0.5, 0.6))
        low = int(self.rest_hr + hr_reserve * low_pct)
        high = int(self.rest_hr + hr_reserve * high_pct)
        return f"{low}-{high} bpm"
    
    def generate_decision(self, state: AthleteState, perceived_effort: Optional[int] = None, thresholds: Optional[Dict[str, Any]] = None) -> Decision:
        """
        Genera decisión de entrenamiento.
        perceived_effort: 1-10 (opcional, del atleta)
        thresholds: dict con valores personalizados para las decisiones
                   (acwr_danger, acwr_caution, tsb_fatigued, tsb_fresh)
        """
        
        # Thresholds personalizables con valores por defecto
        th = thresholds or {}
        acwr_danger = th.get("acwr_danger", 1.5)
        acwr_caution = th.get("acwr_caution", 1.3)
        tsb_fatigued = th.get("tsb_fatigued", -15.0)
        tsb_fresh = th.get("tsb_fresh", 10.0)
        
        # Sin datos suficientes
        if state.activities_count < 3:
            return self._insufficient_data(state)
        
        # Evaluar estado y generar decisión adaptada
        if state.acwr > acwr_danger or state.injury_risk == "high":
            return self._high_load_day(state, perceived_effort)
        
        elif state.acwr > acwr_caution or state.injury_risk == "moderate":
            return self._elevated_load_day(state, perceived_effort)
        
        elif state.training_stress_balance < tsb_fatigued:
            return self._fatigued_day(state, perceived_effort)
        
        elif state.training_stress_balance > tsb_fresh and state.chronic_load > 5:
            return self._fresh_day(state, perceived_effort)
        
        elif state.days_since_last >= 3:
            return self._comeback_day(state, perceived_effort)
        
        else:
            return self._normal_day(state, perceived_effort)
    
    def _high_load_day(self, state: AthleteState, pe: Optional[int]) -> Decision:
        """ACWR > 1.5 — Carga muy alta. NO prohíbe. Adapta."""
        return Decision(
            action="active_recovery",
            confidence=0.85,
            headline="Tu carga reciente es alta — hoy es día de moverte con inteligencia",
            reasoning=f"Tu ACWR está en {state.acwr}. Has acumulado bastante carga últimamente "
                      f"(ATL: {state.acute_load} vs CTL: {state.chronic_load}). "
                      "Esto no significa que debas parar — significa que hoy tu cuerpo "
                      "absorbe mejor una sesión de baja intensidad que te mantenga activo "
                      "sin sumar fatiga. Mañana podrás entrenar más fuerte.",
            risk_note="⚡ Tu ratio de carga está elevado. Si sientes dolor articular o muscular "
                      "inusual (no confundir con fatiga normal), considera reducir aún más. "
                      "Tú conoces tu cuerpo — esto es una guía, no una orden.",
            primary_workout=WorkoutSuggestion(
                title="Trote regenerativo",
                description="Sesión suave enfocada en mover el cuerpo sin acumular fatiga. "
                            "El objetivo es facilitar la recuperación, no sumar carga.",
                duration_min=30,
                intensity_zone="Z1",
                intensity_label="Recuperación",
                hr_range=self._hr_zone(1),
                examples=[
                    "30 min trote muy suave por terreno plano",
                    "Si las piernas están pesadas, camina los primeros 5 min",
                    "Puedes alternar 3 min trote / 1 min caminata",
                    "Termina con 5 min de estiramientos suaves"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Movilidad + Core",
                description="Si prefieres no correr, una sesión de movilidad y core "
                            "es igual de valiosa hoy. Trabaja estabilidad sin impacto.",
                duration_min=40,
                intensity_zone="Z1",
                intensity_label="Recuperación",
                hr_range=None,
                examples=[
                    "15 min movilidad articular (caderas, tobillos, hombros)",
                    "15 min core: planks, dead bugs, bird dogs",
                    "10 min foam roller en cuádriceps, gemelos, IT band",
                    "Yoga suave si prefieres algo más fluido"
                ]
            ),
            nutrition_tip="Hoy prioriza proteína y carbohidratos de calidad. "
                          "Tu cuerpo está reparando — dale los materiales.",
            recovery_tip="Si puedes, duerme 30 min extra esta noche. "
                         "El sueño es donde realmente se absorbe el entrenamiento."
        )
    
    def _elevated_load_day(self, state: AthleteState, pe: Optional[int]) -> Decision:
        """ACWR 1.3-1.5 — Carga elevada pero manejable"""
        return Decision(
            action="easy",
            confidence=0.8,
            headline="Carga acumulada considerable — sesión aeróbica base hoy",
            reasoning=f"Tu ACWR de {state.acwr} muestra que vienes entrenando fuerte. "
                      "Estás en una zona donde una sesión de base aeróbica te beneficia más "
                      "que otra sesión intensa. Piensa en esto como invertir en tu motor "
                      "aeróbico sin estresar más tu cuerpo.",
            risk_note=None,
            primary_workout=WorkoutSuggestion(
                title="Carrera base aeróbica",
                description="Sesión en zona 2 — la base que construye tu motor aeróbico. "
                            "Deberías poder mantener una conversación cómodamente.",
                duration_min=40,
                intensity_zone="Z2",
                intensity_label="Base aeróbica",
                hr_range=self._hr_zone(2),
                examples=[
                    "40 min de carrera continua en zona 2",
                    "Ritmo conversacional — si no puedes hablar, baja",
                    "Terreno plano o ligeramente ondulado",
                    "Enfócate en cadencia: 170-180 pasos/min"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Cross-training suave",
                description="Si tus piernas necesitan un descanso del impacto, "
                            "el ciclismo o natación dan estímulo aeróbico sin el golpeo.",
                duration_min=45,
                intensity_zone="Z2",
                intensity_label="Base aeróbica",
                hr_range=self._hr_zone(2),
                examples=[
                    "45 min de bicicleta estática a ritmo suave",
                    "30 min de natación técnica",
                    "40 min de elíptica a intensidad moderada"
                ]
            ),
            nutrition_tip="Hidrátate bien antes de la sesión. "
                          "Después: recupera con carbohidratos + proteína en los siguientes 30 min.",
            recovery_tip="Estiramientos de 10 min post-sesión. "
                         "Si tienes foam roller, úsalo en los gemelos y cuádriceps."
        )
    
    def _fatigued_day(self, state: AthleteState, pe: Optional[int]) -> Decision:
        """TSB muy negativo — Fatiga acumulada"""
        return Decision(
            action="easy",
            confidence=0.75,
            headline="Fatiga acumulada detectada — sesión técnica de baja intensidad",
            reasoning=f"Tu balance de estrés es {state.training_stress_balance}. "
                      "Hay fatiga acumulada en tu sistema. Esto es normal si vienes "
                      "de un bloque fuerte de entrenamiento. Hoy es día de sesión "
                      "técnica: trabaja en tu forma de correr a ritmo suave.",
            risk_note=None,
            primary_workout=WorkoutSuggestion(
                title="Carrera técnica",
                description="Sesión corta enfocada en técnica de carrera. "
                            "Intensidad baja, atención alta en la forma.",
                duration_min=35,
                intensity_zone="Z1-Z2",
                intensity_label="Técnica",
                hr_range=self._hr_zone(1),
                examples=[
                    "10 min calentamiento trote suave",
                    "4x 30s drills de técnica (skipping, talones, rodillas altas)",
                    "15 min trote suave enfocado en postura y cadencia",
                    "5 min caminata + estiramientos"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Fuerza preventiva",
                description="Sesión de fuerza ligera enfocada en prevención de lesiones. "
                            "Sin peso pesado — solo activación y estabilidad.",
                duration_min=30,
                intensity_zone="Z1",
                intensity_label="Fuerza ligera",
                hr_range=None,
                examples=[
                    "Sentadillas a una pierna (3x10 cada lado)",
                    "Puente de glúteos (3x15)",
                    "Calf raises excéntricos (3x12)",
                    "Plancha lateral (3x30s cada lado)"
                ]
            ),
            nutrition_tip="Incluye alimentos antiinflamatorios: frutas, verduras de hoja verde, "
                          "pescado o nueces. Tu cuerpo está recuperando.",
            recovery_tip="Si tienes acceso, un baño de contraste (agua fría/caliente) "
                         "puede ayudar con la fatiga acumulada."
        )
    
    def _fresh_day(self, state: AthleteState, pe: Optional[int]) -> Decision:
        """TSB positivo + buena fitness — Día para calidad"""
        return Decision(
            action="quality",
            confidence=0.85,
            headline="Estás fresco y con buena base — día ideal para sesión de calidad",
            reasoning=f"Tu readiness es {state.readiness_score}/100 y tu fitness base "
                      f"es sólida (CTL: {state.chronic_load}). Estás descansado y tu cuerpo "
                      "puede absorber una sesión exigente. Aprovecha para trabajar velocidad "
                      "o umbral — estos días no vienen todos los días.",
            risk_note=None,
            primary_workout=WorkoutSuggestion(
                title="Intervalos de umbral",
                description="Sesión de calidad con intervalos en zona 4. "
                            "Esto mejora tu velocidad en umbral anaeróbico.",
                duration_min=50,
                intensity_zone="Z4",
                intensity_label="Umbral",
                hr_range=self._hr_zone(4),
                examples=[
                    "15 min calentamiento progresivo (Z1 → Z2)",
                    "5x 4 min en zona 4 con 2 min trote recuperación",
                    "Si te sientes fuerte, los últimos 2 intervalos puedes subir a Z4 alto",
                    "10 min vuelta a la calma en Z1"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Tempo sostenido",
                description="Si prefieres algo más constante que intervalos, "
                            "un tempo sostenido en zona 3 es excelente para resistencia.",
                duration_min=50,
                intensity_zone="Z3",
                intensity_label="Tempo",
                hr_range=self._hr_zone(3),
                examples=[
                    "10 min calentamiento en Z1-Z2",
                    "25-30 min continuos en zona 3 (ritmo 'cómodamente difícil')",
                    "Deberías poder decir frases cortas pero no mantener conversación",
                    "10 min vuelta a la calma"
                ]
            ),
            nutrition_tip="Come carbohidratos 2-3 horas antes de la sesión. "
                          "Necesitas glucógeno para rendir en los intervalos.",
            recovery_tip="Después de la sesión de calidad, los próximos 1-2 días "
                         "deberían ser más suaves para absorber el estímulo."
        )
    
    def _comeback_day(self, state: AthleteState, pe: Optional[int]) -> Decision:
        """Varios días sin entrenar"""
        return Decision(
            action="moderate",
            confidence=0.7,
            headline=f"Llevas {state.days_since_last} días sin actividad — retoma con progresión",
            reasoning="Después de varios días de descanso tu cuerpo está recuperado, "
                      "pero tus músculos y tendones necesitan readaptarse al impacto. "
                      "Hoy es día de retomar con una sesión moderada que te active "
                      "sin exigirte al máximo desde el primer minuto.",
            risk_note="Después de una pausa, el riesgo de molestias es mayor si arrancas "
                      "demasiado fuerte. Los primeros 10 minutos son clave: empieza más suave "
                      "de lo que crees necesario.",
            primary_workout=WorkoutSuggestion(
                title="Retorno progresivo",
                description="Sesión con progresión gradual de intensidad. "
                            "Empieza en Z1 y sube según cómo te sientas.",
                duration_min=40,
                intensity_zone="Z1→Z2",
                intensity_label="Progresivo",
                hr_range=self._hr_zone(2),
                examples=[
                    "10 min caminata rápida o trote muy suave",
                    "15 min trote en zona 2 baja",
                    "10 min trote en zona 2 alta (si las piernas responden bien)",
                    "5 min vuelta a la calma + estiramientos",
                    "Si algo molesta, reduce y no fuerces"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Activación general",
                description="Si no te apetece correr, una sesión de activación "
                            "general te prepara para retomar mañana con más fuerza.",
                duration_min=35,
                intensity_zone="Z1-Z2",
                intensity_label="Activación",
                hr_range=None,
                examples=[
                    "10 min movilidad articular dinámica",
                    "10 min ejercicios de activación (sentadillas, lunges, skipping)",
                    "10 min trote suave o caminata rápida",
                    "5 min estiramientos"
                ]
            ),
            nutrition_tip="Hidrátate bien antes de volver a entrenar. "
                          "Un snack con carbohidratos 1 hora antes ayuda.",
            recovery_tip="No intentes recuperar los días perdidos de golpe. "
                         "Mejor 3 días progresivos que un día brutal."
        )
    
    def _normal_day(self, state: AthleteState, pe: Optional[int]) -> Decision:
        """Estado equilibrado — día normal de entrenamiento"""
        return Decision(
            action="moderate",
            confidence=0.75,
            headline="Estado equilibrado — sesión aeróbica sólida hoy",
            reasoning=f"Tu estado general es bueno. ACWR: {state.acwr} (rango óptimo), "
                      f"Readiness: {state.readiness_score}/100. No hay señales de alarma. "
                      "Es un buen día para una sesión de base aeróbica que mantenga "
                      "tu progresión sin acumular fatiga innecesaria.",
            risk_note=None,
            primary_workout=WorkoutSuggestion(
                title="Carrera base con fartlek opcional",
                description="Sesión aeróbica con opción de incluir cambios de ritmo "
                            "si te sientes con energía.",
                duration_min=45,
                intensity_zone="Z2",
                intensity_label="Base aeróbica",
                hr_range=self._hr_zone(2),
                examples=[
                    "45 min de carrera en zona 2",
                    "Si te sientes bien después de 20 min: incluye 4-6 aceleraciones de 30s",
                    "Las aceleraciones son a ritmo alegre, no sprint máximo",
                    "Termina siempre con 5-10 min suaves"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Carrera larga suave",
                description="Si es tu día de carrera larga, baja la intensidad "
                            "y extiende la duración.",
                duration_min=60,
                intensity_zone="Z1-Z2",
                intensity_label="Resistencia",
                hr_range=self._hr_zone(2),
                examples=[
                    "60 min de trote continuo a ritmo conversacional",
                    "Lleva agua si hace calor",
                    "No te preocupes por el pace — enfócate en el tiempo en pies",
                    "Es normal que el último tercio se sienta más pesado"
                ]
            ),
            nutrition_tip="Sesión aeróbica moderada: puedes entrenar en ayunas si es corta (<45 min) "
                          "o con un desayuno ligero si es más larga.",
            recovery_tip="Después de la sesión, 10 min de estiramientos marcan la diferencia "
                         "en cómo te sientes mañana."
        )
    
    def _insufficient_data(self, state: AthleteState) -> Decision:
        """Pocos datos para decidir"""
        return Decision(
            action="moderate",
            confidence=0.3,
            headline="Aún estoy aprendiendo tu patrón — entrena como lo sientes hoy",
            reasoning=f"Tengo {state.activities_count} actividades registradas. "
                      "Necesito al menos una semana completa para entender tu carga "
                      "y darte recomendaciones realmente personalizadas. "
                      "Por ahora, confía en tu percepción.",
            risk_note=None,
            primary_workout=WorkoutSuggestion(
                title="Tu sesión planificada",
                description="Sigue con lo que tengas planeado para hoy. "
                            "Mientras más datos me des, mejores serán mis recomendaciones.",
                duration_min=45,
                intensity_zone="Según tu plan",
                intensity_label="Tu elección",
                hr_range=None,
                examples=[
                    "Sigue tu plan de entrenamiento habitual",
                    "Asegúrate de sincronizar la actividad después",
                    "En unos días podré darte sesiones específicas"
                ]
            ),
            alternative_workout=WorkoutSuggestion(
                title="Carrera base",
                description="Si no tienes plan, una carrera base de 40 min "
                            "a ritmo cómodo es siempre una buena opción.",
                duration_min=40,
                intensity_zone="Z2",
                intensity_label="Base",
                hr_range=None,
                examples=[
                    "40 min trote a ritmo conversacional",
                    "Sin presión de pace — solo disfruta la sesión"
                ]
            ),
            nutrition_tip=None,
            recovery_tip="Recuerda sincronizar tu actividad después de cada sesión "
                         "para que pueda aprender tu patrón."
        )