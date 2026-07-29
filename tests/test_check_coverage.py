import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.check_coverage import check_coverage


class CheckCoverageTests(unittest.TestCase):
    def _write_report(self, directory: str, totals) -> Path:
        report = Path(directory) / "coverage.json"
        report.write_text(json.dumps({"totals": totals}), encoding="utf-8")
        return report

    def test_check_coverage_accepts_independent_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._write_report(
                tmp,
                {
                    "percent_statements_covered": 87.5,
                    "percent_branches_covered": 77.1,
                },
            )
            self.assertEqual(check_coverage(report, 85, 75), (87.5, 77.1))

    def test_check_coverage_rejects_each_low_metric(self):
        cases = [
            (84.9, 80.0, "statement coverage"),
            (90.0, 74.9, "branch coverage"),
        ]
        for statements, branches, expected in cases:
            with tempfile.TemporaryDirectory() as tmp, self.subTest(expected=expected):
                report = self._write_report(
                    tmp,
                    {
                        "percent_statements_covered": statements,
                        "percent_branches_covered": branches,
                    },
                )
                with self.assertRaisesRegex(ValueError, expected):
                    check_coverage(report, 85, 75)

    def test_check_coverage_rejects_missing_or_invalid_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaisesRegex(ValueError, "unable to read"):
                check_coverage(directory / "missing.json", 85, 75)

            invalid = directory / "invalid.json"
            invalid.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid coverage JSON"):
                check_coverage(invalid, 85, 75)

            missing_totals = directory / "missing-totals.json"
            missing_totals.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "totals mapping"):
                check_coverage(missing_totals, 85, 75)

            missing_metric = self._write_report(tmp, {})
            with self.assertRaisesRegex(ValueError, "missing numeric field"):
                check_coverage(missing_metric, 85, 75)

    def test_cli_reports_measured_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._write_report(
                tmp,
                {
                    "percent_statements_covered": 87.5,
                    "percent_branches_covered": 77.1,
                },
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_coverage.py",
                    str(report),
                    "--min-statements",
                    "85",
                    "--min-branches",
                    "75",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("statement coverage: 87.50%", result.stdout)
        self.assertIn("branch coverage: 77.10%", result.stdout)


if __name__ == "__main__":
    unittest.main()
