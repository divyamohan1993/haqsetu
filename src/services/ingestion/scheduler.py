"""Auto-update scheduler for the scheme ingestion pipeline.

Manages automatic, periodic execution of the ingestion pipeline to keep
scheme data fresh.

Development mode
    Uses an ``asyncio`` background task with sleep-based scheduling.
    The task runs in the same event loop as the FastAPI application and
    automatically performs daily incremental updates and weekly full
    ingestion runs.

Production mode
    Designed to be triggered externally via Google Cloud Scheduler
    calling the ``/api/v1/admin/ingest`` endpoint on a Cloud Run job.
    The scheduler itself does not run a background loop in production.

Schedule
--------
- **Full ingestion**: Weekly (Sunday 2:00 AM IST / Saturday 20:30 UTC).
- **Incremental update**: Daily (4:00 AM IST / previous day 22:30 UTC).
- **Priority update**: On-demand via the admin API when critical changes
  are detected.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.services.ingestion.pipeline import IngestionResult, SchemeIngestionPipeline

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# IST is UTC+5:30.  We schedule in UTC.
_FULL_INGESTION_DAY = 6  # Sunday (Monday=0, Sunday=6)
_FULL_INGESTION_HOUR_UTC = 20  # 20:30 UTC = 2:00 AM IST (next day)
_FULL_INGESTION_MINUTE_UTC = 30

_INCREMENTAL_HOUR_UTC = 22  # 22:30 UTC = 4:00 AM IST (next day)
_INCREMENTAL_MINUTE_UTC = 30


# ---------------------------------------------------------------------------
# IngestionScheduler
# ---------------------------------------------------------------------------


class IngestionScheduler:
    """Manages automatic scheme data updates.

    Parameters
    ----------
    pipeline:
        The :class:`SchemeIngestionPipeline` to execute.
    settings:
        Application settings object (used for ``is_production``,
        ``enable_auto_ingestion``, and ``ingestion_interval_hours``).
    """

    def __init__(
        self,
        pipeline: SchemeIngestionPipeline,
        settings: object,
    ) -> None:
        self._pipeline = pipeline
        self._settings = settings
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False
        self._last_full_run: datetime | None = None
        self._last_incremental_run: datetime | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the background scheduler loop is currently active."""
        return self._running

    @property
    def last_full_run(self) -> datetime | None:
        """Timestamp of the last full ingestion run."""
        return self._last_full_run

    @property
    def last_incremental_run(self) -> datetime | None:
        """Timestamp of the last incremental update."""
        return self._last_incremental_run

    # ------------------------------------------------------------------
    # Background scheduler (development mode)
    # ------------------------------------------------------------------

    async def start_background_scheduler(self) -> None:
        """Start the background scheduling loop for development mode.

        Creates an ``asyncio.Task`` that runs indefinitely, checking
        every 15 minutes whether it is time to trigger an ingestion.
        The loop is designed to be cancelled gracefully via :meth:`stop`.

        This method returns immediately after creating the background
        task.  The actual scheduling runs asynchronously.
        """
        enable_auto = getattr(self._settings, "enable_auto_ingestion", True)
        if not enable_auto:
            logger.info("scheduler.auto_ingestion_disabled")
            return

        self._running = True
        logger.info("scheduler.background_started")

        try:
            # Run an initial incremental update shortly after startup
            # (give the app 60 seconds to fully initialise)
            await asyncio.sleep(60)

            if self._running:
                logger.info("scheduler.initial_incremental_update")
                await self._safe_run(full=False)

            # Main scheduling loop
            interval_hours = getattr(
                self._settings, "ingestion_interval_hours", 24
            )
            check_interval_seconds = min(
                interval_hours * 3600 / 4, 900  # Check at most every 15 min
            )

            while self._running:
                await asyncio.sleep(check_interval_seconds)

                if not self._running:
                    break

                now = datetime.now(timezone.utc)

                # Check if it's time for a full ingestion (weekly)
                if self._should_run_full(now):
                    logger.info("scheduler.triggering_full_ingestion")
                    await self._safe_run(full=True)
                    self._last_full_run = now

                # Check if it's time for an incremental update (daily)
                elif self._should_run_incremental(now):
                    logger.info("scheduler.triggering_incremental_update")
                    await self._safe_run(full=False)
                    self._last_incremental_run = now

        except asyncio.CancelledError:
            logger.info("scheduler.background_cancelled")
        except Exception:
            logger.error("scheduler.background_error", exc_info=True)
        finally:
            self._running = False
            logger.info("scheduler.background_stopped")

    def _should_run_full(self, now: datetime) -> bool:
        """Check if conditions are met for a weekly full ingestion."""
        # Run on Sunday at ~20:30 UTC (2:00 AM IST Monday)
        if now.weekday() != _FULL_INGESTION_DAY:
            return False

        # Within the target hour window
        if now.hour != _FULL_INGESTION_HOUR_UTC:
            return False

        # Haven't run today
        if self._last_full_run is not None:
            if self._last_full_run.date() == now.date():
                return False

        return True

    def _should_run_incremental(self, now: datetime) -> bool:
        """Check if conditions are met for a daily incremental update."""
        # Run at ~22:30 UTC (4:00 AM IST next day)
        if now.hour != _INCREMENTAL_HOUR_UTC:
            return False

        # Haven't run today
        if self._last_incremental_run is not None:
            if self._last_incremental_run.date() == now.date():
                return False

        return True

    async def _safe_run(self, full: bool) -> IngestionResult | None:
        """Execute an ingestion run with error handling.

        Parameters
        ----------
        full:
            If ``True``, run a full ingestion.  Otherwise, run an
            incremental update.

        Returns
        -------
        IngestionResult | None
            The result if successful, ``None`` on failure.
        """
        try:
            if full:
                result = await self._pipeline.run_full_ingestion()
            else:
                result = await self._pipeline.run_incremental_update()

            logger.info(
                "scheduler.run_complete",
                full=full,
                total=result.total_fetched,
                new=result.new_schemes,
                updated=result.updated_schemes,
                errors=len(result.errors),
                duration_s=round(result.duration_seconds, 2),
            )
            return result

        except Exception:
            logger.error(
                "scheduler.run_failed", full=full, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # On-demand execution (for admin API / Cloud Scheduler)
    # ------------------------------------------------------------------

    async def run_scheduled_update(
        self, full: bool = False
    ) -> IngestionResult | None:
        """Entry point for on-demand / externally-scheduled updates.

        This method is called by the admin API endpoint or by Cloud
        Scheduler in production.

        Parameters
        ----------
        full:
            If ``True``, run a full ingestion.  Otherwise, run an
            incremental update.

        Returns
        -------
        IngestionResult | None
            The result if successful, ``None`` on failure.
        """
        logger.info("scheduler.manual_trigger", full=full)
        result = await self._safe_run(full=full)

        if result is not None:
            if full:
                self._last_full_run = datetime.now(timezone.utc)
            else:
                self._last_incremental_run = datetime.now(timezone.utc)

        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Gracefully stop the background scheduler.

        Sets the running flag to ``False`` and cancels the background
        task if it exists.  Waits for the task to finish (with a
        timeout).
        """
        logger.info("scheduler.stopping")
        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None

        logger.info("scheduler.stopped")
