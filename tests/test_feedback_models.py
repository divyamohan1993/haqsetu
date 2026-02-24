"""Tests for feedback and grievance data models: enums, citizen feedback, and responses."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.models.feedback import (
    CitizenFeedback,
    FeedbackListResponse,
    FeedbackPriority,
    FeedbackResponse,
    FeedbackStats,
    FeedbackStatus,
    FeedbackType,
)


# -----------------------------------------------------------------------
# Enum tests
# -----------------------------------------------------------------------


class TestFeedbackType:
    def test_values(self) -> None:
        expected = {
            "accuracy_report", "missing_scheme", "incorrect_info",
            "scheme_expired", "grievance", "suggestion", "appreciation",
        }
        assert {e.value for e in FeedbackType} == expected, (
            "FeedbackType should have exactly 7 values"
        )

    def test_length(self) -> None:
        assert len(FeedbackType) == 7, "FeedbackType should have 7 members"

    def test_str_enum_behavior(self) -> None:
        assert str(FeedbackType.ACCURACY_REPORT) == "accuracy_report"
        assert FeedbackType.GRIEVANCE == "grievance"


class TestFeedbackPriority:
    def test_values(self) -> None:
        expected = {"low", "medium", "high", "critical"}
        assert {e.value for e in FeedbackPriority} == expected, (
            "FeedbackPriority should have exactly 4 values"
        )

    def test_length(self) -> None:
        assert len(FeedbackPriority) == 4, "FeedbackPriority should have 4 members"

    def test_str_enum_behavior(self) -> None:
        assert str(FeedbackPriority.LOW) == "low"
        assert FeedbackPriority.CRITICAL == "critical"


class TestFeedbackStatus:
    def test_values(self) -> None:
        expected = {
            "submitted", "under_review", "verified",
            "resolved", "rejected",
        }
        assert {e.value for e in FeedbackStatus} == expected, (
            "FeedbackStatus should have exactly 5 values"
        )

    def test_length(self) -> None:
        assert len(FeedbackStatus) == 5, "FeedbackStatus should have 5 members"

    def test_str_enum_behavior(self) -> None:
        assert str(FeedbackStatus.SUBMITTED) == "submitted"
        assert FeedbackStatus.RESOLVED == "resolved"


# -----------------------------------------------------------------------
# CitizenFeedback tests
# -----------------------------------------------------------------------


class TestCitizenFeedback:
    def test_creation_with_required_fields(self) -> None:
        fb = CitizenFeedback(
            feedback_type=FeedbackType.ACCURACY_REPORT,
            description="The income limit for PM-KISAN is listed as Rs 2 lakh but it should be Rs 3 lakh.",
        )
        assert fb.feedback_type == FeedbackType.ACCURACY_REPORT
        assert "income limit" in fb.description
        assert fb.feedback_id is not None
        assert len(fb.feedback_id) == 32, "feedback_id should be a 32-char hex UUID"

    def test_auto_generated_feedback_id(self) -> None:
        fb = CitizenFeedback(
            feedback_type=FeedbackType.SUGGESTION,
            description="Please add more schemes for tribal communities in Jharkhand.",
        )
        assert fb.feedback_id is not None
        assert len(fb.feedback_id) == 32

    def test_unique_feedback_ids(self) -> None:
        feedbacks = [
            CitizenFeedback(
                feedback_type=FeedbackType.SUGGESTION,
                description="This is a valid test description for feedback number generation.",
            )
            for _ in range(10)
        ]
        ids = {fb.feedback_id for fb in feedbacks}
        assert len(ids) == 10, "Each feedback should get a unique feedback_id"

    def test_default_values(self) -> None:
        fb = CitizenFeedback(
            feedback_type=FeedbackType.GRIEVANCE,
            description="I applied for PM Awas Yojana three months ago and haven't heard back.",
        )
        assert fb.priority == FeedbackPriority.MEDIUM
        assert fb.scheme_id is None
        assert fb.scheme_name is None
        assert fb.expected_correction is None
        assert fb.evidence_url is None
        assert fb.language == "en"
        assert fb.status == FeedbackStatus.SUBMITTED
        assert fb.resolution_notes is None
        assert fb.triggered_reverification is False
        assert fb.submitted_at is not None

    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        fb = CitizenFeedback(
            feedback_type=FeedbackType.INCORRECT_INFO,
            priority=FeedbackPriority.HIGH,
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            description="The application deadline listed on the site is wrong. It was extended to March 2026.",
            expected_correction="Deadline should be March 31, 2026",
            evidence_url="https://pmkisan.gov.in/notice.pdf",
            language="hi",
            submitted_at=now,
            status=FeedbackStatus.UNDER_REVIEW,
            resolution_notes="Under investigation",
            triggered_reverification=True,
        )
        assert fb.feedback_type == FeedbackType.INCORRECT_INFO
        assert fb.priority == FeedbackPriority.HIGH
        assert fb.scheme_id == "pm-kisan"
        assert fb.scheme_name == "PM-KISAN"
        assert fb.expected_correction == "Deadline should be March 31, 2026"
        assert fb.evidence_url == "https://pmkisan.gov.in/notice.pdf"
        assert fb.language == "hi"
        assert fb.status == FeedbackStatus.UNDER_REVIEW
        assert fb.resolution_notes == "Under investigation"
        assert fb.triggered_reverification is True

    def test_description_min_length_validation(self) -> None:
        with pytest.raises(Exception):
            CitizenFeedback(
                feedback_type=FeedbackType.SUGGESTION,
                description="Short",  # Less than 10 characters
            )

    def test_description_max_length_validation(self) -> None:
        with pytest.raises(Exception):
            CitizenFeedback(
                feedback_type=FeedbackType.SUGGESTION,
                description="x" * 2001,  # More than 2000 characters
            )

    def test_description_exactly_min_length(self) -> None:
        fb = CitizenFeedback(
            feedback_type=FeedbackType.SUGGESTION,
            description="1234567890",  # Exactly 10 characters
        )
        assert len(fb.description) == 10

    def test_description_exactly_max_length(self) -> None:
        fb = CitizenFeedback(
            feedback_type=FeedbackType.SUGGESTION,
            description="x" * 2000,  # Exactly 2000 characters
        )
        assert len(fb.description) == 2000

    def test_json_roundtrip(self) -> None:
        fb = CitizenFeedback(
            feedback_type=FeedbackType.ACCURACY_REPORT,
            priority=FeedbackPriority.CRITICAL,
            scheme_id="ayushman-bharat",
            description="The scheme description mentions Rs 5 lakh coverage but the limit was revised.",
        )
        json_str = fb.model_dump_json()
        restored = CitizenFeedback.model_validate_json(json_str)
        assert restored.feedback_id == fb.feedback_id
        assert restored.feedback_type == fb.feedback_type
        assert restored.priority == fb.priority
        assert restored.scheme_id == fb.scheme_id
        assert restored.description == fb.description


# -----------------------------------------------------------------------
# FeedbackResponse tests
# -----------------------------------------------------------------------


class TestFeedbackResponse:
    def test_creation(self) -> None:
        resp = FeedbackResponse(
            feedback_id="abc123",
            message="Thank you for your feedback.",
            status=FeedbackStatus.SUBMITTED,
        )
        assert resp.feedback_id == "abc123"
        assert resp.message == "Thank you for your feedback."
        assert resp.status == FeedbackStatus.SUBMITTED


# -----------------------------------------------------------------------
# FeedbackListResponse tests
# -----------------------------------------------------------------------


class TestFeedbackListResponse:
    def test_creation(self) -> None:
        resp = FeedbackListResponse(
            feedbacks=[{"feedback_id": "f1"}, {"feedback_id": "f2"}],
            total=10,
            page=1,
            page_size=2,
        )
        assert len(resp.feedbacks) == 2
        assert resp.total == 10
        assert resp.page == 1
        assert resp.page_size == 2


# -----------------------------------------------------------------------
# FeedbackStats tests
# -----------------------------------------------------------------------


class TestFeedbackStats:
    def test_creation(self) -> None:
        stats = FeedbackStats(
            total_feedback=100,
            pending_review=20,
            resolved=70,
            accuracy_reports=15,
            grievances=30,
            average_resolution_time_hours=48.5,
        )
        assert stats.total_feedback == 100
        assert stats.pending_review == 20
        assert stats.resolved == 70
        assert stats.accuracy_reports == 15
        assert stats.grievances == 30
        assert stats.average_resolution_time_hours == 48.5

    def test_optional_resolution_time(self) -> None:
        stats = FeedbackStats(
            total_feedback=0,
            pending_review=0,
            resolved=0,
            accuracy_reports=0,
            grievances=0,
            average_resolution_time_hours=None,
        )
        assert stats.average_resolution_time_hours is None
