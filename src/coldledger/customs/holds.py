from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class Hold: start:datetime; end:datetime|None; reason:str
def active(holds,at):
    x=at.astimezone(timezone.utc);out=[]
    for h in holds:
        s=h.start.astimezone(timezone.utc);e=h.end.astimezone(timezone.utc) if h.end else None
        if e is not None and e<=s:raise ValueError('bad hold')
        if s<=x and (e is None or x<e):out.append(h)
    return out
def releasable(holds,at):return not active(holds,at)
