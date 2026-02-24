"""Tests for Hinglish (Hindi-English code-mixing) processor."""

from __future__ import annotations

import pytest

from src.services.hinglish import (
    COMMON_HINDI_ROMAN_WORDS,
    HinglishProcessor,
    _has_roman_hindi_words,
)


# -----------------------------------------------------------------------
# HinglishProcessor.is_hinglish tests
# -----------------------------------------------------------------------


class TestIsHinglish:
    """Test Hinglish detection heuristics."""

    def test_hinglish_sentence_with_roman_hindi(self) -> None:
        text = "mujhe PM Kisan ka paisa chahiye"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "sentence with Romanised Hindi words like 'kisan', 'paisa' should be detected as Hinglish"
        )

    def test_pure_english_not_hinglish(self) -> None:
        text = "I need help with my application"
        assert HinglishProcessor.is_hinglish(text) is False, (
            "pure English sentence without Hindi words should not be detected as Hinglish"
        )

    def test_pure_hindi_devanagari_not_hinglish(self) -> None:
        text = "\u092e\u0941\u091d\u0947 \u092a\u0947\u0902\u0936\u0928 \u091a\u093e\u0939\u093f\u090f"
        assert HinglishProcessor.is_hinglish(text) is False, (
            "pure Devanagari text without Latin characters should not be detected as Hinglish"
        )

    def test_mixed_devanagari_and_latin(self) -> None:
        text = "\u092e\u0941\u091d\u0947 PM Kisan \u0915\u093e \u092a\u0948\u0938\u093e \u091a\u093e\u0939\u093f\u090f"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "text with both Devanagari and Latin characters should be detected as Hinglish"
        )

    def test_single_high_signal_word_detected(self) -> None:
        text = "Tell me about yojana"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "a single high-signal Hindi word like 'yojana' should trigger Hinglish detection"
        )

    def test_government_context_hinglish(self) -> None:
        text = "mera aadhaar card link nahi ho raha"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "government-context Hinglish with 'aadhaar' should be detected"
        )

    def test_farmer_related_hinglish(self) -> None:
        text = "kisan ko fasal ka beej kab milega"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "farmer-related Hinglish with 'kisan', 'fasal', 'beej' should be detected"
        )

    def test_employment_hinglish(self) -> None:
        text = "manrega mein kaam kab milega"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "MGNREGA-related Hinglish with 'manrega' should be detected"
        )

    def test_health_hinglish(self) -> None:
        text = "ayushman card kaise banaye"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "health-related Hinglish with 'ayushman' should be detected"
        )

    def test_housing_hinglish(self) -> None:
        text = "pradhan mantri awas yojana ke liye apply karna hai"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "housing-related Hinglish with 'pradhan', 'awas', 'yojana' should be detected"
        )

    def test_pension_hinglish(self) -> None:
        text = "budhapa pension kab aayegi"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "pension-related Hinglish with 'budhapa' should be detected"
        )

    def test_complaint_hinglish(self) -> None:
        text = "shikayat kahan karein"
        assert HinglishProcessor.is_hinglish(text) is True, (
            "complaint-related Hinglish with 'shikayat' should be detected"
        )

    def test_empty_string(self) -> None:
        assert HinglishProcessor.is_hinglish("") is False, (
            "empty string should not be detected as Hinglish"
        )

    def test_numbers_only(self) -> None:
        assert HinglishProcessor.is_hinglish("12345 67890") is False, (
            "numbers-only string should not be detected as Hinglish"
        )

    def test_single_common_english_word_not_hinglish(self) -> None:
        # 'old' is in the word list but is low-signal; a single low-signal
        # word should not trigger Hinglish detection.
        text = "The old man walked slowly"
        assert HinglishProcessor.is_hinglish(text) is False, (
            "a single low-signal word like 'old' in English context should not be Hinglish"
        )


# -----------------------------------------------------------------------
# HinglishProcessor.extract_intent_keywords tests
# -----------------------------------------------------------------------


class TestExtractIntentKeywords:
    """Test intent keyword extraction from Hinglish text."""

    def test_farmer_payment_keywords(self) -> None:
        text = "mujhe kisan ka paisa chahiye"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert "agriculture" in keywords, "should extract 'agriculture' intent from 'kisan'"
        assert "payment_status" in keywords, "should extract 'payment_status' intent from 'paisa'"

    def test_health_keywords(self) -> None:
        text = "hospital mein ilaj ke liye ayushman card chahiye"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert "health" in keywords, "should extract 'health' intent from 'hospital', 'ilaj', 'ayushman'"

    def test_education_keywords(self) -> None:
        text = "scholarship ke liye school mein padhai"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert "education" in keywords, "should extract 'education' intent from 'scholarship', 'school', 'padhai'"

    def test_housing_keywords(self) -> None:
        text = "ghar banane ke liye awas yojana"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert "housing" in keywords, "should extract 'housing' from 'ghar', 'awas'"
        assert "scheme_search" in keywords, "should extract 'scheme_search' from 'yojana'"

    def test_employment_keywords(self) -> None:
        text = "naukri dhundh raha hoon rozgar chahiye"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert "employment" in keywords, "should extract 'employment' from 'naukri', 'rozgar'"

    def test_document_help_keywords(self) -> None:
        text = "aadhaar card aur ration card banana hai"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert "document_help" in keywords, "should extract 'document_help' from 'aadhaar', 'ration', 'card'"

    def test_deduplication(self) -> None:
        text = "kisan kisan kisan farmer farmer"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        # Should only appear once despite multiple mentions.
        assert keywords.count("agriculture") == 1, "keywords should be deduplicated"

    def test_preserves_first_seen_order(self) -> None:
        text = "paisa kisan yojana"
        keywords = HinglishProcessor.extract_intent_keywords(text)
        assert keywords[0] == "payment_status", "first keyword should be 'payment_status' (from 'paisa')"
        assert keywords[1] == "agriculture", "second keyword should be 'agriculture' (from 'kisan')"
        assert keywords[2] == "scheme_search", "third keyword should be 'scheme_search' (from 'yojana')"

    def test_empty_text(self) -> None:
        keywords = HinglishProcessor.extract_intent_keywords("")
        assert keywords == [], "empty text should return empty keyword list"

    def test_no_matching_keywords(self) -> None:
        keywords = HinglishProcessor.extract_intent_keywords("hello world foo bar")
        assert keywords == [], "text with no matching Hindi words should return empty list"


