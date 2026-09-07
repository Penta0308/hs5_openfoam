#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import numpy as np
from foamlib import DimensionSet, FoamFieldFile


RHO_AIR = 1.204


def latest_numeric_time(path: Path) -> Path:
    times = []
    for child in path.iterdir():
        if not child.is_dir():
            continue
        try:
            float(child.name)
        except ValueError:
            continue
        times.append(child)
    if not times:
        raise RuntimeError(f"no numeric times in {path}")
    return sorted(times, key=lambda item: float(item.name))[-1]


def write_mass_phi(source: Path, destination: Path, rho: float = RHO_AIR) -> None:
    shutil.copyfile(source, destination)
    field = FoamFieldFile(destination)
    field.dimensions = DimensionSet(mass=1, time=-1)
    internal = field.internal_field
    if isinstance(internal, np.ndarray):
        field.internal_field = internal * rho
    for patch in field.boundary_field.values():
        value = patch.get("value")
        if isinstance(value, np.ndarray):
            patch["value"] = value * rho


def prepare(case: Path, flow_time: Path | None) -> None:
    flow_time = flow_time or latest_numeric_time(case / "flow")
    shutil.copyfile(flow_time / "U", case / "0" / "fluid" / "U")
    write_mass_phi(flow_time / "phi", case / "0" / "fluid" / "phi")
    for child in case.iterdir():
        if child.is_dir():
            try:
                float(child.name)
            except ValueError:
                continue
            if child.name != "0":
                shutil.rmtree(child)


def run(case: Path, solver: str) -> int:
    log = case / "log.fixedPhiThermal"
    with log.open("w", encoding="utf-8") as output:
        return subprocess.run((solver,), cwd=case, stdout=output, stderr=subprocess.STDOUT, check=False).returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--flow-time", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--solver", default="fixedPhiThermalFoam")
    args = parser.parse_args()
    case = args.case.resolve()
    prepare(case, args.flow_time)
    if not args.prepare_only:
        raise SystemExit(run(case, args.solver))


if __name__ == "__main__":
    main()
