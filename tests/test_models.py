"""Tests for data models: enums, request, response, and scheme models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.models.enums import (
    ChannelType,
    ContentType,
    DeviceType,
    LanguageCode,
    NetworkQuality,
    QueryIntent,
    ServiceProvider,
)
from src.models.request import HaqSetuRequest, RequestMetadata
from src.models.response import (
    HaqSetuResponse,
    LatencyBreakdown,
    ResponseMetadata,
    SchemeReference,
    SuggestedAction,
)
from src.models.scheme import (
    EligibilityCriteria,
    SchemeCategory,
    SchemeDocument,
)


# -----------------------------------------------------------------------
# Enum tests
# -----------------------------------------------------------------------


class TestChannelType:
    def test_values(self) -> None:
        expected = {"ivr", "whatsapp", "sms", "ussd", "missed_call_back", "csc_kiosk", "web"}
        assert {e.value for e in ChannelType} == expected, "ChannelType should have exactly 7 values"

    def test_length(self) -> None:
        assert len(ChannelType) == 7, "ChannelType should have 7 members"

    def test_str_enum_behavior(self) -> None:
        assert str(ChannelType.IVR) == "ivr", "StrEnum value should be directly usable as string"
        assert ChannelType.WHATSAPP == "whatsapp"


class TestContentType:
    def test_values(self) -> None:
        expected = {"audio", "text", "dtmf", "ussd_selection", "image", "location"}
        assert {e.value for e in ContentType} == expected, "ContentType should have exactly 6 values"

    def test_length(self) -> None:
        assert len(ContentType) == 6, "ContentType should have 6 members"


class TestNetworkQuality:
    def test_values(self) -> None:
        expected = {"offline", "2g", "3g", "4g", "wifi"}
        assert {e.value for e in NetworkQuality} == expected, "NetworkQuality should have exactly 5 values"

    def test_length(self) -> None:
        assert len(NetworkQuality) == 5, "NetworkQuality should have 5 members"


class TestDeviceType:
    def test_values(self) -> None:
        expected = {"feature_phone", "smartphone", "csc_kiosk"}
        assert {e.value for e in DeviceType} == expected, "DeviceType should have exactly 3 values"

    def test_length(self) -> None:
        assert len(DeviceType) == 3, "DeviceType should have 3 members"


class TestQueryIntent:
    def test_values(self) -> None:
        expected = {
            "scheme_search", "eligibility_check", "application_guidance",
            "status_inquiry", "mandi_price", "weather_query", "soil_health",
            "document_help", "payment_status", "general_info", "greeting",
            "complaint", "human_escalation",
        }
        assert {e.value for e in QueryIntent} == expected, "QueryIntent should have exactly 13 values"

    def test_length(self) -> None:
        assert len(QueryIntent) == 13, "QueryIntent should have 13 members"


class TestLanguageCode:
    def test_has_exactly_23_values(self) -> None:
        assert len(LanguageCode) == 23, (
            f"LanguageCode should have exactly 23 values (22 scheduled languages + English), "
            f"got {len(LanguageCode)}"
        )

    def test_hindi_present(self) -> None:
        assert LanguageCode.hi == "hi", "Hindi should be present with code 'hi'"

    def test_english_present(self) -> None:
        assert LanguageCode.en == "en", "English should be present with code 'en'"

    def test_odia_keyword_safe_name(self) -> None:
        assert LanguageCode.or_lang == "or", "Odia should use 'or_lang' as attribute name with value 'or'"

    def test_assamese_keyword_safe_name(self) -> None:
        assert LanguageCode.as_lang == "as", "Assamese should use 'as_lang' as attribute name with value 'as'"

    def test_all_codes_are_strings(self) -> None:
        for code in LanguageCode:
            assert isinstance(code.value, str), f"Language code {code.name} should be a string"


class TestServiceProvider:
    def test_values(self) -> None:
        expected = {"bhashini", "sarvam", "google", "ai4bharat", "vertex_ai", "gemini"}
        assert {e.value for e in ServiceProvider} == expected, "ServiceProvider should have exactly 6 values"

    def test_length(self) -> None:
        assert len(ServiceProvider) == 6, "ServiceProvider should have 6 members"


class TestSchemeCategory:
    def test_values(self) -> None:
        expected = {
            "agriculture", "health", "education", "housing", "employment",
            "social_security", "financial_inclusion", "women_child", "tribal",
            "disability", "senior_citizen", "skill_development",
            "infrastructure", "other",
        }
        assert {e.value for e in SchemeCategory} == expected, "SchemeCategory should have exactly 14 values"

    def test_length(self) -> None:
        assert len(SchemeCategory) == 14, "SchemeCategory should have 14 members"


# -----------------------------------------------------------------------
# Request model tests
# -----------------------------------------------------------------------


class TestHaqSetuRequest:
    def test_serialization_basic(self) -> None:
        req = HaqSetuRequest(
            session_id="sess-001",
            channel_type=ChannelType.WHATSAPP,
            content="Hello, I need help",
            content_type=ContentType.TEXT,
            language=LanguageCode.en,
            metadata=RequestMetadata(phone_number="+919876543210"),
        )
        data = req.model_dump()

        assert data["session_id"] == "sess-001"
        assert data["channel_type"] == "whatsapp"
        assert data["content"] == "Hello, I need help"
        assert data["content_type"] == "text"
        assert data["language"] == "en"
        assert data["metadata"]["phone_number"] == "+919876543210"

    def test_auto_generated_request_id(self) -> None:
        req = HaqSetuRequest(
            session_id="sess-002",
            channel_type=ChannelType.IVR,
            content="test",
            content_type=ContentType.TEXT,
            metadata=RequestMetadata(phone_number="+919876543210"),
        )
        assert req.request_id is not None, "request_id should be auto-generated"
        assert len(req.request_id) == 32, "request_id should be a 32-char hex UUID"

    def test_unique_request_ids(self) -> None:
        reqs = [
            HaqSetuRequest(
                session_id="sess",
                channel_type=ChannelType.WEB,
                content="test",
                content_type=ContentType.TEXT,
                metadata=RequestMetadata(phone_number="+91000"),
            )
            for _ in range(10)
        ]
        ids = {r.request_id for r in reqs}
        assert len(ids) == 10, "Each request should get a unique request_id"

    def test_optional_language(self) -> None:
        req = HaqSetuRequest(
            session_id="sess-003",
            channel_type=ChannelType.SMS,
            content="test",
            content_type=ContentType.TEXT,
            metadata=RequestMetadata(phone_number="+91000"),
        )
        assert req.language is None, "language should default to None"

    def test_json_roundtrip(self) -> None:
        req = HaqSetuRequest(
            session_id="sess-004",
            channel_type=ChannelType.WEB,
            content="test content",
            content_type=ContentType.TEXT,
            language=LanguageCode.hi,
            metadata=RequestMetadata(
                phone_number="+919876543210",
                device_type=DeviceType.SMARTPHONE,
                approximate_state="Bihar",
                network_quality=NetworkQuality.THREE_G,
            ),
        )
        json_str = req.model_dump_json()
        restored = HaqSetuRequest.model_validate_json(json_str)
        assert restored.session_id == req.session_id
        assert restored.channel_type == req.channel_type
        assert restored.language == req.language
        assert restored.metadata.approximate_state == "Bihar"
        assert restored.metadata.network_quality == NetworkQuality.THREE_G

    def test_request_metadata_defaults(self) -> None:
        meta = RequestMetadata(phone_number="+91000")
        assert meta.device_type is None
        assert meta.approximate_state is None
        assert meta.network_quality == NetworkQuality.FOUR_G, "Default network quality should be 4G"
        assert meta.timestamp is not None, "timestamp should be auto-generated"


# -----------------------------------------------------------------------
# Response model tests
# -----------------------------------------------------------------------


class TestLatencyBreakdown:
    def test_total_ms_computation(self) -> None:
        lb = LatencyBreakdown(
            asr_ms=100.0,
            language_detection_ms=50.0,
            translation_in_ms=200.0,
            rag_retrieval_ms=150.0,
            llm_reasoning_ms=300.0,
            translation_out_ms=180.0,
            tts_ms=120.0,
        )
        assert lb.total_ms == 1100.0, (
            f"total_ms should be the sum of all latency components; got {lb.total_ms}"
        )

    def test_total_ms_defaults_to_zero(self) -> None:
        lb = LatencyBreakdown()
        assert lb.total_ms == 0.0, "total_ms should be 0.0 when all components default to 0.0"

    def test_total_ms_partial_values(self) -> None:
        lb = LatencyBreakdown(asr_ms=50.0, llm_reasoning_ms=250.0)
        assert lb.total_ms == 300.0, "total_ms should sum only non-zero components correctly"

    def test_total_ms_in_serialization(self) -> None:
        lb = LatencyBreakdown(asr_ms=100.0, tts_ms=200.0)
        data = lb.model_dump()
        assert "total_ms" in data, "total_ms should be present in serialized output (computed_field)"
        assert data["total_ms"] == 300.0


class TestHaqSetuResponse:
    def test_serialization(self) -> None:
        resp = HaqSetuResponse(
            request_id="req-001",
            session_id="sess-001",
            content="Here are the schemes available for farmers.",
            content_type=ContentType.TEXT,
            language=LanguageCode.en,
            metadata=ResponseMetadata(
                confidence=0.85,
                latency=LatencyBreakdown(asr_ms=10.0, llm_reasoning_ms=200.0),
                schemes_referenced=[
                    SchemeReference(
                        scheme_id="pm-kisan",
                        scheme_name="PM-KISAN",
                        relevance_score=0.92,
                        matched_criteria=["farmer", "income support"],
                    )
                ],
                requires_followup=False,
                suggested_actions=[
                    SuggestedAction(
                        type="apply_scheme",
                        description="Apply for PM-KISAN at pmkisan.gov.in",
                    )
                ],
            ),
        )
        data = resp.model_dump()

        assert data["request_id"] == "req-001"
        assert data["content_type"] == "text"
        assert data["language"] == "en"
        assert data["metadata"]["confidence"] == 0.85
        assert len(data["metadata"]["schemes_referenced"]) == 1
        assert data["metadata"]["schemes_referenced"][0]["scheme_id"] == "pm-kisan"
        assert len(data["metadata"]["suggested_actions"]) == 1
        assert data["metadata"]["suggested_actions"][0]["type"] == "apply_scheme"

    def test_scheme_reference_defaults(self) -> None:
        sr = SchemeReference(scheme_id="test", scheme_name="Test Scheme")
        assert sr.relevance_score == 0.0
        assert sr.matched_criteria == []

    def test_suggested_action_types(self) -> None:
        valid_types = [
            "apply_scheme", "check_eligibility", "call_helpline",
            "visit_csc", "upload_document", "track_status", "escalate",
        ]
        for action_type in valid_types:
            action = SuggestedAction(type=action_type, description="test")
            assert action.type == action_type


# -----------------------------------------------------------------------
# Scheme model tests
# -----------------------------------------------------------------------


class TestEligibilityCriteria:
    def test_all_optional_fields(self) -> None:
        ec = EligibilityCriteria()
        assert ec.min_age is None
        assert ec.max_age is None
        assert ec.gender is None
        assert ec.income_limit is None
        assert ec.category is None
        assert ec.occupation is None
        assert ec.state is None
        assert ec.is_bpl is None
        assert ec.land_holding_acres is None
        assert ec.custom_criteria == []

    def test_full_criteria(self) -> None:
        ec = EligibilityCriteria(
            min_age=18,
            max_age=60,
            gender="female",
            income_limit=250000.0,
            category="SC",
            occupation="farmer",
            state="Bihar",
            is_bpl=True,
            land_holding_acres=2.5,
            custom_criteria=["Must have Aadhaar", "Must have bank account"],
        )
        assert ec.min_age == 18
        assert ec.max_age == 60
        assert ec.gender == "female"
        assert ec.income_limit == 250000.0
        assert ec.category == "SC"
        assert ec.occupation == "farmer"
        assert ec.state == "Bihar"
        assert ec.is_bpl is True
        assert ec.land_holding_acres == 2.5
        assert len(ec.custom_criteria) == 2


class TestSchemeDocument:
    def test_parsing_from_dict(self) -> None:
        data = {
            "scheme_id": "test-scheme",
            "name": "Test Scheme",
            "description": "A test scheme for testing",
            "category": "agriculture",
            "ministry": "Ministry of Testing",
            "state": None,
            "eligibility": {
                "occupation": "farmer",
                "is_bpl": True,
                "custom_criteria": ["Must be alive"],
            },
            "benefits": "Rs 1000 per month",
            "application_process": "Apply online",
            "documents_required": ["Aadhaar Card", "PAN Card"],
            "helpline": "1800-000-0000",
            "website": "https://test.gov.in",
            "last_updated": "2025-01-01T00:00:00Z",
            "popularity_score": 0.75,
        }
        scheme = SchemeDocument(
            scheme_id=data["scheme_id"],
            name=data["name"],
            description=data["description"],
            category=SchemeCategory.AGRICULTURE,
            ministry=data["ministry"],
            state=data["state"],
            eligibility=EligibilityCriteria(**data["eligibility"]),
            benefits=data["benefits"],
            application_process=data["application_process"],
            documents_required=data["documents_required"],
            helpline=data["helpline"],
            website=data["website"],
            last_updated=data["last_updated"],
            popularity_score=data["popularity_score"],
        )

        assert scheme.scheme_id == "test-scheme"
        assert scheme.name == "Test Scheme"
        assert scheme.category == SchemeCategory.AGRICULTURE
        assert scheme.eligibility.occupation == "farmer"
        assert scheme.eligibility.is_bpl is True
        assert len(scheme.documents_required) == 2
        assert scheme.popularity_score == 0.75

    def test_optional_fields_default(self) -> None:
        scheme = SchemeDocument(
            scheme_id="minimal",
            name="Minimal Scheme",
            description="Desc",
            category=SchemeCategory.OTHER,
            ministry="Ministry",
            eligibility=EligibilityCriteria(),
            benefits="Some benefits",
            application_process="Apply",
            documents_required=[],
            last_updated="2025-01-01T00:00:00Z",
        )
        assert scheme.state is None
        assert scheme.helpline is None
        assert scheme.website is None
        assert scheme.deadline is None
        assert scheme.popularity_score == 0.0
        assert scheme.embedding is None
        assert scheme.name_translations == {}
        assert scheme.description_translations == {}

    def test_json_roundtrip(self) -> None:
        scheme = SchemeDocument(
            scheme_id="roundtrip",
            name="Roundtrip Scheme",
            description="Desc",
            category=SchemeCategory.HEALTH,
            ministry="Ministry of Health",
            eligibility=EligibilityCriteria(min_age=18, max_age=65),
            benefits="Free treatment",
            application_process="Visit hospital",
            documents_required=["Aadhaar"],
            last_updated="2025-06-01T00:00:00Z",
            popularity_score=0.5,
        )
        json_str = scheme.model_dump_json()
        restored = SchemeDocument.model_validate_json(json_str)
        assert restored.scheme_id == scheme.scheme_id
        assert restored.category == scheme.category
        assert restored.eligibility.min_age == 18
        assert restored.eligibility.max_age == 65
