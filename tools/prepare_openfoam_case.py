#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pythoncom

from inventor_client import (
    InventorPaths,
    collect_state,
    connect_inventor,
    export_obj_profiles,
    open_part,
    write_text_atomic,
)
from obj_region_normalizer import normalize_obj_file
from template_render import render_text


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CFD = ROOT / "hs5_cfd.ipt"
TEMPLATE_DIR = ROOT / "openfoam" / "templates"
DEFAULT_OUTPUT = ROOT / "openfoam" / "cases" / "finless-current"


def expression(parameters: dict[str, dict[str, str]], name: str) -> str:
    return parameters[name]["expression"]


_MILLIMETRE_LITERAL = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+mm\s*$")


def location_in_mesh_m(parameters: dict[str, dict[str, str]]) -> str:
    """Resolve the parameterized fan-centre candidate from saved CAD state.

    This is deliberately limited to dimensional millimetre literals: rendering a
    historical case must not evaluate arbitrary Inventor expressions or access
    a live Inventor session.
    """
    values: list[float] = []
    for name in ("OfanX", "OfanY"):
        try:
            source = expression(parameters, name)
        except KeyError as error:
            raise ValueError(f"missing required location parameter: {name}") from error
        match = _MILLIMETRE_LITERAL.fullmatch(source)
        if match is None:
            raise ValueError(f"{name} must be a millimetre literal in saved CAD state: {source!r}")
        values.append(float(match.group(1)) / 1000.0)
    return f"({values[0]:.12g} {values[1]:.12g} -0.001)"


def fan_origin_m(parameters: dict[str, dict[str, str]]) -> str:
    values: list[float] = []
    for name in ("OfanX", "OfanY", "OfanZ"):
        try:
            source = expression(parameters, name)
        except KeyError as error:
            raise ValueError(f"missing required fan-origin parameter: {name}") from error
        match = _MILLIMETRE_LITERAL.fullmatch(source)
        if match is None:
            raise ValueError(f"{name} must be a millimetre literal in saved CAD state: {source!r}")
        values.append(float(match.group(1)) / 1000.0)
    return f"({values[0]:.12g} {values[1]:.12g} {values[2]:.12g})"


def render_context(cfd: Path, output: Path, state: dict[str, Any], generated_at: str) -> dict[str, object]:
    parameters = state["cfd"]["parameters"]
    return {
        "CASE_ID": output.name,
        "GENERATED_AT": generated_at,
        "CFD_IPT_JSON": json.dumps(str(cfd.resolve())),
        "MASTER_PROFILE_OBJ": "constant/triSurface/master.obj",
        "MRF_PROFILE_OBJ": "constant/triSurface/mrf.obj",
        "ALUMINUM_PROFILE_OBJ": "constant/triSurface/master_1.obj",
        "NORMALIZED_CFD_OBJ": "constant/triSurface/hs5_cfd.openfoam.obj",
        "MRF_ZONE_OBJ": "constant/geometry/mrf-zone.obj",
        "ALUMINUM_ZONE_OBJ": "constant/geometry/aluminum-zone.obj",
        "OBJ_REGION_MANIFEST": "obj_region_manifest.json",
        "LOCATION_IN_MESH_M": location_in_mesh_m(parameters),
        "FAN_ORIGIN_M": fan_origin_m(parameters),
        "PARAMETERS_JSON": json.dumps(parameters, indent=2, ensure_ascii=False),
        "CFD_NAMING_CONTRACT_JSON": json.dumps(state["cfdNamingContract"], indent=2, ensure_ascii=False),
    }


def render_templates(output: Path, context: dict[str, object]) -> None:
    # Imported here so the static renderer remains usable without importing
    # this Inventor-aware module or any COM dependency.
    from case_render import render_case_files

    render_case_files(output, context)


def prepare_dirs(output: Path) -> None:
    (output / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)


def reset_output_dir(output: Path) -> None:
    resolved = output.resolve()
    allowed_root = (ROOT / "openfoam" / "cases").resolve()
    if resolved == ROOT.resolve() or allowed_root not in (resolved, *resolved.parents):
        raise RuntimeError(f"refusing to delete output outside {allowed_root}: {resolved}")
    if output.exists():
        shutil.rmtree(output)


