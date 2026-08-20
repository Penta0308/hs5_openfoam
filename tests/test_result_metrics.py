from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.result_metrics import RESULT_FIELDS, extract_metrics, make_result, write_result


def scalar_field(internal: str, boundaries: str = "") -> str:
    return (
        "FoamFile\n{\n    format ascii;\n}\n"
        f"internalField nonuniform List<scalar> 3\n(\n{internal}\n)\n;\n"
        f"boundaryField\n{{\n{boundaries}}}\n"
    )


def patch(name: str, values: str, count: int = 2) -> str:
    return (
        f"{name}\n{{\n    value nonuniform List<scalar> {count}\n"
        f"    (\n    {values}\n    )\n    ;\n}}\n"
    )


class ResultMetricTests(unittest.TestCase):
    def test_known_ascii_fields_emit_compact_result_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            fluid = case / "1000" / "fluid"
            aluminum = case / "1000" / "aluminum"
            fluid.mkdir(parents=True)
            aluminum.mkdir()
            (fluid / "T").write_text(scalar_field("293.15\n310\n301"), encoding="utf-8")
            (aluminum / "T").write_text(scalar_field("293.15\n325\n310"), encoding="utf-8")
            (fluid / "p").write_text(
                scalar_field("1\n2\n3", patch("patch_inlet", "105\n107") + patch("patch_outlet", "100\n100")),
                encoding="utf-8",
            )
            (fluid / "phi").write_text(
                scalar_field("1\n2\n3", patch("patch_inlet", "-0.01\n-0.02")), encoding="utf-8"
            )
            properties = case / "constant" / "fluid"
            properties.mkdir(parents=True)
            (properties / "thermophysicalProperties").write_text(
                "FoamFile { format ascii; }\nequationOfState { rho 1.2; }\n", encoding="utf-8"
            )

            metrics = extract_metrics(case, "1000")
            result = make_result(True, True, metrics)
            output = case / "result.json"
            write_result(output, result)

            self.assertEqual(metrics, {
                "fluidTmaxK": 310.0,
                "solidTmaxK": 325.0,
                "pressureDropPa": 6.0,
                "massFlowKgPerS": 0.036,
            })
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {
                "flowConverged": True,
                "thermalConverged": True,
                "fluidTmaxK": 310.0,
                "solidTmaxK": 325.0,
                "pressureDropPa": 6.0,
                "massFlowKgPerS": 0.036,
                "status": "success",
            })

    def test_missing_or_invalid_fields_emit_null_metrics_and_non_success_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            fluid = case / "1000" / "fluid"
            aluminum = case / "1000" / "aluminum"
            fluid.mkdir(parents=True)
            aluminum.mkdir()
            (aluminum / "T").write_text(scalar_field("293\nnot-a-number\n310"), encoding="utf-8")
            (fluid / "p").write_text(scalar_field("1\n2\n3", patch("patch_inlet", "100", count=2)), encoding="utf-8")

            metrics = extract_metrics(case, "1000")
            result = make_result(True, True, metrics)
            output = case / "result.json"
            write_result(output, result)

            self.assertEqual(metrics, {
                "fluidTmaxK": None,
                "solidTmaxK": None,
                "pressureDropPa": None,
                "massFlowKgPerS": None,
            })
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(tuple(payload), RESULT_FIELDS)
            self.assertEqual(payload["status"], "non_converged")
            self.assertTrue(all(payload[name] is None for name in RESULT_FIELDS[2:6]))


if __name__ == "__main__":
    unittest.main()
