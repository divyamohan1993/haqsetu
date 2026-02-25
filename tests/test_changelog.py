"""Tests for the SchemeChangelogService.

Covers change detection, impact summary generation, scheme diffing,
record/retrieval persistence via mock cache, and statistics.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.services.changelog import (
    ChangeType,
    SchemeChangeEntry,
    SchemeChangelogService,
)


# ---------------------------------------------------------------------------
# Mock cache for tests
# ---------------------------------------------------------------------------


class FakeCache:
    """Minimal in-memory cache matching CacheManager interface."""

    def __init__(self):
        self._store: dict[str, object] = {}

    async def get(self, key: str, default: object = None) -> object | None:
        return self._store.get(key, default)

    async def set(self, key: str, value: object, ttl_seconds: int = 0) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def service(cache: FakeCache) -> SchemeChangelogService:
    return SchemeChangelogService(cache=cache)


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestSchemeChangelogServiceInit:
    def test_initialization(self, service: SchemeChangelogService) -> None:
        assert service is not None
        assert service._cache is not None


# ---------------------------------------------------------------------------
# Change detection tests
# ---------------------------------------------------------------------------


class TestDetectChanges:
    def test_benefits_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "pm-kisan",
            "name": "PM-KISAN",
            "benefits": "Rs 6,000 per year",
        }
        new = {
            "scheme_id": "pm-kisan",
            "name": "PM-KISAN",
            "benefits": "Rs 8,000 per year",
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "benefits_changed"
        assert changes[0].field_changed == "benefits"
        assert changes[0].old_value == "Rs 6,000 per year"
        assert changes[0].new_value == "Rs 8,000 per year"
        assert changes[0].scheme_id == "pm-kisan"
        assert changes[0].scheme_name == "PM-KISAN"

    def test_eligibility_income_limit_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "pm-kisan",
            "name": "PM-KISAN",
            "eligibility": {"income_limit": 200000},
        }
        new = {
            "scheme_id": "pm-kisan",
            "name": "PM-KISAN",
            "eligibility": {"income_limit": 300000},
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "amount_changed"
        assert changes[0].field_changed == "eligibility.income_limit"

    def test_eligibility_age_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "test",
            "name": "Test",
            "eligibility": {"min_age": 18, "max_age": 60},
        }
        new = {
            "scheme_id": "test",
            "name": "Test",
            "eligibility": {"min_age": 21, "max_age": 65},
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 2
        field_names = {c.field_changed for c in changes}
        assert "eligibility.min_age" in field_names
        assert "eligibility.max_age" in field_names
        for c in changes:
            assert c.change_type == "eligibility_changed"

    def test_eligibility_category_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "test",
            "name": "Test",
            "eligibility": {"category": "SC"},
        }
        new = {
            "scheme_id": "test",
            "name": "Test",
            "eligibility": {"category": "SC/ST"},
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].field_changed == "eligibility.category"
        assert changes[0].change_type == "eligibility_changed"

    def test_application_process_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "test",
            "name": "Test",
            "application_process": "Apply offline at CSC",
        }
        new = {
            "scheme_id": "test",
            "name": "Test",
            "application_process": "Apply online at portal.gov.in",
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].field_changed == "application_process"
        assert changes[0].change_type == "updated"

    def test_deadline_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "test",
            "name": "Test",
            "deadline": "2025-12-31",
        }
        new = {
            "scheme_id": "test",
            "name": "Test",
            "deadline": "2026-03-31",
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].field_changed == "deadline"
        # Deadline was extended (new date is later)
        assert changes[0].change_type == ChangeType.EXTENDED

    def test_documents_changed(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "test",
            "name": "Test",
            "documents_required": ["Aadhaar Card"],
        }
        new = {
            "scheme_id": "test",
            "name": "Test",
            "documents_required": ["Aadhaar Card", "PAN Card"],
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].field_changed == "documents_required"
        assert changes[0].change_type == "documents_changed"

    def test_no_changes_same_scheme(self, service: SchemeChangelogService) -> None:
        scheme = {
            "scheme_id": "test",
            "name": "Test Scheme",
            "description": "A test scheme",
            "benefits": "Some benefits",
            "eligibility": {"min_age": 18, "income_limit": 200000},
        }
        changes = service.detect_changes(scheme, scheme)
        assert len(changes) == 0, "Identical schemes should produce no changes"

    def test_multiple_changes_detected(self, service: SchemeChangelogService) -> None:
        old = {
            "scheme_id": "test",
            "name": "Old Name",
            "benefits": "Old benefits",
            "description": "Old description",
        }
        new = {
            "scheme_id": "test",
            "name": "New Name",
            "benefits": "New benefits",
            "description": "New description",
        }
        changes = service.detect_changes(old, new)
        assert len(changes) == 3
        fields = {c.field_changed for c in changes}
        assert "name" in fields
        assert "benefits" in fields
        assert "description" in fields

    def test_none_to_value_not_treated_as_change_for_empty(self, service: SchemeChangelogService) -> None:
        """None and empty string are treated as equivalent (no noisy diff)."""
        old = {"scheme_id": "test", "name": "Test", "helpline": None}
        new = {"scheme_id": "test", "name": "Test", "helpline": ""}
        changes = service.detect_changes(old, new)
        assert len(changes) == 0

    def test_none_to_actual_value_is_change(self, service: SchemeChangelogService) -> None:
        old = {"scheme_id": "test", "name": "Test", "helpline": None}
        new = {"scheme_id": "test", "name": "Test", "helpline": "1800-000-0000"}
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].field_changed == "helpline"

    def test_change_entry_has_impact_summary(self, service: SchemeChangelogService) -> None:
        old = {"scheme_id": "test", "name": "Test", "benefits": "Old"}
        new = {"scheme_id": "test", "name": "Test", "benefits": "New"}
        changes = service.detect_changes(old, new)
        assert len(changes) == 1
        assert changes[0].impact_summary != "", "Impact summary should be generated"


# ---------------------------------------------------------------------------
# Impact summary tests
# ---------------------------------------------------------------------------


class TestGenerateImpactSummary:
    def test_income_limit_increased(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            change_type="amount_changed",
            field_changed="eligibility.income_limit",
            old_value="200000",
            new_value="300000",
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "more families now eligible" in summary

    def test_income_limit_decreased(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            change_type="amount_changed",
            field_changed="eligibility.income_limit",
            old_value="300000",
            new_value="200000",
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "fewer families" in summary

    def test_age_limit_changed(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="test",
            scheme_name="Test",
            change_type="eligibility_changed",
            field_changed="eligibility.min_age",
            old_value="18",
            new_value="21",
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "Minimum age" in summary
        assert "18" in summary
        assert "21" in summary

    def test_deadline_extended(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="test",
            scheme_name="Test",
            change_type=ChangeType.EXTENDED,
            field_changed="deadline",
            old_value="2025-12-31",
            new_value="2026-03-31",
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "extended" in summary.lower()

    def test_benefits_updated(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            change_type="benefits_changed",
            field_changed="benefits",
            old_value="Rs 6,000 per year",
            new_value="Rs 8,000 per year",
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "Benefits" in summary or "benefits" in summary.lower()
        assert "PM-KISAN" in summary

    def test_documents_added(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="test",
            scheme_name="Test",
            change_type="documents_changed",
            field_changed="documents_required",
            old_value='["Aadhaar Card"]',
            new_value='["Aadhaar Card", "PAN Card"]',
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "PAN Card" in summary

    def test_scheme_renamed(self, service: SchemeChangelogService) -> None:
        entry = SchemeChangeEntry(
            scheme_id="test",
            scheme_name="New Name",
            change_type="updated",
            field_changed="name",
            old_value="Old Name",
            new_value="New Name",
            detected_at=datetime.now(UTC),
            source="ingestion",
        )
        summary = service.generate_impact_summary(entry)
        assert "renamed" in summary.lower() or "Old Name" in summary


# ---------------------------------------------------------------------------
# Diff schemes tests
# ---------------------------------------------------------------------------


class TestDiffSchemes:
    def test_added_field(self, service: SchemeChangelogService) -> None:
        scheme_a = {"name": "Test"}
        scheme_b = {"name": "Test", "helpline": "1800-000-0000"}
        diff = service.diff_schemes(scheme_a, scheme_b)
        assert "helpline" in diff["added"]
        assert diff["added"]["helpline"] == "1800-000-0000"
        assert diff["removed"] == {}
        assert diff["changed"] == {}

    def test_removed_field(self, service: SchemeChangelogService) -> None:
        scheme_a = {"name": "Test", "helpline": "1800-000-0000"}
        scheme_b = {"name": "Test"}
        diff = service.diff_schemes(scheme_a, scheme_b)
        assert "helpline" in diff["removed"]
        assert diff["removed"]["helpline"] == "1800-000-0000"
        assert diff["added"] == {}

    def test_changed_field(self, service: SchemeChangelogService) -> None:
        scheme_a = {"name": "Test", "benefits": "Rs 6,000"}
        scheme_b = {"name": "Test", "benefits": "Rs 8,000"}
        diff = service.diff_schemes(scheme_a, scheme_b)
        assert "benefits" in diff["changed"]
        assert diff["changed"]["benefits"]["old"] == "Rs 6,000"
        assert diff["changed"]["benefits"]["new"] == "Rs 8,000"

    def test_no_diff_identical(self, service: SchemeChangelogService) -> None:
        scheme = {"name": "Test", "benefits": "Rs 6,000"}
        diff = service.diff_schemes(scheme, scheme)
        assert diff["added"] == {}
        assert diff["removed"] == {}
        assert diff["changed"] == {}

    def test_combined_diff(self, service: SchemeChangelogService) -> None:
        scheme_a = {"name": "Old Name", "helpline": "1234", "website": "https://old.gov.in"}
        scheme_b = {"name": "New Name", "website": "https://old.gov.in", "deadline": "2026-03-31"}
        diff = service.diff_schemes(scheme_a, scheme_b)
        assert "deadline" in diff["added"]
        assert "helpline" in diff["removed"]
        assert "name" in diff["changed"]


# ---------------------------------------------------------------------------
# Record and retrieval tests
# ---------------------------------------------------------------------------


class TestRecordAndRetrieve:
    @pytest.mark.asyncio
    async def test_record_changes_and_get_changelog(self, service: SchemeChangelogService) -> None:
        changes = [
            SchemeChangeEntry(
                scheme_id="pm-kisan",
                scheme_name="PM-KISAN",
                change_type="benefits_changed",
                field_changed="benefits",
                old_value="Rs 6,000",
                new_value="Rs 8,000",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
            SchemeChangeEntry(
                scheme_id="pm-kisan",
                scheme_name="PM-KISAN",
                change_type="eligibility_changed",
                field_changed="eligibility.income_limit",
                old_value="200000",
                new_value="300000",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
        ]
        await service.record_changes(changes)

        changelog = await service.get_changelog("pm-kisan")
        assert len(changelog) == 2
        # Should be sorted newest first -- but since both have same time,
        # just check we got them back
        fields = {entry.field_changed for entry in changelog}
        assert "benefits" in fields
        assert "eligibility.income_limit" in fields

    @pytest.mark.asyncio
    async def test_record_empty_changes(self, service: SchemeChangelogService) -> None:
        await service.record_changes([])
        changelog = await service.get_changelog("nonexistent")
        assert changelog == []

    @pytest.mark.asyncio
    async def test_get_changelog_nonexistent_scheme(self, service: SchemeChangelogService) -> None:
        changelog = await service.get_changelog("nonexistent-scheme")
        assert changelog == []

    @pytest.mark.asyncio
    async def test_get_changelog_with_limit(self, service: SchemeChangelogService) -> None:
        changes = [
            SchemeChangeEntry(
                scheme_id="test",
                scheme_name="Test",
                change_type="updated",
                field_changed=f"field_{i}",
                old_value=f"old_{i}",
                new_value=f"new_{i}",
                detected_at=datetime.now(UTC),
                source="ingestion",
            )
            for i in range(10)
        ]
        await service.record_changes(changes)
        changelog = await service.get_changelog("test", limit=3)
        assert len(changelog) == 3

    @pytest.mark.asyncio
    async def test_record_changes_multiple_schemes(
        self, service: SchemeChangelogService
    ) -> None:
        changes = [
            SchemeChangeEntry(
                scheme_id="scheme-a",
                scheme_name="Scheme A",
                change_type="updated",
                field_changed="name",
                old_value="Old A",
                new_value="New A",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
            SchemeChangeEntry(
                scheme_id="scheme-b",
                scheme_name="Scheme B",
                change_type="benefits_changed",
                field_changed="benefits",
                old_value="Old B",
                new_value="New B",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
        ]
        await service.record_changes(changes)

        changelog_a = await service.get_changelog("scheme-a")
        assert len(changelog_a) == 1
        assert changelog_a[0].scheme_id == "scheme-a"

        changelog_b = await service.get_changelog("scheme-b")
        assert len(changelog_b) == 1
        assert changelog_b[0].scheme_id == "scheme-b"


# ---------------------------------------------------------------------------
# Recent changes tests
# ---------------------------------------------------------------------------


class TestGetRecentChanges:
    @pytest.mark.asyncio
    async def test_get_recent_changes(self, service: SchemeChangelogService) -> None:
        changes = [
            SchemeChangeEntry(
                scheme_id=f"scheme-{i}",
                scheme_name=f"Scheme {i}",
                change_type="updated",
                field_changed="description",
                old_value=f"old_{i}",
                new_value=f"new_{i}",
                detected_at=datetime.now(UTC),
                source="ingestion",
            )
            for i in range(5)
        ]
        await service.record_changes(changes)

        recent = await service.get_recent_changes(limit=3)
        assert len(recent) == 3

    @pytest.mark.asyncio
    async def test_get_recent_changes_empty(self, service: SchemeChangelogService) -> None:
        recent = await service.get_recent_changes()
        assert recent == []


# ---------------------------------------------------------------------------
# Statistics tests
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_empty(self, service: SchemeChangelogService) -> None:
        stats = await service.get_stats()
        assert stats["total_changes"] == 0
        assert stats["changes_today"] == 0
        assert stats["most_changed_schemes"] == []
        assert stats["change_type_distribution"] == {}

    @pytest.mark.asyncio
    async def test_get_stats_with_data(self, service: SchemeChangelogService) -> None:
        changes = [
            SchemeChangeEntry(
                scheme_id="pm-kisan",
                scheme_name="PM-KISAN",
                change_type="benefits_changed",
                field_changed="benefits",
                old_value="old",
                new_value="new",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
            SchemeChangeEntry(
                scheme_id="pm-kisan",
                scheme_name="PM-KISAN",
                change_type="eligibility_changed",
                field_changed="eligibility.min_age",
                old_value="18",
                new_value="21",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
            SchemeChangeEntry(
                scheme_id="pm-awas",
                scheme_name="PM Awas Yojana",
                change_type="updated",
                field_changed="description",
                old_value="old desc",
                new_value="new desc",
                detected_at=datetime.now(UTC),
                source="ingestion",
            ),
        ]
        await service.record_changes(changes)

        stats = await service.get_stats()
        assert stats["total_changes"] == 3
        assert stats["changes_today"] == 3
        assert len(stats["most_changed_schemes"]) == 2
        # PM-KISAN has 2 changes, PM Awas has 1
        most_changed = stats["most_changed_schemes"][0]
        assert most_changed["scheme_id"] == "pm-kisan"
        assert most_changed["change_count"] == 2
        # Change type distribution
        assert stats["change_type_distribution"]["benefits_changed"] == 1
        assert stats["change_type_distribution"]["eligibility_changed"] == 1
        assert stats["change_type_distribution"]["updated"] == 1
