from __future__ import annotations
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Iterable

@dataclass(frozen=True)
class LaneNeed:
    key: str
    demand: Decimal
    priority: int
    group: str
    risk: float = 0.0
    locked: bool = False

    def validate(self) -> None:
        if self.demand <= 0:
            raise ValueError("demand must be positive")
        if not 0.0 <= self.risk <= 1.0:
            raise ValueError("risk outside 0..1")
        if not self.group:
            raise ValueError("group required")

@dataclass(frozen=True)
class CarrierPool:
    key: str
    capacity: Decimal
    group: str
    enabled: bool = True
    reserve: Decimal = Decimal("0")

    @property
    def usable(self) -> Decimal:
        value = self.capacity - self.reserve
        return max(Decimal("0"), value)

@dataclass(frozen=True)
class Allocation:
    demand_key: str
    resource_key: str
    quantity: Decimal
    score: Decimal


def _score(d: LaneNeed, r: CarrierPool, remaining: Decimal) -> Decimal:
    group_penalty = Decimal("0") if d.group == r.group else Decimal("7.5")
    risk_penalty = Decimal(str(d.risk)) * Decimal("10")
    slack = max(Decimal("0"), remaining - d.demand)
    priority_credit = Decimal(d.priority) * Decimal("0.25")
    return slack + group_penalty + risk_penalty - priority_credit


def plan(demands: Iterable[LaneNeed], resources: Iterable[CarrierPool], allow_cross_group: bool = False) -> list[Allocation]:
    ds = list(demands)
    rs = list(resources)
    if not rs and ds:
        raise ValueError("no resources")
    remaining = {r.key: r.usable for r in rs if r.enabled}
    by_key = {r.key: r for r in rs}
    result: list[Allocation] = []
    for d in sorted(ds, key=lambda x: (-x.priority, -x.risk, x.key)):
        d.validate()
        if d.locked:
            continue
        candidates = []
        for rk, left in remaining.items():
            r = by_key[rk]
            if left <= 0:
                continue
            if not allow_cross_group and r.group != d.group:
                continue
            take = min(left, d.demand)
            if take <= 0:
                continue
            candidates.append((_score(d, r, left), r.key, take))
        if not candidates:
            raise ValueError(f"unallocated lane: {d.key}")
        outstanding = d.demand
        for score, rk, take in sorted(candidates, key=lambda x: (x[0], x[1])):
            if outstanding <= 0:
                break
            q = min(outstanding, remaining[rk])
            if q <= 0:
                continue
            result.append(Allocation(d.key, rk, q, score))
            remaining[rk] -= q
            outstanding -= q
        if outstanding > 0:
            raise ValueError(f"insufficient carrier capacity for {d.key}")
    return result


def summarize(rows: Iterable[Allocation]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        totals[row.resource_key] = totals.get(row.resource_key, Decimal("0")) + row.quantity
    return totals


def validate_plan(rows: Iterable[Allocation], demands: Iterable[LaneNeed], resources: Iterable[CarrierPool]) -> None:
    allocations = list(rows)
    demand_map = {d.key: d for d in demands}
    resource_map = {r.key: r for r in resources}
    by_demand: dict[str, Decimal] = {}
    by_resource: dict[str, Decimal] = {}
    for row in allocations:
        if row.demand_key not in demand_map or row.resource_key not in resource_map:
            raise ValueError("plan references unknown key")
        if row.quantity <= 0:
            raise ValueError("allocation must be positive")
        by_demand[row.demand_key] = by_demand.get(row.demand_key, Decimal("0")) + row.quantity
        by_resource[row.resource_key] = by_resource.get(row.resource_key, Decimal("0")) + row.quantity
    for key, d in demand_map.items():
        if d.locked:
            continue
        if by_demand.get(key, Decimal("0")) != d.demand:
            raise ValueError("demand not exactly satisfied")
    for key, used in by_resource.items():
        if used > resource_map[key].usable:
            raise ValueError("resource over capacity")


def rebalance(rows: list[Allocation], disabled_resource: str, demands: list[LaneNeed], resources: list[CarrierPool]) -> list[Allocation]:
    if disabled_resource not in {r.key for r in resources}:
        return rows
    impacted = {r.demand_key for r in rows if r.resource_key == disabled_resource}
    keep = [r for r in rows if r.demand_key not in impacted]
    reserved = summarize(keep)
    adjusted_resources = []
    for r in resources:
        enabled = r.enabled and r.key != disabled_resource
        extra_reserve = reserved.get(r.key, Decimal("0"))
        adjusted_resources.append(replace(r, enabled=enabled, reserve=r.reserve + extra_reserve))
    retry = [d for d in demands if d.key in impacted]
    return keep + plan(retry, adjusted_resources, allow_cross_group=True)


def explain(rows: Iterable[Allocation]) -> list[str]:
    return [f"{r.demand_key} -> {r.resource_key}: {r.quantity}" for r in rows]

def carrier_mix(rows):
    return len({r.resource_key for r in rows})
