from core.config import settings
from core.logging import setup_logging
import logging

# Initialize Logging
setup_logging(settings)

from services.inference import load_model
from services.storage import check_database_connections, engine
from services.schemas import Base
from api.endpoints import router as api_router
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the model on startup
    logging.info("Loading AI Model...")
    app.state.model = load_model()
    
    # Check Database Connections
    await check_database_connections()

    # Create Tables
    logging.info("Initializing Database Tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield
    # Cleanup on shutdown if needed
    logging.info("Shutting down...")

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Register Router
app.include_router(api_router)

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "config": "loaded",
        "project_name": settings.PROJECT_NAME,
        "model_status": "loaded" if hasattr(app.state, "model") else "not_loaded"
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_config=None)
