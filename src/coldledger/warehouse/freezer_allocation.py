from dataclasses import dataclass
@dataclass(frozen=True)
class Slot: slot_id:str; zone:str; circuit:str; neighbors:frozenset
@dataclass(frozen=True)
class Load: load_id:str; zone:str; high_value:bool; redundancy:bool
def allocate(loads,slots,occupied):
    by={s.slot_id:s for s in slots};result=dict(occupied);circuits={}
    for sid,lid in occupied.items():circuits.setdefault(lid,set()).add(by[sid].circuit)
    for load in sorted(loads,key=lambda x:(not x.high_value,x.load_id)):
        if load.load_id in result.values():continue
        cand=[]
        for s in slots:
            if s.slot_id in result or s.zone!=load.zone:continue
            neighbor_loads={result[n] for n in s.neighbors if n in result}
            if load.high_value and load.load_id in neighbor_loads:continue
            if load.redundancy and s.circuit in circuits.get(load.load_id,set()):continue
            cand.append(s)
        if not cand:raise ValueError('no slot')
        c=min(cand,key=lambda s:(sum(n in result for n in s.neighbors),s.slot_id));result[c.slot_id]=load.load_id;circuits.setdefault(load.load_id,set()).add(c.circuit)
    return result
