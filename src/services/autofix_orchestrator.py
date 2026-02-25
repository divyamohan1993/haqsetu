"""AI-Powered Auto-Fix Orchestrator for HaqSetu.

Uses Gemini 3 Pro (via Vertex AI) to:
    1. Analyze system state (health, data integrity, performance)
    2. Diagnose root causes of issues
    3. Generate and execute fix plans
    4. Validate fixes and report results

Falls back to rule-based fixes when Gemini is unavailable.

Can also delegate to Claude CLI on the GCP VM instance for
deep code-level auto-fix when the admin provides device login.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompts for Gemini-powered diagnosis
# ---------------------------------------------------------------------------

_SYSTEM_DIAGNOSIS_PROMPT: Final[str] = """\
You are the HaqSetu Auto-Fix Orchestrator, an AI system administrator for \
India's government scheme verification platform. You analyze system health \
data, diagnose issues, and generate executable fix plans.

Your responsibilities:
1. DIAGNOSE: Identify root causes from system status, error logs, and metrics
2. PRIORITIZE: Rank issues by severity (critical > high > medium > low)
3. PLAN: Generate specific, safe fix actions that can be executed programmatically
4. VALIDATE: Verify fixes don't break existing functionality

You MUST return a JSON object with this structure:
{
  "diagnosis": [
    {
      "issue": "description of the issue",
      "severity": "critical|high|medium|low",
      "root_cause": "explanation of why this is happening",
      "fix_action": "action_name",
      "fix_params": {},
      "estimated_impact": "what will change when fixed",
      "risk_level": "none|low|medium|high"
    }
  ],
  "summary": "one-line overall assessment",
  "recommended_order": ["action1", "action2", ...],
  "requires_human_approval": true|false
}

SAFETY RULES:
- NEVER recommend deleting user data (profiles, feedback) without explicit admin request
- NEVER recommend disabling security features (rate limiting, auth, encryption)
- ALWAYS recommend creating a snapshot before destructive operations
- If unsure, set requires_human_approval to true
"""

_ANALYZE_PROMPT: Final[str] = """\
Analyze the following HaqSetu system state and generate a diagnosis with fix plan.

SYSTEM STATUS:
{system_status}

DATA INTEGRITY:
{data_integrity}

RECENT ERRORS:
{recent_errors}

PERFORMANCE METRICS:
{performance_metrics}

Return a JSON diagnosis object.\
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FixAction:
    """A single auto-fix action to execute."""

    action_id: str = ""
    action_name: str = ""
    description: str = ""
    severity: str = "medium"
    risk_level: str = "low"
    status: str = "pending"  # pending, running, completed, failed, skipped
    result: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(slots=True)
class DiagnosisReport:
    """Complete diagnosis and fix report from the orchestrator."""

    report_id: str = ""
    diagnosis_source: str = "gemini"  # "gemini" or "rules"
    model_used: str = ""
    summary: str = ""
    issues_found: int = 0
    fixes_planned: int = 0
    fixes_executed: int = 0
    fixes_succeeded: int = 0
    fixes_failed: int = 0
    actions: list[FixAction] = field(default_factory=list)
    requires_human_approval: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_seconds: float = 0.0
    raw_diagnosis: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Available fix actions (safe, pre-approved operations)
# ---------------------------------------------------------------------------

SAFE_ACTIONS: Final[dict[str, str]] = {
    "rebuild_feedback_index": "Rebuild the feedback index from the feedback store",
    "remove_orphan_verifications": "Remove verification results for non-existent schemes",
    "flush_stale_cache": "Clear expired cache entries",
    "create_snapshot": "Create a backup snapshot before fixes",
    "reload_scheme_data": "Reload scheme data from seed files",
    "restart_health_checks": "Reset health check failure counters",
    "compact_audit_log": "Trim audit log to last 1000 entries",
    "validate_scheme_ids": "Check all scheme IDs are unique and non-empty",
    "fix_feedback_references": "Clean feedback entries referencing deleted schemes",
    "reset_rate_limit_counters": "Clear rate limit counters for all IPs",
}


# ---------------------------------------------------------------------------
# AutoFixOrchestrator
# ---------------------------------------------------------------------------


