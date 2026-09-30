from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class Evidence: start:datetime;end:datetime;complete:bool;score:float
@dataclass(frozen=True)
class Case: shipped:datetime;returned:datetime;sealed:bool;evidence:tuple
def may_reintegrate(case,max_score):
    if not case.sealed:return False
    start,end=case.shipped.astimezone(timezone.utc),case.returned.astimezone(timezone.utc)
    if end<=start:return False
    cursor=start;total=0
    for e in sorted(case.evidence,key=lambda x:x.start):
        if not e.complete:return False
        s,t=e.start.astimezone(timezone.utc),e.end.astimezone(timezone.utc)
        if t<=s or s>cursor:return False
        cursor=max(cursor,t);total+=e.score
    return cursor>=end and total<=max_score
