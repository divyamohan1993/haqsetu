from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import orjson
from pydantic import BaseModel, Field

from src.models.enums import ChannelType, ContentType, DeviceType, LanguageCode, NetworkQuality


def _orjson_dumps(v: object, *, default: object = None) -> str:
    return orjson.dumps(v, default=default).decode()


class RequestMetadata(BaseModel):
    model_config = {"frozen": False}

    phone_number: str
    device_type: DeviceType | None = None
    approximate_state: str | None = None
    telecom_circle: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    network_quality: NetworkQuality = NetworkQuality.FOUR_G
    csc_id: str | None = None
    vle_id: str | None = None


class HaqSetuRequest(BaseModel):
    model_config = {
        "frozen": False,
        "json_encoders": {bytes: lambda v: v.hex()},
    }

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    channel_type: ChannelType
    content: str | bytes
    content_type: ContentType
    language: LanguageCode | None = None
    metadata: RequestMetadata
