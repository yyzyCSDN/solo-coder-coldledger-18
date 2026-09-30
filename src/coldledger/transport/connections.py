from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
@dataclass(frozen=True)
class Leg: leg_id:str; arrive:datetime; depart:datetime; location:str
def feasible(inbound,outbound,minimum,customs=timedelta()):
    if inbound.location!=outbound.location:return False
    return outbound.depart.astimezone(timezone.utc)-inbound.arrive.astimezone(timezone.utc)>=minimum+customs
def choose(inbound,candidates,minimum,customs=timedelta()):
    rows=[x for x in candidates if feasible(inbound,x,minimum,customs)]
    return min(rows,key=lambda x:(x.depart.astimezone(timezone.utc),x.leg_id),default=None)
