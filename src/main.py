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

    # -- 12. Voice Agent service -----------------------------------------------
    voice_agent_service = None
    if settings.voice_agent_enabled and llm is not None:
        try:
            from src.services.voice_agent import VoiceAgentService

            voice_agent_service = VoiceAgentService(
                llm=llm,
                translation=translation,
            )
            logger.info("app.voice_agent_initialised")
        except Exception:
            logger.warning("app.voice_agent_init_failed", exc_info=True)
    app.state.voice_agent = voice_agent_service

    # -- 13. Document Scanner service ------------------------------------------
    doc_scanner = None
    if settings.document_scanner_enabled and llm is not None:
        try:
            from src.services.document_scanner import DocumentScannerService

            doc_scanner = DocumentScannerService(
                project_id=settings.gcp_project_id,
                llm=llm,
                translation=translation,
            )
            logger.info("app.document_scanner_initialised")
        except Exception:
            logger.warning("app.document_scanner_init_failed", exc_info=True)
    app.state.document_scanner = doc_scanner

    # -- 14. Legal Rights / BNS service ----------------------------------------
    legal_rights_service = None
    if settings.legal_rights_enabled and llm is not None:
        try:
            from src.services.legal_rights import LegalRightsService

            legal_rights_service = LegalRightsService(llm=llm)
            logger.info("app.legal_rights_initialised")
        except Exception:
            logger.warning("app.legal_rights_init_failed", exc_info=True)
    app.state.legal_rights = legal_rights_service

    # -- 15. RTI Generator service ---------------------------------------------
    rti_service = None
    if settings.rti_generator_enabled and llm is not None:
        try:
            from src.services.rti_generator import RTIGeneratorService

            rti_service = RTIGeneratorService(llm=llm, translation=translation)
            logger.info("app.rti_generator_initialised")
        except Exception:
            logger.warning("app.rti_generator_init_failed", exc_info=True)
    app.state.rti_generator = rti_service

    # -- 16. Emergency SOS service ---------------------------------------------
    sos_service = None
    if settings.emergency_sos_enabled:
        try:
            from src.services.emergency_sos import EmergencySOSService

            sos_service = EmergencySOSService()
            logger.info("app.emergency_sos_initialised")
        except Exception:
            logger.warning("app.emergency_sos_init_failed", exc_info=True)
    app.state.emergency_sos = sos_service

    # -- 17. Grievance Tracker service -----------------------------------------
    grievance_service = None
    if settings.grievance_tracker_enabled and llm is not None:
        try:
            from src.services.grievance_tracker import GrievanceTrackerService

            grievance_service = GrievanceTrackerService(llm=llm)
            logger.info("app.grievance_tracker_initialised")
        except Exception:
            logger.warning("app.grievance_tracker_init_failed", exc_info=True)
    app.state.grievance_tracker = grievance_service

    # -- 18. Nearby Services locator -------------------------------------------
    nearby_service = None
    if settings.nearby_services_enabled:
        try:
            from src.services.nearby_services import NearbyServicesLocator

            nearby_service = NearbyServicesLocator()
            logger.info("app.nearby_services_initialised")
        except Exception:
            logger.warning("app.nearby_services_init_failed", exc_info=True)
    app.state.nearby_services = nearby_service

    # -- 19. Accessibility service ---------------------------------------------
    a11y_service = None
    if settings.accessibility_enabled:
        try:
            from src.services.accessibility import AccessibilityService

            a11y_service = AccessibilityService(
                translation=translation,
                tts=tts,
            )
            logger.info("app.accessibility_initialised")
        except Exception:
            logger.warning("app.accessibility_init_failed", exc_info=True)
    app.state.accessibility = a11y_service

    # -- 20. Compliance Audit service ------------------------------------------
    compliance_service = None
    if settings.compliance_audit_enabled:
        try:
            from src.services.compliance_audit import ComplianceAuditService

            compliance_service = ComplianceAuditService(
                retention_days=settings.audit_log_retention_days,
            )
            logger.info("app.compliance_audit_initialised")
        except Exception:
            logger.warning("app.compliance_audit_init_failed", exc_info=True)
    app.state.compliance_audit = compliance_service

    # -- 21. Self-Sustaining automation service --------------------------------
    self_sustaining_service = None
    if settings.self_sustaining_enabled:
        try:
            from src.services.self_sustaining import SelfSustainingService

            self_sustaining_service = SelfSustainingService(
                project_id=settings.gcp_project_id,
                budget_limit=settings.monthly_budget_limit_usd,
                stale_threshold_days=settings.stale_data_threshold_days,
            )
            logger.info("app.self_sustaining_initialised")
        except Exception:
            logger.warning("app.self_sustaining_init_failed", exc_info=True)
    app.state.self_sustaining = self_sustaining_service

    # -- 22. WhatsApp/SMS Messaging service ------------------------------------
    messaging_service = None
    if settings.whatsapp_enabled:
        try:
            from src.services.whatsapp_sms import MessagingService

            messaging_service = MessagingService(
                whatsapp_phone_id=settings.whatsapp_phone_number_id,
                whatsapp_token=settings.whatsapp_access_token,
                sms_provider=settings.sms_provider,
                sms_api_key=settings.sms_api_key,
            )
            logger.info("app.messaging_initialised")
        except Exception:
            logger.warning("app.messaging_init_failed", exc_info=True)
    app.state.messaging = messaging_service

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
# SECURITY: allow_credentials=True must NOT be combined with allow_origins=["*"]
# per the CORS specification (browsers will reject it).
if settings.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-DPDPA-Consent", "X-Admin-API-Key"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS", "HEAD"],
        allow_headers=["Content-Type", "Accept", "Authorization", "X-DPDPA-Consent", "X-Admin-API-Key"],
    )

# -- Custom middleware ------------------------------------------------------
app.add_middleware(DPDPAMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    max_requests_per_minute=settings.rate_limit_per_minute,
    trusted_proxy_count=settings.trusted_proxy_count,
)

# -- Prometheus metrics -----------------------------------------------------
# SECURITY: In production, metrics are exposed only internally (scraped by
# Prometheus within the cluster).  The endpoint is not rate-limited but
# is excluded from public documentation.
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/api/v1/health"],
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=not settings.is_production,
    )
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
            "voice_agent": "/api/v1/voice-agent",
            "document_scanner": "/api/v1/document",
            "legal_rights": "/api/v1/legal-rights",
            "rti_generator": "/api/v1/rti",
            "emergency_sos": "/api/v1/emergency",
            "grievance_tracker": "/api/v1/grievance",
            "nearby_services": "/api/v1/nearby",
            "accessibility": "/api/v1/accessibility",
            "sustainability": "/api/v1/sustainability",
        },
        "features": [
            "Voice-first conversational agent with proactive rights detection",
            "Document scanner & plain-language explainer (Vision AI OCR)",
            "BNS/BNSS/BSA legal rights narrator",
            "Automated RTI application generator",
            "Emergency SOS legal distress system",
            "Cross-portal grievance tracker",
            "Nearby CSC/DLSA/office finder",
            "Accessibility: ISL sign language, screen reader, haptic, Braille",
            "Family-level scheme eligibility matching",
            "Multi-source scheme verification (5 government sources)",
            "Self-sustaining GCP automation (auto-heal, cost monitor, stale data)",
            "DPDPA compliance audit trail",
            "WhatsApp/SMS integration for feature phones",
            "23 Indian language support",
        ],
    }
