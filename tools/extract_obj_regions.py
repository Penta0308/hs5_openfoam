#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


REGIONS = frozenset({"patch_inlet", "patch_heatsource", "patch_outlet", "patch_blade", "wall", "mrf", "aluminum"})
EXTRACTABLE_REGIONS = frozenset({"mrf", "aluminum"})


class ObjRegionExtractionError(ValueError):
    pass


def extract_region_text(source_text: str, region: str) -> str:
    if region not in EXTRACTABLE_REGIONS:
        raise ObjRegionExtractionError(f"region must be one of {sorted(EXTRACTABLE_REGIONS)}")

    vertices: list[str] = []
    faces: list[list[int]] = []
    current_group: str | None = None
    for line_number, raw_line in enumerate(source_text.splitlines(), start=1):
        record, value = _record(raw_line)
        if record == "v":
            if not value.split():
                raise ObjRegionExtractionError(f"line {line_number}: malformed vertex")
            vertices.append(raw_line)
        elif record == "g":
            names = value.split()
            if len(names) != 1:
                raise ObjRegionExtractionError(f"line {line_number}: mixed OBJ groups are not supported")
            if names[0] not in REGIONS:
                raise ObjRegionExtractionError(f"line {line_number}: unknown normalized group {names[0]!r}")
            current_group = names[0]
        elif record == "f":
            if current_group is None:
                raise ObjRegionExtractionError(f"line {line_number}: face appears before a group")
            if current_group == region:
                faces.append(_face_vertex_indices(value, len(vertices), line_number))

    if not faces:
        raise ObjRegionExtractionError(f"normalized OBJ has no faces in g {region}")

    used_indices = sorted({index for face in faces for index in face})
    reindex = {source_index: output_index for output_index, source_index in enumerate(used_indices, start=1)}
    output = [f"# Extracted from normalized OBJ: g {region}\n", f"g {region}\n"]
    output.extend(f"{vertices[index - 1]}\n" for index in used_indices)
    output.extend("f " + " ".join(str(reindex[index]) for index in face) + "\n" for face in faces)
    return "".join(output)


def extract_region_file(source: Path | str, destination: Path | str, region: str) -> None:
    destination_path = Path(destination)
    text = extract_region_text(Path(source).read_text(encoding="utf-8"), region)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(text, encoding="utf-8")


def _record(line: str) -> tuple[str, str]:
    body = line.strip()
    if not body or body.startswith("#"):
        return "", ""
    parts = body.split(None, 1)
    return parts[0], parts[1] if len(parts) == 2 else ""


def _face_vertex_indices(value: str, vertex_count: int, line_number: int) -> list[int]:
    tokens = value.split()
    if len(tokens) < 3:
        raise ObjRegionExtractionError(f"line {line_number}: face must have at least three vertices")
    indices: list[int] = []
    for token in tokens:
        try:
            index = int(token.split("/", 1)[0])
        except ValueError as error:
            raise ObjRegionExtractionError(f"line {line_number}: invalid face vertex {token!r}") from error
        if index == 0:
            raise ObjRegionExtractionError(f"line {line_number}: OBJ index 0 is invalid")
        index = index if index > 0 else vertex_count + index + 1
        if not 1 <= index <= vertex_count:
            raise ObjRegionExtractionError(f"line {line_number}: face vertex index is out of range")
        indices.append(index)
    return indices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obj", required=True, type=Path)
    parser.add_argument("--region", required=True, choices=sorted(EXTRACTABLE_REGIONS))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    extract_region_file(args.obj, args.output, args.region)


if __name__ == "__main__":
    main()
