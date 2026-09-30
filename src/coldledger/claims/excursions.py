from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class Sample: at:datetime;value:float
@dataclass(frozen=True)
class Excursion: start:datetime;end:datetime;peak:float
def detect(samples,low,high,margin,min_seconds):
    rows=sorted(samples,key=lambda s:s.at);out=[];start=None;peak=None;last=None
    for s in rows:
        t=s.at.astimezone(timezone.utc)
        if last is not None and t<=last:raise ValueError('duplicate time')
        outside=s.value<low or s.value>high
        if start is None and outside:start=t;peak=s.value
        elif start is not None:
            if abs(s.value-(low if s.value<low else high))>abs(peak-(low if peak<low else high)):peak=s.value
            cleared=low+margin<=s.value<=high-margin
            if cleared:
                if (t-start).total_seconds()>=min_seconds:out.append(Excursion(start,t,peak))
                start=None;peak=None
        last=t
    if start is not None and last is not None and (last-start).total_seconds()>=min_seconds:out.append(Excursion(start,last,peak))
    return out
