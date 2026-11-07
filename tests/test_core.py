import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from costledger.core import (
    PriceTable,
    UsageRecord,
    cost_of,
    load_prices,
    load_usage_csv,
    reconcile,
    summarise,
    total_cost,
)

PRICES = PriceTable({
    "gpt": ("2.50", "10.00", "1.25"),
    "mini": ("0.15", "0.60", "0.075"),
})


class PriceTests(unittest.TestCase):
    def test_two_value_row_defaults_cached_to_input(self):
        table = PriceTable({"m": ("1.00", "2.00")})
        self.assertEqual(table.get("m"), (Decimal("1.00"), Decimal("2.00"), Decimal("1.00")))

    def test_bad_row_length(self):
        with self.assertRaises(ValueError):
            PriceTable({"m": ("1.00",)})

    def test_contains(self):
        self.assertIn("gpt", PRICES)
        self.assertNotIn("unknown", PRICES)


class RecordTests(unittest.TestCase):
    def test_negative_tokens_rejected(self):
        with self.assertRaises(ValueError):
            UsageRecord("t", "gpt", input_tokens=-1)

    def test_cached_cannot_exceed_input(self):
        with self.assertRaises(ValueError):
            UsageRecord("t", "gpt", input_tokens=10, cached_tokens=11)

    def test_billable_input(self):
        self.assertEqual(UsageRecord("t", "gpt", input_tokens=100, cached_tokens=40)
                         .billable_input, 60)


class CostTests(unittest.TestCase):
    def test_one_million_input_tokens(self):
        cost = cost_of(UsageRecord("t", "gpt", input_tokens=1_000_000), PRICES)
        self.assertEqual(cost, Decimal("2.50"))

    def test_output_is_priced_higher(self):
        record = UsageRecord("t", "gpt", output_tokens=1_000_000)
        self.assertEqual(cost_of(record, PRICES), Decimal("10.00"))

    def test_cached_tokens_use_cached_price(self):
        record = UsageRecord("t", "gpt", input_tokens=1_000_000, cached_tokens=500_000)
        self.assertEqual(cost_of(record, PRICES), Decimal("1.875"))

    def test_unknown_model_is_none_not_zero(self):
        self.assertIsNone(cost_of(UsageRecord("t", "mystery", input_tokens=10), PRICES))

    def test_total_quantises_once(self):
        records = [UsageRecord("t", "mini", input_tokens=1) for _ in range(20_000)]
        total = total_cost(records, PRICES)
        # 20,000 tokens at $0.15/Mtok = $0.003 exactly, reported as zero cents.
        self.assertEqual(total, Decimal("0.00"))
        self.assertEqual(total.as_tuple().exponent, -2)

    def test_many_small_records_sum_exactly(self):
        # 2,000,000 tokens at $0.15/Mtok = $0.30 exactly. Float summation drifts.
        records = [UsageRecord("t", "mini", input_tokens=100_000) for _ in range(20)]
        self.assertEqual(total_cost(records, PRICES), Decimal("0.30"))

    def test_total_skips_unpriced(self):
        records = [UsageRecord("t", "gpt", input_tokens=1_000_000),
                   UsageRecord("t", "mystery", input_tokens=1_000_000)]
        self.assertEqual(total_cost(records, PRICES), Decimal("2.50"))


class SummariseTests(unittest.TestCase):
    def test_groups_by_tenant_and_model(self):
        records = [
            UsageRecord("acme", "gpt", input_tokens=1_000_000),
            UsageRecord("acme", "mini", input_tokens=1_000_000),
            UsageRecord("beta", "gpt", output_tokens=1_000_000),
        ]
        report = summarise(records, PRICES)
        self.assertEqual(report["tenants"]["acme"], "2.65")
        self.assertEqual(report["tenants"]["beta"], "10.00")
        self.assertEqual(report["total"], "12.65")

    def test_unpriced_is_reported(self):
        report = summarise([UsageRecord("acme", "mystery", input_tokens=500)], PRICES)
        self.assertFalse(report["complete"])
        self.assertEqual(report["unpriced"]["mystery"]["tokens"], 500)
        self.assertEqual(report["unpriced"]["mystery"]["calls"], 1)

    def test_complete_when_all_priced(self):
        report = summarise([UsageRecord("acme", "gpt", input_tokens=10)], PRICES)
        self.assertTrue(report["complete"])
        self.assertEqual(report["unpriced"], {})

    def test_call_count(self):
        report = summarise([UsageRecord("a", "gpt")] * 3, PRICES)
        self.assertEqual(report["calls"], 3)


class ReconcileTests(unittest.TestCase):
    def test_match(self):
        self.assertEqual(reconcile(Decimal("10.00"), Decimal("10.00"))["status"], "match")

    def test_within_tolerance(self):
        result = reconcile(Decimal("10.00"), Decimal("10.005"), tolerance=Decimal("0.01"))
        self.assertEqual(result["status"], "match")

    def test_over(self):
        result = reconcile(Decimal("10.00"), Decimal("9.00"))
        self.assertEqual(result["status"], "over")
        self.assertEqual(result["difference"], "1.00")

    def test_under(self):
        result = reconcile(Decimal("9.00"), Decimal("10.00"))
        self.assertEqual(result["status"], "under")
        self.assertEqual(result["difference"], "-1.00")


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_usage_csv(self):
        path = self.root / "usage.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["tenant", "model", "input_tokens", "output_tokens", "cached_tokens"])
            writer.writerow(["acme", "gpt", "100", "20", "10"])
        records = load_usage_csv(str(path))
        self.assertEqual(records[0], UsageRecord("acme", "gpt", 100, 20, 10))

    def test_usage_csv_defaults_tenant(self):
        path = self.root / "usage.csv"
        path.write_text("model,input_tokens\ngpt,10\n", encoding="utf-8")
        self.assertEqual(load_usage_csv(str(path))[0].tenant, "default")

    def test_prices_json(self):
        path = self.root / "prices.json"
        path.write_text(json.dumps({"prices": {"gpt": [2.5, 10.0, 1.25]}}), encoding="utf-8")
        self.assertEqual(load_prices(str(path)).get("gpt"),
                         (Decimal("2.5"), Decimal("10.0"), Decimal("1.25")))


if __name__ == "__main__":
    unittest.main()
