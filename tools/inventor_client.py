from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, cast

import pythoncom
import win32com.client


OBJ_TRANSLATOR_ID = "{F539FB09-FC01-4260-A429-1818B14D6BAC}"
K_FILE_BROWSE_IO = 13059


@dataclass(frozen=True)
class InventorPaths:
    cfd: Path


@dataclass(frozen=True)
class ObjProfile:
    body_name: str
    filename: str


OBJ_PROFILES = (
    ObjProfile("master", "master.obj"),
    ObjProfile("mrf", "mrf.obj"),
    ObjProfile("master_1", "master_1.obj"),
)


PATCH_INLET = "patch_inlet"
PATCH_HEAT_SOURCE = "patch_heatsource"
PATCH_OUTLET_PATTERN = re.compile(r"^patch_outlet(?:_\d+)?$")


def classify_patch_name(name: str) -> str | None:
    if name == PATCH_INLET:
        return "inlet"
    if name == PATCH_HEAT_SOURCE:
        return "heat_source"
    if PATCH_OUTLET_PATTERN.fullmatch(name):
        return "outlet"
    return None


def classify_solid_name(name: str) -> str:
    if name == "master":
        return "cfd_domain"
    if name == "master_1":
        return "aluminum"
    if name == "mrf":
        return "fan_mrf_zone"
    return "unclassified"


def connect_inventor(*, visible: bool) -> win32com.client.CDispatch:
    try:
        raw_app = pythoncom.GetActiveObject("Inventor.Application")
    except pythoncom.com_error as exc:
        raise RuntimeError("Inventor is not running; open Inventor before running optimization tools") from exc
    else:
        dispatch = raw_app.QueryInterface(pythoncom.IID_IDispatch)
        app = win32com.client.Dispatch(dispatch)
        #print(type(app))
        #app = cast(Any, app)
    # This is a user-owned interactive session.  Attaching must not alter its
    # visibility, irrespective of the legacy CLI flag.
    return cast(win32com.client.CDispatch, app)


def open_part(app: win32com.client.CDispatch, path: Path) -> Any:
    return app.Documents.Open(str(path.resolve()), True)


def read_parameters(document: Any) -> dict[str, dict[str, str]]:
    parameters = document.ComponentDefinition.Parameters
    result: dict[str, dict[str, str]] = {}
    for index in range(1, parameters.Count + 1):
        parameter = parameters.Item(index)
        result[str(parameter.Name)] = {
            "expression": str(parameter.Expression),
            "units": str(parameter.Units),
        }
    return result


def read_bodies(document: Any) -> list[dict[str, Any]]:
    bodies = document.ComponentDefinition.SurfaceBodies
    return [
        {
            "name": str(bodies.Item(index).Name),
            "role": classify_solid_name(str(bodies.Item(index).Name)),
            "visible": bool(bodies.Item(index).Visible),
        }
        for index in range(1, bodies.Count + 1)
    ]


