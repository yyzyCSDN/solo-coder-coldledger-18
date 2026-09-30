from dataclasses import dataclass
from collections import defaultdict,deque
@dataclass(frozen=True)
class Link: parent:str; child:str; quantity:float
def affected(seeds,links):
    f=defaultdict(set)
    for l in links:
        if l.quantity<=0: raise ValueError('positive quantity required')
        f[l.parent].add(l.child)
    seen=set(seeds);q=deque(seeds)
    while q:
        cur=q.popleft()
        for child in f[cur]:
            if child not in seen:seen.add(child);q.append(child)
    return seen
def sources(unit,links):
    r=defaultdict(set)
    for l in links:r[l.child].add(l.parent)
    out=set();q=deque([unit]);seen=set()
    while q:
        cur=q.popleft()
        if cur in seen:continue
        seen.add(cur)
        if not r[cur]:out.add(cur)
        else:q.extend(r[cur])
    return out
