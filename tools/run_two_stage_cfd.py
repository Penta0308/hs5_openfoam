#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

try:
    from .flow_runner import parse_flow_log
    from .result_metrics import extract_metrics, make_result, write_result
except ImportError:
    from flow_runner import parse_flow_log
    from result_metrics import extract_metrics, make_result, write_result


FLOW_FIELDS = ("U", "p", "k", "omega", "nut")
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_THERMAL_RESIDUAL = re.compile(
    rf"Solving for\s+(h|e)\s*,.*?Final residual\s*=\s*({_NUMBER})"
)
_CONVERGED = re.compile(
    r"(?:\b(?:PIMPLE|SIMPLE)\s*:\s*converged\b|\bsolution\s+converged\b|"
    r"\bresidualControl\b.*\bconverged\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ThermalConvergence:
    final_residuals: dict[str, float]
    solver_reported_convergence: bool
    converged: bool


CommandRunner = Callable[[tuple[str, ...], Path], int]


def parse_thermal_log(log_text: str) -> ThermalConvergence:
    residuals = {field: float(value) for field, value in _THERMAL_RESIDUAL.findall(log_text)}
    solver_reported_convergence = _CONVERGED.search(log_text) is not None
    converged = (
        solver_reported_convergence
        and residuals.get("h", float("inf")) <= 1e-5
        and residuals.get("e", float("inf")) <= 1e-6
    )
    return ThermalConvergence(residuals, solver_reported_convergence, converged)


def numeric_times(case_dir: Path) -> list[Path]:
    return sorted(
        (path for path in case_dir.iterdir() if path.is_dir() and path.name != "0" and _is_number(path.name)),
        key=lambda path: float(path.name),
    )


def _is_number(name: str) -> bool:
    try:
        float(name)
    except ValueError:
        return False
    return True


def latest_flow_time(case_dir: Path) -> Path:
    for time_dir in reversed(numeric_times(case_dir)):
        if all((time_dir / "fluid" / field).is_file() for field in FLOW_FIELDS):
            return time_dir
    raise RuntimeError("flow completed without a numeric time containing U/p/k/omega/nut")


def remove_latest_numeric_time(case_dir: Path) -> None:
    times = numeric_times(case_dir)
    if times:
        shutil.rmtree(times[-1])


@contextmanager
def copy_flow_fields(case_dir: Path, time_dir: Path):
    destination = case_dir / "0" / "fluid"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as backup_directory:
        backup = Path(backup_directory)
        present_fields = {field: (destination / field).is_file() for field in FLOW_FIELDS}
        for field, present in present_fields.items():
            if present:
                shutil.copyfile(destination / field, backup / field)
        for field in FLOW_FIELDS:
            shutil.copyfile(time_dir / "fluid" / field, destination / field)
        try:
            yield
        finally:
            for field, present in present_fields.items():
                target = destination / field
                if present:
                    shutil.copyfile(backup / field, target)
                elif target.exists():
                    target.unlink()


def _run(command: Sequence[str], case_dir: Path) -> int:
    if tuple(command) == ("foamMultiRun",):
        with (case_dir / "log.thermal").open("w", encoding="utf-8") as log:
            return subprocess.run(command, cwd=case_dir, check=False, stdout=log, stderr=subprocess.STDOUT).returncode
    return subprocess.run(command, cwd=case_dir, check=False).returncode


def _read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def run_two_stage(
    case_dir: Path | str,
    *,
    fresh: bool = False,
    mesh: bool = False,
    run_command: CommandRunner = _run,
) -> int:
    case = Path(case_dir).resolve()
    result_path = case / "result.json"
    if fresh:
        remove_latest_numeric_time(case)
    try:
        mesh_status = run_command(("bash", "Allrun"), case) if mesh else 0
    except OSError:
        mesh_status = 1
    if mesh_status != 0:
        write_result(result_path, make_result(False, False, {}))
        return 1

    try:
        run_command(("bash", "Allrun.flow"), case)
    except OSError:
        write_result(result_path, make_result(False, False, {}))
        return 1
    flow = parse_flow_log(_read_log(case / "log.flow"))
    if not flow.converged:
        write_result(result_path, make_result(False, False, {}))
        return 1

    try:
        flow_time = latest_flow_time(case)
    except RuntimeError:
        write_result(result_path, make_result(True, False, {}))
        return 1
    with copy_flow_fields(case, flow_time):
        try:
            thermal_status = run_command(("foamMultiRun",), case)
        except OSError:
            thermal_status = 1
        if thermal_status != 0:
            write_result(result_path, make_result(True, False, {}))
            return 1
        thermal = parse_thermal_log(_read_log(case / "log.thermal"))
        result = make_result(True, thermal.converged, extract_metrics(case, numeric_times(case)[-1].name))
        write_result(result_path, result)
        return 0 if thermal.converged and result["status"] == "success" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run flow followed by thermal CHT in the official hs5 case.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--fresh", action="store_true", help="remove only the latest generated numeric time before flow")
    parser.add_argument("--mesh", action="store_true", help="run rendered mesh preflight before flow")
    args = parser.parse_args()
    raise SystemExit(run_two_stage(args.case, fresh=args.fresh, mesh=args.mesh))


if __name__ == "__main__":
    main()