def copy_readme(output: Path) -> None:
    readme = output / "README.md"
    readme.write_text(
        "# hs5 Inventor-derived OpenFOAM v14 case\n\n"
        "Generated only from `hs5_cfd.ipt`.\n\n"
        "## Raw vs Normalized Surface\n\n"
        "The canonical profile exports are `constant/triSurface/master.obj`, "
        "`mrf.obj`, and `master_1.obj`. The latter two are copied byte-for-byte to "
        "`constant/geometry/mrf-zone.obj` and `aluminum-zone.obj` for createZones. "
        "Only the normalized master OBJ, `constant/triSurface/hs5_cfd.openfoam.obj`, "
        "is consumed by snappyHexMesh. It is derived from `master.obj` by replacing "
        "RGB-valued material runs with `g` region names.\n\n"
        "## Color-Based Region Classification\n\n"
        "The normalizer maps RGB colors from the raw OBJ's `usemtl` names into "
        "OpenFOAM `g` regions:\n\n"
        "  - `(0,92,255)` blue     -> `patch_inlet` (inlet)\n"
        "  - `(255,64,0)` red-orange -> `patch_heatsource` (heat_source)\n"
        "  - `(0,180,80)` green    -> `patch_outlet` (outlet)\n"
        "  - `(255,0,255)` magenta  -> `patch_blade` (blade_cavity_wall)\n"
        "  - `(160,160,160)` / `(191,191,191)` gray -> `wall`\n\n"
        "OpenFOAM.org v14 reads patch regions from the OBJ `g` field only and "
        "ignores `usemtl`/MTL. The classification manifest is recorded as "
        "`obj_region_manifest.json`.\n\n"
        "## CHT smoke boundary\n\n"
        "The rendered `chtMultiRegionFoam` control dictionary is deliberately a "
        "bounded smoke setup, not mesh acceptance. Regional checkMesh evidence "
        "still includes 42 small-determinant cells and concavity findings; it is "
        "not suppressed or relaxed by the CHT templates.\n\n"
        "Run `bash Allrun` on the remote host after sourcing OpenFOAM v14 or "
        "let the script source it.\n",
        encoding="utf-8",
    )


def prepare_case(
    cfd: Path,
    output: Path,
    *,
    visible: bool,
    inventor_access_confirmed: bool,
) -> dict[str, Any]:
    if not inventor_access_confirmed:
        raise RuntimeError(
            "Refusing to attach to live Inventor without "
            "inventor_access_confirmed=True"
        )
    if cfd.resolve() != DEFAULT_CFD.resolve():
        raise RuntimeError(f"live preparation accepts only repository CFD file: {DEFAULT_CFD.resolve()}")

    prepare_dirs(output)
    pythoncom.CoInitialize()
    app = None
    try:
        app = connect_inventor(visible=visible)
        paths = InventorPaths(cfd=cfd)
        state = collect_state(app, paths)

        cfd_doc = open_part(app, cfd)
        master_obj, _mrf_obj, _aluminum_obj = export_obj_profiles(
            app, cfd_doc, output / "constant" / "triSurface"
        )
    finally:
        app = None
        pythoncom.CoUninitialize()

    normalized_obj = output / "constant" / "triSurface" / "hs5_cfd.openfoam.obj"
    region_manifest = output / "obj_region_manifest.json"
    normalize_obj_file(
        master_obj,
        normalized_obj,
        region_manifest,
        source_obj="constant/triSurface/master.obj",
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    context = render_context(cfd, output, state, generated_at)
    render_templates(output, context)
    copy_readme(output)
    write_text_atomic(output / "cad_state.json", json.dumps(state, indent=2, ensure_ascii=False))
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare an Inventor-derived OpenFOAM v14 case directory.")
    parser.add_argument("--cfd", type=Path, default=DEFAULT_CFD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument(
        "--i-understand-this-touches-live-inventor",
        action="store_true",
        help="Required safety acknowledgement: this script attaches to the live Inventor session and exports OBJ.",
    )
    args = parser.parse_args()

    if not args.i_understand_this_touches_live_inventor:
        raise SystemExit(
            "Refusing to attach to live Inventor without "
            "--i-understand-this-touches-live-inventor"
        )

    reset_output_dir(args.output)

    state = prepare_case(
        args.cfd,
        args.output,
        visible=args.visible,
        inventor_access_confirmed=args.i_understand_this_touches_live_inventor,
    )
    print(f"prepared {args.output}")
    print(
        f"cfd bodies={len(state['cfd']['bodies'])}; "
        f"cfd params={len(state['cfd']['parameters'])}; "
        f"named faces={len(state['cfd']['namedFaces'])}"
    )


if __name__ == "__main__":
    main()
