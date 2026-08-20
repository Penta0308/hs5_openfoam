#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


REQUIRED_FIELDS = ("p", "U", "k", "omega")
RESIDUAL_LIMIT = 1e-5
_FINAL_RESIDUAL = re.compile(
    r"Solving for\s+(p|U|k|omega)\s*,.*?Final residual\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)
_CONVERGED = re.compile(
    r"(?:\b(?:PIMPLE|SIMPLE)\s*:\s*converged\b|\bsolution\s+converged\b|"
    r"\bresidualControl\b.*\bconverged\b)",
    re.IGNORECASE,
)
_END = re.compile(r"^\s*End\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FlowConvergence:
    final_residuals: dict[str, float]
    solver_reported_convergence: bool
    reached_end: bool
    converged: bool


def parse_flow_log(log_text: str) -> FlowConvergence:
    residuals: dict[str, float] = {}
    for field, value in _FINAL_RESIDUAL.findall(log_text):
        residuals[field] = float(value)
    solver_reported_convergence = _CONVERGED.search(log_text) is not None
    has_all_required_residuals = all(field in residuals for field in REQUIRED_FIELDS)
    residuals_within_limit = all(residuals.get(field, float("inf")) <= RESIDUAL_LIMIT for field in REQUIRED_FIELDS)
    return FlowConvergence(
        final_residuals=residuals,
        solver_reported_convergence=solver_reported_convergence,
        reached_end=_END.search(log_text) is not None,
        converged=solver_reported_convergence and has_all_required_residuals and residuals_within_limit,
    )


parse_convergence_log = parse_flow_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate final flow residuals from an OpenFOAM log.")
    parser.add_argument("log", type=Path)
    args = parser.parse_args()

    result = parse_flow_log(args.log.read_text(encoding="utf-8", errors="replace"))
    print(json.dumps(asdict(result), sort_keys=True))
    if not result.converged:
        raise SystemExit("flow run did not explicitly converge; an end-time cap is not convergence")


if __name__ == "__main__":
    main()
