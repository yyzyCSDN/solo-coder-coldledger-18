from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict, deque
from typing import Iterable

@dataclass(frozen=True)
class GenealogyEdge:
    source: str
    target: str
    weight: float = 1.0
    kind: str = "direct"


def adjacency(edges: Iterable[GenealogyEdge]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e.weight < 0:
            raise ValueError("negative weight")
        if e.source == e.target:
            raise ValueError("self edge")
        out[e.source].add(e.target)
    return out


def reachable(seeds: Iterable[str], edges: Iterable[GenealogyEdge], max_depth: int | None = None) -> set[str]:
    graph = adjacency(edges)
    seen = set(seeds)
    q = deque((s, 0) for s in seeds)
    while q:
        cur, depth = q.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for nxt in graph[cur]:
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, depth + 1))
    return seen


def shortest_hops(source: str, target: str, edges: Iterable[GenealogyEdge]) -> list[str] | None:
    graph = adjacency(edges)
    q = deque([source])
    previous: dict[str, str | None] = {source: None}
    while q:
        cur = q.popleft()
        if cur == target:
            path = []
            while cur is not None:
                path.append(cur)
                cur = previous[cur]
            return list(reversed(path))
        for nxt in sorted(graph[cur]):
            if nxt not in previous:
                previous[nxt] = cur
                q.append(nxt)
    return None


def reverse_index(edges: Iterable[GenealogyEdge]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        out[e.target].add(e.source)
    return out


def roots(nodes: Iterable[str], edges: Iterable[GenealogyEdge]) -> set[str]:
    nodes = set(nodes)
    incoming = reverse_index(edges)
    return {n for n in nodes if not incoming.get(n)}


def topological(nodes: Iterable[str], edges: Iterable[GenealogyEdge]) -> list[str]:
    nodes = set(nodes)
    graph = adjacency(edges)
    incoming = {n: 0 for n in nodes}
    for src, targets in graph.items():
        incoming.setdefault(src, 0)
        for t in targets:
            incoming[t] = incoming.get(t, 0) + 1
    q = deque(sorted(n for n, d in incoming.items() if d == 0))
    out = []
    while q:
        cur = q.popleft()
        out.append(cur)
        for nxt in sorted(graph[cur]):
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                q.append(nxt)
    if len(out) != len(incoming):
        raise ValueError("cycle detected")
    return out


def weighted_exposure(seeds: Iterable[str], edges: Iterable[GenealogyEdge]) -> dict[str, float]:
    rows = list(edges)
    order = topological({e.source for e in rows} | {e.target for e in rows} | set(seeds), rows)
    outgoing: dict[str, list[GenealogyEdge]] = defaultdict(list)
    for e in rows:
        outgoing[e.source].append(e)
    score = {n: 0.0 for n in order}
    for s in seeds:
        score[s] = 1.0
    for n in order:
        for e in outgoing[n]:
            score[e.target] = max(score[e.target], score[n] * e.weight)
    return score
