#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pythoncom

from inventor_client import InventorPaths, collect_state, connect_inventor, write_text_atomic


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CFD = ROOT / "hs5_cfd.ipt"
DEFAULT_OUTPUT = ROOT / "cad_state.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect hs5 CFD Inventor part state.")
    parser.add_argument("--cfd", type=Path, default=DEFAULT_CFD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hidden", action="store_true", help="Do not force Inventor UI visible.")
    parser.add_argument(
        "--i-understand-this-touches-live-inventor",
        action="store_true",
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
        app = connect_inventor(visible=not args.hidden)
        state = collect_state(app, InventorPaths(cfd=args.cfd))
        write_text_atomic(args.output, json.dumps(state, indent=2, ensure_ascii=False))
    finally:
        app = None
        pythoncom.CoUninitialize()

    print(f"wrote {args.output}")
    print(
        f"cfd: {len(state['cfd']['parameters'])} parameters, {len(state['cfd']['bodies'])} bodies, "
        f"{len(state['cfd']['namedFaces'])} named faces"
    )


if __name__ == "__main__":
    main()