def _read_attribute_sets(entity: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        attribute_sets = entity.AttributeSets
    except Exception:
        return result
    for set_index in range(1, attribute_sets.Count + 1):
        try:
            attribute_set = attribute_sets.Item(set_index)
            attributes: dict[str, str] = {}
            for attribute_index in range(1, attribute_set.Count + 1):
                attribute = attribute_set.Item(attribute_index)
                attributes[str(attribute.Name)] = str(attribute.Value)
            result.append({"name": str(attribute_set.Name), "attributes": attributes})
        except Exception as exc:
            result.append({"name": f"<unreadable:{set_index}>", "error": str(exc)})
    return result


def _attribute_value(entity: Any, set_name: str, attribute_name: str) -> str | None:
    try:
        attribute_sets = entity.AttributeSets
        if not bool(attribute_sets.NameIsUsed(set_name)):
            return None
        attribute_set = attribute_sets.Item(set_name)
        if not bool(attribute_set.NameIsUsed(attribute_name)):
            return None
        return str(attribute_set.Item(attribute_name).Value)
    except Exception:
        return None


def _entity_name(entity: Any) -> str | None:
    for set_name, attribute_name in (
        ("iLogicEntityNameSet", "iLogicEntityName"),
        ("NamedGeometry", "Name"),
    ):
        value = _attribute_value(entity, set_name, attribute_name)
        if value:
            return value
    try:
        value = str(entity.Name)
    except Exception:
        return None
    return value if value else None


def read_named_faces(document: Any) -> list[dict[str, str]]:
    faces: list[dict[str, str]] = []
    bodies = document.ComponentDefinition.SurfaceBodies
    for body_index in range(1, bodies.Count + 1):
        body = bodies.Item(body_index)
        body_name = str(body.Name)
        try:
            body_faces = body.Faces
        except Exception:
            continue
        for face_index in range(1, body_faces.Count + 1):
            face = body_faces.Item(face_index)
            name = _entity_name(face)
            if not name:
                continue
            role = classify_patch_name(name)
            faces.append(
                {
                    "body": body_name,
                    "name": name,
                    "role": role if role is not None else "wall",
                }
            )
    return faces


def read_face_name_diagnostics(document: Any) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {"bodies": []}
    bodies = document.ComponentDefinition.SurfaceBodies
    for body_index in range(1, bodies.Count + 1):
        body = bodies.Item(body_index)
        body_entry: dict[str, Any] = {
            "name": str(body.Name),
            "attributes": _read_attribute_sets(body),
            "namedFaces": [],
            "facesWithAttributes": [],
        }
        try:
            body_faces = body.Faces
        except Exception as exc:
            body_entry["faceReadError"] = str(exc)
            diagnostics["bodies"].append(body_entry)
            continue
        for face_index in range(1, body_faces.Count + 1):
            face = body_faces.Item(face_index)
            attributes = _read_attribute_sets(face)
            name = _entity_name(face)
            if name:
                body_entry["namedFaces"].append({"index": face_index, "name": name})
            if attributes:
                body_entry["facesWithAttributes"].append(
                    {"index": face_index, "name": name, "attributes": attributes}
                )
        diagnostics["bodies"].append(body_entry)
    return diagnostics


def cfd_naming_contract(named_faces: list[dict[str, str]]) -> dict[str, Any]:
    detected = sorted({face["name"] for face in named_faces})
    required = [PATCH_INLET, PATCH_HEAT_SOURCE]
    return {
        "sourceDocument": "hs5_cfd.ipt",
        "solidRoles": {
            "master": "cfd_domain",
            "master_1": "aluminum",
            "mrf": "fan_mrf_zone",
        },
        "patchRules": {
            PATCH_INLET: "inlet",
            PATCH_HEAT_SOURCE: "heat_source",
            "patch_outlet": "outlet",
            "patch_outlet_#": "outlet",
            "default": "wall",
        },
        "detectedNamedFaces": detected,
        "missingRequiredNamedFaces": [name for name in required if name not in detected],
    }


def set_parameters(document: Any, values: dict[str, str]) -> None:
    parameters = document.ComponentDefinition.Parameters
    for name, expression in values.items():
        parameters.Item(name).Expression = expression
    document.Update()


def export_obj(app: Any, document: Any, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    translator = app.ApplicationAddIns.ItemById(OBJ_TRANSLATOR_ID)
    context = app.TransientObjects.CreateTranslationContext()
    options = app.TransientObjects.CreateNameValueMap()
    data = app.TransientObjects.CreateDataMedium()
    context.Type = K_FILE_BROWSE_IO
    translator.HasSaveCopyAsOptions(document, context, options)
    data.FileName = str(output.resolve())
    translator.SaveCopyAs(document, context, options, data)


def obj_profile_paths(output_directory: Path) -> tuple[Path, ...]:
    return tuple(output_directory / profile.filename for profile in OBJ_PROFILES)


def _required_surface_bodies(document: Any) -> dict[str, Any]:
    bodies = document.ComponentDefinition.SurfaceBodies
    found: dict[str, list[Any]] = {}
    for index in range(1, bodies.Count + 1):
        body = bodies.Item(index)
        found.setdefault(str(body.Name), []).append(body)

    required_names = {profile.body_name for profile in OBJ_PROFILES}
    missing = sorted(required_names - found.keys())
    duplicates = sorted(name for name, matches in found.items() if len(matches) > 1)
    unexpected = sorted(set(found) - required_names)
    if missing or duplicates or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if duplicates:
            details.append(f"duplicate: {', '.join(duplicates)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise RuntimeError(f"invalid SurfaceBodies for OBJ profile export ({'; '.join(details)})")
    return {name: matches[0] for name, matches in found.items()}


def export_obj_profiles(
    app: Any, document: Any, output_directory: Path
) -> tuple[Path, ...]:
    bodies_by_name = _required_surface_bodies(document)
    outputs = obj_profile_paths(output_directory)
    for profile, output in zip(OBJ_PROFILES, outputs, strict=True):
        for body_name, body in bodies_by_name.items():
            body.Visible = body_name == profile.body_name
        export_obj(app, document, output)
    return outputs


def collect_state(app: win32com.client.CDispatch, paths: InventorPaths) -> dict[str, Any]:
    cfd = open_part(app, paths.cfd)
    cfd_named_faces = read_named_faces(cfd)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cfd": {
            "displayName": str(cfd.DisplayName),
            "path": str(cfd.FullFileName),
            "parameters": read_parameters(cfd),
            "bodies": read_bodies(cfd),
            "namedFaces": cfd_named_faces,
            "faceNameDiagnostics": read_face_name_diagnostics(cfd),
        },
        "cfdNamingContract": cfd_naming_contract(cfd_named_faces),
    }


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def require_parameters(parameters: dict[str, dict[str, str]], names: Iterable[str]) -> None:
    missing = [name for name in names if name not in parameters]
    if missing:
        raise RuntimeError(f"missing Inventor parameters: {', '.join(missing)}")
