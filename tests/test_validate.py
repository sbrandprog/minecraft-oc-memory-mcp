from __future__ import annotations

import pytest
from pydantic import ValidationError

from memory_mcp.schemas import PoiRecord
from memory_mcp.validate import validate_item_uid


@pytest.mark.parametrize(
    "value",
    ["", "ore:ingotIron", "minecraft:iron_ingot", "gregtech:meta_ingot_iron"],
)
def test_valid_uids(value: str) -> None:
    assert validate_item_uid(value) == value


@pytest.mark.parametrize("value", ["265", "265:0", "15:3", "Iron Ingot", "ore:"])
def test_raw_or_malformed_uids_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_item_uid(value)


def test_poi_record_rejects_raw_id() -> None:
    with pytest.raises(ValidationError):
        PoiRecord(
            kind="ore",
            label="iron ore",
            dim=0,
            x=10,
            y=12,
            z=10,
            item_uid="15:0",
        )
