"""Tests for language configuration."""

from __future__ import annotations

import pytest

from config.languages import (
    LANGUAGE_CODE_MAP,
    LANGUAGES,
    LanguageConfig,
    get_gcp_tts_languages,
    get_high_priority_languages,
    get_language,
    get_supported_languages,
)


# -----------------------------------------------------------------------
# LANGUAGES registry tests
# -----------------------------------------------------------------------


class TestLanguagesRegistry:
    def test_all_23_languages_present(self) -> None:
        assert len(LANGUAGES) == 23, (
            f"LANGUAGES should have 23 entries (22 scheduled + English), got {len(LANGUAGES)}"
        )

    def test_expected_language_codes(self) -> None:
        expected_codes = {
            "hi", "bn", "en", "te", "mr", "ta", "ur", "gu", "kn", "or",
            "ml", "pa", "mai", "as", "sat", "ks", "ne", "sd", "doi",
            "kok", "mni", "brx", "sa",
        }
        actual_codes = set(LANGUAGES.keys())
        assert actual_codes == expected_codes, (
            f"Missing: {expected_codes - actual_codes}, Extra: {actual_codes - expected_codes}"
        )

    def test_all_entries_are_language_config(self) -> None:
        for code, config in LANGUAGES.items():
            assert isinstance(config, LanguageConfig), (
                f"LANGUAGES['{code}'] should be a LanguageConfig instance"
            )

    def test_hindi_config(self) -> None:
        hi = LANGUAGES["hi"]
        assert hi.code == "hi"
        assert hi.name_english == "Hindi"
        assert hi.script == "Devanagari"
        assert hi.gcp_tts_code == "hi-IN"
        assert hi.gcp_stt_code == "hi-IN"
        assert hi.is_high_priority is True

    def test_english_config(self) -> None:
        en = LANGUAGES["en"]
        assert en.code == "en"
        assert en.name_english == "English"
        assert en.script == "Latin"
        assert en.is_high_priority is True

    def test_sanskrit_config(self) -> None:
        sa = LANGUAGES["sa"]
        assert sa.code == "sa"
        assert sa.name_english == "Sanskrit"
        assert sa.is_high_priority is False
        assert sa.gcp_tts_code is None, "Sanskrit should not have GCP TTS support"

    def test_all_have_required_fields(self) -> None:
        for code, config in LANGUAGES.items():
            assert config.code, f"Language '{code}' should have a code"
            assert config.name_english, f"Language '{code}' should have name_english"
            assert config.name_native, f"Language '{code}' should have name_native"
            assert config.script, f"Language '{code}' should have script"
            assert config.gcp_translation_code, f"Language '{code}' should have gcp_translation_code"
            assert config.population_millions >= 0, f"Language '{code}' should have non-negative population"


# -----------------------------------------------------------------------
# get_language tests
# -----------------------------------------------------------------------


class TestGetLanguage:
    def test_get_hindi(self) -> None:
        config = get_language("hi")
        assert config is not None, "get_language('hi') should return Hindi config"
        assert config.name_english == "Hindi"
        assert config.code == "hi"

    def test_get_english(self) -> None:
        config = get_language("en")
        assert config is not None, "get_language('en') should return English config"
        assert config.name_english == "English"

    def test_get_bengali(self) -> None:
        config = get_language("bn")
        assert config is not None, "get_language('bn') should return Bengali config"
        assert config.name_english == "Bengali"

    def test_get_nonexistent_returns_none(self) -> None:
        config = get_language("xx")
        assert config is None, "get_language for unknown code should return None"

    def test_get_via_alias_hin(self) -> None:
        config = get_language("hin")
        assert config is not None, "get_language('hin') should resolve alias to Hindi"
        assert config.code == "hi"

    def test_get_via_alias_ben(self) -> None:
        config = get_language("ben")
        assert config is not None, "get_language('ben') should resolve alias to Bengali"
        assert config.code == "bn"

    def test_get_via_alias_tam(self) -> None:
        config = get_language("tam")
        assert config is not None, "get_language('tam') should resolve alias to Tamil"
        assert config.code == "ta"

    def test_get_via_region_tagged_hi_IN(self) -> None:
        config = get_language("hi-IN")
        assert config is not None, "get_language('hi-IN') should resolve to Hindi"
        assert config.code == "hi"

    def test_get_via_region_tagged_en_IN(self) -> None:
        config = get_language("en-IN")
        assert config is not None, "get_language('en-IN') should resolve to English"
        assert config.code == "en"


# -----------------------------------------------------------------------
# get_high_priority_languages tests
# -----------------------------------------------------------------------


