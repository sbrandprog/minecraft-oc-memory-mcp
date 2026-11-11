from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .validate import validate_item_uid

PoiKind = Literal[
    "chest",
    "ore",
    "machine",
    "portal",
    "hazard",
    "base",
    "waypoint",
    "other",
]
Scope = Literal["world", "profile", "robot"]
Source = Literal["observed", "told", "inferred"]
Outcome = Literal["success", "partial", "failed"]


class Vec3(BaseModel):
    x: int
    y: int = Field(ge=0, le=255)
    z: int


class OpenResult(BaseModel):
    session_id: str = Field(
        description="Session token. Pass it to later memory tools, or omit it."
    )
    ctx_id: str = Field(description="Alias of session_id, kept for compatibility.")
    world_id: str
    profile_id: str
    robot_id: str
    hint: str = Field(description="How to use session_id in later calls.")


class Pose(Vec3):
    dim: int = Field(
        default=0,
        description=(
            "Dimension id: 0 = overworld, -1 = nether, 1 = end. "
            "Defaults to 0; pass it explicitly outside the overworld."
        ),
    )


class PoiRecord(BaseModel):
    kind: PoiKind
    label: str = Field(min_length=1, max_length=256)
    dim: int
    x: int
    y: int = Field(ge=0, le=255)
    z: int
    radius: int = Field(default=0, ge=0, le=512)
    item_uid: str = ""
    tags: list[str] = Field(default_factory=list)
    scope: Scope = "world"
    source: Source = "observed"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ttl_s: int | None = Field(default=None, ge=0)

    @field_validator("item_uid")
    @classmethod
    def _check_item_uid(cls, value: str) -> str:
        return validate_item_uid(value)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be empty")
        return value


class PreferenceRecord(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=4000)
    scope: Scope = "profile"
    importance: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("key must not be empty")
        return value


class EpisodeRecord(BaseModel):
    goal: str = Field(
        min_length=1,
        max_length=1000,
        description="What the player asked for, in the user's terms.",
    )
    outcome: Outcome = Field(
        default="partial",
        description="How the task ended: success, partial or failed.",
    )
    obstacles: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Difficulties encountered, one short phrase each.",
    )
    lesson: str = Field(
        min_length=1,
        max_length=2000,
        description="Actionable advice for doing this better next time.",
    )
    tags: list[str] = Field(default_factory=list, max_length=32)
    scope: Scope = "world"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("goal", "lesson")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("obstacles")
    @classmethod
    def _clean_obstacles(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            value = value.strip()
            if not value:
                continue
            if len(value) > 500:
                raise ValueError("each obstacle must be at most 500 characters")
            cleaned.append(value)
        if len(cleaned) > 10:
            raise ValueError("at most 10 obstacles are allowed")
        return cleaned

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            value = value.strip()
            if not value:
                continue
            if len(value) > 128:
                raise ValueError("each tag must be at most 128 characters")
            cleaned.append(value)
        return cleaned


class PoiPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=256)
    item_uid: str | None = None
    tags: list[str] | None = None
    radius: int | None = Field(default=None, ge=0, le=512)
    source: Source | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    ttl_s: int | None = Field(default=None, ge=0)

    @field_validator("item_uid")
    @classmethod
    def _check_item_uid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_item_uid(value)
