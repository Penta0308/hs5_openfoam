#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Iterable


PHYSICAL_GROUPS = frozenset({"patch_inlet", "patch_heatsource", "patch_outlet", "patch_blade", "wall"})
RECOGNIZED_GROUPS = PHYSICAL_GROUPS | {"mrf", "aluminum"}
POINT_VALUES = re.compile(r"^\s*\(\s*([^\s,()]+)[\s,]+([^\s,()]+)[\s,]+([^\s,()]+)\s*\)\s*$")
RAY = (1.0, 0.3713906763541037, 0.15915494309189535)
EPSILON = 1e-9


class LocationValidationError(ValueError):
    pass


def validate_location_text(
    source_text: str,
    point_m: tuple[float, float, float],
    *,
    excluded_closed_surface_texts: Iterable[str] = (),
) -> None:
    triangles = _parse_triangles(source_text)
    point_mm = (point_m[0] * 1000.0, point_m[1] * 1000.0, point_m[2] * 1000.0)
    physical = [triangle for group in PHYSICAL_GROUPS for triangle in triangles[group]]
    if not physical:
        raise LocationValidationError("normalized OBJ has no physical boundary faces")
    if not _is_inside(point_mm, physical):
        raise LocationValidationError("locationInMesh point is outside the physical air boundary")
    if _is_inside(point_mm, triangles["aluminum"]):
        raise LocationValidationError("locationInMesh point is inside aluminum")
    for source in excluded_closed_surface_texts:
        if _is_inside(point_mm, _parse_closed_surface_triangles(source)):
            raise LocationValidationError("locationInMesh point is inside an excluded closed surface")


def validate_location_file(
    source: Path | str,
    point_m: tuple[float, float, float],
    *,
    excluded_closed_surfaces: Iterable[Path | str] = (),
) -> None:
    validate_location_text(
        Path(source).read_text(encoding="utf-8"),
        point_m,
        excluded_closed_surface_texts=(Path(path).read_text(encoding="utf-8") for path in excluded_closed_surfaces),
    )


def parse_point(value: str) -> tuple[float, float, float]:
    match = POINT_VALUES.fullmatch(value)
    if match is None:
        raise LocationValidationError("point must use OpenFOAM syntax: '(x y z)'")
    try:
        return (float(match.group(1)), float(match.group(2)), float(match.group(3)))
    except ValueError as error:
        raise LocationValidationError("point coordinates must be numeric") from error


def _parse_triangles(source_text: str) -> dict[str, list[tuple[tuple[float, float, float], ...]]]:
    vertices: list[tuple[float, float, float]] = []
    result: dict[str, list[tuple[tuple[float, float, float], ...]]] = {group: [] for group in RECOGNIZED_GROUPS}
    current_group: str | None = None
    for line_number, raw_line in enumerate(source_text.splitlines(), start=1):
        record, value = _record(raw_line)
        if record == "v":
            parts = value.split()
            if len(parts) < 3:
                raise LocationValidationError(f"line {line_number}: malformed vertex")
            try:
                vertices.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError as error:
                raise LocationValidationError(f"line {line_number}: malformed vertex") from error
        elif record == "g":
            names = value.split()
            if len(names) != 1 or names[0] not in RECOGNIZED_GROUPS:
                raise LocationValidationError(f"line {line_number}: expected one normalized group name")
            current_group = names[0]
        elif record == "f":
            if current_group is None:
                raise LocationValidationError(f"line {line_number}: face appears before a group")
            indices = _face_indices(value, len(vertices), line_number)
            points = [vertices[index - 1] for index in indices]
            result[current_group].extend((points[0], points[index], points[index + 1]) for index in range(1, len(points) - 1))
    return result


def _parse_closed_surface_triangles(source_text: str) -> list[tuple[tuple[float, float, float], ...]]:
    vertices: list[tuple[float, float, float]] = []
    result: list[tuple[tuple[float, float, float], ...]] = []
    for line_number, raw_line in enumerate(source_text.splitlines(), start=1):
        record, value = _record(raw_line)
        if record == "v":
            parts = value.split()
            if len(parts) < 3:
                raise LocationValidationError(f"line {line_number}: malformed vertex")
            try:
                vertices.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError as error:
                raise LocationValidationError(f"line {line_number}: malformed vertex") from error
        elif record == "f":
            indices = _face_indices(value, len(vertices), line_number)
            points = [vertices[index - 1] for index in indices]
            result.extend((points[0], points[index], points[index + 1]) for index in range(1, len(points) - 1))
    if not result:
        raise LocationValidationError("excluded closed surface has no faces")
    return result


def _record(line: str) -> tuple[str, str]:
    body = line.strip()
    if not body or body.startswith("#"):
        return "", ""
    parts = body.split(None, 1)
    return parts[0], parts[1] if len(parts) == 2 else ""


def _face_indices(value: str, vertex_count: int, line_number: int) -> list[int]:
    tokens = value.split()
    if len(tokens) < 3:
        raise LocationValidationError(f"line {line_number}: face must have at least three vertices")
    result: list[int] = []
    for token in tokens:
        try:
            index = int(token.split("/", 1)[0])
        except ValueError as error:
            raise LocationValidationError(f"line {line_number}: invalid face index") from error
        index = index if index > 0 else vertex_count + index + 1
        if not 1 <= index <= vertex_count:
            raise LocationValidationError(f"line {line_number}: face index is out of range")
        result.append(index)
    return result


def _is_inside(point: tuple[float, float, float], triangles: list[tuple[tuple[float, float, float], ...]]) -> bool:
    if not triangles:
        return False
    distances = sorted(
        distance for triangle in triangles if (distance := _ray_triangle_distance(point, triangle)) is not None
    )
    crossings = 0
    previous: float | None = None
    for distance in distances:
        if previous is None or not math.isclose(distance, previous, rel_tol=0.0, abs_tol=EPSILON):
            crossings += 1
            previous = distance
    return crossings % 2 == 1


def _ray_triangle_distance(point: tuple[float, float, float], triangle: tuple[tuple[float, float, float], ...]) -> float | None:
    first, second, third = triangle
    edge_one = _subtract(second, first)
    edge_two = _subtract(third, first)
    determinant = _dot(edge_one, _cross(RAY, edge_two))
    if abs(determinant) < EPSILON:
        return None
    inverse = 1.0 / determinant
    offset = _subtract(point, first)
    u = inverse * _dot(offset, _cross(RAY, edge_two))
    if not EPSILON < u < 1.0 - EPSILON:
        return None
    v = inverse * _dot(RAY, _cross(offset, edge_one))
    if v <= EPSILON or u + v >= 1.0 - EPSILON:
        return None
    distance = inverse * _dot(edge_two, _cross(offset, edge_one))
    return distance if distance > EPSILON else None


def _subtract(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[1] * right[2] - left[2] * right[1], left[2] * right[0] - left[0] * right[2], left[0] * right[1] - left[1] * right[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--obj", required=True, type=Path)
    parser.add_argument("--point", required=True)
    parser.add_argument("--exclude-closed-surface", action="append", default=[], type=Path)
    args = parser.parse_args()
    validate_location_file(
        args.obj,
        parse_point(args.point),
        excluded_closed_surfaces=args.exclude_closed_surface,
    )


if __name__ == "__main__":
    main()
