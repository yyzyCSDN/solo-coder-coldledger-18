from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class Segment: start:datetime; end:datetime; ambient_c:float; door_fraction:float=0.0
def consumed(initial,segments,base_rate,temp_factor,door_factor):
    if min(initial,base_rate)<0: raise ValueError('invalid mass/rate')
    total=0.0;last=None
    for s in sorted(segments,key=lambda x:x.start):
        a,b=s.start.astimezone(timezone.utc),s.end.astimezone(timezone.utc)
        if b<=a: raise ValueError('bad segment')
        if last is not None and a<last: raise ValueError('overlap')
        if not 0<=s.door_fraction<=1: raise ValueError('door fraction')
        hours=(b-a).total_seconds()/3600; mult=1+max(0,s.ambient_c-20)*temp_factor+s.door_fraction*door_factor
        total+=base_rate*mult*hours;last=b
    return min(initial,total)
def remaining(initial,segments,base_rate,temp_factor,door_factor): return max(0.0,initial-consumed(initial,segments,base_rate,temp_factor,door_factor))
