#!/usr/bin/env python3
"""Read-only diagnostic: report each SurfaceBody's face appearance/style identifiers and counts.

Safety:
- Attaches only to an already-running Inventor session.
- Opens only hs5_cfd.ipt.
- Reads face appearance/style properties defensively (no writes, no saves, no closes).
- Requires --i-understand-this-touches-live-inventor acknowledgement.

Output:
- JSON evidence file (face_appearance_evidence.json) listing per-body face appearance
  identifiers, counts, and color data.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import pythoncom

from inventor_client import InventorPaths, connect_inventor, open_part, write_text_atomic

ROOT = Path(__file__).resolve().parents[1]
CFD_PATH = ROOT / "hs5_cfd.ipt"
EVIDENCE_PATH = ROOT / "face_appearance_evidence.json"


@dataclass
class ColorData:
    """RGB color components from Inventor Color object."""
    r: float | None = None
    g: float | None = None
    b: float | None = None
    error: str | None = None


@dataclass
class MaterialData:
    """Material reference and its color."""
    name: str | None = None
    color: ColorData = field(default_factory=ColorData)
    error: str | None = None


@dataclass
class FaceAppearanceData:
    """Face appearance identifier and associated color."""
    name: str | None = None
    color: ColorData = field(default_factory=ColorData)
    error: str | None = None


@dataclass
class BodyAppearanceReport:
    """Per-SurfaceBody face appearance summary."""
    name: str = ""
    faceCount: int = 0
    appearances: list[dict[str, Any]] = field(default_factory=list)
    appearanceCounts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _color_component(color_obj: Any, *names: str) -> float | None:
    for name in names:
        value = getattr(color_obj, name, None)
        if value is not None:
            return _safe_float(value)
    return None


def _read_color(color_obj: Any) -> ColorData:
    """Read RGB from an Inventor Color object defensively."""
    result = ColorData()
    try:
        result.r = _color_component(color_obj, "Red", "r", "R")
        result.g = _color_component(color_obj, "Green", "g", "G")
        result.b = _color_component(color_obj, "Blue", "b", "B")
    except Exception as exc:
        result.error = str(exc)
    return result


def _read_face_material(material_obj: Any) -> MaterialData:
    """Read material name and its color from a Material object."""
    result = MaterialData()
    try:
        result.name = str(getattr(material_obj, "Name", ""))
        color_obj = getattr(material_obj, "Color", None)
        if color_obj is not None:
            result.color = _read_color(color_obj)
    except Exception as exc:
        result.error = str(exc)
    return result


def _read_face_appearance(face: Any) -> FaceAppearanceData:
    """Read a face's appearance identifier and its color."""
    result = FaceAppearanceData()
    try:
        appearance_obj = getattr(face, "Appearance", None)
        if appearance_obj is None:
            return result
        result.name = str(getattr(appearance_obj, "Name", ""))
        color_obj = getattr(appearance_obj, "Color", None)
        if color_obj is not None:
            result.color = _read_color(color_obj)
    except Exception as exc:
        result.error = str(exc)
    return result


def _read_face_material_assignment(face: Any) -> MaterialData:
    """Read the material assigned to a face."""
    result = MaterialData()
    try:
        material_obj = getattr(face, "Material", None)
        if material_obj is None:
            return result
        return _read_face_material(material_obj)
    except Exception as exc:
        result.error = str(exc)
        return result


def read_face_appearances(document: Any) -> list[BodyAppearanceReport]:
    """Read face appearance/style data for every SurfaceBody in the document.

    This is a read-only operation. No visibility, save, close, or other mutations.
    """
    reports: list[BodyAppearanceReport] = []
    bodies = document.ComponentDefinition.SurfaceBodies

    for body_index in range(1, bodies.Count + 1):
        body = bodies.Item(body_index)
        report = BodyAppearanceReport(name=str(body.Name))

        try:
            face_collection = body.Faces
        except Exception as exc:
            report.errors.append(f"Faces collection: {exc}")
            reports.append(report)
            continue

        report.faceCount = face_collection.Count

        appearance_counts: dict[str, int] = {}
        appearance_list: list[dict[str, Any]] = []

        for face_index in range(1, face_collection.Count + 1):
            face = face_collection.Item(face_index)

            # Face appearance (style)
            face_appearance = _read_face_appearance(face)
            # Material assignment
            face_material = _read_face_material_assignment(face)

            # Build serializable entry
            entry: dict[str, Any] = {
                "faceIndex": face_index,
                "appearance": {
                    "name": face_appearance.name,
                    "color": asdict(face_appearance.color) if face_appearance.color else None,
                    "error": face_appearance.error,
                },
                "material": {
                    "name": face_material.name,
                    "color": asdict(face_material.color) if face_material.color else None,
                    "error": face_material.error,
                },
            }
            appearance_list.append(entry)

            # Count appearances by name
            name = face_appearance.name or "<none>"
            appearance_counts[name] = appearance_counts.get(name, 0) + 1

            # Track errors
            if face_appearance.error:
                report.errors.append(f"face[{face_index}] appearance: {face_appearance.error}")
            if face_material.error:
                report.errors.append(f"face[{face_index}] material: {face_material.error}")

        report.appearances = appearance_list
        report.appearanceCounts = appearance_counts
        reports.append(report)

    return reports


