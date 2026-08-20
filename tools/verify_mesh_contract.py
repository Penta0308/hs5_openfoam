#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class MeshContractError(ValueError):
    pass


_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_TOKEN = re.compile(r"[{}();]|[^\s{}();]+")
_COUNTED_PROBLEMS = {
    "open edges": re.compile(r"(?:number\s+of\s+)?open\s+edges?\s*[:=]\s*(\d+)", re.IGNORECASE),
    "non-manifold edges": re.compile(r"(?:number\s+of\s+)?non[- ]manifold\s+edges?\s*[:=]\s*(\d+)", re.IGNORECASE),
    "invalid facets": re.compile(r"(?:number\s+of\s+)?(?:invalid\s+(?:facets?|triangles?)|illegal\s+(?:facets?|triangles?))\s*[:=]\s*(\d+)", re.IGNORECASE),
}


def validate_check_mesh_text(text: str) -> None:
    failed_match = re.search(r"\bfailed\s+([1-9]\d*)\s+mesh checks?\b", text, re.IGNORECASE)
    if failed_match is not None:
        has_concavity = bool(re.search(r"\bconcave\s+cells?\b", text, re.IGNORECASE))
        non_concavity_patterns = [
            r"\bnon[- ]orthogonal\s+faces?\b",
            r"\bnon[- ]manifold\s+edges?\b",
            r"\bdegenerate\s+faces?\b",
            r"\bopen\s+edges?\b",
            r"\bopen\s+surface\b",
            r"\btopology\s+(?:check\s+)?failed\b",
            r"\binvalid\s+(?:facets?|triangles?)\b",
            r"\billegal\s+(?:facets?|triangles?)\b",
            r"\bboundary\s+check\s+failed\b",
            r"\bhigh\s+skewness\b",
        ]
        has_non_concavity_failure = any(
            re.search(p, text, re.IGNORECASE) for p in non_concavity_patterns
        )
        if has_concavity and not has_non_concavity_failure:
            print(
                "warning: checkMesh reported concavity-only failures",
                file=sys.stderr,
            )
            return
        raise MeshContractError(
            f"checkMesh reported failed {failed_match.group(1)} mesh checks"
        )
    return


def validate_surface_check_text(text: str, label: str) -> None:
    for problem, pattern in _COUNTED_PROBLEMS.items():
        if any(int(match.group(1)) != 0 for match in pattern.finditer(text)):
            raise MeshContractError(f"{label}: surfaceCheck reports {problem}")

    lowered = text.lower()
    if re.search(r"\b(?:not\s+closed|open\s+surface|topology\s+(?:check\s+)?failed)\b", lowered):
        raise MeshContractError(f"{label}: surfaceCheck did not report a closed topology")
    topology_mentions = re.finditer(r"\b(?:non[- ]manifold|invalid\s+(?:facet|triangle)|illegal\s+(?:facet|triangle))", lowered)
    has_unqualified_problem = any(not re.search(r"\bno\s+$", lowered[:match.start()]) for match in topology_mentions)
    if has_unqualified_problem and not any(pattern.search(text) for pattern in _COUNTED_PROBLEMS.values()):
        raise MeshContractError(f"{label}: surfaceCheck reports invalid topology")
    if not re.search(r"\b(?:surface\s+is\s+closed|closed\s+surface)\b", lowered):
        raise MeshContractError(f"{label}: surfaceCheck does not confirm the surface is closed")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(_COMMENT.sub("", text))


def _parse_named_blocks(text: str, artifact: str) -> dict[str, list[str]]:
    tokens = _tokens(text)
    try:
        start = tokens.index("(")
    except ValueError as error:
        raise MeshContractError(f"{artifact}: missing top-level list") from error
    blocks: dict[str, list[str]] = {}
    index = start + 1
    while index < len(tokens) and tokens[index] != ")":
        name = tokens[index]
        if index + 1 >= len(tokens) or tokens[index + 1] != "{":
            raise MeshContractError(f"{artifact}: expected dictionary for {name!r}")
        depth = 1
        end = index + 2
        while end < len(tokens) and depth:
            depth += (tokens[end] == "{") - (tokens[end] == "}")
            end += 1
        if depth:
            raise MeshContractError(f"{artifact}: unclosed dictionary for {name!r}")
        if name in blocks:
            raise MeshContractError(f"{artifact}: duplicate entry {name!r}")
        blocks[name] = tokens[index + 2 : end - 1]
        index = end
    if index >= len(tokens) or tokens[index] != ")":
        raise MeshContractError(f"{artifact}: unclosed top-level list")
    return blocks


def parse_boundary_text(text: str) -> set[str]:
    blocks = _parse_named_blocks(text, "boundary")
    if not blocks:
        raise MeshContractError("boundary: no patches")
    for name, body in blocks.items():
        if "type" not in body or "nFaces" not in body or "startFace" not in body:
            raise MeshContractError(f"boundary: malformed patch {name!r}")
    return set(blocks)


def parse_cell_zones_text(text: str) -> dict[str, set[int]]:
    blocks = _parse_named_blocks(text, "cellZones")
    result: dict[str, set[int]] = {}
    for name, body in blocks.items():
        try:
            label_index = body.index("cellLabels")
            list_start = body.index("(", label_index)
            list_end = body.index(")", list_start)
        except ValueError as error:
            raise MeshContractError(f"cellZones: {name!r} has no cellLabels list") from error
        if list_start == 0 or not body[list_start - 1].isdigit():
            raise MeshContractError(f"cellZones: {name!r} has malformed cellLabels count")
        expected = int(body[list_start - 1])
        try:
            labels = [int(token) for token in body[list_start + 1 : list_end]]
        except ValueError as error:
            raise MeshContractError(f"cellZones: {name!r} has a non-integer cell label") from error
        if len(labels) != expected or len(set(labels)) != len(labels) or any(label < 0 for label in labels):
            raise MeshContractError(f"cellZones: {name!r} has malformed cellLabels")
        result[name] = set(labels)
    return result


