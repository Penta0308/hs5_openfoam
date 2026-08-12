#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pythoncom

from inventor_client import (
    InventorPaths,
    collect_state,
    connect_inventor,
    export_obj,
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


def render_templates(output: Path, context: dict[str, object]) -> None:
    for template in TEMPLATE_DIR.glob("*.tmpl"):
        relative_name = template.name.removesuffix(".tmpl")
        rendered = render_text(template.read_text(encoding="utf-8"), context)
        destination = output / relative_name
        write_text_atomic(destination, rendered)


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
        "The raw export provenance is `constant/triSurface/hs5_cfd.obj`, produced by "
        "Inventor COM export. It is **not** used as OpenFOAM input. The normalized "
        "single OBJ, `constant/triSurface/hs5_cfd.openfoam.obj`, is the only surface "
        "consumed by OpenFOAM v14. It is derived from the raw OBJ by replacing "
        "RGB-valued material runs with `g` region names.\n\n"
        "## Color-Based Region Classification\n\n"
        "The normalizer maps RGB colors from the raw OBJ's `usemtl` names into "
        "OpenFOAM `g` regions:\n\n"
        "  - `(0,92,255)` blue     -> `patch_inlet` (inlet)\n"
        "  - `(255,64,0)` red-orange -> `patch_heatsource` (heat_source)\n"
        "  - `(0,180,80)` green    -> `patch_outlet` (outlet)\n"
        "  - `(160,160,160)` / `(191,191,191)` gray -> `wall`\n"
        "  - `mrf` group           -> `mrf` (fan_mrf_zone)\n"
        "  - `master_1` group       -> `aluminum`\n\n"
        "OpenFOAM.org v14 reads patch regions from the OBJ `g` field only and "
        "ignores `usemtl`/MTL. The classification manifest is recorded as "
        "`obj_region_manifest.json`.\n\n"
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

    prepare_dirs(output)
    pythoncom.CoInitialize()
    app = None
    try:
        app = connect_inventor(visible=visible)
        paths = InventorPaths(cfd=cfd)
        state = collect_state(app, paths)

        cfd_doc = open_part(app, cfd)
        raw_obj = output / "constant" / "triSurface" / "hs5_cfd.obj"
        export_obj(app, cfd_doc, raw_obj)
    finally:
        app = None
        pythoncom.CoUninitialize()

    normalized_obj = output / "constant" / "triSurface" / "hs5_cfd.openfoam.obj"
    region_manifest = output / "obj_region_manifest.json"
    normalize_obj_file(raw_obj, normalized_obj, region_manifest)

    generated_at = datetime.now(timezone.utc).isoformat()
    context: dict[str, object] = {
        "CASE_ID": output.name,
        "GENERATED_AT": generated_at,
        "CFD_IPT_JSON": json.dumps(str(cfd.resolve())),
        "CFD_OBJ": "hs5_cfd.obj",
        "RAW_CFD_OBJ": "constant/triSurface/hs5_cfd.obj",
        "NORMALIZED_CFD_OBJ": "constant/triSurface/hs5_cfd.openfoam.obj",
        "OBJ_REGION_MANIFEST": "obj_region_manifest.json",
        "PARAMETERS_JSON": json.dumps(state["cfd"]["parameters"], indent=2, ensure_ascii=False),
        "CFD_NAMING_CONTRACT_JSON": json.dumps(state["cfdNamingContract"], indent=2, ensure_ascii=False),
    }
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