def detect_blade_appearance(reports: list[BodyAppearanceReport]) -> dict[str, Any]:
    """Analyze reports to determine if a distinct blade face appearance exists.

    Returns a summary indicating whether blade faces have a unique appearance
    different from the other bodies (master, aluminum, fan_mrf_zone).
    """
    if not reports:
        return {"found": False, "reason": "no bodies found"}

    # Identify the blade body (master_1 = aluminum)
    blade_body = None
    other_bodies: list[BodyAppearanceReport] = []
    for report in reports:
        name = report.name.lower()
        if "master_1" in name or "aluminum" in name:
            blade_body = report
        else:
            other_bodies.append(report)

    if blade_body is None:
        return {"found": False, "reason": "no blade body (master_1/aluminum) detected"}

    blade_appearance_names = {
        info["appearance"]["name"] for info in blade_body.appearances
        if info["appearance"]["name"] and info["appearance"].get("error") is None
    }
    blade_material_names = {
        info["material"]["name"] for info in blade_body.appearances
        if info["material"]["name"] and info["material"].get("error") is None
    }

    # Check if blade has any appearance that is unique vs other bodies
    blade_has_unique_appearance = False
    blade_unique_names: list[str] = []
    blade_unique_materials: list[str] = []

    if other_bodies:
        other_appearance_names = set()
        for other in other_bodies:
            for info in other.appearances:
                if info["appearance"].get("name"):
                    other_appearance_names.add(info["appearance"]["name"])

        for name in blade_appearance_names:
            if name not in other_appearance_names:
                blade_has_unique_appearance = True
                blade_unique_names.append(name)

        other_material_names = set()
        for other in other_bodies:
            for info in other.appearances:
                if info["material"].get("name"):
                    other_material_names.add(info["material"]["name"])

        for name in blade_material_names:
            if name not in other_material_names:
                blade_has_unique_appearance = True
                blade_unique_materials.append(name)

    # Also check for non-MTL color assignments (appearance with no material or
    # material not matching standard MTL materials)
    non_mtl_appearances = []
    for info in blade_body.appearances:
        mat = info["material"]
        if mat.get("error"):
            non_mtl_appearances.append(info)
        elif mat.get("name") in ("", "<none>", None):
            non_mtl_appearances.append(info)

    return {
        "found": blade_has_unique_appearance,
        "bladeBody": blade_body.name,
        "bladeFaceCount": blade_body.faceCount,
        "bladeAppearanceNames": sorted(blade_appearance_names),
        "bladeUniqueAppearanceNames": sorted(blade_unique_names),
        "bladeMaterialNames": sorted(blade_material_names),
        "bladeUniqueMaterialNames": sorted(blade_unique_materials),
        "nonMtlAppearances": len(non_mtl_appearances),
        "reason": (
            "blade has appearance(s) not shared by other bodies"
            if blade_has_unique_appearance
            else "no distinct blade appearance detected"
        ),
    }


def build_evidence(reports: list[BodyAppearanceReport], blade_analysis: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON evidence document."""
    return {
        "generatedAt": None,  # filled by caller
        "sourceDocument": str(CFD_PATH),
        "summary": {
            "totalBodies": len(reports),
            "totalFaces": sum(r.faceCount for r in reports),
            "bladeDistinctAppearance": blade_analysis["found"],
            "bladeReason": blade_analysis["reason"],
        },
        "bodies": [
            {
                "name": r.name,
                "faceCount": r.faceCount,
                "appearanceCounts": r.appearanceCounts,
                "errors": r.errors,
            }
            for r in reports
        ],
        "bladeAnalysis": blade_analysis,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostic: report each SurfaceBody's face appearance/style identifiers and counts."
    )
    parser.add_argument("--cfd", type=Path, default=CFD_PATH, help="CFD part file (default: hs5_cfd.ipt).")
    parser.add_argument("--output", type=Path, default=EVIDENCE_PATH, help="JSON evidence output (default: face_appearance_evidence.json).")
    parser.add_argument(
        "--i-understand-this-touches-live-inventor",
        action="store_true",
        required=True,
        help="Required safety acknowledgement: this script attaches to the live Inventor session.",
    )
    args = parser.parse_args()

    if not args.i_understand_this_touches_live_inventor:
        raise SystemExit(
            "Refusing to attach to live Inventor without "
            "--i-understand-this-touches-live-inventor"
        )

    pythoncom.CoInitialize()
    app = None
    try:
        app = connect_inventor(visible=False)
        document = open_part(app, args.cfd)
        reports = read_face_appearances(document)
        blade_analysis = detect_blade_appearance(reports)
        evidence = build_evidence(reports, blade_analysis)
        evidence["generatedAt"] = None  # Will be set by caller if needed
        write_text_atomic(args.output, json.dumps(evidence, indent=2, ensure_ascii=False))

        # Print summary
        print(f"wrote {args.output}")
        print(f"bodies: {len(reports)}")
        total_faces = sum(r.faceCount for r in reports)
        print(f"total faces: {total_faces}")
        print(f"blade distinct appearance: {blade_analysis['found']}")
        print(f"reason: {blade_analysis['reason']}")
        for report in reports:
            print(f"  {report.name}: {report.faceCount} faces, "
                  f"{len(report.appearanceCounts)} unique appearances")
            for name, count in sorted(report.appearanceCounts.items()):
                print(f"    appearance '{name}': {count} face(s)")
    finally:
        app = None
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
