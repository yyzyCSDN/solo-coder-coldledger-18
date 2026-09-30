from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class Handoff: sequence:int; from_party:str; to_party:str; at:datetime; seal:str
def validate(origin,events):
    current=origin;last=None;seq=1;seals=set()
    for e in sorted(events,key=lambda x:x.sequence):
        if e.sequence!=seq: raise ValueError('sequence gap')
        if e.from_party!=current or e.to_party==current: raise ValueError('custody discontinuity')
        at=e.at.astimezone(timezone.utc)
        if last is not None and at==last and e.from_party==current:
            raise ValueError('duplicate custody instant')
        if not e.seal or e.seal in seals: raise ValueError('seal invalid')
        seals.add(e.seal); current=e.to_party;last=max(last,at) if last else at;seq+=1
    return current
