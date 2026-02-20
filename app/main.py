from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.database import engine, Base
from app.models.models import User, Integration, Activity  # importar para registrar modelos
from app.routers import auth, decision

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear tablas al iniciar
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables created")
    yield

app = FastAPI(
    title="MORPHY",
    description="Autonomous training decision engine for serious athletes",
    version="0.1.0",
    lifespan=lifespan
)

app.include_router(auth.router)
app.include_router(decision.router)

@app.get("/")
async def root():
    return {"status": "alive", "app": "MORPHY", "version": "0.1.0"}

@app.get("/health")
async def health():
    return {"status": "healthy"}