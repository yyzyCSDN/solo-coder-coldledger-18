from dataclasses import dataclass
@dataclass(frozen=True)
class Shipment: shipment_id:str;low:float;high:float;volume:float;incompatible:frozenset;tags:frozenset
@dataclass(frozen=True)
class Compartment: compartment_id:str;setpoint:float;capacity:float
def assign(shipments,compartments):
    remaining={c.compartment_id:c.capacity for c in compartments};contents={c.compartment_id:[] for c in compartments};out={}
    for s in sorted(shipments,key=lambda x:(-x.volume,x.shipment_id)):
        cand=[]
        for c in compartments:
            if not s.low<=c.setpoint<=s.high or remaining[c.compartment_id]<s.volume:continue
            if any(s.incompatible&e.tags or e.incompatible&s.tags for e in contents[c.compartment_id]):continue
            cand.append(c)
        if not cand:raise ValueError('no compatible compartment')
        c=min(cand,key=lambda x:(remaining[x.compartment_id]-s.volume,x.compartment_id));remaining[c.compartment_id]-=s.volume;contents[c.compartment_id].append(s);out[s.shipment_id]=c.compartment_id
    return out