class TestGetHighPriorityLanguages:
    def test_returns_10_languages(self) -> None:
        hp = get_high_priority_languages()
        assert len(hp) == 10, (
            f"get_high_priority_languages should return 10, got {len(hp)}"
        )

    def test_all_are_high_priority(self) -> None:
        hp = get_high_priority_languages()
        for lang in hp:
            assert lang.is_high_priority is True, (
                f"Language '{lang.code}' should have is_high_priority=True"
            )

    def test_sorted_by_population_descending(self) -> None:
        hp = get_high_priority_languages()
        populations = [lang.population_millions for lang in hp]
        assert populations == sorted(populations, reverse=True), (
            "high priority languages should be sorted by population (descending)"
        )

    def test_hindi_is_first(self) -> None:
        hp = get_high_priority_languages()
        assert hp[0].code == "hi", "Hindi should be the first high-priority language"

    def test_includes_english(self) -> None:
        hp = get_high_priority_languages()
        codes = {lang.code for lang in hp}
        assert "en" in codes, "English should be in the high-priority languages"


# -----------------------------------------------------------------------
# get_gcp_tts_languages tests
# -----------------------------------------------------------------------


class TestGetGcpTtsLanguages:
    def test_returns_correct_count(self) -> None:
        tts = get_gcp_tts_languages()
        # Count languages with non-None gcp_tts_code manually.
        expected = sum(1 for lang in LANGUAGES.values() if lang.gcp_tts_code is not None)
        assert len(tts) == expected, (
            f"get_gcp_tts_languages should return {expected} languages, got {len(tts)}"
        )

    def test_all_have_tts_code(self) -> None:
        tts = get_gcp_tts_languages()
        for lang in tts:
            assert lang.gcp_tts_code is not None, (
                f"Language '{lang.code}' in TTS list should have a gcp_tts_code"
            )

    def test_hindi_has_tts(self) -> None:
        tts = get_gcp_tts_languages()
        codes = {lang.code for lang in tts}
        assert "hi" in codes, "Hindi should have GCP TTS support"

    def test_english_has_tts(self) -> None:
        tts = get_gcp_tts_languages()
        codes = {lang.code for lang in tts}
        assert "en" in codes, "English should have GCP TTS support"

    def test_sanskrit_not_in_tts(self) -> None:
        tts = get_gcp_tts_languages()
        codes = {lang.code for lang in tts}
        assert "sa" not in codes, "Sanskrit should NOT have GCP TTS support"

    def test_sorted_by_population_descending(self) -> None:
        tts = get_gcp_tts_languages()
        populations = [lang.population_millions for lang in tts]
        assert populations == sorted(populations, reverse=True), (
            "TTS languages should be sorted by population (descending)"
        )


# -----------------------------------------------------------------------
# get_supported_languages tests
# -----------------------------------------------------------------------


class TestGetSupportedLanguages:
    def test_returns_all_23(self) -> None:
        all_langs = get_supported_languages()
        assert len(all_langs) == 23, (
            f"get_supported_languages should return 23 languages, got {len(all_langs)}"
        )

    def test_sorted_by_population_descending(self) -> None:
        all_langs = get_supported_languages()
        populations = [lang.population_millions for lang in all_langs]
        assert populations == sorted(populations, reverse=True)


# -----------------------------------------------------------------------
# LANGUAGE_CODE_MAP alias tests
# -----------------------------------------------------------------------


class TestLanguageCodeMap:
    def test_alias_hin_maps_to_hi(self) -> None:
        assert LANGUAGE_CODE_MAP["hin"] == "hi"

    def test_alias_ben_maps_to_bn(self) -> None:
        assert LANGUAGE_CODE_MAP["ben"] == "bn"

    def test_alias_eng_maps_to_en(self) -> None:
        assert LANGUAGE_CODE_MAP["eng"] == "en"

    def test_alias_hi_IN_maps_to_hi(self) -> None:
        assert LANGUAGE_CODE_MAP["hi-IN"] == "hi"

    def test_alias_bn_IN_maps_to_bn(self) -> None:
        assert LANGUAGE_CODE_MAP["bn-IN"] == "bn"

    def test_alias_gom_maps_to_kok(self) -> None:
        assert LANGUAGE_CODE_MAP["gom"] == "kok"

    def test_alias_mni_Mtei_maps_to_mni(self) -> None:
        assert LANGUAGE_CODE_MAP["mni-Mtei"] == "mni"

    def test_all_aliases_resolve_to_valid_languages(self) -> None:
        for alias, canonical in LANGUAGE_CODE_MAP.items():
            assert canonical in LANGUAGES, (
                f"Alias '{alias}' -> '{canonical}' does not map to a valid language"
            )
