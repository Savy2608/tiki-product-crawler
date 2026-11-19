"""Schemas for every LLM output.

Each output has two definitions:
- a JSON schema sent to the API (structured outputs), kept to the keywords the
  API supports: types, enums, required fields, no extra properties;
- a pydantic model that validates the answer again on our side, including the
  limits the JSON schema does not carry (confidence range, at most 5 attributes).
An answer is used only when the pydantic model accepts it.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from enrichment.config import CATEGORIES

LANGUAGES = ["vi", "en", "mixed", "other"]
SEVERITIES = ["low", "medium", "high"]
LLM_FLAGS = ["name_description_mismatch", "counterfeit_or_brand_risk", "spam_or_low_quality"]

Category = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
Language = Literal[tuple(LANGUAGES)]  # type: ignore[valid-type]
Severity = Literal[tuple(SEVERITIES)]  # type: ignore[valid-type]
FlagName = Literal[tuple(LLM_FLAGS)]  # type: ignore[valid-type]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attribute(_Strict):
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)


class Enrichment(_Strict):
    category: Category
    brand: Optional[str]
    key_attributes: List[Attribute] = Field(max_length=5)
    language: Language
    confidence: float = Field(ge=0, le=1)

    @field_validator("brand")
    @classmethod
    def empty_brand_is_null(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class Flag(_Strict):
    flag: FlagName
    severity: Severity
    evidence: str = Field(min_length=1)


class FlagResult(_Strict):
    flags: List[Flag]

    @field_validator("flags")
    @classmethod
    def one_entry_per_flag(cls, flags):
        names = [f.flag for f in flags]
        if len(names) != len(set(names)):
            raise ValueError("each flag type may appear only once")
        return flags


ENRICH_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "brand": {"type": ["string", "null"]},
        "key_attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                "required": ["name", "value"],
                "additionalProperties": False,
            },
        },
        "language": {"type": "string", "enum": LANGUAGES},
        "confidence": {"type": "number"},
    },
    "required": ["category", "brand", "key_attributes", "language", "confidence"],
    "additionalProperties": False,
}

FLAG_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag": {"type": "string", "enum": LLM_FLAGS},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "evidence": {"type": "string"},
                },
                "required": ["flag", "severity", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["flags"],
    "additionalProperties": False,
}


def validate(model_cls, text):
    """(dict, None) when `text` is valid JSON for model_cls, else (None, short error)."""
    try:
        return model_cls.model_validate_json(text).model_dump(), None
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}"
                             for e in exc.errors()[:5])
        return None, f"schema validation failed: {problems}"
