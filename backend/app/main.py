from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.app.api.endpoints import router as api_router
from backend.app.db.database import engine, Base
import backend.app.db.models  # Import models to register them

import logging
from backend.app.core.logging_config import setup_structured_logging

setup_structured_logging()
logger = logging.getLogger("cloudlens.main")


# Safely create tables if database is available
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully.")
except Exception as db_init_err:
    logger.warning(f"Could not connect to database on startup (running in resilient mode): {db_init_err}")


app = FastAPI(
    title="Autonomous Cloud RCA & Incident Correlation Platform",
    description="Deterministic rule-based and graph-theoretic Root Cause Analysis engine for cloud microservices",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Router
app.include_router(api_router, prefix="/api")

@app.on_event("startup")
async def startup_event():
    import anyio.to_thread
    from backend.app.core.config import config
    try:
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = config.database.thread_limiter_tokens
    except Exception as e:
        logger.warning(f"Could not adjust anyio thread limiter: {e}")

    # Check database pool limit against worker count
    total_pool_conns = (config.database.pool_size + config.database.max_overflow) * config.database.workers
    if total_pool_conns > config.database.max_db_connections:
        logger.warning(
            f"WARNING: Database pool limit risk! (pool_size={config.database.pool_size} + max_overflow={config.database.max_overflow}) "
            f"* workers={config.database.workers} = {total_pool_conns} exceeds max_connections={config.database.max_db_connections}."
        )
    else:
        logger.info(
            f"Database pool configured safely: {config.database.pool_size} base + {config.database.max_overflow} overflow "
            f"across {config.database.workers} worker(s) (Total {total_pool_conns} <= {config.database.max_db_connections} max)."
        )

    from backend.app.services.outbox_worker import outbox_worker
    outbox_worker.start()

    from backend.app.services.correlator_consumer import correlator_consumer
    if config.correlator.embedded:
        correlator_consumer.start()
    else:
        logger.info("Correlator consumer embedded execution disabled (running as standalone worker process).")

    logger.info("CloudLens application startup sequence completed.")

@app.on_event("shutdown")
def shutdown_event():
    from backend.app.core.config import config
    from backend.app.services.correlator_consumer import correlator_consumer
    if config.correlator.embedded:
        correlator_consumer.stop(timeout=5.0)

    from backend.app.services.outbox_worker import outbox_worker
    outbox_worker.stop(timeout=5.0)

    logger.info("CloudLens graceful shutdown completed.")

# Real-Time WebSocket Streaming Endpoint
from fastapi import WebSocket, WebSocketDisconnect
from backend.app.services.websocket_manager import ws_manager

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, listen for client heartbeat
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)

# Mount Static Frontend
frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"

if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