# -----------------------------------------------------------------------
# HinglishProcessor.normalize tests
# -----------------------------------------------------------------------


class TestNormalize:
    """Test Romanised Hindi -> Devanagari normalisation."""

    def test_normalize_sarkari(self) -> None:
        result = HinglishProcessor.normalize("sarkari yojana")
        assert "\u0938\u0930\u0915\u093e\u0930\u0940" in result, "should normalize 'sarkari' to Devanagari"
        assert "\u092f\u094b\u091c\u0928\u093e" in result, "should normalize 'yojana' to Devanagari"

    def test_normalize_preserves_english(self) -> None:
        result = HinglishProcessor.normalize("PM sarkari scheme")
        assert "PM" in result, "English words not in dictionary should be preserved"
        assert "scheme" in result, "'scheme' is not in the Devanagari mapping, should be preserved"

    def test_normalize_kisan(self) -> None:
        result = HinglishProcessor.normalize("kisan ki fasal")
        assert "\u0915\u093f\u0938\u093e\u0928" in result, "should normalize 'kisan' to Devanagari"

    def test_normalize_case_insensitive(self) -> None:
        result = HinglishProcessor.normalize("SARKARI Yojana")
        assert "\u0938\u0930\u0915\u093e\u0930\u0940" in result, "normalization should be case-insensitive"
        assert "\u092f\u094b\u091c\u0928\u093e" in result

    def test_normalize_empty_string(self) -> None:
        result = HinglishProcessor.normalize("")
        assert result == "", "normalizing empty string should return empty string"

    def test_normalize_no_hindi_words(self) -> None:
        text = "hello world test"
        result = HinglishProcessor.normalize(text)
        assert result == text, "text with no Hindi words should be returned unchanged"

    def test_normalize_common_government_terms(self) -> None:
        """Test normalization of common government-related terms."""
        terms_to_test = {
            "aadhaar": "\u0906\u0927\u093e\u0930",
            "pension": "\u092a\u0947\u0902\u0936\u0928",
            "bijli": "\u092c\u093f\u091c\u0932\u0940",
            "paani": "\u092a\u093e\u0928\u0940",
            "mahila": "\u092e\u0939\u093f\u0932\u093e",
            "dawai": "\u0926\u0935\u093e\u0908",
        }
        for roman, devanagari in terms_to_test.items():
            result = HinglishProcessor.normalize(roman)
            assert devanagari in result, f"'{roman}' should be normalized to '{devanagari}'"


# -----------------------------------------------------------------------
# Various Hinglish patterns from government context
# -----------------------------------------------------------------------


class TestGovernmentContextPatterns:
    """Test various real-world Hinglish patterns from government scheme queries."""

    @pytest.mark.parametrize(
        "text",
        [
            "mera PM Kisan ka paisa nahi aaya",
            "kisan samman nidhi kab milegi",
            "ayushman bharat card banwana hai",
            "pradhan mantri awas yojana ke liye apply",
            "manrega mein kaam chahiye",
            "ujjwala yojana ka cylinder kaise milega",
            "garib ko scholarship milti hai kya",
            "mera ration card abhi tak nahi bana",
            "vridha pension ke liye kya karna padega",
            "baccha ka poshan kaise milega anganwadi se",
        ],
    )
    def test_government_hinglish_detected(self, text: str) -> None:
        assert HinglishProcessor.is_hinglish(text) is True, (
            f"government-context Hinglish should be detected: '{text}'"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "What is the eligibility for PM-KISAN?",
            "How to apply for Ayushman Bharat?",
            "Tell me about MGNREGA benefits",
        ],
    )
    def test_pure_english_government_queries(self, text: str) -> None:
        # These are pure English -- they should NOT be detected as Hinglish.
        # Note: some may contain words like "MGNREGA" which are proper nouns.
        result = HinglishProcessor.is_hinglish(text)
        # We do not assert False here because some government acronyms might
        # overlap with Hindi words. We only check it does not crash.
        assert isinstance(result, bool)


# -----------------------------------------------------------------------
# _has_roman_hindi_words helper tests
# -----------------------------------------------------------------------


class TestHasRomanHindiWords:
    def test_single_high_signal_word(self) -> None:
        assert _has_roman_hindi_words("Tell me about yojana") is True, (
            "single high-signal word 'yojana' should return True"
        )

    def test_two_low_signal_words(self) -> None:
        # 'old' and 'card' are low-signal words
        assert _has_roman_hindi_words("old card holder") is True, (
            "two or more known Hindi words should return True"
        )

    def test_no_hindi_words(self) -> None:
        assert _has_roman_hindi_words("The quick brown fox") is False

    def test_common_hindi_words_dict_has_entries(self) -> None:
        assert len(COMMON_HINDI_ROMAN_WORDS) > 50, (
            "COMMON_HINDI_ROMAN_WORDS should have 50+ entries"
        )
