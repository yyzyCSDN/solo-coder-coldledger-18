from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

@dataclass(frozen=True)
class CalibrationJob:
    key: str
    earliest: datetime
    latest: datetime
    duration: timedelta
    zone: str
    priority: int = 0
    setup_group: str = "default"

    def validate(self) -> None:
        if self.duration <= timedelta():
            raise ValueError("duration must be positive")
        if self.latest <= self.earliest:
            raise ValueError("invalid scheduling window")

@dataclass(frozen=True)
class Bench:
    key: str
    zone: str
    available_from: datetime
    available_until: datetime
    setup_minutes: int = 0

@dataclass(frozen=True)
class Assignment:
    item_key: str
    resource_key: str
    start: datetime
    end: datetime


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return v.astimezone(timezone.utc)


def build(items: Iterable[CalibrationJob], resources: Iterable[Bench]) -> list[Assignment]:
    jobs = list(items)
    rs = list(resources)
    free = {r.key: _utc(r.available_from) for r in rs}
    previous_group: dict[str, str] = {}
    by_key = {r.key: r for r in rs}
    result: list[Assignment] = []
    for job in sorted(jobs, key=lambda j: (-j.priority, _utc(j.latest), _utc(j.earliest), j.key)):
        job.validate()
        choices = []
        for r in rs:
            if r.zone != job.zone:
                continue
            setup = timedelta(minutes=r.setup_minutes if previous_group.get(r.key) not in (None, job.setup_group) else 0)
            start = max(_utc(job.earliest), free[r.key] + setup, _utc(r.available_from))
            end = start + job.duration
            if end <= min(_utc(job.latest), _utc(r.available_until)):
                idle = (start - free[r.key]).total_seconds()
                choices.append((idle, start, r.key, end))
        if not choices:
            raise ValueError(f"cannot schedule {job.key}")
        _, start, rk, end = min(choices, key=lambda x: (x[0], x[1], x[2]))
        result.append(Assignment(job.key, rk, start, end))
        free[rk] = end
        previous_group[rk] = job.setup_group
    return result


def conflicts(assignments: Iterable[Assignment]) -> list[tuple[str, str]]:
    by_resource: dict[str, list[Assignment]] = {}
    for a in assignments:
        by_resource.setdefault(a.resource_key, []).append(a)
    out = []
    for rows in by_resource.values():
        rows.sort(key=lambda x: x.start)
        for a, b in zip(rows, rows[1:]):
            if b.start < a.end:
                out.append((a.item_key, b.item_key))
    return out


def utilization(assignments: Iterable[Assignment], resources: Iterable[Bench]) -> dict[str, float]:
    rows = list(assignments)
    result = {}
    for r in resources:
        window = (_utc(r.available_until) - _utc(r.available_from)).total_seconds()
        used = sum((a.end - a.start).total_seconds() for a in rows if a.resource_key == r.key)
        result[r.key] = 0.0 if window <= 0 else min(1.0, used / window)
    return result


def shift(assignments: Iterable[Assignment], item_key: str, delta: timedelta) -> list[Assignment]:
    out = []
    for a in assignments:
        if a.item_key == item_key:
            out.append(Assignment(a.item_key, a.resource_key, a.start + delta, a.end + delta))
        else:
            out.append(a)
    if conflicts(out):
        raise ValueError("shift creates conflict")
    return out
