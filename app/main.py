from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from app.database import engine, Base
from app.models.models import User, Integration, Activity
from app.routers import auth, decision

@asynccontextmanager
async def lifespan(app: FastAPI):
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

# Servir archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/health")
async def health():
    return {"status": "healthy"}