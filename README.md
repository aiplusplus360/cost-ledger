# cost-ledger

**Attribute model spend per tenant in exact decimal cents, name every model you could not price, and reconcile the total against the invoice.**

[![CI](https://github.com/aiplusplus360/cost-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/aiplusplus360/cost-ledger/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](pyproject.toml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

```console
$ cost-ledger report usage.csv --prices prices.json
calls: 148213   total: $412.77
  acme             $301.14
  beta             $98.20
  internal         $13.43
unpriced models:
  gpt-5-turbo      204 call(s), 1830004 token(s)
```

The total is $412.77 **and** it does not include 204 calls. That is the point.

---

## Why this exists

`cost = tokens * price_per_million` is a one-liner with two production bugs.

**Floating point.** A million records at $0.0000025 each do not sum to $2.50 in `float`. They sum to something whose error depends on insertion order, so the same month reconciles against the invoice on one machine and is off by cents on another — which is exactly the amount a finance reconciliation flags.

**The silent zero.** A model missing from the price table becomes `$0.00`. No error, no warning; a newly deployed model's spend simply vanishes from the dashboard until someone notices the invoice is bigger than the report.

Neither is a maths problem. Both are a choice about types and about what "unknown" means.

## Decimal, quantised once

Cost is accumulated as `Decimal`, and quantised to cents **once**, at the boundary:

```python
total = Σ cost_of(record)          # Decimal, exact
total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

Quantising per record rounds a million times in one direction and produces a total that is wrong by a visible amount. Quantising once produces a total that matches arithmetic.

## Unknown is not zero

`cost_of` returns `None` for an unpriced model, never `Decimal("0")`. `summarise` puts those models in an `unpriced` bucket with their call and token counts, and sets `complete: false`. `--strict` turns that into a failing exit code, so a price table that has drifted from the model catalogue breaks CI instead of the monthly close.

## Cached tokens are priced differently

Providers charge a reduced rate for cached input. A record carries `cached_tokens`, and the cost is:

```
(billable_input × input_price) + (cached_tokens × cached_price) + (output × output_price)
```

Ignoring the cached rate overbills by the difference on every cached call, which on a RAG workload is most of them.

## Reconciliation

```bash
$ cost-ledger reconcile usage.csv --prices prices.json --invoice 412.77
computed: $412.77   invoiced: $412.77   difference: $0.00   [match]
```

Status is `match`, `over`, or `under` — named, not a sign the reader has to interpret — and the exit code is 1 when it does not match, so this can run monthly in CI.

## Install and use

No dependencies. Python 3.9+.

```bash
pip install git+https://github.com/aiplusplus360/cost-ledger
```

```bash
cost-ledger report usage.csv --prices prices.json --strict
cost-ledger report usage.csv --prices prices.json --json
cost-ledger reconcile usage.csv --prices prices.json --invoice 412.77 --tolerance 0.05
```

**usage.csv**

```csv
tenant,model,input_tokens,output_tokens,cached_tokens
acme,gpt,1000000,0,0
```

**prices.json**

```json
{"gpt": [2.50, 10.00, 1.25], "mini": [0.15, 0.60, 0.075]}
```

Prices are dollars per million tokens: `[input, output, cached_input]`. A two-value row reuses the input price for cached tokens, which is wrong for every provider and therefore visible.

## Library use

```python
from decimal import Decimal
from costledger import PriceTable, UsageRecord, summarise, reconcile

prices = PriceTable({"gpt": ("2.50", "10.00", "1.25")})
report = summarise(records, prices)
assert report["complete"], report["unpriced"]

print(reconcile(Decimal(report["total"]), Decimal("412.77")))
```

## Tests

```bash
python -m unittest discover -s tests -v
```

24 core tests and 6 CLI tests: two-value price rows, negative-token and cached-over-input rejection, cached-rate arithmetic, exact summation over 2,000,000 tokens, unpriced reporting, tenant/model grouping, all three reconciliation directions, and CSV/JSON loading.
