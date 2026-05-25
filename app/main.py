import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.security import limiter
from app.api.v1 import health, score, alert, feedback, novelty, analyze_graph

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.graph.precompute.nightly_batch import start_scheduler, stop_scheduler
    from app.detection.novelty.isolation_forest import novelty_detector
    logger.info("BLING Blue Team API starting", version=settings.model_version)
    start_scheduler()
    novelty_loaded = novelty_detector.load()
    if novelty_loaded:
        logger.info("novelty_detection_enabled")
    else:
        logger.warning("novelty_detection_disabled", fix="Run: python ml/train_isolation_forest.py")
    yield
    stop_scheduler()
    logger.info("BLING Blue Team API shutting down")


app = FastAPI(
    title="BLING Blue Team Detection Engine",
    description="Post-transaction forensic fraud detection API",
    version=settings.model_version,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Routers
app.include_router(health.router, tags=["health"])
app.include_router(score.router, tags=["detection"])
app.include_router(alert.router, tags=["investigation"])
app.include_router(feedback.router, tags=["investigation"])
app.include_router(novelty.router, tags=["novelty"])
app.include_router(analyze_graph.router, tags=["graph-analysis"])


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"data": None, "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}, "meta": {}},
    )
