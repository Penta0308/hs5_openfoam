from __future__ import annotations

import shutil
from pathlib import Path

try:
    from .template_render import render_text
except ImportError:
    from template_render import render_text


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "openfoam" / "templates"
CASE_TOOLS = (
    "verify_location_in_mesh.py",
    "verify_mesh_contract.py",
    "flow_runner.py",
    "result_metrics.py",
    "run_two_stage_cfd.py",
)
NORMALIZED_SURFACE = Path("constant/triSurface/hs5_cfd.openfoam.obj")
V14_GEOMETRY_SURFACE = Path("constant/geometry/hs5_cfd.openfoam.obj")
PROFILE_HELPER_SURFACES = (
    (Path("constant/triSurface/mrf.obj"), Path("constant/geometry/mrf-zone.obj")),
    (Path("constant/triSurface/master_1.obj"), Path("constant/geometry/aluminum-zone.obj")),
)


def render_case_files(output: Path | str, context: dict[str, object], template_dir: Path | str = TEMPLATE_DIR) -> None:
    output_path = Path(output)
    source_templates = Path(template_dir)
    for template in source_templates.rglob("*.tmpl"):
        destination = output_path / template.relative_to(source_templates).with_suffix("")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as rendered_file:
            rendered_file.write(render_text(template.read_text(encoding="utf-8"), context))
    copy_case_tools(output_path)
    stage_v14_tri_surface(output_path)
    stage_profile_helpers(output_path)


def copy_case_tools(output: Path | str, tools_dir: Path | str = ROOT / "tools") -> None:
    destination_dir = Path(output) / "tools"
    destination_dir.mkdir(parents=True, exist_ok=True)
    for name in CASE_TOOLS:
        shutil.copyfile(Path(tools_dir) / name, destination_dir / name)


def stage_v14_tri_surface(output: Path | str) -> None:
    output_path = Path(output)
    source = output_path / NORMALIZED_SURFACE
    if source.is_file():
        destination = output_path / V14_GEOMETRY_SURFACE
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def stage_profile_helpers(output: Path | str) -> None:
    output_path = Path(output)
    for source_relative, destination_relative in PROFILE_HELPER_SURFACES:
        source = output_path / source_relative
        if source.is_file():
            destination = output_path / destination_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
