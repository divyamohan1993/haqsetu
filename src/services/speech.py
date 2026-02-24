"""Google Cloud Speech services for HaqSetu.

Provides async STT (Speech-to-Text) and TTS (Text-to-Speech) wrappers
around the official GCP client libraries.  All audio flowing through
these services is ephemeral -- nothing is persisted to GCS or logged
beyond operational metrics.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final

import structlog
from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech
from google.cloud.texttospeech_v1 import TextToSpeechAsyncClient
from google.cloud.texttospeech_v1.types import (
    AudioConfig,
    AudioEncoding,
    SsmlVoiceGender,
    SynthesisInput,
    SynthesizeSpeechRequest,
    VoiceSelectionParams,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Voice / language mappings
# ---------------------------------------------------------------------------

# Mapping from internal ISO 639-1 codes to (BCP-47, TTS voice name).
# Neural2 voices are preferred for natural-sounding output.
VOICE_MAP: Final[dict[str, tuple[str, str]]] = {
    "hi": ("hi-IN", "hi-IN-Neural2-A"),
    "bn": ("bn-IN", "bn-IN-Neural2-A"),
    "te": ("te-IN", "te-IN-Neural2-A"),
    "ta": ("ta-IN", "ta-IN-Neural2-A"),
    "mr": ("mr-IN", "mr-IN-Neural2-A"),
    "gu": ("gu-IN", "gu-IN-Neural2-A"),
    "kn": ("kn-IN", "kn-IN-Neural2-A"),
    "ml": ("ml-IN", "ml-IN-Neural2-A"),
    "pa": ("pa-IN", "pa-IN-Neural2-A"),
    "en": ("en-IN", "en-IN-Neural2-A"),
}

# BCP-47 codes supported by the GCP Chirp model for Indian languages.
_CHIRP_SUPPORTED_STT: Final[frozenset[str]] = frozenset(
    {
        "hi-IN",
        "bn-IN",
        "te-IN",
        "mr-IN",
        "ta-IN",
        "ur-PK",
        "gu-IN",
        "kn-IN",
        "ml-IN",
        "pa-IN",
        "en-IN",
    }
)

# Internal code -> BCP-47 for STT.
_STT_LANGUAGE_MAP: Final[dict[str, str]] = {
    "hi": "hi-IN",
    "bn": "bn-IN",
    "te": "te-IN",
    "mr": "mr-IN",
    "ta": "ta-IN",
    "ur": "ur-PK",
    "gu": "gu-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "pa": "pa-IN",
    "en": "en-IN",
    # Additional scheduled languages without direct Chirp support --
    # fall back to Hindi (largest overlap in acoustic space).
    "or": "hi-IN",
    "as": "hi-IN",
    "mai": "hi-IN",
    "sat": "hi-IN",
    "ks": "hi-IN",
    "ne": "hi-IN",
    "sd": "hi-IN",
    "kok": "hi-IN",
    "doi": "hi-IN",
    "mni": "hi-IN",
    "brx": "hi-IN",
    "sa": "hi-IN",
}

# Encoding name -> cloud_speech enum value.
_ENCODING_MAP: Final[dict[str, cloud_speech.ExplicitDecodingConfig.AudioEncoding]] = {
    "LINEAR16": cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
    "MULAW": cloud_speech.ExplicitDecodingConfig.AudioEncoding.MULAW,
    "ALAW": cloud_speech.ExplicitDecodingConfig.AudioEncoding.ALAW,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ASRResult:
    """Result of a speech-to-text recognition request."""

    text: str
    confidence: float
    language: str
    processing_time_ms: float
    is_partial: bool = False
    provider: str = field(default="google")


# ---------------------------------------------------------------------------
# SpeechToTextService
# ---------------------------------------------------------------------------


class SpeechToTextService:
    """Async wrapper around Google Cloud Speech-to-Text v2 (Chirp).

    Uses the ``asia-south1`` regional endpoint by default for lowest
    latency when serving Indian users.
    """

    def __init__(self, project_id: str, region: str = "asia-south1") -> None:
        self._project_id = project_id
        self._region = region
        self._client: SpeechAsyncClient | None = None

    # -- client lifecycle ---------------------------------------------------

    async def _get_client(self) -> SpeechAsyncClient:
        if self._client is None:
            self._client = SpeechAsyncClient(
                client_options={"api_endpoint": f"{self._region}-speech.googleapis.com"},
            )
        return self._client

    # -- helpers ------------------------------------------------------------

    @property
    def _recognizer_name(self) -> str:
        """Full resource name for the default recognizer."""
        return f"projects/{self._project_id}/locations/{self._region}/recognizers/_"

    @staticmethod
    def _map_language_to_stt(lang_code: str) -> str:
        """Map an internal language code to a GCP STT BCP-47 code.

        Falls back to Hindi for unsupported languages, as Hindi has the
        broadest acoustic coverage across North-Indian language families.
        """
        return _STT_LANGUAGE_MAP.get(lang_code, "hi-IN")

    # -- public API ---------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def transcribe(
        self,
        audio_data: bytes,
        language_code: str,
        sample_rate: int = 16000,
        encoding: str = "LINEAR16",
    ) -> ASRResult:
        """Transcribe a complete audio clip and return the best result.

        Parameters
        ----------
        audio_data:
            Raw audio bytes.
        language_code:
            Internal language code (e.g. ``"hi"``, ``"bn"``).
        sample_rate:
            Sample rate in Hz.  Defaults to 16 kHz (telephony standard).
        encoding:
            Audio encoding.  One of ``LINEAR16``, ``MULAW``, ``ALAW``.
        """
        start = time.perf_counter()
        client = await self._get_client()

        bcp47 = self._map_language_to_stt(language_code)
        audio_encoding = _ENCODING_MAP.get(
            encoding,
            cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
        )

        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=audio_encoding,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[bcp47],
            model="chirp",
        )

        request = cloud_speech.RecognizeRequest(
            recognizer=self._recognizer_name,
            config=config,
            content=audio_data,
        )

        logger.debug(
            "stt_request",
            language=bcp47,
            audio_bytes=len(audio_data),
            sample_rate=sample_rate,
        )

        response = await client.recognize(request=request)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Extract best alternative from the first result.
        if response.results and response.results[0].alternatives:
            best = response.results[0].alternatives[0]
            result = ASRResult(
                text=best.transcript.strip(),
                confidence=best.confidence,
                language=bcp47,
                processing_time_ms=round(elapsed_ms, 2),
            )
        else:
            result = ASRResult(
                text="",
                confidence=0.0,
                language=bcp47,
                processing_time_ms=round(elapsed_ms, 2),
            )

        logger.info(
            "stt_result",
            text_length=len(result.text),
            confidence=result.confidence,
            language=result.language,
            processing_time_ms=result.processing_time_ms,
        )
        return result

    async def transcribe_streaming(
        self,
        audio_stream: AsyncIterator[bytes],
        language_code: str,
        sample_rate: int = 16000,
        encoding: str = "LINEAR16",
    ) -> AsyncIterator[ASRResult]:
        """Stream audio chunks and yield partial / final transcription results.

        This is intended for real-time voice pipelines (e.g. IVR, WebSocket)
        where the caller sends audio incrementally.
        """
        client = await self._get_client()
        bcp47 = self._map_language_to_stt(language_code)
        audio_encoding = _ENCODING_MAP.get(
            encoding,
            cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
        )

        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=cloud_speech.RecognitionConfig(
                explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                    encoding=audio_encoding,
                    sample_rate_hertz=sample_rate,
                    audio_channel_count=1,
                ),
                language_codes=[bcp47],
                model="chirp",
            ),
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
            ),
        )

        async def _request_generator() -> AsyncIterator[cloud_speech.StreamingRecognizeRequest]:
            # First message carries the config.
            yield cloud_speech.StreamingRecognizeRequest(
                recognizer=self._recognizer_name,
                streaming_config=streaming_config,
            )
            # Subsequent messages carry audio.
            async for chunk in audio_stream:
                yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

        logger.debug("stt_streaming_start", language=bcp47)
        start = time.perf_counter()

        stream = await client.streaming_recognize(requests=_request_generator())

        async for response in stream:
            for result in response.results:
                if not result.alternatives:
                    continue
                best = result.alternatives[0]
                elapsed_ms = (time.perf_counter() - start) * 1000
                is_final = result.is_final

                yield ASRResult(
                    text=best.transcript.strip(),
                    confidence=best.confidence,
                    language=bcp47,
                    processing_time_ms=round(elapsed_ms, 2),
                    is_partial=not is_final,
                )

                if is_final:
                    logger.info(
                        "stt_streaming_final",
                        text_length=len(best.transcript),
                        confidence=best.confidence,
                        processing_time_ms=round(elapsed_ms, 2),
                    )

    async def close(self) -> None:
        """Release underlying gRPC resources."""
        if self._client is not None:
            transport = self._client.transport
            if hasattr(transport, "close"):
                await transport.close()  # type: ignore[misc]
            self._client = None


# ---------------------------------------------------------------------------
# TextToSpeechService
# ---------------------------------------------------------------------------

# Fallback chains for languages without Neural2 voices.
# Maps an unsupported language code to the closest supported code.
_TTS_FALLBACK: Final[dict[str, str]] = {
    "ur": "hi",   # Urdu -> Hindi (Hindustani continuum)
    "or": "hi",   # Odia -> Hindi
    "as": "bn",   # Assamese -> Bengali (close phonology)
    "mai": "hi",  # Maithili -> Hindi
    "sat": "hi",  # Santali -> Hindi
    "ks": "hi",   # Kashmiri -> Hindi
    "ne": "hi",   # Nepali -> Hindi
    "sd": "hi",   # Sindhi -> Hindi
    "kok": "mr",  # Konkani -> Marathi
    "doi": "hi",  # Dogri -> Hindi
    "mni": "bn",  # Manipuri -> Bengali
    "brx": "hi",  # Bodo -> Hindi
    "sa": "hi",   # Sanskrit -> Hindi
}


class TextToSpeechService:
    """Async wrapper around Google Cloud Text-to-Speech v1.

    Produces MP3 audio using Neural2 voices for all major Indian
    languages.  For languages without a dedicated Neural2 voice the
    service falls back to the closest supported language.
    """

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._client: TextToSpeechAsyncClient | None = None

    async def _get_client(self) -> TextToSpeechAsyncClient:
        if self._client is None:
            self._client = TextToSpeechAsyncClient()
        return self._client

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _map_language_to_tts(lang_code: str) -> tuple[str, str]:
        """Return ``(bcp47_code, voice_name)`` for *lang_code*.

        If the language lacks a Neural2 voice the method transparently
        falls back via ``_TTS_FALLBACK``.
        """
        resolved = lang_code
        if resolved not in VOICE_MAP:
            resolved = _TTS_FALLBACK.get(resolved, "hi")
        bcp47, voice_name = VOICE_MAP.get(resolved, ("hi-IN", "hi-IN-Neural2-A"))
        return bcp47, voice_name

    # -- public API ---------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def synthesize(
        self,
        text: str,
        language_code: str,
        speaking_rate: float = 1.0,
    ) -> bytes:
        """Synthesize *text* to MP3 audio bytes.

        Parameters
        ----------
        text:
            Plain text to speak.
        language_code:
            Internal language code (e.g. ``"hi"``, ``"bn"``).
        speaking_rate:
            Speed multiplier.  ``1.0`` is normal, ``0.8`` is slower.
        """
        start = time.perf_counter()
        client = await self._get_client()

        bcp47, voice_name = self._map_language_to_tts(language_code)

        request = SynthesizeSpeechRequest(
            input=SynthesisInput(text=text),
            voice=VoiceSelectionParams(
                language_code=bcp47,
                name=voice_name,
                ssml_gender=SsmlVoiceGender.FEMALE,
            ),
            audio_config=AudioConfig(
                audio_encoding=AudioEncoding.MP3,
                speaking_rate=speaking_rate,
                pitch=0.0,
                effects_profile_id=["telephony-class-application"],
            ),
        )

        response = await client.synthesize_speech(request=request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "tts_result",
            language=bcp47,
            voice=voice_name,
            text_length=len(text),
            audio_bytes=len(response.audio_content),
            processing_time_ms=round(elapsed_ms, 2),
        )
        return response.audio_content

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def synthesize_ssml(
        self,
        ssml: str,
        language_code: str,
        speaking_rate: float = 1.0,
    ) -> bytes:
        """Synthesize SSML markup to MP3 audio bytes.

        SSML allows fine-grained control over pronunciation, pauses, and
        emphasis -- useful for reading out scheme names or monetary amounts.
        """
        start = time.perf_counter()
        client = await self._get_client()

        bcp47, voice_name = self._map_language_to_tts(language_code)

        request = SynthesizeSpeechRequest(
            input=SynthesisInput(ssml=ssml),
            voice=VoiceSelectionParams(
                language_code=bcp47,
                name=voice_name,
                ssml_gender=SsmlVoiceGender.FEMALE,
            ),
            audio_config=AudioConfig(
                audio_encoding=AudioEncoding.MP3,
                speaking_rate=speaking_rate,
                pitch=0.0,
                effects_profile_id=["telephony-class-application"],
            ),
        )

        response = await client.synthesize_speech(request=request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "tts_ssml_result",
            language=bcp47,
            voice=voice_name,
            ssml_length=len(ssml),
            audio_bytes=len(response.audio_content),
            processing_time_ms=round(elapsed_ms, 2),
        )
        return response.audio_content

    async def close(self) -> None:
        """Release underlying gRPC resources."""
        if self._client is not None:
            transport = self._client.transport
            if hasattr(transport, "close"):
                await transport.close()  # type: ignore[misc]
            self._client = None
