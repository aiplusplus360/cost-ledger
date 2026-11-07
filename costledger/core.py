"""Attribute model spend to tenants, in exact decimal cents, and reconcile it.

Two failure modes hide in "cost = tokens * price":

**Floating point.** A million records at $0.0000025 each lose cents when summed
as floats, and lose them differently depending on summation order. This uses
``Decimal`` throughout and only quantises at the reporting boundary.

**Silent zero.** A model that is not in the price table is billed at zero by
most implementations, so a new model ships and its spend disappears. Here an
unknown model is *unpriced*: reported, counted, and never folded into a total
as if it cost nothing.

>>> prices = PriceTable({"gpt": ("2.50", "10.00", "1.25")})
>>> record = UsageRecord("acme", "gpt", input_tokens=1_000_000)
>>> cents = total_cost([record], prices)
>>> str(cents)
'2.50'
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "PriceTable",
    "UsageRecord",
    "cost_of",
    "total_cost",
    "summarise",
    "reconcile",
    "load_usage_csv",
    "load_prices",
    "CENT",
]

CENT = Decimal("0.01")
MICRO = Decimal("0.000001")


def _money(value: object) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class PriceTable:
    """Per-model prices, in dollars per million tokens.

    ``(input, output, cached_input)``. Cached input defaults to the input
    price when omitted, which is wrong for every provider but at least visible.
    """

    prices: Dict[str, Tuple[Decimal, Decimal, Decimal]]

    def __init__(self, prices: Dict[str, Sequence[object]]):
        table: Dict[str, Tuple[Decimal, Decimal, Decimal]] = {}
        for model, row in prices.items():
            if len(row) == 2:
                row = (row[0], row[1], row[0])
            if len(row) != 3:
                raise ValueError("price row for %r must have 2 or 3 values" % model)
            table[model] = tuple(_money(value) for value in row)  # type: ignore[assignment]
        object.__setattr__(self, "prices", table)

    def get(self, model: str) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
        return self.prices.get(model)

    def __contains__(self, model: object) -> bool:
        return model in self.prices


@dataclass(frozen=True)
class UsageRecord:
    """One billable call."""

    tenant: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "cached_tokens"):
            if getattr(self, field_name) < 0:
                raise ValueError("%s must be non-negative" % field_name)
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached tokens cannot exceed input tokens")

    @property
    def billable_input(self) -> int:
        return self.input_tokens - self.cached_tokens


def cost_of(record: UsageRecord, prices: PriceTable) -> Optional[Decimal]:
    """Dollar cost of one record, or None when the model has no price."""
    row = prices.get(record.model)
    if row is None:
        return None
    input_price, output_price, cached_price = row
    cost = (record.billable_input * input_price
            + record.cached_tokens * cached_price
            + record.output_tokens * output_price)
    return cost * MICRO


def total_cost(records: Iterable[UsageRecord], prices: PriceTable,
                quantize: bool = True) -> Decimal:
    """Total dollars, quantised to cents at the end rather than per record."""
    total = Decimal("0")
    for record in records:
        cost = cost_of(record, prices)
        if cost is not None:
            total += cost
    return total.quantize(CENT, rounding=ROUND_HALF_UP) if quantize else total


def summarise(records: Iterable[UsageRecord], prices: PriceTable) -> Dict[str, object]:
    """Per-tenant and per-model spend, plus anything that could not be priced.

    Unknown models are listed under ``unpriced`` with their token counts, so a
    zero in the total is explainable rather than silent.
    """
    by_tenant: Dict[str, Decimal] = {}
    by_model: Dict[str, Decimal] = {}
    tokens: Dict[str, int] = {}
    unpriced: Dict[str, Dict[str, int]] = {}
    count = 0
    for record in records:
        count += 1
        tokens[record.tenant] = tokens.get(record.tenant, 0) + \
            record.input_tokens + record.output_tokens
        cost = cost_of(record, prices)
        if cost is None:
            bucket = unpriced.setdefault(record.model, {"calls": 0, "tokens": 0})
            bucket["calls"] += 1
            bucket["tokens"] += record.input_tokens + record.output_tokens
            continue
        by_tenant[record.tenant] = by_tenant.get(record.tenant, Decimal("0")) + cost
        by_model[record.model] = by_model.get(record.model, Decimal("0")) + cost

    unquantized = sum(by_tenant.values(), Decimal("0"))
    return {
        "calls": count,
        "tenants": {name: str(value.quantize(CENT, rounding=ROUND_HALF_UP))
                    for name, value in sorted(by_tenant.items())},
        "models": {name: str(value.quantize(CENT, rounding=ROUND_HALF_UP))
                   for name, value in sorted(by_model.items())},
        "tokens": dict(sorted(tokens.items())),
        "total": str(unquantized.quantize(CENT, rounding=ROUND_HALF_UP)),
        "unpriced": {name: dict(values) for name, values in sorted(unpriced.items())},
        "complete": not unpriced,
    }


def reconcile(computed: Decimal, invoiced: Decimal, tolerance: Decimal = CENT
              ) -> Dict[str, object]:
    """Compare computed spend with an invoice total.

    Returns ``match`` when the absolute difference is within ``tolerance``,
    otherwise ``over`` (computed above invoice) or ``under``, with the
    direction named rather than implied by a sign.

    >>> reconcile(Decimal("100.00"), Decimal("100.00"))["status"]
    'match'
    >>> reconcile(Decimal("100.00"), Decimal("99.00"))["status"]
    'over'
    """
    difference = computed - invoiced
    if abs(difference) <= tolerance:
        status = "match"
    elif difference > 0:
        status = "over"
    else:
        status = "under"
    return {
        "computed": str(computed.quantize(CENT, rounding=ROUND_HALF_UP)),
        "invoiced": str(invoiced.quantize(CENT, rounding=ROUND_HALF_UP)),
        "difference": str(difference.quantize(CENT, rounding=ROUND_HALF_UP)),
        "status": status,
    }


def load_usage_csv(path: str) -> List[UsageRecord]:
    """Read usage records from CSV with tenant/model/input/output/cached columns."""
    records: List[UsageRecord] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            records.append(UsageRecord(
                tenant=(row.get("tenant") or "default").strip(),
                model=(row.get("model") or "").strip(),
                input_tokens=int(row.get("input_tokens") or 0),
                output_tokens=int(row.get("output_tokens") or 0),
                cached_tokens=int(row.get("cached_tokens") or 0),
            ))
    return records


def load_prices(path: str) -> PriceTable:
    """Load a JSON price table: ``{"model": [input, output, cached]}``."""
    import json
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    table = payload.get("prices", payload)
    return PriceTable({name: list(values) for name, values in table.items()})
