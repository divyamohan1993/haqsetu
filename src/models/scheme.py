from __future__ import annotations

from datetime import datetime
from enum import StrEnum

import orjson
from pydantic import BaseModel, Field


def _orjson_dumps(v: object, *, default: object = None) -> str:
    return orjson.dumps(v, default=default).decode()


class SchemeCategory(StrEnum):
    __slots__ = ()

    AGRICULTURE = "agriculture"
    HEALTH = "health"
    EDUCATION = "education"
    HOUSING = "housing"
    EMPLOYMENT = "employment"
    SOCIAL_SECURITY = "social_security"
    FINANCIAL_INCLUSION = "financial_inclusion"
    WOMEN_CHILD = "women_child"
    TRIBAL = "tribal"
    DISABILITY = "disability"
    SENIOR_CITIZEN = "senior_citizen"
    SKILL_DEVELOPMENT = "skill_development"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"


class EligibilityCriteria(BaseModel):
    min_age: int | None = None
    max_age: int | None = None
    gender: str | None = None
    income_limit: float | None = None
    category: str | None = None  # SC/ST/OBC/General
    occupation: str | None = None
    state: str | None = None
    is_bpl: bool | None = None
    land_holding_acres: float | None = None
    custom_criteria: list[str] = Field(default_factory=list)


class SchemeDocument(BaseModel):
    model_config = {"populate_by_name": True}

    scheme_id: str
    name: str
    name_translations: dict[str, str] = Field(default_factory=dict)
    description: str
    description_translations: dict[str, str] = Field(default_factory=dict)
    category: SchemeCategory
    ministry: str
    state: str | None = None  # None for central schemes
    eligibility: EligibilityCriteria
    benefits: str
    application_process: str
    documents_required: list[str]
    helpline: str | None = None
    website: str | None = None
    deadline: str | None = None
    last_updated: datetime
    popularity_score: float = 0.0
    embedding: list[float] | None = None  # for vector search
