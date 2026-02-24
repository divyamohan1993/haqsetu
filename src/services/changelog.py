"""Scheme changelog service -- tracks changes to government schemes over time.

Maintains a full audit trail of every change detected across ingestion runs.
By comparing scheme data between successive pipeline executions, this service
detects meaningful modifications and records them for transparency and
public accountability.

Use cases:
    * **Public dashboard feed** -- Citizens see a live feed of scheme changes
      (e.g., "Income limit for PM-KISAN increased to Rs 3,00,000").
    * **Impact analysis** -- Understanding how policy changes affect
      eligibility pools.
    * **Audit trail** -- Complete history of what changed, when, and from
      which source.
    * **Notification triggers** -- Downstream services (e.g., notifications)
      can subscribe to specific change types.

Change detection covers:
    - Top-level fields: name, description, benefits, application_process,
      documents_required, deadline, helpline, website, ministry, state.
    - Eligibility sub-fields: min_age, max_age, income_limit, category,
      occupation, gender, is_bpl, land_holding_acres, custom_criteria.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHANGELOG_CACHE_TTL = 24 * 60 * 60  # 24 hours
_MAX_ENTRIES_PER_SCHEME = 100
_RECENT_CHANGES_KEY = "changelog:recent"

# Top-level scheme fields to monitor for changes
_MONITORED_FIELDS = (
    "name",
    "description",
    "benefits",
    "application_process",
    "documents_required",
    "deadline",
    "helpline",
    "website",
    "ministry",
    "state",
)

# Eligibility sub-fields to monitor
_ELIGIBILITY_FIELDS = (
    "min_age",
    "max_age",
    "income_limit",
    "category",
    "occupation",
    "gender",
    "is_bpl",
    "land_holding_acres",
    "custom_criteria",
)

# Mapping from field name to the most appropriate ChangeType
_FIELD_CHANGE_TYPE_MAP: dict[str, str] = {
    "benefits": "benefits_changed",
    "income_limit": "amount_changed",
    "min_age": "eligibility_changed",
    "max_age": "eligibility_changed",
    "category": "eligibility_changed",
    "occupation": "eligibility_changed",
    "gender": "eligibility_changed",
    "is_bpl": "eligibility_changed",
    "land_holding_acres": "eligibility_changed",
    "custom_criteria": "eligibility_changed",
    "documents_required": "documents_changed",
    "deadline": "deadline_changed",
    "application_process": "updated",
}


# ---------------------------------------------------------------------------
# ChangeType enum
# ---------------------------------------------------------------------------


class ChangeType(StrEnum):
    """Types of changes that can be detected on a government scheme."""

    __slots__ = ()

    CREATED = "created"
    UPDATED = "updated"
    BENEFITS_CHANGED = "benefits_changed"
    ELIGIBILITY_CHANGED = "eligibility_changed"
    REVOKED = "revoked"
    EXTENDED = "extended"
    AMOUNT_CHANGED = "amount_changed"
    DOCUMENTS_CHANGED = "documents_changed"
    DEADLINE_CHANGED = "deadline_changed"


# ---------------------------------------------------------------------------
# SchemeChangeEntry model
# ---------------------------------------------------------------------------


class SchemeChangeEntry(BaseModel):
    """A single recorded change to a government scheme."""

    scheme_id: str
    scheme_name: str
    change_type: str
    field_changed: str
    old_value: str
    new_value: str
    detected_at: datetime
    source: str
    verified: bool = False
    impact_summary: str = ""


# ---------------------------------------------------------------------------
# SchemeChangelogService
# ---------------------------------------------------------------------------


class SchemeChangelogService:
    """Tracks changes to government schemes across ingestion runs.

    Compares scheme snapshots to detect meaningful modifications, records
    a full changelog per scheme, and provides aggregated views for the
    public dashboard.

    Parameters
    ----------
    cache:
        Shared cache manager for persisting changelog entries.
    """

    __slots__ = ("_cache",)

    def __init__(self, cache: CacheManager) -> None:
        self._cache = cache

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def detect_changes(
        self, old_scheme: dict, new_scheme: dict
    ) -> list[SchemeChangeEntry]:
        """Compare two versions of a scheme and detect all meaningful changes.

        Checks top-level fields (name, description, benefits, etc.) as well
        as eligibility sub-fields (min_age, max_age, income_limit, category,
        occupation, gender, is_bpl, land_holding_acres, custom_criteria).

        Parameters
        ----------
        old_scheme:
            The previous version of the scheme dictionary.
        new_scheme:
            The current (newer) version of the scheme dictionary.

        Returns
        -------
        list[SchemeChangeEntry]
            All detected changes between the two versions.
        """
        changes: list[SchemeChangeEntry] = []
        now = datetime.now(UTC)

        scheme_id = new_scheme.get("scheme_id") or old_scheme.get("scheme_id", "")
        scheme_name = new_scheme.get("name") or old_scheme.get("name", "")
        source = new_scheme.get("source", "ingestion")

        # -- Check top-level fields ----------------------------------------
        for field in _MONITORED_FIELDS:
            old_val = old_scheme.get(field)
            new_val = new_scheme.get(field)

            if self._values_differ(old_val, new_val):
                change_type = _FIELD_CHANGE_TYPE_MAP.get(field, ChangeType.UPDATED)

                # Detect deadline extension specifically
                if field == "deadline" and old_val and new_val:
                    if self._is_deadline_extended(str(old_val), str(new_val)):
                        change_type = ChangeType.EXTENDED

                entry = SchemeChangeEntry(
                    scheme_id=scheme_id,
                    scheme_name=scheme_name,
                    change_type=change_type,
                    field_changed=field,
                    old_value=self._serialize_value(old_val),
                    new_value=self._serialize_value(new_val),
                    detected_at=now,
                    source=source,
                )
                entry.impact_summary = self.generate_impact_summary(entry)
                changes.append(entry)

        # -- Check eligibility sub-fields ----------------------------------
        old_elig = old_scheme.get("eligibility") or {}
        new_elig = new_scheme.get("eligibility") or {}

        # Handle Pydantic models -- convert to dicts if needed
        if hasattr(old_elig, "model_dump"):
            old_elig = old_elig.model_dump()
        if hasattr(new_elig, "model_dump"):
            new_elig = new_elig.model_dump()

        for field in _ELIGIBILITY_FIELDS:
            old_val = old_elig.get(field)
            new_val = new_elig.get(field)

            if self._values_differ(old_val, new_val):
                change_type = _FIELD_CHANGE_TYPE_MAP.get(
                    field, ChangeType.ELIGIBILITY_CHANGED
                )

                entry = SchemeChangeEntry(
                    scheme_id=scheme_id,
                    scheme_name=scheme_name,
                    change_type=change_type,
                    field_changed=f"eligibility.{field}",
                    old_value=self._serialize_value(old_val),
                    new_value=self._serialize_value(new_val),
                    detected_at=now,
                    source=source,
                )
                entry.impact_summary = self.generate_impact_summary(entry)
                changes.append(entry)

        if changes:
            logger.info(
                "changelog.changes_detected",
                scheme_id=scheme_id,
                scheme_name=scheme_name,
                change_count=len(changes),
                change_types=[c.change_type for c in changes],
            )

        return changes

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def record_changes(self, changes: list[SchemeChangeEntry]) -> None:
        """Save detected changes to cache, appending to existing history.

        Each scheme maintains its own changelog keyed as
        ``changelog:{scheme_id}``.  Only the most recent
        ``_MAX_ENTRIES_PER_SCHEME`` entries are retained per scheme.

        Also appends to a global recent-changes list for the public
        dashboard feed.

        Parameters
        ----------
        changes:
            List of change entries to persist.
        """
        if not changes:
            return

        # Group changes by scheme_id for per-scheme storage
        by_scheme: dict[str, list[dict]] = {}
        all_serialized: list[dict] = []

        for change in changes:
            serialized = change.model_dump(mode="json")
            all_serialized.append(serialized)

            if change.scheme_id not in by_scheme:
                by_scheme[change.scheme_id] = []
            by_scheme[change.scheme_id].append(serialized)

        # Persist per-scheme changelogs
        for scheme_id, scheme_changes in by_scheme.items():
            cache_key = f"changelog:{scheme_id}"
            try:
                existing = await self._cache.get(cache_key, default=[])
                if not isinstance(existing, list):
                    existing = []

                # Append new changes and trim to max
                combined = existing + scheme_changes
                combined = combined[-_MAX_ENTRIES_PER_SCHEME:]

                await self._cache.set(
                    cache_key, combined, ttl_seconds=_CHANGELOG_CACHE_TTL
                )
            except Exception:
                logger.warning(
                    "changelog.record_failed",
                    scheme_id=scheme_id,
                    exc_info=True,
                )

        # Persist global recent-changes list
        try:
            existing_recent = await self._cache.get(
                _RECENT_CHANGES_KEY, default=[]
            )
            if not isinstance(existing_recent, list):
                existing_recent = []

            combined_recent = existing_recent + all_serialized
            # Keep last 500 recent entries globally
            combined_recent = combined_recent[-500:]

            await self._cache.set(
                _RECENT_CHANGES_KEY,
                combined_recent,
                ttl_seconds=_CHANGELOG_CACHE_TTL,
            )
        except Exception:
            logger.warning("changelog.record_recent_failed", exc_info=True)

        logger.info(
            "changelog.changes_recorded",
            total_changes=len(changes),
            schemes_affected=len(by_scheme),
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def get_changelog(
        self, scheme_id: str, limit: int = 50
    ) -> list[SchemeChangeEntry]:
        """Retrieve change history for a specific scheme.

        Parameters
        ----------
        scheme_id:
            The scheme identifier.
        limit:
            Maximum number of entries to return.

        Returns
        -------
        list[SchemeChangeEntry]
            Change entries sorted by ``detected_at`` descending (newest first).
        """
        cache_key = f"changelog:{scheme_id}"

        try:
            raw = await self._cache.get(cache_key, default=[])
            if not isinstance(raw, list):
                return []

            entries = [SchemeChangeEntry(**item) for item in raw]

            # Sort newest first
            entries.sort(key=lambda e: e.detected_at, reverse=True)

            return entries[:limit]
        except Exception:
            logger.warning(
                "changelog.get_changelog_failed",
                scheme_id=scheme_id,
                exc_info=True,
            )
            return []

    async def get_recent_changes(
        self, limit: int = 100
    ) -> list[SchemeChangeEntry]:
        """Get all recent changes across all schemes.

        Useful for the public dashboard feed showing a live timeline
        of government scheme modifications.

        Parameters
        ----------
        limit:
            Maximum number of entries to return.

        Returns
        -------
        list[SchemeChangeEntry]
            Change entries sorted by ``detected_at`` descending (newest first).
        """
        try:
            raw = await self._cache.get(_RECENT_CHANGES_KEY, default=[])
            if not isinstance(raw, list):
                return []

            entries = [SchemeChangeEntry(**item) for item in raw]

            # Sort newest first
            entries.sort(key=lambda e: e.detected_at, reverse=True)

            return entries[:limit]
        except Exception:
            logger.warning(
                "changelog.get_recent_changes_failed", exc_info=True
            )
            return []

    # ------------------------------------------------------------------
    # Diffing
    # ------------------------------------------------------------------

    def diff_schemes(self, scheme_a: dict, scheme_b: dict) -> dict:
        """Return a structured diff between two scheme versions.

        Parameters
        ----------
        scheme_a:
            The older scheme version.
        scheme_b:
            The newer scheme version.

        Returns
        -------
        dict
            A diff with three keys:

            - ``added``: fields present in *scheme_b* but not *scheme_a*.
            - ``removed``: fields present in *scheme_a* but not *scheme_b*.
            - ``changed``: fields present in both but with different values,
              formatted as ``{field: {"old": v1, "new": v2}}``.
        """
        added: dict[str, object] = {}
        removed: dict[str, object] = {}
        changed: dict[str, dict[str, object]] = {}

        all_keys = set(scheme_a.keys()) | set(scheme_b.keys())

        for key in all_keys:
            in_a = key in scheme_a
            in_b = key in scheme_b
            val_a = scheme_a.get(key)
            val_b = scheme_b.get(key)

            if in_a and not in_b:
                removed[key] = val_a
            elif in_b and not in_a:
                added[key] = val_b
            elif self._values_differ(val_a, val_b):
                changed[key] = {"old": val_a, "new": val_b}

        return {"added": added, "removed": removed, "changed": changed}

    # ------------------------------------------------------------------
    # Impact summary generation
    # ------------------------------------------------------------------

    def generate_impact_summary(self, change: SchemeChangeEntry) -> str:
        """Generate a human-readable summary of what a change means for citizens.

        Produces clear, actionable language that a citizen can understand
        without government jargon.

        Parameters
        ----------
        change:
            The change entry to summarise.

        Returns
        -------
        str
            A plain-language impact description.

        Examples
        --------
        - "Income limit increased from Rs 2,00,000 to Rs 3,00,000 -- more families now eligible"
        - "Application deadline extended to March 31, 2026"
        - "New document required: Caste Certificate"
        """
        field = change.field_changed
        old = change.old_value
        new = change.new_value
        scheme = change.scheme_name

        # -- Eligibility: income limit -------------------------------------
        if field == "eligibility.income_limit":
            old_fmt = self._format_currency(old)
            new_fmt = self._format_currency(new)
            if old and new and self._parse_number(new) > self._parse_number(old):
                return (
                    f"Income limit increased from {old_fmt} to {new_fmt} "
                    f"-- more families now eligible"
                )
            elif old and new:
                return (
                    f"Income limit decreased from {old_fmt} to {new_fmt} "
                    f"-- fewer families may qualify"
                )
            elif new and not old:
                return f"Income limit set to {new_fmt}"
            return f"Income limit changed for {scheme}"

        # -- Eligibility: age limits ---------------------------------------
        if field in ("eligibility.min_age", "eligibility.max_age"):
            label = "Minimum age" if "min_age" in field else "Maximum age"
            if old and new:
                return f"{label} changed from {old} to {new} years"
            elif new and not old:
                return f"{label} requirement set to {new} years"
            elif old and not new:
                return f"{label} requirement removed (was {old} years)"
            return f"Age eligibility changed for {scheme}"

        # -- Eligibility: category -----------------------------------------
        if field == "eligibility.category":
            if new and old:
                return f"Eligible categories changed from '{old}' to '{new}'"
            elif new:
                return f"Category eligibility set to '{new}'"
            return f"Category eligibility changed for {scheme}"

        # -- Eligibility: other sub-fields ---------------------------------
        if field.startswith("eligibility."):
            sub_field = field.replace("eligibility.", "").replace("_", " ")
            if old and new:
                return f"Eligibility criteria '{sub_field}' changed from '{old}' to '{new}'"
            elif new:
                return f"New eligibility criteria added: {sub_field} = '{new}'"
            elif old:
                return f"Eligibility criteria '{sub_field}' removed (was '{old}')"
            return f"Eligibility criteria updated for {scheme}"

        # -- Deadline ------------------------------------------------------
        if field == "deadline":
            if change.change_type == ChangeType.EXTENDED:
                return f"Application deadline extended to {new}"
            if old and new:
                return f"Application deadline changed from {old} to {new}"
            elif new:
                return f"Application deadline set to {new}"
            elif old:
                return f"Application deadline removed (was {old})"
            return f"Deadline updated for {scheme}"

        # -- Benefits ------------------------------------------------------
        if field == "benefits":
            if old and new:
                return f"Benefits description updated for {scheme}"
            elif new:
                return f"Benefits information added for {scheme}"
            return f"Benefits changed for {scheme}"

        # -- Documents required --------------------------------------------
        if field == "documents_required":
            old_docs = self._parse_list_value(old)
            new_docs = self._parse_list_value(new)
            added = set(new_docs) - set(old_docs)
            removed = set(old_docs) - set(new_docs)
            parts: list[str] = []
            if added:
                parts.append(f"New document(s) required: {', '.join(sorted(added))}")
            if removed:
                parts.append(f"Document(s) no longer required: {', '.join(sorted(removed))}")
            if parts:
                return ". ".join(parts)
            return f"Required documents updated for {scheme}"

        # -- Name ----------------------------------------------------------
        if field == "name":
            if old and new:
                return f"Scheme renamed from '{old}' to '{new}'"
            return f"Scheme name updated"

        # -- Description ---------------------------------------------------
        if field == "description":
            return f"Scheme description updated for {scheme}"

        # -- Ministry / State / Website / Helpline -------------------------
        if field == "ministry":
            if old and new:
                return f"Administering ministry changed from {old} to {new}"
            return f"Ministry information updated for {scheme}"

        if field == "state":
            if new and not old:
                return f"Scheme now restricted to {new}"
            elif old and not new:
                return f"Scheme expanded from {old} to all states"
            elif old and new:
                return f"Scheme state changed from {old} to {new}"
            return f"State eligibility updated for {scheme}"

        if field == "website":
            return f"Official website updated for {scheme}"

        if field == "helpline":
            if new:
                return f"Helpline updated to {new} for {scheme}"
            return f"Helpline information updated for {scheme}"

        if field == "application_process":
            return f"Application process updated for {scheme}"

        # -- Fallback ------------------------------------------------------
        return f"{field} changed for {scheme}"

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict:
        """Return changelog statistics for monitoring and dashboard display.

        Returns
        -------
        dict
            Statistics including:

            - ``total_changes``: Total number of recorded changes.
            - ``changes_today``: Number of changes detected today.
            - ``most_changed_schemes``: Top 10 schemes by change count.
            - ``change_type_distribution``: Count per change type.
        """
        try:
            raw = await self._cache.get(_RECENT_CHANGES_KEY, default=[])
            if not isinstance(raw, list):
                raw = []

            entries = [SchemeChangeEntry(**item) for item in raw]
        except Exception:
            logger.warning("changelog.get_stats_failed", exc_info=True)
            entries = []

        total_changes = len(entries)

        # Changes today
        today = datetime.now(UTC).date()
        changes_today = sum(
            1 for e in entries if e.detected_at.date() == today
        )

        # Most changed schemes (top 10)
        scheme_counts: dict[str, int] = {}
        for entry in entries:
            key = entry.scheme_id
            scheme_counts[key] = scheme_counts.get(key, 0) + 1

        most_changed = sorted(
            scheme_counts.items(), key=lambda x: x[1], reverse=True
        )[:10]
        most_changed_schemes = [
            {"scheme_id": sid, "change_count": count}
            for sid, count in most_changed
        ]

        # Change type distribution
        type_counts: dict[str, int] = {}
        for entry in entries:
            ct = entry.change_type
            type_counts[ct] = type_counts.get(ct, 0) + 1

        return {
            "total_changes": total_changes,
            "changes_today": changes_today,
            "most_changed_schemes": most_changed_schemes,
            "change_type_distribution": type_counts,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _values_differ(val_a: object, val_b: object) -> bool:
        """Return True if two values represent a meaningful difference.

        Treats ``None`` and empty string / empty list as equivalent to
        avoid noisy diffs from missing-vs-empty normalisation.
        """
        # Normalise "empty" representations
        empty_sentinels = (None, "", [], {})
        a_empty = val_a in empty_sentinels
        b_empty = val_b in empty_sentinels
        if a_empty and b_empty:
            return False

        # For lists, compare sorted representations to ignore ordering
        if isinstance(val_a, list) and isinstance(val_b, list):
            return sorted(str(x) for x in val_a) != sorted(str(x) for x in val_b)

        return str(val_a) != str(val_b)

    @staticmethod
    def _serialize_value(value: object) -> str:
        """Serialise a value to a stable string representation for storage."""
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    @staticmethod
    def _is_deadline_extended(old_deadline: str, new_deadline: str) -> bool:
        """Return True if the new deadline is later than the old one.

        Attempts multiple date formats common in Indian government data.
        Falls back to lexicographic comparison for ISO-format dates.
        """
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%B %d, %Y",
            "%d %B %Y",
            "%d-%m-%Y",
        ]

        old_dt = None
        new_dt = None

        for fmt in formats:
            if old_dt is None:
                try:
                    old_dt = datetime.strptime(old_deadline.strip(), fmt)
                except ValueError:
                    pass
            if new_dt is None:
                try:
                    new_dt = datetime.strptime(new_deadline.strip(), fmt)
                except ValueError:
                    pass

        if old_dt and new_dt:
            return new_dt > old_dt

        # Fallback: lexicographic comparison works for ISO dates
        return new_deadline.strip() > old_deadline.strip()

    @staticmethod
    def _format_currency(value: str) -> str:
        """Format a numeric string as Indian Rupees.

        Handles raw numbers like ``"300000"`` and already-formatted
        strings like ``"Rs 3,00,000"``.
        """
        if not value:
            return "N/A"

        # If it already looks formatted, return as-is
        if "Rs" in value or "\u20b9" in value:
            return value

        try:
            amount = float(value)
            if amount >= 10_000_000:  # 1 crore
                return f"\u20b9{amount / 10_000_000:,.2f} crore"
            elif amount >= 100_000:  # 1 lakh
                return f"\u20b9{amount / 100_000:,.2f} lakh"
            else:
                return f"\u20b9{amount:,.0f}"
        except (ValueError, TypeError):
            return value

    @staticmethod
    def _parse_number(value: str) -> float:
        """Parse a numeric value from a string, returning 0.0 on failure."""
        if not value:
            return 0.0
        cleaned = value.replace(",", "").replace("Rs", "").replace("\u20b9", "").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _parse_list_value(value: str) -> list[str]:
        """Parse a serialised list value back into a Python list."""
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return []
