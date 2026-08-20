from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


RESULT_FIELDS = (
    "flowConverged",
    "thermalConverged",
    "fluidTmaxK",
    "solidTmaxK",
    "pressureDropPa",
    "massFlowKgPerS",
    "status",
)

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"


def _read_ascii(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return text if re.search(r"\bformat\s+ascii\s*;", text) else None


def _block_after(text: str, name: str) -> str | None:
    match = re.search(rf"\b{re.escape(name)}\s*\{{", text)
    if match is None:
        return None
    start = match.end()
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return None


def _scalar_values(text: str, key: str = "value") -> list[float] | None:
    uniform = re.search(rf"\b{key}\s+uniform\s+({_NUMBER})\s*;", text)
    if uniform is not None:
        return _finite([uniform.group(1)])

    nonuniform = re.search(
        rf"\b{key}\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;",
        text,
        re.DOTALL,
    )
    if nonuniform is None:
        return None
    values = re.findall(_NUMBER, nonuniform.group(2))
    if len(values) != int(nonuniform.group(1)):
        return None
    return _finite(values)


def _finite(values: list[str]) -> list[float] | None:
    try:
        parsed = [float(value) for value in values]
    except ValueError:
        return None
    return parsed if parsed and all(math.isfinite(value) for value in parsed) else None


def internal_scalar_max(field: Path | str) -> float | None:
    text = _read_ascii(Path(field))
    if text is None:
        return None
    values = _scalar_values(text, "internalField")
    return max(values) if values is not None else None


def boundary_scalar_mean(field: Path | str, patch: str) -> float | None:
    text = _read_ascii(Path(field))
    boundary = _block_after(text, "boundaryField") if text is not None else None
    patch_body = _block_after(boundary, patch) if boundary is not None else None
    values = _scalar_values(patch_body) if patch_body is not None else None
    return sum(values) / len(values) if values is not None else None


def pressure_drop(field: Path | str, inlet: str = "patch_inlet", outlet: str = "patch_outlet") -> float | None:
    inlet_pressure = boundary_scalar_mean(field, inlet)
    outlet_pressure = boundary_scalar_mean(field, outlet)
    if inlet_pressure is None or outlet_pressure is None:
        return None
    return inlet_pressure - outlet_pressure


def inlet_mass_flow(
    phi_field: Path | str,
    density_kg_per_m3: float | None,
    inlet: str = "patch_inlet",
) -> float | None:
    if density_kg_per_m3 is None or not math.isfinite(density_kg_per_m3):
        return None
    text = _read_ascii(Path(phi_field))
    boundary = _block_after(text, "boundaryField") if text is not None else None
    patch_body = _block_after(boundary, inlet) if boundary is not None else None
    values = _scalar_values(patch_body) if patch_body is not None else None
    return abs(sum(values) * density_kg_per_m3) if values is not None else None


def fluid_density(properties: Path | str) -> float | None:
    text = _read_ascii(Path(properties))
    if text is None:
        return None
    match = re.search(rf"\brho\s+({_NUMBER})\s*;", text)
    values = _finite([match.group(1)]) if match is not None else None
    return values[0] if values is not None else None


def extract_metrics(case_dir: Path | str, time_name: str) -> dict[str, float | None]:
    case = Path(case_dir)
    fluid = case / time_name / "fluid"
    return {
        "fluidTmaxK": internal_scalar_max(fluid / "T"),
        "solidTmaxK": internal_scalar_max(case / time_name / "aluminum" / "T"),
        "pressureDropPa": pressure_drop(fluid / "p"),
        "massFlowKgPerS": inlet_mass_flow(fluid / "phi", fluid_density(case / "constant" / "fluid" / "thermophysicalProperties")),
    }


def make_result(
    flow_converged: bool,
    thermal_converged: bool,
    metrics: dict[str, float | None],
) -> dict[str, Any]:
    complete = all(metrics.get(name) is not None for name in RESULT_FIELDS[2:6])
    success = flow_converged and thermal_converged and complete
    return {
        "flowConverged": bool(flow_converged),
        "thermalConverged": bool(thermal_converged),
        "fluidTmaxK": metrics.get("fluidTmaxK"),
        "solidTmaxK": metrics.get("solidTmaxK"),
        "pressureDropPa": metrics.get("pressureDropPa"),
        "massFlowKgPerS": metrics.get("massFlowKgPerS"),
        "status": "success" if success else "non_converged",
    }


def write_result(path: Path | str, result: dict[str, Any]) -> None:
    payload = {name: result.get(name) for name in RESULT_FIELDS}
    Path(path).write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
