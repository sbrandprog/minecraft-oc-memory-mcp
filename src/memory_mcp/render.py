from __future__ import annotations

import math

KIND_CHAR = {
    "chest": "C",
    "ore": "O",
    "machine": "M",
    "portal": "P",
    "hazard": "!",
    "base": "B",
    "waypoint": "W",
    "other": "?",
}

LEGEND = "B base, C chest, O ore, M machine, P portal, ! hazard, W waypoint, ? other"


def render_ascii(
    *,
    world_id: str,
    dim: int,
    center: tuple[int, int, int],
    radius: int,
    pois: list[dict],
) -> str:
    cx, cy, cz = center
    width, height = 41, 21
    step_x = max(1, math.ceil((2 * radius) / (width - 1)))
    step_z = max(1, math.ceil((2 * radius) / (height - 1)))

    x0, z0 = cx - radius, cz - radius
    grid = [["." for _ in range(width)] for _ in range(height)]

    for poi in pois:
        col = round((poi["x"] - x0) / step_x)
        row = round((poi["z"] - z0) / step_z)
        if 0 <= row < height and 0 <= col < width:
            char = KIND_CHAR.get(poi["kind"], "?")
            if grid[row][col] == ".":
                grid[row][col] = char
            else:
                grid[row][col] = "*"

    lines = [
        f"world={world_id} dim={dim} center=({cx},{cy},{cz}) r={radius}",
        (
            f"x={x0}..{x0 + (width - 1) * step_x} step={step_x}  "
            f"z={z0}..{z0 + (height - 1) * step_z} step={step_z}"
        ),
    ]
    for row_index, grid_row in enumerate(grid):
        z = z0 + row_index * step_z
        lines.append(f"z={z:>6}  " + " ".join(grid_row))
    lines.append(f"legend: {LEGEND}")
    lines.append(f"pois: {len(pois)}")
    for poi in pois:
        dist = poi.get("dist")
        dist_part = f" d={dist:.0f}" if dist is not None else ""
        uid_part = f" uid={poi['item_uid']}" if poi.get("item_uid") else ""
        lines.append(
            f"  {poi['id']} {KIND_CHAR.get(poi['kind'], '?')} {poi['kind']} "
            f"{poi['label']!r} ({poi['x']},{poi['y']},{poi['z']}){dist_part}{uid_part}"
        )
    return "\n".join(lines)
