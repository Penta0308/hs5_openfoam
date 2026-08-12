from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile


SOURCE_GROUPS = ("master", "mrf", "master_1")
REGION_NAMES = (
    "patch_inlet",
    "patch_heatsource",
    "patch_outlet",
    "wall",
    "mrf",
    "aluminum",
)
KNOWN_COLOR_ROLES = {
    "0,92,255": "patch_inlet",
    "255,64,0": "patch_heatsource",
    "0,180,80": "patch_outlet",
    "160,160,160": "wall",
    "191,191,191": "wall",
}
RGB = re.compile(r"(\d+),(\d+),(\d+)")


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizationResult:
    obj_text: str
    manifest: dict[str, object]
    warnings: tuple[str, ...]


def normalize_obj_text(
    source_text: str,
    *,
    source_obj: str = "constant/triSurface/hs5_cfd.obj",
    normalized_obj: str = "constant/triSurface/hs5_cfd.openfoam.obj",
) -> NormalizationResult:
    output: list[str] = []
    counts = {name: 0 for name in REGION_NAMES}
    unknown_counts: dict[str, int] = {}
    current_group: str | None = None
    current_material: str | None = None
    expected_group_index = 0
    run_number = 0
    emitted_run: tuple[int, str] | None = None

    for line_number, line in enumerate(source_text.splitlines(keepends=True), start=1):
        record, value = _record(line)
        if record == "mtllib":
            continue
        if record == "g":
            group = _one_token(value, line_number, "group")
            if group not in SOURCE_GROUPS:
                raise NormalizationError(f"line {line_number}: unsupported group {group!r}")
            if SOURCE_GROUPS.index(group) != expected_group_index:
                raise NormalizationError(
                    f"line {line_number}: groups must occur once in order "
                    "master -> mrf -> master_1"
                )
            current_group = group
            expected_group_index += 1
            run_number += 1
            emitted_run = None
            continue
        if record == "usemtl":
            current_material = value.strip() or None
            run_number += 1
            emitted_run = None
            continue
        if record != "f":
            output.append(line)
            continue

        if current_group is None:
            raise NormalizationError(f"line {line_number}: face appears before a valid group")
        region, unknown_rgb = _face_region(current_group, current_material, line_number)
        if unknown_rgb is not None:
            unknown_counts[unknown_rgb] = unknown_counts.get(unknown_rgb, 0) + 1
        run = (run_number, region)
        if emitted_run != run:
            output.append(f"g {region}\n")
            emitted_run = run
        output.append(line)
        counts[region] += 1

    unknown_colors = [
        {"rgb": rgb, "faceCount": unknown_counts[rgb], "mappedRole": "wall"}
        for rgb in sorted(unknown_counts)
    ]
    warnings = tuple(
        f"unknown master RGB {item['rgb']} mapped to wall ({item['faceCount']} faces)"
        for item in unknown_colors
    )
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "sourceObj": _posix_path(source_obj),
        "normalizedObj": _posix_path(normalized_obj),
        "regionFaceCounts": counts,
        "knownColorRoles": KNOWN_COLOR_ROLES,
        "unknownColors": unknown_colors,
        "warnings": list(warnings),
    }
    return NormalizationResult("".join(output), manifest, warnings)


def normalize_obj_file(
    source_path: Path | str,
    normalized_path: Path | str,
    manifest_path: Path | str,
    *,
    source_obj: str = "constant/triSurface/hs5_cfd.obj",
    normalized_obj: str = "constant/triSurface/hs5_cfd.openfoam.obj",
) -> NormalizationResult:
    source = Path(source_path)
    result = normalize_obj_text(
        source.read_text(encoding="utf-8"),
        source_obj=source_obj,
        normalized_obj=normalized_obj,
    )
    manifest_text = json.dumps(result.manifest, indent=2) + "\n"
    _replace_pair_transactionally(
        Path(normalized_path), result.obj_text.encode("utf-8"),
        Path(manifest_path), manifest_text.encode("utf-8"),
    )
    return result


def _record(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    if not body or body.lstrip().startswith("#"):
        return "", ""
    parts = body.split(None, 1)
    return parts[0], parts[1] if len(parts) == 2 else ""


def _one_token(value: str, line_number: int, noun: str) -> str:
    tokens = value.split()
    if len(tokens) != 1:
        raise NormalizationError(f"line {line_number}: {noun} must have exactly one name")
    return tokens[0]


def _face_region(group: str, material: str | None, line_number: int) -> tuple[str, str | None]:
    if group == "mrf":
        return "mrf", None
    if group == "master_1":
        return "aluminum", None
    if material is None:
        raise NormalizationError(f"line {line_number}: master face has no active RGB material")
    match = RGB.fullmatch(material)
    if match is None or any(int(component) > 255 for component in match.groups()):
        raise NormalizationError(f"line {line_number}: master material must be RGB values from 0 to 255")
    rgb = material
    return KNOWN_COLOR_ROLES.get(rgb, "wall"), None if rgb in KNOWN_COLOR_ROLES else rgb


def _posix_path(value: str) -> str:
    return value.replace("\\", "/")


def _replace_pair_transactionally(
    first_path: Path,
    first_bytes: bytes,
    second_path: Path,
    second_bytes: bytes,
) -> None:
    paths = (first_path, second_path)
    payloads = (first_bytes, second_bytes)
    originals: tuple[bytes | None, bytes | None] = (
        first_path.read_bytes() if first_path.exists() else None,
        second_path.read_bytes() if second_path.exists() else None,
    )
    staged: list[Path] = []
    try:
        for path, payload in zip(paths, payloads):
            path.parent.mkdir(parents=True, exist_ok=True)
            staged.append(_stage_bytes(path.parent, payload))
        for temporary, path in zip(tuple(staged), paths):
            os.replace(temporary, path)
            staged.remove(temporary)
    except Exception:
        _restore_pair(paths, originals)
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)


def _stage_bytes(directory: Path, payload: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".obj-region-", suffix=".tmp", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _restore_pair(paths: tuple[Path, Path], originals: tuple[bytes | None, bytes | None]) -> None:
    for path, original in zip(paths, originals):
        if original is None:
            path.unlink(missing_ok=True)
        else:
            restoration = _stage_bytes(path.parent, original)
            try:
                os.replace(restoration, path)
            finally:
                restoration.unlink(missing_ok=True)
