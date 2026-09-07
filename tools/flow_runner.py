#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


REQUIRED_FIELDS = ("p", "Ux", "Uy", "Uz")
RESIDUAL_LIMIT = 1e-5
FLOW_SAMPLE_COUNT = 6
FLOW_VARIATION_LIMIT = 0.015
MASS_IMBALANCE_LIMIT = 0.001
_FINAL_RESIDUAL = re.compile(
    r"Solving for\s+(p|U[xyz])\s*,.*?Final residual\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)
_CONVERGED = re.compile(
    r"(?:\b(?:PIMPLE|SIMPLE)\s*:\s*converged\b|\bsolution\s+converged\b|"
    r"\bresidualControl\b.*\bconverged\b)",
    re.IGNORECASE,
)
_END = re.compile(r"^\s*End\s*$", re.MULTILINE)
_PATCH_BLOCK = r"\b{patch}\s*\{{(.*?)\n\s*\}}"
_NONUNIFORM_SCALARS = re.compile(r"nonuniform List<scalar>\s*\d+\s*\((.*?)\)", re.DOTALL)


@dataclass(frozen=True)
class FlowSample:
    time: float
    inlet_flux: float
    outlet_flux: float

    @property
    def flow_rate(self) -> float:
        return (abs(self.inlet_flux) + abs(self.outlet_flux)) / 2.0

    @property
    def mass_imbalance(self) -> float:
        flow_rate = self.flow_rate
        return abs(self.inlet_flux + self.outlet_flux) / flow_rate if flow_rate else float("inf")


@dataclass(frozen=True)
class FlowConvergence:
    final_residuals: dict[str, float]
    solver_reported_convergence: bool
    reached_end: bool
    converged: bool
    integral_converged: bool = False
    flow_rate: float | None = None
    flow_rate_variation: float | None = None
    mass_imbalance: float | None = None
    sample_count: int = 0


def _patch_flux(field_text: str, patch: str) -> float | None:
    match = re.search(_PATCH_BLOCK.format(patch=re.escape(patch)), field_text, re.DOTALL)
    if match is None:
        return None
    values = _NONUNIFORM_SCALARS.search(match.group(1))
    if values is None:
        return None
    return sum(float(value) for value in values.group(1).split())


def flow_samples(case_dir: Path | str) -> list[FlowSample]:
    case = Path(case_dir)
    if not case.is_dir():
        return []
    samples: list[FlowSample] = []
    for time_dir in sorted(
        (path for path in case.iterdir() if path.is_dir() and _is_number(path.name)),
        key=lambda path: float(path.name),
    ):
        phi = time_dir / "phi"
        if not phi.is_file():
            continue
        text = phi.read_text(encoding="utf-8", errors="replace")
        inlet_flux = _patch_flux(text, "patch_inlet")
        outlet_flux = _patch_flux(text, "patch_outlet")
        if inlet_flux is not None and outlet_flux is not None:
            samples.append(FlowSample(float(time_dir.name), inlet_flux, outlet_flux))
    return samples


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def integral_flow_convergence(case_dir: Path | str) -> tuple[bool, float | None, float | None, float | None, int]:
    samples = flow_samples(case_dir)
    if len(samples) < FLOW_SAMPLE_COUNT:
        return False, None, None, None, len(samples)
    recent = samples[-FLOW_SAMPLE_COUNT:]
    rates = [sample.flow_rate for sample in recent]
    mean_rate = sum(rates) / len(rates)
    variation = (max(rates) - min(rates)) / mean_rate if mean_rate else float("inf")
    imbalance = recent[-1].mass_imbalance
    converged = variation <= FLOW_VARIATION_LIMIT and imbalance <= MASS_IMBALANCE_LIMIT
    return converged, recent[-1].flow_rate, variation, imbalance, len(samples)


def parse_flow_log(log_text: str, case_dir: Path | str | None = None) -> FlowConvergence:
    residuals: dict[str, float] = {}
    for field, value in _FINAL_RESIDUAL.findall(log_text):
        residuals[field] = float(value)
    solver_reported_convergence = _CONVERGED.search(log_text) is not None
    has_all_required_residuals = all(field in residuals for field in REQUIRED_FIELDS)
    residuals_within_limit = all(residuals.get(field, float("inf")) <= RESIDUAL_LIMIT for field in REQUIRED_FIELDS)
    residual_converged = solver_reported_convergence and has_all_required_residuals and residuals_within_limit
    integral_converged = False
    flow_rate = variation = imbalance = None
    sample_count = 0
    if case_dir is not None:
        integral_converged, flow_rate, variation, imbalance, sample_count = integral_flow_convergence(case_dir)
    return FlowConvergence(
        final_residuals=residuals,
        solver_reported_convergence=solver_reported_convergence,
        reached_end=_END.search(log_text) is not None,
        converged=residual_converged or integral_converged,
        integral_converged=integral_converged,
        flow_rate=flow_rate,
        flow_rate_variation=variation,
        mass_imbalance=imbalance,
        sample_count=sample_count,
    )


parse_convergence_log = parse_flow_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate flow convergence from an OpenFOAM log and patch flux history.")
    parser.add_argument("log", type=Path)
    parser.add_argument("--case", type=Path)
    args = parser.parse_args()

    result = parse_flow_log(args.log.read_text(encoding="utf-8", errors="replace"), args.case)
    print(json.dumps(asdict(result), sort_keys=True))
    if not result.converged:
        raise SystemExit("flow run did not meet residual or integral convergence criteria")


if __name__ == "__main__":
    main()