class AutoFixOrchestrator:
    """AI-powered system diagnosis and auto-fix orchestrator.

    Uses Gemini 3 Pro for intelligent diagnosis when available,
    falls back to rule-based analysis otherwise.

    Parameters
    ----------
    project_id:
        GCP project ID for Vertex AI calls.
    model_name:
        Gemini model to use. Default: ``gemini-3.0-pro``.
    region:
        GCP region for Vertex AI. Default: ``asia-south1``.
    """

    __slots__ = (
        "_history",
        "_model",
        "_model_name",
        "_project_id",
        "_region",
    )

    def __init__(
        self,
        project_id: str = "",
        model_name: str = "gemini-3.0-pro",
        region: str = "asia-south1",
    ) -> None:
        self._project_id = project_id
        self._model_name = model_name
        self._region = region
        self._model: Any = None
        self._history: list[DiagnosisReport] = []

    # ------------------------------------------------------------------
    # Gemini initialisation
    # ------------------------------------------------------------------

    def _ensure_model(self) -> bool:
        """Lazily initialise the Gemini model. Returns True if available."""
        if self._model is not None:
            return True

        if not self._project_id:
            logger.info("autofix.no_project_id", note="Running in rule-based mode")
            return False

        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            vertexai.init(project=self._project_id, location=self._region)
            self._model = GenerativeModel(
                self._model_name,
                system_instruction=_SYSTEM_DIAGNOSIS_PROMPT,
            )
            logger.info(
                "autofix.model_initialised",
                model=self._model_name,
                region=self._region,
            )
            return True
        except Exception as exc:
            logger.warning(
                "autofix.model_init_failed",
                model=self._model_name,
                error=str(exc),
            )
            return False

    # ------------------------------------------------------------------
    # Main entry: diagnose and fix
    # ------------------------------------------------------------------

    async def diagnose_and_fix(
        self,
        app_state: Any,
        execute_fixes: bool = True,
        auto_approve: bool = False,
    ) -> DiagnosisReport:
        """Run full diagnosis and optionally execute fixes.

        Parameters
        ----------
        app_state:
            The FastAPI ``app.state`` object containing all services.
        execute_fixes:
            If True, execute the recommended fixes automatically.
        auto_approve:
            If True, execute even actions marked requires_human_approval.

        Returns
        -------
        DiagnosisReport
        """
        start = time.monotonic()
        report_id = f"diag-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"

        # Step 1: Gather system state
        system_state = self._gather_system_state(app_state)

        # Step 2: Diagnose (Gemini or rules)
        if self._ensure_model():
            diagnosis = await self._gemini_diagnose(system_state)
            source = "gemini"
        else:
            diagnosis = self._rule_based_diagnose(system_state)
            source = "rules"

        # Step 3: Build fix actions
        actions = self._build_actions(diagnosis)

        report = DiagnosisReport(
            report_id=report_id,
            diagnosis_source=source,
            model_used=self._model_name if source == "gemini" else "rule-engine",
            summary=diagnosis.get("summary", "Diagnosis complete."),
            issues_found=len(diagnosis.get("diagnosis", [])),
            fixes_planned=len(actions),
            actions=actions,
            requires_human_approval=diagnosis.get("requires_human_approval", False),
            raw_diagnosis=diagnosis,
        )

        # Step 4: Execute fixes if requested
        if execute_fixes and (auto_approve or not report.requires_human_approval):
            await self._execute_fixes(report, app_state)

        report.duration_seconds = round(time.monotonic() - start, 3)

        # Store in history
        self._history.insert(0, report)
        if len(self._history) > 50:
            self._history.pop()

        logger.info(
            "autofix.diagnosis_complete",
            report_id=report_id,
            source=source,
            issues=report.issues_found,
            fixes_executed=report.fixes_executed,
            fixes_succeeded=report.fixes_succeeded,
            duration_s=report.duration_seconds,
        )

        return report

    # ------------------------------------------------------------------
    # State gathering
    # ------------------------------------------------------------------

    def _gather_system_state(self, app_state: Any) -> dict[str, Any]:
        """Collect all relevant system state for diagnosis."""
        from src.api.v1.feedback import _feedback_index, _feedback_store
        from src.api.v1.profile import _profiles

        scheme_data = getattr(app_state, "scheme_data", [])
        verification_results = getattr(app_state, "verification_results", {})
        cache = getattr(app_state, "cache", None)
        self_sustaining = getattr(app_state, "self_sustaining", None)

        scheme_ids = {getattr(s, "scheme_id", "") for s in scheme_data}

        # Detect issues
        orphan_verifications = [k for k in verification_results if k not in scheme_ids]
        orphan_feedback = sum(
            1 for fb in _feedback_store.values()
            if fb.scheme_id and fb.scheme_id not in scheme_ids
        )
        index_mismatches = len(_feedback_index) - len(
            [fid for fid in _feedback_index if fid in _feedback_store]
        )
        missing_from_index = len(
            [fid for fid in _feedback_store if fid not in _feedback_index]
        )
        duplicate_scheme_ids = len(scheme_data) - len(scheme_ids)
        profiles_without_consent = sum(
            1 for p in _profiles.values() if not p.consent_given
        )
        profiles_without_location = sum(
            1 for p in _profiles.values() if not p.state and not p.pin_code
        )

        return {
            "system_status": {
                "schemes_loaded": len(scheme_data),
                "verifications": len(verification_results),
                "feedback_entries": len(_feedback_store),
                "feedback_index_size": len(_feedback_index),
                "profiles": len(_profiles),
                "cache_available": cache is not None,
                "self_sustaining_active": self_sustaining is not None,
            },
            "data_integrity": {
                "orphan_verifications": len(orphan_verifications),
                "orphan_feedback": orphan_feedback,
                "index_mismatches": index_mismatches,
                "missing_from_index": missing_from_index,
                "duplicate_scheme_ids": duplicate_scheme_ids,
                "profiles_without_consent": profiles_without_consent,
                "profiles_without_location": profiles_without_location,
            },
            "recent_errors": [],
            "performance_metrics": {
                "uptime_seconds": time.time() - getattr(app_state, "start_time", time.time()),
            },
        }

    # ------------------------------------------------------------------
    # Gemini-powered diagnosis
    # ------------------------------------------------------------------

    async def _gemini_diagnose(self, state: dict[str, Any]) -> dict[str, Any]:
        """Use Gemini 3 Pro to analyze system state and generate fixes."""
        try:
            from vertexai.generative_models import GenerationConfig

            prompt = _ANALYZE_PROMPT.format(
                system_status=json.dumps(state["system_status"], indent=2),
                data_integrity=json.dumps(state["data_integrity"], indent=2),
                recent_errors=json.dumps(state.get("recent_errors", []), indent=2),
                performance_metrics=json.dumps(state.get("performance_metrics", {}), indent=2),
            )

            response = self._model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                ),
            )

            text = response.text.strip()
            diagnosis = json.loads(text)

            logger.info(
                "autofix.gemini_diagnosis_complete",
                issues=len(diagnosis.get("diagnosis", [])),
            )

            return diagnosis

        except Exception as exc:
            logger.warning(
                "autofix.gemini_diagnosis_failed",
                error=str(exc),
                exc_info=True,
            )
            # Fall back to rule-based
            return self._rule_based_diagnose(state)

    # ------------------------------------------------------------------
    # Rule-based fallback diagnosis
    # ------------------------------------------------------------------

    def _rule_based_diagnose(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic rule-based diagnosis when Gemini is unavailable."""
        integrity = state.get("data_integrity", {})
        status = state.get("system_status", {})
        issues: list[dict[str, Any]] = []

        # Rule 1: Orphan verifications
        if integrity.get("orphan_verifications", 0) > 0:
            issues.append({
                "issue": f"{integrity['orphan_verifications']} verification results reference non-existent schemes",
                "severity": "medium",
                "root_cause": "Schemes were deleted but verification results were not cleaned up",
                "fix_action": "remove_orphan_verifications",
                "fix_params": {},
                "estimated_impact": "Removes stale verification data, frees memory",
                "risk_level": "none",
            })

        # Rule 2: Feedback index mismatches
        if integrity.get("index_mismatches", 0) > 0:
            issues.append({
                "issue": f"{integrity['index_mismatches']} feedback index entries point to missing store entries",
                "severity": "high",
                "root_cause": "Feedback store and index fell out of sync (likely after partial writes)",
                "fix_action": "rebuild_feedback_index",
                "fix_params": {},
                "estimated_impact": "Feedback listing will return correct results",
                "risk_level": "none",
            })

        # Rule 3: Missing from index
        if integrity.get("missing_from_index", 0) > 0:
            issues.append({
                "issue": f"{integrity['missing_from_index']} feedback entries are not in the index",
                "severity": "high",
                "root_cause": "Feedback entries added to store but index write failed",
                "fix_action": "rebuild_feedback_index",
                "fix_params": {},
                "estimated_impact": "All feedback will appear in listings",
                "risk_level": "none",
            })

        # Rule 4: No scheme data
        if status.get("schemes_loaded", 0) == 0:
            issues.append({
                "issue": "No scheme data loaded",
                "severity": "critical",
                "root_cause": "Seed data failed to load or was never initialized",
                "fix_action": "reload_scheme_data",
                "fix_params": {},
                "estimated_impact": "Scheme search and eligibility matching will work again",
                "risk_level": "low",
            })

        # Rule 5: Duplicate scheme IDs
        if integrity.get("duplicate_scheme_ids", 0) > 0:
            issues.append({
                "issue": f"{integrity['duplicate_scheme_ids']} duplicate scheme IDs detected",
                "severity": "medium",
                "root_cause": "Scheme data contains duplicate entries",
                "fix_action": "validate_scheme_ids",
                "fix_params": {},
                "estimated_impact": "Deduplicates scheme data for correct search results",
                "risk_level": "low",
            })

        # Rule 6: Orphan feedback
        if integrity.get("orphan_feedback", 0) > 0:
            issues.append({
                "issue": f"{integrity['orphan_feedback']} feedback entries reference non-existent schemes",
                "severity": "low",
                "root_cause": "Schemes were removed after feedback was submitted",
                "fix_action": "fix_feedback_references",
                "fix_params": {},
                "estimated_impact": "Cleans up dangling scheme references in feedback",
                "risk_level": "none",
            })

        # Rule 7: Profiles without consent (DPDPA violation)
        if integrity.get("profiles_without_consent", 0) > 0:
            issues.append({
                "issue": f"{integrity['profiles_without_consent']} profiles lack DPDPA consent",
                "severity": "critical",
                "root_cause": "Profiles were created bypassing consent validation",
                "fix_action": "flag_consent_violation",
                "fix_params": {},
                "estimated_impact": "Flags profiles for review; no data deleted without admin approval",
                "risk_level": "none",
            })

        # Always recommend snapshot if there are issues to fix
        if issues:
            issues.insert(0, {
                "issue": "Pre-fix safety snapshot recommended",
                "severity": "low",
                "root_cause": "Best practice before executing any fixes",
                "fix_action": "create_snapshot",
                "fix_params": {},
                "estimated_impact": "Creates rollback point in case fixes cause problems",
                "risk_level": "none",
            })

        summary = "All systems healthy." if not issues else (
            f"Found {len(issues)} issue(s) requiring attention."
        )

        return {
            "diagnosis": issues,
            "summary": summary,
            "recommended_order": [i["fix_action"] for i in issues],
            "requires_human_approval": any(
                i["severity"] == "critical" for i in issues
            ),
        }

    # ------------------------------------------------------------------
    # Action building and execution
    # ------------------------------------------------------------------

    def _build_actions(self, diagnosis: dict[str, Any]) -> list[FixAction]:
        """Convert diagnosis issues into executable FixAction objects."""
        actions = []
        for item in diagnosis.get("diagnosis", []):
            action_name = item.get("fix_action", "unknown")
            actions.append(FixAction(
                action_id=uuid4().hex[:10],
                action_name=action_name,
                description=item.get("issue", ""),
                severity=item.get("severity", "medium"),
                risk_level=item.get("risk_level", "low"),
            ))
        return actions

    async def _execute_fixes(
        self,
        report: DiagnosisReport,
        app_state: Any,
    ) -> None:
        """Execute all planned fix actions sequentially."""
        for action in report.actions:
            action.status = "running"
            action.started_at = datetime.now(UTC)

            try:
                result = await self._execute_single_fix(action, app_state)
                action.status = "completed"
                action.result = result
                report.fixes_executed += 1
                report.fixes_succeeded += 1
            except Exception as exc:
                action.status = "failed"
                action.result = f"Error: {exc!s}"
                report.fixes_executed += 1
                report.fixes_failed += 1
                logger.error(
                    "autofix.action_failed",
                    action=action.action_name,
                    error=str(exc),
                )

            action.completed_at = datetime.now(UTC)

    async def _execute_single_fix(
        self,
        action: FixAction,
        app_state: Any,
    ) -> str:
        """Execute a single fix action and return result description."""
        name = action.action_name

        if name == "create_snapshot":
            return await self._fix_create_snapshot(app_state)
        elif name == "rebuild_feedback_index":
            return self._fix_rebuild_feedback_index()
        elif name == "remove_orphan_verifications":
            return self._fix_remove_orphan_verifications(app_state)
        elif name == "reload_scheme_data":
            return await self._fix_reload_scheme_data(app_state)
        elif name == "validate_scheme_ids":
            return self._fix_validate_scheme_ids(app_state)
        elif name == "fix_feedback_references":
            return self._fix_feedback_references(app_state)
        elif name == "flush_stale_cache":
            return await self._fix_flush_cache(app_state)
        elif name == "restart_health_checks":
            return self._fix_restart_health_checks(app_state)
        elif name == "compact_audit_log":
            return self._fix_compact_audit_log()
        elif name == "flag_consent_violation":
            return self._fix_flag_consent_violations()
        else:
            action.status = "skipped"
            return f"Unknown action '{name}' — skipped for safety."

    # ------------------------------------------------------------------
    # Individual fix implementations
    # ------------------------------------------------------------------

    async def _fix_create_snapshot(self, app_state: Any) -> str:
        """Create a pre-fix snapshot for rollback safety."""
        from src.api.v1.admin_recovery import _record_audit, _rollback_points, _snapshots

        import copy
        import hashlib

        from src.api.v1.feedback import _feedback_index, _feedback_store
        from src.api.v1.profile import _profiles

        scheme_data = getattr(app_state, "scheme_data", [])
        verification_results = getattr(app_state, "verification_results", {})

        snapshot_id = f"autofix-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        data: dict[str, Any] = {}

        if scheme_data:
            data["schemes"] = [
                s.model_dump(mode="json") if hasattr(s, "model_dump") else str(s)
                for s in scheme_data
            ]
        if verification_results:
            data["verification"] = copy.deepcopy(verification_results)
        if _feedback_store:
            data["feedback"] = {fid: fb.model_dump(mode="json") for fid, fb in _feedback_store.items()}
            data["feedback_index"] = list(_feedback_index)
        if _profiles:
            data["profiles"] = {
                pid: p.model_dump(mode="json") if hasattr(p, "model_dump") else str(p)
                for pid, p in _profiles.items()
            }

        raw = json.dumps(data, sort_keys=True, default=str)
        checksum = hashlib.sha256(raw.encode()).hexdigest()[:16]

        snapshot = {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(UTC).isoformat(),
            "components": list(data.keys()),
            "data": data,
            "checksum": checksum,
        }
        _snapshots[snapshot_id] = snapshot
        _rollback_points.insert(0, {
            "snapshot_id": snapshot_id,
            "created_at": snapshot["created_at"],
            "components": snapshot["components"],
            "checksum": checksum,
        })
        _record_audit("autofix_snapshot", f"Auto-fix safety snapshot: {snapshot_id}")
        return f"Snapshot {snapshot_id} created ({len(raw) / 1024:.1f} KB)"

    def _fix_rebuild_feedback_index(self) -> str:
        from src.api.v1.feedback import _feedback_index, _feedback_store

        old_len = len(_feedback_index)
        # Rebuild: keep only IDs that exist in store, then add missing ones
        valid = [fid for fid in _feedback_index if fid in _feedback_store]
        missing = [fid for fid in _feedback_store if fid not in valid]
        _feedback_index.clear()
        _feedback_index.extend(valid + missing)
        return f"Rebuilt index: {old_len} -> {len(_feedback_index)} entries ({len(missing)} recovered)"

    def _fix_remove_orphan_verifications(self, app_state: Any) -> str:
        scheme_data = getattr(app_state, "scheme_data", [])
        verification_results = getattr(app_state, "verification_results", {})
        scheme_ids = {getattr(s, "scheme_id", "") for s in scheme_data}
        orphans = [k for k in verification_results if k not in scheme_ids]
        for k in orphans:
            del verification_results[k]
        return f"Removed {len(orphans)} orphaned verification results"

    async def _fix_reload_scheme_data(self, app_state: Any) -> str:
        try:
            from src.services.scheme_search import SchemeSearchService

            scheme_search = getattr(app_state, "scheme_search", None)
            if scheme_search is None:
                from src.services.cache import CacheManager
                from src.services.rag import RAGService

                rag = RAGService()
                cache = getattr(app_state, "cache", CacheManager())
                scheme_search = SchemeSearchService(rag=rag, cache=cache)

            from src.data.seed import seed_scheme_data

            scheme_data = await seed_scheme_data(scheme_search)
            app_state.scheme_data = scheme_data
            return f"Reloaded {len(scheme_data)} schemes from seed data"
        except Exception as exc:
            return f"Failed to reload: {exc!s}"

    def _fix_validate_scheme_ids(self, app_state: Any) -> str:
        scheme_data = getattr(app_state, "scheme_data", [])
        seen: dict[str, int] = {}
        duplicates = 0
        for s in scheme_data:
            sid = getattr(s, "scheme_id", "")
            if sid in seen:
                duplicates += 1
            seen[sid] = seen.get(sid, 0) + 1

        if duplicates > 0:
            # Deduplicate keeping first occurrence
            unique = []
            seen_ids: set[str] = set()
            for s in scheme_data:
                sid = getattr(s, "scheme_id", "")
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    unique.append(s)
            app_state.scheme_data = unique
            return f"Deduplicated: removed {duplicates} duplicates, {len(unique)} schemes remain"
        return "No duplicates found"

    def _fix_feedback_references(self, app_state: Any) -> str:
        from src.api.v1.feedback import _feedback_store

        scheme_data = getattr(app_state, "scheme_data", [])
        scheme_ids = {getattr(s, "scheme_id", "") for s in scheme_data}
        cleaned = 0
        for fb in _feedback_store.values():
            if fb.scheme_id and fb.scheme_id not in scheme_ids:
                fb.scheme_id = None
                fb.scheme_name = None
                cleaned += 1
        return f"Cleaned {cleaned} orphaned scheme references in feedback"

    async def _fix_flush_cache(self, app_state: Any) -> str:
        cache = getattr(app_state, "cache", None)
        if cache is None:
            return "No cache backend available"
        try:
            await cache.close()
            from config.settings import settings
            from src.services.cache import CacheManager

            new_cache = CacheManager(
                redis_url=settings.redis_url,
                namespace="haqsetu:",
            )
            app_state.cache = new_cache
            return "Cache flushed and rebuilt"
        except Exception as exc:
            return f"Cache flush failed: {exc!s}"

    def _fix_restart_health_checks(self, app_state: Any) -> str:
        self_sustaining = getattr(app_state, "self_sustaining", None)
        if self_sustaining is None:
            return "Self-sustaining service not active"
        # Reset failure counters
        for state in self_sustaining._service_states.values():
            state.consecutive_failures = 0
            state.health = "healthy"
            state.last_error = ""
        return "Reset health check failure counters for all services"

    def _fix_compact_audit_log(self) -> str:
        from src.api.v1.admin_recovery import _admin_audit_log

        old_len = len(_admin_audit_log)
        if old_len > 1000:
            _admin_audit_log[:] = _admin_audit_log[:1000]
            return f"Compacted audit log: {old_len} -> 1000 entries"
        return f"Audit log already within limits ({old_len} entries)"

    def _fix_flag_consent_violations(self) -> str:
        from src.api.v1.profile import _profiles

        flagged = []
        for pid, p in _profiles.items():
            if not p.consent_given:
                flagged.append(pid)
        if flagged:
            logger.warning(
                "autofix.consent_violations_flagged",
                profile_ids=flagged,
                count=len(flagged),
            )
            return f"Flagged {len(flagged)} profiles without consent for admin review: {', '.join(flagged[:5])}"
        return "No consent violations found"

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent diagnosis history as serialisable dicts."""
        results = []
        for r in self._history[:limit]:
            results.append({
                "report_id": r.report_id,
                "diagnosis_source": r.diagnosis_source,
                "model_used": r.model_used,
                "summary": r.summary,
                "issues_found": r.issues_found,
                "fixes_executed": r.fixes_executed,
                "fixes_succeeded": r.fixes_succeeded,
                "fixes_failed": r.fixes_failed,
                "requires_human_approval": r.requires_human_approval,
                "created_at": r.created_at.isoformat(),
                "duration_seconds": r.duration_seconds,
                "actions": [
                    {
                        "action_name": a.action_name,
                        "description": a.description,
                        "severity": a.severity,
                        "status": a.status,
                        "result": a.result,
                    }
                    for a in r.actions
                ],
            })
        return results
