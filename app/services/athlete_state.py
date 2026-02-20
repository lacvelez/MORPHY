"""
MORPHY - Athlete State Calculator
Calcula el estado fisiológico del atleta basado en su historial de entrenamiento.
Usa modelo de Banister (impulse-response) simplificado.
"""
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass
import math

@dataclass
class ActivityData:
    date: datetime
    activity_type: str
    duration_min: float
    distance_km: float
    elevation_m: float
    avg_hr: Optional[int]
    max_hr: Optional[int]
    avg_pace: Optional[float]

@dataclass
class AthleteState:
    # Training Load
    acute_load: float       # ATL - últimos 7 días (fatiga)
    chronic_load: float     # CTL - últimos 42 días (fitness)
    training_stress_balance: float  # TSB = CTL - ATL (readiness)
    
    # Ratios
    acwr: float             # Acute:Chronic Workload Ratio
    
    # Risk assessment
    injury_risk: str        # "low", "moderate", "high"
    readiness_score: float  # 0-100
    
    # Context
    days_analyzed: int
    activities_count: int
    last_activity_date: Optional[datetime]
    days_since_last: int

class AthleteStateCalculator:
    """
    Calcula el estado del atleta usando:
    - Training Load estimado (TRIMP simplificado)
    - Acute:Chronic Workload Ratio (ACWR)
    - Training Stress Balance (TSB)
    """
    
    # Constantes de decaimiento exponencial (Banister model)
    ATL_DECAY = 7    # días para fatiga aguda
    CTL_DECAY = 42   # días para fitness crónica
    
    def __init__(self, max_hr: int = 190, rest_hr: int = 60):
        self.max_hr = max_hr
        self.rest_hr = rest_hr
    
    def calculate_training_load(self, activity: ActivityData) -> float:
        """
        Calcula training load de una actividad.
        Si tiene HR: usa TRIMP (Training Impulse)
        Si no tiene HR: estima con duración, distancia y elevación
        """
        if activity.avg_hr and activity.avg_hr > 0:
            return self._trimp_with_hr(activity)
        else:
            return self._estimated_load(activity)
    
    def _trimp_with_hr(self, activity: ActivityData) -> float:
        """TRIMP de Banister basado en HR"""
        duration = activity.duration_min
        avg_hr = activity.avg_hr
        
        # Heart Rate Reserve ratio
        hr_reserve = (avg_hr - self.rest_hr) / (self.max_hr - self.rest_hr)
        hr_reserve = max(0, min(1, hr_reserve))  # clamp 0-1
        
        # TRIMP = duration * HRreserve * 0.64 * e^(1.92 * HRreserve)
        # Factor genérico (promedio hombre/mujer)
        trimp = duration * hr_reserve * 0.64 * math.exp(1.92 * hr_reserve)
        
        return round(trimp, 1)
    
    def _estimated_load(self, activity: ActivityData) -> float:
        """
        Estima training load sin HR.
        Usa duración como base, ajustada por intensidad estimada.
        """
        duration = activity.duration_min
        
        # Factor de intensidad basado en pace (si disponible)
        intensity = 0.6  # default: moderado
        
        if activity.avg_pace and activity.avg_pace > 0 and activity.distance_km > 0:
            # pace en min/km (convertir de m/s)
            pace_min_km = 1000 / (activity.avg_pace * 60) if activity.avg_pace > 0 else 8
            
            # Estimar intensidad por pace
            if pace_min_km < 4.5:
                intensity = 0.9   # muy rápido
            elif pace_min_km < 5.5:
                intensity = 0.75  # rápido
            elif pace_min_km < 7.0:
                intensity = 0.6   # moderado
            else:
                intensity = 0.45  # fácil
        
        # Ajuste por tipo de actividad
        type_factor = {
            "Run": 1.0,
            "WeightTraining": 0.7,
            "Ride": 0.8,
            "Swim": 0.9,
            "Walk": 0.4,
            "Hike": 0.6,
        }.get(activity.activity_type, 0.6)
        
        # Ajuste por elevación
        elev_factor = 1.0
        if activity.elevation_m > 100:
            elev_factor = 1.15
        elif activity.elevation_m > 300:
            elev_factor = 1.3
        
        load = duration * intensity * type_factor * elev_factor
        return round(load, 1)
    
    def calculate_state(self, activities: List[ActivityData]) -> AthleteState:
        """Calcula el estado completo del atleta"""
        now = datetime.utcnow()
        
        if not activities:
            return AthleteState(
                acute_load=0, chronic_load=0, training_stress_balance=0,
                acwr=0, injury_risk="low", readiness_score=100,
                days_analyzed=0, activities_count=0,
                last_activity_date=None, days_since_last=999
            )
        
        # Calcular load diario (últimos 42 días)
        daily_loads = {}
        for a in activities:
            date_key = a.date.strftime("%Y-%m-%d")
            load = self.calculate_training_load(a)
            daily_loads[date_key] = daily_loads.get(date_key, 0) + load
        
        # ATL: Exponential Moving Average últimos 7 días
        atl = self._exponential_load(daily_loads, now, self.ATL_DECAY)
        
        # CTL: Exponential Moving Average últimos 42 días
        ctl = self._exponential_load(daily_loads, now, self.CTL_DECAY)
        
        # TSB = CTL - ATL (positivo = descansado, negativo = fatigado)
        tsb = ctl - atl
        
        # ACWR (Acute:Chronic Workload Ratio)
        acwr = round(atl / ctl, 2) if ctl > 0 else 0
        
        # Injury Risk basado en ACWR
        injury_risk = self._assess_injury_risk(acwr)
        
        # Readiness Score (0-100)
        readiness = self._calculate_readiness(tsb, acwr, activities, now)
        
        # Última actividad
        sorted_acts = sorted(activities, key=lambda x: x.date, reverse=True)
        last_date = sorted_acts[0].date
        days_since = (now - last_date).days
        
        return AthleteState(
            acute_load=round(atl, 1),
            chronic_load=round(ctl, 1),
            training_stress_balance=round(tsb, 1),
            acwr=acwr,
            injury_risk=injury_risk,
            readiness_score=round(readiness, 1),
            days_analyzed=42,
            activities_count=len(activities),
            last_activity_date=last_date,
            days_since_last=days_since
        )
    
    def _exponential_load(self, daily_loads: dict, now: datetime, decay_days: int) -> float:
        """Calcula carga con decaimiento exponencial"""
        total = 0
        for i in range(decay_days):
            date = now - timedelta(days=i)
            date_key = date.strftime("%Y-%m-%d")
            day_load = daily_loads.get(date_key, 0)
            
            # Peso exponencial: más reciente = más peso
            weight = math.exp(-i / decay_days)
            total += day_load * weight
        
        # Normalizar
        return total / decay_days
    
    def _assess_injury_risk(self, acwr: float) -> str:
        """
        Evalúa riesgo de lesión basado en ACWR.
        Sweet spot: 0.8 - 1.3 (Gabbett, 2016)
        """
        if acwr < 0.8:
            return "low"      # Subtrained, pero sin riesgo agudo
        elif acwr <= 1.3:
            return "low"      # Sweet spot
        elif acwr <= 1.5:
            return "moderate"  # Peligro creciente
        else:
            return "high"      # Alto riesgo de lesión
    
    def _calculate_readiness(self, tsb: float, acwr: float, activities: List[ActivityData], now: datetime) -> float:
        """Calcula readiness score 0-100"""
        score = 50.0  # base
        
        # TSB component (±25 puntos)
        if tsb > 0:
            score += min(25, tsb * 2)   # descansado = más ready
        else:
            score += max(-25, tsb * 1.5)  # fatigado = menos ready
        
        # ACWR component (±15 puntos)
        if 0.8 <= acwr <= 1.3:
            score += 15  # sweet spot
        elif acwr > 1.5:
            score -= 15  # sobrecarga
        elif acwr < 0.5:
            score -= 5   # muy poco entrenamiento
        
        # Días desde última actividad (±10 puntos)
        sorted_acts = sorted(activities, key=lambda x: x.date, reverse=True)
        days_since = (now - sorted_acts[0].date).days
        
        if days_since == 1:
            score += 5   # recuperación normal
        elif days_since == 2:
            score += 10  # buena recuperación
        elif days_since >= 4:
            score -= 5   # demasiado descanso
        
        return max(0, min(100, score))