def validate_pre_split(boundary_text: str, cell_zones_text: str) -> None:
    parse_boundary_text(boundary_text)
    zones = parse_cell_zones_text(cell_zones_text)
    missing = {"mrf", "aluminum"} - set(zones)
    if missing:
        raise MeshContractError(f"cellZones: missing required zone(s): {', '.join(sorted(missing))}")
    for name in ("mrf", "aluminum"):
        if not zones[name]:
            raise MeshContractError(f"cellZones: {name!r} must not be empty")
    if zones["mrf"] & zones["aluminum"]:
        raise MeshContractError("cellZones: mrf and aluminum overlap")


def validate_pre_split_files(boundary: Path | str, cell_zones: Path | str) -> None:
    validate_pre_split(Path(boundary).read_text(encoding="utf-8"), Path(cell_zones).read_text(encoding="utf-8"))


def validate_post_split(fluid_boundary_text: str, fluid_cell_zones_text: str, aluminum_boundary_text: str) -> None:
    fluid_patches = parse_boundary_text(fluid_boundary_text)
    aluminum_patches = parse_boundary_text(aluminum_boundary_text)
    if "patch_heatsource" not in aluminum_patches:
        raise MeshContractError("aluminum boundary: missing required patch_heatsource patch")
    if "patch_heatsource" in fluid_patches:
        raise MeshContractError("fluid boundary: patch_heatsource must be owned by aluminum")
    if aluminum_patches == {"patch_heatsource"}:
        raise MeshContractError("aluminum boundary: fluid/aluminum interface must be separate from patch_heatsource")

    zones = parse_cell_zones_text(fluid_cell_zones_text)
    if "mrf" not in zones:
        raise MeshContractError("fluid cellZones: missing required mrf zone")
    if not zones["mrf"]:
        raise MeshContractError("fluid cellZones: mrf must not be empty")


def validate_post_split_files(
    fluid_boundary: Path | str,
    fluid_cell_zones: Path | str,
    aluminum_boundary: Path | str,
) -> None:
    fluid_boundary_path = Path(fluid_boundary)
    fluid_cell_zones_path = Path(fluid_cell_zones)
    aluminum_boundary_path = Path(aluminum_boundary)
    expected = {
        "fluid": fluid_boundary_path,
        "aluminum": aluminum_boundary_path,
    }
    for region, boundary_path in expected.items():
        if boundary_path.name != "boundary" or boundary_path.parent.name != "polyMesh" or boundary_path.parent.parent.name != region:
            raise MeshContractError(f"{region}: expected its polyMesh/boundary path")
        if not boundary_path.is_file():
            raise MeshContractError(f"{region}: missing mesh boundary")
    if fluid_cell_zones_path.parent != fluid_boundary_path.parent or fluid_cell_zones_path.name != "cellZones":
        raise MeshContractError("fluid: expected cellZones beside polyMesh/boundary")
    if not fluid_cell_zones_path.is_file():
        raise MeshContractError("fluid: missing cellZones")

    region_root = fluid_boundary_path.parent.parent.parent
    mesh_regions = {path.name for path in region_root.iterdir() if (path / "polyMesh").is_dir()}
    if mesh_regions != {"fluid", "aluminum"}:
        raise MeshContractError(
            f"mesh regions must be exactly aluminum and fluid, found: {', '.join(sorted(mesh_regions)) or '(none)'}"
        )
    validate_post_split(
        fluid_boundary_path.read_text(encoding="utf-8"),
        fluid_cell_zones_path.read_text(encoding="utf-8"),
        aluminum_boundary_path.read_text(encoding="utf-8"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--surface-check", type=Path)
    modes.add_argument("--check-mesh", type=Path)
    modes.add_argument("--pre-split", action="store_true")
    modes.add_argument("--post-split", action="store_true")
    parser.add_argument("--require-closed")
    parser.add_argument("--boundary", type=Path)
    parser.add_argument("--cell-zones", type=Path)
    parser.add_argument("--fluid-boundary", type=Path)
    parser.add_argument("--fluid-cell-zones", type=Path)
    parser.add_argument("--aluminum-boundary", type=Path)
    args = parser.parse_args()
    try:
        if args.surface_check:
            if not args.require_closed:
                raise MeshContractError("--surface-check requires --require-closed")
            validate_surface_check_text(args.surface_check.read_text(encoding="utf-8"), args.require_closed)
        elif args.check_mesh:
            validate_check_mesh_text(args.check_mesh.read_text(encoding="utf-8"))
        elif args.pre_split:
            if args.boundary is None or args.cell_zones is None:
                raise MeshContractError("--pre-split requires --boundary and --cell-zones")
            validate_pre_split_files(args.boundary, args.cell_zones)
        else:
            if None in (args.fluid_boundary, args.fluid_cell_zones, args.aluminum_boundary):
                raise MeshContractError(
                    "--post-split requires --fluid-boundary, --fluid-cell-zones, and --aluminum-boundary"
                )
            validate_post_split_files(args.fluid_boundary, args.fluid_cell_zones, args.aluminum_boundary)
    except (MeshContractError, OSError) as error:
        print(f"mesh contract failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
