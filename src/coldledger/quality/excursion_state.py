from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable

STATES = ['detected', 'triaged', 'investigating', 'adjudicated', 'closed']

@dataclass(frozen=True)
class ExcursionState:
    reference: str
    state: str
    revision: int = 0
    owner: str | None = None
    updated_at: datetime | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    def validate(self) -> None:
        if self.state not in STATES:
            raise ValueError("unknown state")
        if self.revision < 0:
            raise ValueError("negative revision")

@dataclass(frozen=True)
class Command:
    reference: str
    expected_revision: int
    target: str
    actor: str
    at: datetime
    reason: str = ""

@dataclass(frozen=True)
class Transition:
    reference: str
    before: str
    after: str
    revision: int
    actor: str
    at: datetime
    reason: str

ALLOWED = {
    STATES[i]: frozenset(STATES[i + 1:i + 3])
    for i in range(len(STATES))
}
if STATES:
    ALLOWED[STATES[-1]] = frozenset()


def apply(current: ExcursionState, command: Command) -> tuple[ExcursionState, Transition]:
    current.validate()
    if command.reference != current.reference:
        raise ValueError("reference mismatch")
    if command.expected_revision != current.revision:
        raise RuntimeError("optimistic concurrency conflict")
    if command.target not in STATES:
        raise ValueError("invalid target state")
    if command.target == current.state:
        raise ValueError("no-op transition")
    allowed = ALLOWED.get(current.state, frozenset())
    if command.target not in allowed:
        raise ValueError(f"transition {current.state} -> {command.target} not allowed")
    at = command.at.astimezone(timezone.utc)
    previous_at = current.updated_at.astimezone(timezone.utc) if current.updated_at else None
    if previous_at is not None and at < previous_at:
        raise ValueError("event time moved backward")
    nxt = replace(current, state=command.target, revision=current.revision + 1, owner=command.actor, updated_at=at)
    event = Transition(current.reference, current.state, command.target, nxt.revision, command.actor, at, command.reason)
    return nxt, event


def replay(initial: ExcursionState, events: Iterable[Transition]) -> ExcursionState:
    state = initial
    expected = initial.revision + 1
    for event in sorted(events, key=lambda e: e.revision):
        if event.reference != state.reference:
            raise ValueError("mixed references")
        if event.revision != expected:
            raise ValueError("revision gap")
        if event.before != state.state:
            raise ValueError("event before-state mismatch")
        if event.after not in ALLOWED.get(state.state, frozenset()):
            raise ValueError("event contains illegal transition")
        state = replace(state, state=event.after, revision=event.revision, owner=event.actor, updated_at=event.at)
        expected += 1
    return state


def can_transition(current: ExcursionState, target: str) -> bool:
    return target in ALLOWED.get(current.state, frozenset())


def next_states(current: ExcursionState) -> tuple[str, ...]:
    return tuple(sorted(ALLOWED.get(current.state, frozenset())))


def state_age_seconds(current: ExcursionState, now: datetime) -> float | None:
    if current.updated_at is None:
        return None
    value = (now.astimezone(timezone.utc) - current.updated_at.astimezone(timezone.utc)).total_seconds()
    return max(0.0, value)


def with_attribute(current: ExcursionState, key: str, value: str) -> ExcursionState:
    data = dict(current.attributes)
    data[key] = value
    return replace(current, attributes=tuple(sorted(data.items())))


def attribute(current: ExcursionState, key: str, default: str | None = None) -> str | None:
    return dict(current.attributes).get(key, default)
