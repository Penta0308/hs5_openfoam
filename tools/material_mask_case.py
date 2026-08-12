#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from template_render import render_file


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "openfoam" / "material-mask" / "templates" / "materialMask.tmpl"
DEFAULT_OUTPUT = ROOT / "openfoam" / "material-mask" / "case" / "0" / "materialMask"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the initial OpenFOAM materialMask field.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mask-default", default="0")
    parser.add_argument("--manifest", type=Path, default=ROOT / "openfoam" / "material-mask" / "case" / "mask_manifest.json")
    args = parser.parse_args()

    context = {"MASK_DEFAULT": args.mask_default}
    render_file(TEMPLATE, args.output, context)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({"maskDefault": args.mask_default}, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
