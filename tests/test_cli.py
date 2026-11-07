import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from costledger.cli import main


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.usage = self.root / "usage.csv"
        self.usage.write_text(
            "tenant,model,input_tokens,output_tokens,cached_tokens\n"
            "acme,gpt,1000000,0,0\n"
            "beta,mini,2000000,0,0\n",
            encoding="utf-8")
        self.prices = self.root / "prices.json"
        self.prices.write_text(json.dumps({"gpt": [2.5, 10.0, 1.25],
                                           "mini": [0.15, 0.6, 0.075]}),
                               encoding="utf-8")

    def test_report_json(self):
        code, out, _ = run(["report", str(self.usage), "--prices", str(self.prices),
                            "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["tenants"]["acme"], "2.50")
        self.assertEqual(payload["total"], "2.80")

    def test_report_plain(self):
        code, out, _ = run(["report", str(self.usage), "--prices", str(self.prices)])
        self.assertEqual(code, 0)
        self.assertIn("acme", out)

    def test_strict_flags_unpriced(self):
        usage = self.root / "u.csv"
        usage.write_text("tenant,model,input_tokens\nacme,mystery,10\n", encoding="utf-8")
        code, _, _ = run(["report", str(usage), "--prices", str(self.prices),
                          "--strict"])
        self.assertEqual(code, 2)

    def test_reconcile_match(self):
        code, out, _ = run(["reconcile", str(self.usage), "--prices", str(self.prices),
                            "--invoice", "2.80", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "match")

    def test_reconcile_mismatch_exits_one(self):
        code, out, _ = run(["reconcile", str(self.usage), "--prices", str(self.prices),
                            "--invoice", "3.00", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "under")

    def test_missing_file(self):
        code, _, err = run(["report", str(self.root / "nope.csv"),
                            "--prices", str(self.prices)])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()
