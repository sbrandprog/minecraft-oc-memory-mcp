from __future__ import annotations

import re

# Stable item identity comes from NEI. Memory stores it verbatim and never
# derives it from runtime numeric ids (265:0), metadata or NBT.
UID_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
RAW_RE = re.compile(r"^\d+(:\d+)?$")
MAX_UID_LEN = 128


def validate_item_uid(value: str) -> str:
    """Validate the shape of an NEI-provided stable item uid.

    Empty string is allowed: it means "not an item" or "NEI has not resolved it
    yet". A raw numeric id is never allowed.
    """
    if value is None:
        return ""
    value = value.strip()
    if value == "":
        return ""
    if len(value) > MAX_UID_LEN:
        raise ValueError(f"item_uid is longer than {MAX_UID_LEN} characters")
    if RAW_RE.match(value):
        raise ValueError(
            f"raw runtime item id is not allowed: {value!r}; use a stable uid from NEI"
        )
    if not UID_RE.match(value):
        raise ValueError(
            f"invalid item_uid: {value!r}; expected 'scheme:name', e.g. 'ore:ingotIron'"
        )
    return value


def validate_world_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("world_id must not be empty")
    if len(value) > 128:
        raise ValueError("world_id is too long")
    return value


def validate_profile_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("profile_id must not be empty")
    if len(value) > 128:
        raise ValueError("profile_id is too long")
    return value


def validate_robot_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("robot_id must not be empty")
    if len(value) > 128:
        raise ValueError("robot_id is too long")
    return value
