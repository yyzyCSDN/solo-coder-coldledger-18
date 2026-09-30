from __future__ import annotations
from dataclasses import dataclass
from statistics import median
from math import exp
from typing import Iterable

@dataclass(frozen=True)
class ReleaseRiskCase:
    reference: str
    severity: float
    age_hours: float
    value: float
    uncertainty: float
    flags: frozenset[str] = frozenset()

    def validate(self) -> None:
        if self.severity < 0 or self.age_hours < 0 or self.value < 0:
            raise ValueError("negative input")
        if not 0 <= self.uncertainty <= 1:
            raise ValueError("uncertainty outside 0..1")


def raw_score(row: ReleaseRiskCase) -> float:
    row.validate()
    flag_weight = sum({"critical": 35.0, "regulated": 18.0, "customer": 9.0, "repeat": 7.0}.get(f, 1.5) for f in row.flags)
    age_component = min(row.age_hours, 240.0) ** 0.72
    value_component = min(row.value / 10000.0, 50.0)
    uncertainty_penalty = row.uncertainty * 12.0
    return row.severity * 22.0 + age_component + value_component + flag_weight - uncertainty_penalty


def normalized_probability(row: ReleaseRiskCase) -> float:
    score = raw_score(row)
    z = (score - 50.0) / 18.0
    return 1.0 / (1.0 + exp(-z))


def rank(rows: Iterable[ReleaseRiskCase]) -> list[ReleaseRiskCase]:
    return sorted(rows, key=lambda r: (-raw_score(r), r.reference))


def percentile_threshold(rows: Iterable[ReleaseRiskCase], fraction: float) -> float:
    if not 0 < fraction <= 1:
        raise ValueError("fraction outside (0,1]")
    scores = sorted(raw_score(r) for r in rows)
    if not scores:
        return 0.0
    index = max(0, min(len(scores) - 1, int((len(scores) - 1) * fraction)))
    return scores[index]


def robust_center(rows: Iterable[ReleaseRiskCase]) -> float:
    scores = [raw_score(r) for r in rows]
    return median(scores) if scores else 0.0


def buckets(rows: Iterable[ReleaseRiskCase]) -> dict[str, list[str]]:
    out = {"low": [], "medium": [], "high": [], "critical": []}
    for r in rank(rows):
        p = normalized_probability(r)
        key = "critical" if p >= .9 else "high" if p >= .7 else "medium" if p >= .4 else "low"
        out[key].append(r.reference)
    return out


def reserve_cost(rows: Iterable[ReleaseRiskCase], unit_cost: float) -> float:
    if unit_cost < 0:
        raise ValueError("negative unit cost")
    return sum(normalized_probability(r) * unit_cost * (1 + r.severity / 10.0) for r in rows)
