"""Tests for the VoiceAgentService."""

from __future__ import annotations

import pytest

from src.services.voice_agent import (
    LEGAL_DISCLAIMER,
    CaseAnalysis,
    ConversationSession,
    VoiceAgentService,
)


class MockLLMService:
    """Mock LLM service for testing."""

    async def generate(self, prompt, context=None, conversation_history=None, temperature=0.7):
        """Return a mock structured response."""

        class MockResult:
            answer = (
                '{"response_text": "I understand your situation. Based on what you described, '
                'the Protection of Women from Domestic Violence Act, 2005 may apply.", '
                '"identified_laws": [{"law": "DV Act 2005", "description": "Protection from domestic violence", '
                '"relevance": "Applies to your situation"}], '
                '"applicable_schemes": [{"scheme": "One Stop Centre", "relevance": "Immediate shelter and support"}], '
                '"recommended_actions": ["Call Women Helpline 181", "Visit nearest DLSA"], '
                '"helplines": [{"name": "Women Helpline", "number": "181"}], '
                '"severity": "high", "needs_more_info": false}'
            )

        return MockResult()


class MockTranslationService:
    """Mock translation service for testing."""

    async def translate(self, text, source_lang="en", target_lang="hi"):
        return f"[translated:{target_lang}] {text}"


@pytest.fixture
def voice_agent():
    return VoiceAgentService(
        llm=MockLLMService(),
        translation=MockTranslationService(),
    )


@pytest.mark.asyncio
async def test_start_session(voice_agent):
    session = await voice_agent.start_session(language="hi")
    assert isinstance(session, ConversationSession)
    assert session.user_language == "hi"
    assert session.session_id


@pytest.mark.asyncio
async def test_start_session_with_id(voice_agent):
    session = await voice_agent.start_session(session_id="test-123", language="en")
    assert session.session_id == "test-123"
    assert session.user_language == "en"


@pytest.mark.asyncio
async def test_process_message(voice_agent):
    session = await voice_agent.start_session(language="en")
    response = await voice_agent.process_message(
        session_id=session.session_id,
        user_message="My husband beats me. What can I do?",
        language="en",
    )
    assert response.response_text
    assert response.disclaimer
    assert response.language == "en"


@pytest.mark.asyncio
async def test_process_message_creates_session(voice_agent):
    response = await voice_agent.process_message(
        session_id="new-session",
        user_message="I need help",
    )
    assert response.response_text
    session = voice_agent.get_session("new-session")
    assert session is not None
    assert len(session.turns) == 2  # user + agent


@pytest.mark.asyncio
async def test_case_analysis_updated(voice_agent):
    session = await voice_agent.start_session(session_id="case-test")
    await voice_agent.process_message(
        session_id="case-test",
        user_message="My employer has not paid my salary for 3 months",
        language="en",
    )
    case = voice_agent.get_case_analysis("case-test")
    assert case is not None
    assert isinstance(case, CaseAnalysis)


@pytest.mark.asyncio
async def test_disclaimer_always_present(voice_agent):
    response = await voice_agent.process_message(
        session_id="disclaimer-test",
        user_message="Someone stole my land",
        language="en",
    )
    assert response.disclaimer
    assert "NOT legal advice" in response.disclaimer or "not legal advice" in response.disclaimer.lower()


@pytest.mark.asyncio
async def test_get_nonexistent_session(voice_agent):
    assert voice_agent.get_session("nonexistent") is None
    assert voice_agent.get_case_analysis("nonexistent") is None


def test_legal_disclaimer_constant():
    assert "NOT legal advice" in LEGAL_DISCLAIMER
    assert "DLSA" in LEGAL_DISCLAIMER
    assert "1516" in LEGAL_DISCLAIMER
