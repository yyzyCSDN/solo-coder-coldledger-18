from dataclasses import dataclass
@dataclass(frozen=True)
class Route:
    route_id:str; hours:float; cost:float; qualified_hours:float; risk:float; customs_stops:int; capacity_kg:float
def select(routes,payload_kg,max_risk,max_cost=None):
    eligible=[]
    for r in routes:
        if r.capacity_kg<payload_kg or r.hours>r.qualified_hours or r.risk>max_risk: continue
        if max_cost is not None and r.cost>max_cost: continue
        score=r.cost+12*r.hours+800*r.risk+40*r.customs_stops
        eligible.append((score,r.route_id,r))
    if not eligible:return None
    return min(eligible,key=lambda x:(x[0],x[1]))[2]
def pareto(routes):
    out=[]
    for r in routes:
        dominated=False
        for o in routes:
            if o is r:continue
            if o.cost<=r.cost and o.hours<=r.hours and o.risk<=r.risk and (o.cost<r.cost or o.hours<r.hours or o.risk<r.risk): dominated=True;break
        if not dominated:out.append(r)
    return sorted(out,key=lambda x:x.route_id)
