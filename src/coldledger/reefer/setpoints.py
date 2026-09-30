from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class Instruction: start:datetime;end:datetime;setpoint:float;priority:int;revision:int
def at(instructions,when):
    x=when.astimezone(timezone.utc);rows=[]
    for i in instructions:
        s,e=i.start.astimezone(timezone.utc),i.end.astimezone(timezone.utc)
        if e<=s:raise ValueError('bad interval')
        if s<=x<e:rows.append(i)
    if not rows:return None
    return max(rows,key=lambda i:(i.priority,i.revision,i.start.astimezone(timezone.utc))).setpoint
