-- ============================================
-- MORPHY PLAN — Sprint 14 Database Migration
-- ============================================

-- 1. Perfil deportivo del atleta
CREATE TABLE IF NOT EXISTS sport_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Disciplina y terreno
    discipline VARCHAR(50) NOT NULL, 
    -- 'running_urban' | 'trail_running' | 'ultra_trail' | 
    -- 'cycling_road' | 'cycling_mountain' | 'triathlon' | 'mixed'
    
    terrain_type VARCHAR(30) NOT NULL DEFAULT 'mixed',
    -- 'flat' | 'mixed' | 'mountain'
    
    -- Disponibilidad
    weekly_days_available INTEGER NOT NULL DEFAULT 4,
    preferred_long_run_day VARCHAR(10) DEFAULT 'sunday',
    
    -- Acceso indoor
    gym_access BOOLEAN DEFAULT FALSE,
    gym_equipment JSONB DEFAULT '[]',
    -- ["treadmill", "stairmaster", "elliptical", "spin_bike", 
    --  "curved_treadmill", "rowing"]
    
    -- Contexto de vida (de las 4 capas)
    experience_years DECIMAL(4,1),
    injury_history JSONB DEFAULT '[]',
    
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 2. Plan de entrenamiento
CREATE TABLE IF NOT EXISTS training_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sport_profile_id UUID REFERENCES sport_profiles(id),
    
    -- Objetivo de carrera
    race_name VARCHAR(200),
    race_type VARCHAR(30) NOT NULL,
    -- '10k' | '21k' | 'marathon' | 'ultra_50k' | 'ultra_100k' |
    -- 'mtb_race' | 'cycling_gran_fondo' | 'triathlon_sprint' |
    -- 'triathlon_olympic' | 'ironman'
    
    race_date DATE NOT NULL,
    race_distance_km DECIMAL(8,2),
    race_elevation_m INTEGER DEFAULT 0,    -- D+ total de la carrera
    target_time_minutes INTEGER,           -- opcional
    
    -- Baseline fisiológico al momento de crear el plan
    ctl_start DECIMAL(6,2),
    ctl_target DECIMAL(6,2),
    atl_start DECIMAL(6,2),
    tsb_start DECIMAL(6,2),
    
    -- Estructura del plan
    total_weeks INTEGER NOT NULL,
    build_weeks INTEGER NOT NULL,          -- semanas de construcción
    peak_weeks INTEGER NOT NULL DEFAULT 1, -- semanas de pico
    taper_weeks INTEGER NOT NULL DEFAULT 2,
    
    -- Estado
    status VARCHAR(20) DEFAULT 'active',
    -- 'draft' | 'active' | 'completed' | 'abandoned'
    
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 3. Semanas del plan
CREATE TABLE IF NOT EXISTS plan_weeks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
    
    week_number INTEGER NOT NULL,          -- 1 = primera semana
    week_start_date DATE NOT NULL,
    
    -- Fase de periodización
    phase VARCHAR(20) NOT NULL,
    -- 'base' | 'construccion' | 'pico' | 'tapering' | 'descarga'
    
    -- Objetivos de carga
    trimp_target DECIMAL(8,2),
    trimp_target_adjusted DECIMAL(8,2),    -- con factor desnivel
    elevation_target_m INTEGER DEFAULT 0,  -- D+ objetivo semanal
    volume_km_target DECIMAL(6,2),
    volume_hours_target DECIMAL(5,2),      -- para ultra (horas > km)
    sessions_planned INTEGER,
    
    -- Resultado real (se llena durante la semana)
    trimp_actual DECIMAL(8,2),
    elevation_actual_m INTEGER,
    volume_km_actual DECIMAL(6,2),
    compliance_pct DECIMAL(5,2),
    
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. Sesiones diarias
CREATE TABLE IF NOT EXISTS plan_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    week_id UUID NOT NULL REFERENCES plan_weeks(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES training_plans(id) ON DELETE CASCADE,
    
    session_date DATE NOT NULL,
    day_of_week INTEGER NOT NULL,          -- 0=lun, 6=dom
    
    -- Prescripción
    session_type VARCHAR(30) NOT NULL,
    -- 'easy' | 'tempo' | 'long_run' | 'intervals' | 
    -- 'trail_climb' | 'strength' | 'rest' | 'cross_training'
    
    zone_target VARCHAR(10),               -- 'Z1' | 'Z2' | 'Z3' | 'Z4' | 'Z5'
    zone_target_secondary VARCHAR(10),     -- para sesiones mixtas (calentamiento Z1 + Z3)
    
    duration_min INTEGER,
    distance_km_target DECIMAL(6,2),
    elevation_m_target INTEGER DEFAULT 0,  -- D+ objetivo por sesión
    trimp_target DECIMAL(7,2),
    trimp_target_adjusted DECIMAL(7,2),
    
    -- Equivalencias indoor (JSON)
    -- {"primary": {"equipment": "treadmill_incline", "duration_min": 45, 
    --   "setting": "8-10% incline", "notes": "Z2 equiv."}, 
    --  "alternatives": [...]}
    indoor_equivalents JSONB DEFAULT '[]',
    
    -- Resultado real
    completed BOOLEAN DEFAULT FALSE,
    actual_activity_id UUID REFERENCES activities(id),
    trimp_actual DECIMAL(7,2),
    elevation_actual_m INTEGER,
    compliance_note TEXT,
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_training_plans_user 
    ON training_plans(user_id, status);
CREATE INDEX IF NOT EXISTS idx_plan_weeks_plan 
    ON plan_weeks(plan_id, week_number);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_date 
    ON plan_sessions(session_date);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_week 
    ON plan_sessions(week_id);
CREATE INDEX IF NOT EXISTS idx_sport_profiles_user 
    ON sport_profiles(user_id);