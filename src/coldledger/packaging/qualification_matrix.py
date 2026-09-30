from dataclasses import dataclass
@dataclass(frozen=True)
class QualifiedPackage:
    package:str;lane_class:str;min_ambient:float;max_ambient:float;hours:float;payload_min:float;payload_max:float;cost:float=0.0
def candidates(lane_class,forecast_low,forecast_high,transit_hours,payload_kg,matrix):
    out=[]
    for q in matrix:
        if q.lane_class not in {lane_class,'*'}:continue
        if forecast_low<q.min_ambient or forecast_high>q.max_ambient:continue
        if transit_hours>q.hours:continue
        if not q.payload_min<=payload_kg<=q.payload_max:continue
        out.append(q)
    return sorted(out,key=lambda q:(q.cost,q.hours-transit_hours,q.package))
def margin(q,forecast_low,forecast_high,transit_hours):
    return min(forecast_low-q.min_ambient,q.max_ambient-forecast_high,q.hours-transit_hours)
