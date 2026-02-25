"""HaqSetu FastAPI application entry point.

Creates the FastAPI app, configures middleware, includes routers, and
manages the lifecycle of all backend services (Translation, Speech, LLM,
RAG, SchemeSearch, Cache, Hinglish, Orchestrator).
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import settings
from src.api.router import api_router
from src.middleware.privacy import DPDPAMiddleware
from src.middleware.rate_limit import RateLimitMiddleware

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured logging configuration
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Set up structlog with JSON or console rendering based on settings."""
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.get_level_from_name(settings.log_level),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of all HaqSetu services.

    On startup:
      1. Initialise cache manager
      2. Initialise Translation service
      3. Initialise Speech-to-Text and Text-to-Speech services
      4. Initialise LLM, SchemeSearch, and Hinglish services
      5. Load and index scheme data
      6. Initialise ingestion pipeline and auto-update scheduler
      7. Create the QueryOrchestrator
      8. Store everything on ``app.state``

    On shutdown:
      - Stop ingestion scheduler.
      - Close all HTTP clients and caches gracefully.
    """
    _configure_logging()
    logger.info(
        "app.startup",
        env=settings.env,
        gcp_project=settings.gcp_project_id,
        region=settings.gcp_region,
    )

    app.state.start_time = time.time()

    # -- 1. Cache -----------------------------------------------------------
    from src.services.cache import CacheManager

    cache = CacheManager(
        redis_url=settings.redis_url if settings.redis_url else None,
        namespace="haqsetu:",
    )
    app.state.cache = cache
    logger.info("app.cache_initialised")

    # -- 2. Translation -----------------------------------------------------
    from src.services.translation import TranslationService

    translation = TranslationService(
        project_id=settings.gcp_project_id,
        region=settings.gcp_region,
        cache=CacheManager.for_namespace(
            "translation:",
            redis_url=settings.redis_url if settings.redis_url else None,
        ),
    )
    app.state.translation = translation
    logger.info("app.translation_initialised")

    # -- 3. Speech services -------------------------------------------------
    from src.services.speech import SpeechToTextService, TextToSpeechService

    stt: SpeechToTextService | None = None
    tts: TextToSpeechService | None = None

    if settings.gcp_project_id:
        try:
            stt = SpeechToTextService(
                project_id=settings.gcp_project_id,
                region=settings.gcp_region,
            )
            logger.info("app.stt_initialised")
        except Exception:
            logger.warning("app.stt_init_failed", exc_info=True)

        try:
            tts = TextToSpeechService(project_id=settings.gcp_project_id)
            logger.info("app.tts_initialised")
        except Exception:
            logger.warning("app.tts_init_failed", exc_info=True)

    app.state.stt = stt
    app.state.tts = tts

    # -- 4. LLM service (Vertex AI / Gemini) --------------------------------
    from src.services.llm import LLMService

    llm: LLMService | None = None
    if settings.gcp_project_id:
        try:
            llm = LLMService(
                project_id=settings.gcp_project_id,
                region=settings.vertex_ai_location,
                model_name=settings.vertex_ai_model,
            )
            logger.info("app.llm_initialised", model=settings.vertex_ai_model)
        except Exception:
            logger.warning("app.llm_init_failed", exc_info=True)

    app.state.llm = llm

    # -- 5. Hinglish processor (pure Python, no external deps) -------------
    from src.services.hinglish import HinglishProcessor

    hinglish = HinglishProcessor()
    app.state.hinglish = hinglish
    logger.info("app.hinglish_initialised")

    # -- 6. RAG service and scheme search ------------------------------------
    from src.services.rag import RAGService
    from src.services.scheme_search import SchemeSearchService

    rag = RAGService()
    scheme_search = SchemeSearchService(rag=rag, cache=cache)
    app.state.scheme_search = scheme_search
    logger.info("app.scheme_search_initialised")

    # -- 7. Load and index scheme data via seed module ----------------------
    app.state.scheme_data = []
    try:
        from src.data.seed import seed_scheme_data

        scheme_data = await seed_scheme_data(scheme_search)
        app.state.scheme_data = scheme_data
        logger.info("app.scheme_data_loaded", count=len(scheme_data))
    except Exception:
        logger.warning("app.scheme_data_load_failed", exc_info=True)

    # -- 8. Initialise ingestion pipeline for auto-updates ------------------
    from src.services.ingestion import (
        DataGovClient,
        IngestionScheduler,
        MySchemeClient,
        SchemeIngestionPipeline,
    )

    ingestion_cache = CacheManager.for_namespace(
        "ingestion:",
        redis_url=settings.redis_url if settings.redis_url else None,
    )

    myscheme_client = MySchemeClient(
        cache=ingestion_cache,
        rate_limit_delay=settings.myscheme_rate_limit_delay,
    )
    datagov_client = DataGovClient(
        cache=ingestion_cache,
        api_key=settings.data_gov_api_key,
    )
    pipeline = SchemeIngestionPipeline(
        myscheme=myscheme_client,
        datagov=datagov_client,
        cache=ingestion_cache,
        translation=translation,
    )
    app.state.ingestion_pipeline = pipeline
    app.state.myscheme_client = myscheme_client
    app.state.datagov_client = datagov_client
    logger.info("app.ingestion_pipeline_initialised")

    # Start background scheduler in development mode
    scheduler: IngestionScheduler | None = None
    if not settings.is_production and settings.enable_auto_ingestion:
        scheduler = IngestionScheduler(pipeline=pipeline, settings=settings)
        asyncio.create_task(scheduler.start_background_scheduler())
        app.state.scheduler = scheduler
        logger.info("app.ingestion_scheduler_started")
    else:
        app.state.scheduler = None

    # -- 9. Initialise verification services ---------------------------------
    from src.services.changelog import SchemeChangelogService
    from src.services.verification.engine import SchemeVerificationEngine
    from src.services.verification.gazette_client import GazetteClient

    verification_cache = CacheManager.for_namespace(
        "verification:",
        redis_url=settings.redis_url if settings.redis_url else None,
    )

    gazette_client = GazetteClient(cache=verification_cache)

    # Import optional clients (sansad, indiacode) — graceful if not yet available
    sansad_client = None
    indiacode_client = None
    try:
        from src.services.verification.sansad_client import SansadClient

        sansad_client = SansadClient(cache=verification_cache)
        logger.info("app.sansad_client_initialised")
    except ImportError:
        logger.warning("app.sansad_client_not_available")

    try:
        from src.services.verification.indiacode_client import IndiaCodeClient

        indiacode_client = IndiaCodeClient(cache=verification_cache)
        logger.info("app.indiacode_client_initialised")
    except ImportError:
        logger.warning("app.indiacode_client_not_available")

    verification_engine = SchemeVerificationEngine(
        gazette_client=gazette_client,
        sansad_client=sansad_client,
        indiacode_client=indiacode_client,
        myscheme_client=myscheme_client,
        datagov_client=datagov_client,
        cache=verification_cache,
    )
    app.state.verification_engine = verification_engine
    app.state.verification_results = {}  # scheme_id -> VerificationResult
    app.state.gazette_client = gazette_client
    app.state.sansad_client = sansad_client
    app.state.indiacode_client = indiacode_client
    logger.info("app.verification_engine_initialised")

    # -- 10. Initialise changelog service -----------------------------------
    changelog_service = SchemeChangelogService(cache=cache)
    app.state.changelog_service = changelog_service
    logger.info("app.changelog_service_initialised")

    # -- 11. Create orchestrator --------------------------------------------
    from src.pipeline.orchestrator import QueryOrchestrator

    # Use a fallback LLM service if the real one failed to initialise.
    # The orchestrator will handle errors gracefully during query processing.
    if llm is None:
        logger.warning("app.orchestrator_no_llm", note="LLM service not available; queries will use fallbacks")
        from src.services.llm import LLMService as _LLMService

        llm = _LLMService(
            project_id=settings.gcp_project_id or "placeholder",
            region=settings.vertex_ai_location,
            model_name=settings.vertex_ai_model,
        )

    orchestrator = QueryOrchestrator(
        translation=translation,
        llm=llm,
        scheme_search=scheme_search,
        hinglish=hinglish,
        cache=cache,
        speech_to_text=stt,
        text_to_speech=tts,
    )
    app.state.orchestrator = orchestrator
    logger.info("app.orchestrator_initialised")

    logger.info("app.startup_complete")

    yield

    # -- Shutdown -----------------------------------------------------------
    logger.info("app.shutdown_start")

    # Stop ingestion scheduler
    if scheduler is not None:
        await scheduler.stop()

    # Close verification HTTP clients
    await gazette_client.close()
    if sansad_client is not None:
        await sansad_client.close()
    if indiacode_client is not None:
        await indiacode_client.close()
    await verification_cache.close()

    # Close ingestion HTTP clients
    await myscheme_client.close()
    await datagov_client.close()
    await ingestion_cache.close()

    if stt is not None:
        await stt.close()
    if tts is not None:
        await tts.close()
    await cache.close()

    logger.info("app.shutdown_complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HaqSetu API",
    description=(
        "HaqSetu (हक़सेतु) -- Voice-First AI Civic Assistant for Rural India. "
        "Provides multilingual access to government scheme information across "
        "all 22 Scheduled Languages of India plus English."
    ),
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# -- CORS middleware --------------------------------------------------------
if settings.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://haqsetu.in", "https://www.haqsetu.in"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# -- Custom middleware ------------------------------------------------------
app.add_middleware(DPDPAMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    max_requests_per_minute=settings.rate_limit_per_minute,
    trusted_proxy_count=settings.trusted_proxy_count,
)

# -- Prometheus metrics -----------------------------------------------------
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/api/v1/health"],
    ).instrument(app).expose(app, endpoint="/metrics")
    logger.info("app.prometheus_metrics_enabled")
except ImportError:
    logger.warning("app.prometheus_not_available")

# -- Include routers -------------------------------------------------------
app.include_router(api_router)

# -- Static files ----------------------------------------------------------
import pathlib

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STATIC_DIR = _PROJECT_ROOT / "static"
_TEMPLATES_DIR = _PROJECT_ROOT / "templates"

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.info("app.static_files_mounted", path=str(_STATIC_DIR))


# -- Root endpoint (serves skeuomorphic dashboard) -------------------------


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    """Serve the HaqSetu skeuomorphic dashboard."""
    index_path = _TEMPLATES_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    # Fallback to JSON API info if no template exists
    return HTMLResponse(
        content="<h1>HaqSetu API</h1><p>Dashboard template not found. "
        'Visit <a href="/docs">/docs</a> for API documentation.</p>',
        status_code=200,
    )


@app.get("/api", response_class=ORJSONResponse)
async def api_info() -> dict:
    """API information endpoint."""
    return {
        "name": "HaqSetu API",
        "description": "Voice-First AI Civic Assistant for Rural India",
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "languages_supported": 23,
        "dashboard": "/",
        "verification_sources": [
            "Gazette of India (egazette.gov.in)",
            "India Code (indiacode.nic.in)",
            "Parliament (sansad.in)",
            "MyScheme (myscheme.gov.in)",
            "data.gov.in",
        ],
        "endpoints": {
            "query": "/api/v1/query",
            "voice": "/api/v1/voice",
            "schemes": "/api/v1/schemes",
            "verification": "/api/v1/verification",
            "feedback": "/api/v1/feedback",
            "languages": "/api/v1/languages",
            "health": "/api/v1/health",
        },
    }
