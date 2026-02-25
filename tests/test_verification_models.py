"""Tests for verification data models: enums, evidence, result, summary, changelog, and dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.models.verification import (
    ChangeType,
    SchemeChangelog,
    TrustLevel,
    VerificationDashboardStats,
    VerificationEvidence,
    VerificationResult,
    VerificationSource,
    VerificationStatus,
    VerificationSummary,
)


# -----------------------------------------------------------------------
# Enum tests
# -----------------------------------------------------------------------


class TestVerificationStatus:
    def test_values(self) -> None:
        expected = {
            "unverified", "pending", "partially_verified",
            "verified", "disputed", "revoked",
        }
        assert {e.value for e in VerificationStatus} == expected, (
            "VerificationStatus should have exactly 6 values"
        )

    def test_length(self) -> None:
        assert len(VerificationStatus) == 6, "VerificationStatus should have 6 members"

    def test_str_enum_behavior(self) -> None:
        assert str(VerificationStatus.UNVERIFIED) == "unverified"
        assert VerificationStatus.VERIFIED == "verified"


class TestVerificationSource:
    def test_values(self) -> None:
        expected = {
            "gazette_of_india", "india_code", "sansad_parliament",
            "myscheme_gov", "data_gov_in", "api_setu",
            "state_gazette", "court_order",
        }
        assert {e.value for e in VerificationSource} == expected, (
            "VerificationSource should have exactly 8 values"
        )

    def test_length(self) -> None:
        assert len(VerificationSource) == 8, "VerificationSource should have 8 members"

    def test_str_enum_behavior(self) -> None:
        assert str(VerificationSource.GAZETTE_OF_INDIA) == "gazette_of_india"
        assert VerificationSource.MYSCHEME_GOV == "myscheme_gov"


class TestTrustLevel:
    def test_values(self) -> None:
        expected = {
            "official_gazette", "legislation", "parliamentary",
            "government_portal", "government_data", "unverified",
        }
        assert {e.value for e in TrustLevel} == expected, (
            "TrustLevel should have exactly 6 values"
        )

    def test_length(self) -> None:
        assert len(TrustLevel) == 6, "TrustLevel should have 6 members"

    def test_weight_official_gazette(self) -> None:
        assert TrustLevel.OFFICIAL_GAZETTE.weight == 1.0, (
            "Official gazette should have weight 1.0"
        )

    def test_weight_legislation(self) -> None:
        assert TrustLevel.LEGISLATION.weight == 0.9, (
            "Legislation should have weight 0.9"
        )

    def test_weight_parliamentary(self) -> None:
        assert TrustLevel.PARLIAMENTARY.weight == 0.85, (
            "Parliamentary should have weight 0.85"
        )

    def test_weight_government_portal(self) -> None:
        assert TrustLevel.GOVERNMENT_PORTAL.weight == 0.7, (
            "Government portal should have weight 0.7"
        )

    def test_weight_government_data(self) -> None:
        assert TrustLevel.GOVERNMENT_DATA.weight == 0.5, (
            "Government data should have weight 0.5"
        )

    def test_weight_unverified(self) -> None:
        assert TrustLevel.UNVERIFIED.weight == 0.0, (
            "Unverified should have weight 0.0"
        )

    def test_weights_are_ordered(self) -> None:
        assert (
            TrustLevel.OFFICIAL_GAZETTE.weight
            > TrustLevel.LEGISLATION.weight
            > TrustLevel.PARLIAMENTARY.weight
            > TrustLevel.GOVERNMENT_PORTAL.weight
            > TrustLevel.GOVERNMENT_DATA.weight
            > TrustLevel.UNVERIFIED.weight
        ), "Trust weights should be strictly decreasing"


class TestChangeType:
    def test_values(self) -> None:
        expected = {
            "created", "updated", "benefits_changed",
            "eligibility_changed", "revoked", "extended",
            "amount_changed",
        }
        assert {e.value for e in ChangeType} == expected, (
            "ChangeType should have exactly 7 values"
        )

    def test_length(self) -> None:
        assert len(ChangeType) == 7, "ChangeType should have 7 members"

    def test_str_enum_behavior(self) -> None:
        assert str(ChangeType.CREATED) == "created"
        assert ChangeType.BENEFITS_CHANGED == "benefits_changed"


# -----------------------------------------------------------------------
# VerificationEvidence tests
# -----------------------------------------------------------------------


class TestVerificationEvidence:
    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        ev = VerificationEvidence(
            source=VerificationSource.GAZETTE_OF_INDIA,
            source_url="https://egazette.gov.in/test",
            document_type="notification",
            document_id="GAZ-2025-001",
            document_date=now,
            title="PM-KISAN Gazette Notification",
            excerpt="The government hereby notifies...",
            trust_weight=1.0,
            verified_at=now,
            verified_by="manual_review",
            raw_metadata={"part": "II", "section": "3(ii)"},
        )
        assert ev.source == VerificationSource.GAZETTE_OF_INDIA
        assert ev.source_url == "https://egazette.gov.in/test"
        assert ev.document_type == "notification"
        assert ev.document_id == "GAZ-2025-001"
        assert ev.document_date == now
        assert ev.title == "PM-KISAN Gazette Notification"
        assert ev.excerpt == "The government hereby notifies..."
        assert ev.trust_weight == 1.0
        assert ev.verified_by == "manual_review"
        assert ev.raw_metadata["part"] == "II"

    def test_default_values(self) -> None:
        ev = VerificationEvidence(
            source=VerificationSource.MYSCHEME_GOV,
            source_url="https://myscheme.gov.in/test",
            document_type="listing",
            title="Test Scheme",
        )
        assert ev.document_id is None
        assert ev.document_date is None
        assert ev.excerpt == ""
        assert ev.trust_weight == 0.0
        assert ev.verified_by == "auto_pipeline"
        assert ev.raw_metadata == {}
        assert ev.verified_at is not None

    def test_trust_weight_bounds(self) -> None:
        ev = VerificationEvidence(
            source=VerificationSource.INDIA_CODE,
            source_url="https://indiacode.nic.in/test",
            document_type="act",
            title="Test Act",
            trust_weight=0.9,
        )
        assert 0.0 <= ev.trust_weight <= 1.0

    def test_trust_weight_invalid_above_one(self) -> None:
        with pytest.raises(Exception):
            VerificationEvidence(
                source=VerificationSource.GAZETTE_OF_INDIA,
                source_url="https://test.gov.in",
                document_type="notification",
                title="Test",
                trust_weight=1.5,
            )

    def test_trust_weight_invalid_negative(self) -> None:
        with pytest.raises(Exception):
            VerificationEvidence(
                source=VerificationSource.GAZETTE_OF_INDIA,
                source_url="https://test.gov.in",
                document_type="notification",
                title="Test",
                trust_weight=-0.1,
            )


# -----------------------------------------------------------------------
# VerificationResult tests
# -----------------------------------------------------------------------


class TestVerificationResult:
    def test_creation_with_defaults(self) -> None:
        result = VerificationResult(scheme_id="pm-kisan")
        assert result.scheme_id == "pm-kisan"
        assert result.status == VerificationStatus.UNVERIFIED
        assert result.trust_score == 0.0
        assert result.evidences == []
        assert result.sources_checked == []
        assert result.sources_confirmed == []
        assert result.verification_started_at is None
        assert result.verification_completed_at is None
        assert result.last_reverification_at is None
        assert result.reverification_interval_hours == 168
        assert result.notes == []
        assert result.gazette_notification_number is None
        assert result.enabling_act is None
        assert result.parliamentary_session is None

    def test_default_reverification_interval(self) -> None:
        result = VerificationResult(scheme_id="test")
        assert result.reverification_interval_hours == 168, (
            "Default reverification interval should be 168 hours (1 week)"
        )

    def test_custom_reverification_interval(self) -> None:
        result = VerificationResult(
            scheme_id="test",
            reverification_interval_hours=48,
        )
        assert result.reverification_interval_hours == 48

    def test_trust_score_bounds(self) -> None:
        result = VerificationResult(
            scheme_id="test",
            trust_score=0.85,
        )
        assert 0.0 <= result.trust_score <= 1.0

    def test_trust_score_invalid_above_one(self) -> None:
        with pytest.raises(Exception):
            VerificationResult(scheme_id="test", trust_score=1.5)

    def test_trust_score_invalid_negative(self) -> None:
        with pytest.raises(Exception):
            VerificationResult(scheme_id="test", trust_score=-0.1)

    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        ev = VerificationEvidence(
            source=VerificationSource.GAZETTE_OF_INDIA,
            source_url="https://egazette.gov.in/test",
            document_type="notification",
            title="Test Gazette Notification",
            trust_weight=1.0,
        )
        result = VerificationResult(
            scheme_id="pm-kisan",
            status=VerificationStatus.VERIFIED,
            trust_score=0.95,
            evidences=[ev],
            sources_checked=[VerificationSource.GAZETTE_OF_INDIA, VerificationSource.INDIA_CODE],
            sources_confirmed=[VerificationSource.GAZETTE_OF_INDIA],
            verification_started_at=now,
            verification_completed_at=now,
            last_reverification_at=now,
            reverification_interval_hours=72,
            notes=["Confirmed via gazette"],
            gazette_notification_number="GSR-123-E",
            enabling_act="PM-KISAN Act 2019",
            parliamentary_session="17th Lok Sabha, Budget Session 2019",
        )
        assert result.status == VerificationStatus.VERIFIED
        assert result.trust_score == 0.95
        assert len(result.evidences) == 1
        assert len(result.sources_checked) == 2
        assert len(result.sources_confirmed) == 1
        assert result.gazette_notification_number == "GSR-123-E"
        assert result.enabling_act == "PM-KISAN Act 2019"
        assert result.parliamentary_session == "17th Lok Sabha, Budget Session 2019"
        assert result.reverification_interval_hours == 72
        assert result.notes == ["Confirmed via gazette"]

    def test_json_roundtrip(self) -> None:
        result = VerificationResult(
            scheme_id="roundtrip-test",
            status=VerificationStatus.PARTIALLY_VERIFIED,
            trust_score=0.6,
        )
        json_str = result.model_dump_json()
        restored = VerificationResult.model_validate_json(json_str)
        assert restored.scheme_id == result.scheme_id
        assert restored.status == result.status
        assert restored.trust_score == result.trust_score


# -----------------------------------------------------------------------
# VerificationSummary tests
# -----------------------------------------------------------------------


class TestVerificationSummary:
    def test_creation_with_defaults(self) -> None:
        summary = VerificationSummary(
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            status=VerificationStatus.VERIFIED,
        )
        assert summary.scheme_id == "pm-kisan"
        assert summary.scheme_name == "PM-KISAN"
        assert summary.status == VerificationStatus.VERIFIED
        assert summary.trust_score == 0.0
        assert summary.source_count == 0
        assert summary.last_verified is None
        assert summary.gazette_confirmed is False
        assert summary.act_confirmed is False
        assert summary.parliament_confirmed is False

    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        summary = VerificationSummary(
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            status=VerificationStatus.VERIFIED,
            trust_score=0.95,
            source_count=3,
            last_verified=now,
            gazette_confirmed=True,
            act_confirmed=True,
            parliament_confirmed=True,
        )
        assert summary.trust_score == 0.95
        assert summary.source_count == 3
        assert summary.last_verified == now
        assert summary.gazette_confirmed is True
        assert summary.act_confirmed is True
        assert summary.parliament_confirmed is True

    def test_trust_score_bounds(self) -> None:
        with pytest.raises(Exception):
            VerificationSummary(
                scheme_id="test",
                scheme_name="Test",
                status=VerificationStatus.UNVERIFIED,
                trust_score=1.5,
            )


# -----------------------------------------------------------------------
# SchemeChangelog tests
# -----------------------------------------------------------------------


class TestSchemeChangelog:
    def test_creation_with_required_fields(self) -> None:
        cl = SchemeChangelog(
            scheme_id="pm-kisan",
            change_type=ChangeType.BENEFITS_CHANGED,
            field_changed="benefits",
        )
        assert cl.scheme_id == "pm-kisan"
        assert cl.change_type == ChangeType.BENEFITS_CHANGED
        assert cl.field_changed == "benefits"
        assert cl.old_value is None
        assert cl.new_value is None
        assert cl.detected_at is not None
        assert cl.source is None
        assert cl.verified is False

    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        cl = SchemeChangelog(
            scheme_id="pm-kisan",
            change_type=ChangeType.ELIGIBILITY_CHANGED,
            field_changed="eligibility.income_limit",
            old_value="200000",
            new_value="300000",
            detected_at=now,
            source=VerificationSource.GAZETTE_OF_INDIA,
            verified=True,
        )
        assert cl.old_value == "200000"
        assert cl.new_value == "300000"
        assert cl.detected_at == now
        assert cl.source == VerificationSource.GAZETTE_OF_INDIA
        assert cl.verified is True

    def test_json_roundtrip(self) -> None:
        cl = SchemeChangelog(
            scheme_id="test",
            change_type=ChangeType.UPDATED,
            field_changed="name",
            old_value="Old Name",
            new_value="New Name",
        )
        json_str = cl.model_dump_json()
        restored = SchemeChangelog.model_validate_json(json_str)
        assert restored.scheme_id == cl.scheme_id
        assert restored.change_type == cl.change_type
        assert restored.old_value == cl.old_value
        assert restored.new_value == cl.new_value


# -----------------------------------------------------------------------
# VerificationDashboardStats tests
# -----------------------------------------------------------------------


class TestVerificationDashboardStats:
    def test_creation_with_defaults(self) -> None:
        stats = VerificationDashboardStats()
        assert stats.total_schemes == 0
        assert stats.verified_count == 0
        assert stats.partially_verified_count == 0
        assert stats.unverified_count == 0
        assert stats.disputed_count == 0
        assert stats.average_trust_score == 0.0
        assert stats.last_pipeline_run is None
        assert stats.sources_status == {}

    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        stats = VerificationDashboardStats(
            total_schemes=100,
            verified_count=60,
            partially_verified_count=25,
            unverified_count=10,
            disputed_count=5,
            average_trust_score=0.72,
            last_pipeline_run=now,
            sources_status={
                "gazette_of_india": True,
                "india_code": True,
                "myscheme_gov": False,
            },
        )
        assert stats.total_schemes == 100
        assert stats.verified_count == 60
        assert stats.partially_verified_count == 25
        assert stats.unverified_count == 10
        assert stats.disputed_count == 5
        assert stats.average_trust_score == 0.72
        assert stats.last_pipeline_run == now
        assert stats.sources_status["gazette_of_india"] is True
        assert stats.sources_status["myscheme_gov"] is False

    def test_average_trust_score_bounds(self) -> None:
        with pytest.raises(Exception):
            VerificationDashboardStats(average_trust_score=1.5)

    def test_json_roundtrip(self) -> None:
        stats = VerificationDashboardStats(
            total_schemes=50,
            verified_count=30,
            average_trust_score=0.8,
        )
        json_str = stats.model_dump_json()
        restored = VerificationDashboardStats.model_validate_json(json_str)
        assert restored.total_schemes == stats.total_schemes
        assert restored.verified_count == stats.verified_count
        assert restored.average_trust_score == stats.average_trust_score
