from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from typing import Any
import unittest


fake_pythoncom = types.ModuleType("pythoncom")
fake_win32com = types.ModuleType("win32com")
fake_win32com_client = types.ModuleType("win32com.client")
setattr(fake_pythoncom, "com_error", RuntimeError)
setattr(fake_win32com, "client", fake_win32com_client)
sys.modules.setdefault("pythoncom", fake_pythoncom)
sys.modules.setdefault("win32com", fake_win32com)
sys.modules.setdefault("win32com.client", fake_win32com_client)

from tools import inventor_client


class FakeBody:
    def __init__(self, name: str, visible: bool = False) -> None:
        self.Name = name
        self.Visible = visible


class FakeCollection:
    def __init__(self, items: list[FakeBody]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> FakeBody:
        return self._items[index - 1]


class FakeDocument:
    def __init__(self, bodies: list[FakeBody]) -> None:
        self.ComponentDefinition = types.SimpleNamespace(SurfaceBodies=FakeCollection(bodies))
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        if name in {"Save", "Close"}:
            self.calls.append(name)
            raise AssertionError(f"prohibited document call: {name}")
        raise AttributeError(name)


class FakeTranslator:
    def __init__(self, bodies: list[FakeBody]) -> None:
        self._bodies = bodies
        self.exports: list[tuple[Path, tuple[str, ...]]] = []

    def HasSaveCopyAsOptions(self, document: FakeDocument, context: object, options: object) -> bool:
        return True

    def SaveCopyAs(self, document: FakeDocument, context: object, options: object, data: Any) -> None:
        self.exports.append(
            (Path(data.FileName), tuple(body.Name for body in self._bodies if body.Visible))
        )


class FakeApp:
    def __init__(self, bodies: list[FakeBody]) -> None:
        self.translator = FakeTranslator(bodies)
        self.ApplicationAddIns = types.SimpleNamespace(ItemById=lambda identifier: self.translator)
        self.TransientObjects = types.SimpleNamespace(
            CreateTranslationContext=lambda: types.SimpleNamespace(),
            CreateNameValueMap=lambda: types.SimpleNamespace(),
            CreateDataMedium=lambda: types.SimpleNamespace(),
        )
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        if name in {"Quit", "Save", "Close", "Visible"}:
            self.calls.append(name)
            raise AssertionError(f"prohibited application access: {name}")
        raise AttributeError(name)


class InventorProfileExportTests(unittest.TestCase):
    def test_exports_named_bodies_in_canonical_order_with_one_visible_body(self) -> None:
        bodies = [FakeBody("master_1", True), FakeBody("mrf", True), FakeBody("master", True)]
        document = FakeDocument(bodies)
        app = FakeApp(bodies)

        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "profiles"
            outputs = inventor_client.export_obj_profiles(app, document, output_directory)

            self.assertEqual(
                (output_directory / "master.obj", output_directory / "mrf.obj", output_directory / "master_1.obj"),
                outputs,
            )
            self.assertEqual(
                [
                    ((output_directory / "master.obj").resolve(), ("master",)),
                    ((output_directory / "mrf.obj").resolve(), ("mrf",)),
                    ((output_directory / "master_1.obj").resolve(), ("master_1",)),
                ],
                app.translator.exports,
            )
        self.assertEqual([True, False, False], [body.Visible for body in bodies])
        self.assertEqual([], app.calls)
        self.assertEqual([], document.calls)

    def test_rejects_missing_duplicate_and_unexpected_surface_body_names_before_export(self) -> None:
        cases = (
            ("missing", [FakeBody("master"), FakeBody("mrf")]),
            ("duplicate", [FakeBody("master"), FakeBody("master"), FakeBody("mrf"), FakeBody("master_1")]),
            ("unexpected", [FakeBody("master"), FakeBody("mrf"), FakeBody("master_1"), FakeBody("extra")]),
        )
        for expected, bodies in cases:
            with self.subTest(expected=expected):
                document = FakeDocument(bodies)
                app = FakeApp(bodies)
                initial_visibility = [body.Visible for body in bodies]

                with tempfile.TemporaryDirectory() as directory:
                    with self.assertRaisesRegex(RuntimeError, expected):
                        inventor_client.export_obj_profiles(app, document, Path(directory))

                self.assertEqual([], app.translator.exports)
                self.assertEqual(initial_visibility, [body.Visible for body in bodies])
                self.assertEqual([], app.calls)
                self.assertEqual([], document.calls)


if __name__ == "__main__":
    unittest.main()
