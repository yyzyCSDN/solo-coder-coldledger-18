from dataclasses import dataclass
from datetime import datetime
from .clock import utc
@dataclass(frozen=True,order=True)
class Interval:
    start:datetime; end:datetime
    def norm(self):
        s,e=utc(self.start),utc(self.end)
        if e<=s: raise ValueError('non-positive interval')
        return Interval(s,e)
def merge(items):
    rows=sorted((x.norm() for x in items),key=lambda x:x.start)
    out=[]
    for cur in rows:
        if not out or cur.start>out[-1].end: out.append(cur)
        else: out[-1]=Interval(out[-1].start,max(out[-1].end,cur.end))
    return out
def subtract(base,blocks):
    pieces=[base.norm()]
    for b in merge(blocks):
        nxt=[]
        for p in pieces:
            if b.end<=p.start or b.start>=p.end: nxt.append(p); continue
            if p.start<b.start: nxt.append(Interval(p.start,b.start))
            if b.end<p.end: nxt.append(Interval(b.end,p.end))
        pieces=nxt
    return pieces
def union_seconds(items): return sum((x.end-x.start).total_seconds() for x in merge(items))
def contains(items,at): return any(x.norm().start<=utc(at)<x.norm().end for x in items)
