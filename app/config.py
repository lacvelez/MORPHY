from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    STRAVA_CLIENT_ID: str
    STRAVA_CLIENT_SECRET: str
    DATABASE_URL: str = "postgresql://morphy:morphy_dev_2026@db:5432/morphy"
    STRAVA_REDIRECT_URI: str = "http://localhost:8000/auth/strava/callback"

    class Config:
        env_file = ".env"

settings = Settings()