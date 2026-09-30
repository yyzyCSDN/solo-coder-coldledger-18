from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

@dataclass(frozen=True)
class Rule:
    key: str
    operator: str
    value: str
    priority: int
    effective_at: datetime
    expires_at: datetime | None = None
    scope: str = "*"

@dataclass(frozen=True)
class Decision:
    key: str
    allowed: bool
    matched_rule: Rule | None
    explanation: str

class CustomsPolicy:
    def __init__(self, rules: Iterable[Rule]):
        self.rules = list(rules)
        self._validate()

    def _validate(self) -> None:
        for r in self.rules:
            if r.operator not in {"eq","ne","in","not_in","gte","lte"}:
                raise ValueError("unsupported operator")
            if r.expires_at is not None and r.expires_at <= r.effective_at:
                raise ValueError("rule expires before effective time")

    def effective_rules(self, at: datetime, scope: str) -> list[Rule]:
        x = at.astimezone(timezone.utc)
        rows = []
        for r in self.rules:
            start = r.effective_at.astimezone(timezone.utc)
            end = r.expires_at.astimezone(timezone.utc) if r.expires_at else None
            if start <= x and (end is None or x < end) and r.scope in {"*", scope}:
                rows.append(r)
        return sorted(rows, key=lambda r: (-r.priority, r.scope == "*", r.key))

    @staticmethod
    def _compare(actual, rule: Rule) -> bool:
        if rule.operator == "eq": return str(actual) == rule.value
        if rule.operator == "ne": return str(actual) != rule.value
        if rule.operator == "in": return str(actual) in {x.strip() for x in rule.value.split(",")}
        if rule.operator == "not_in": return str(actual) not in {x.strip() for x in rule.value.split(",")}
        if rule.operator == "gte": return float(actual) >= float(rule.value)
        if rule.operator == "lte": return float(actual) <= float(rule.value)
        raise AssertionError(rule.operator)

    def decide(self, key: str, actual, at: datetime, scope: str) -> Decision:
        candidates = [r for r in self.effective_rules(at, scope) if r.key == key]
        if not candidates:
            return Decision(key, False, None, "no effective rule for customs clearance")
        winner = candidates[0]
        allowed = self._compare(actual, winner)
        explanation = f"{winner.operator} {winner.value} under {winner.scope} priority {winner.priority}"
        return Decision(key, allowed, winner, explanation)

    def evaluate_all(self, facts: dict[str, object], at: datetime, scope: str) -> dict[str, Decision]:
        keys = {r.key for r in self.effective_rules(at, scope)}
        return {k: self.decide(k, facts.get(k), at, scope) for k in sorted(keys)}

    def allowed(self, facts: dict[str, object], at: datetime, scope: str) -> bool:
        decisions = self.evaluate_all(facts, at, scope)
        return bool(decisions) and all(d.allowed for d in decisions.values())

    def explain_failures(self, facts: dict[str, object], at: datetime, scope: str) -> list[str]:
        return [f"{k}: {d.explanation}" for k,d in self.evaluate_all(facts,at,scope).items() if not d.allowed]

    def overlay(self, extra: Iterable[Rule]) -> "CustomsPolicy":
        return CustomsPolicy([*self.rules, *list(extra)])


def conflicts(rules: Iterable[Rule]) -> list[tuple[Rule, Rule]]:
    rows=list(rules);out=[]
    for i,a in enumerate(rows):
        for b in rows[i+1:]:
            if a.key!=b.key or a.scope!=b.scope or a.priority!=b.priority: continue
            a_end=a.expires_at or datetime.max.replace(tzinfo=timezone.utc)
            b_end=b.expires_at or datetime.max.replace(tzinfo=timezone.utc)
            if max(a.effective_at,b.effective_at)<min(a_end,b_end) and (a.operator,a.value)!=(b.operator,b.value):out.append((a,b))
    return out
