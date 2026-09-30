from dataclasses import dataclass
from datetime import date
from decimal import Decimal
@dataclass(frozen=True)
class Stock: lot:str; expiry:date; quantity:Decimal; released:bool=True; quarantined:bool=False
@dataclass(frozen=True)
class Pick: lot:str; quantity:Decimal; expiry:date
def reserve(stocks,requested,ship_date,min_remaining_days):
    need=Decimal(str(requested))
    if need<=0: raise ValueError('positive request required')
    eligible=[]
    for s in stocks:
        if s.quantity<=0 or not s.released or s.quarantined: continue
        if (s.expiry-ship_date).days<min_remaining_days: continue
        eligible.append(s)
    eligible.sort(key=lambda s:(s.expiry,s.lot)); out=[]
    for s in eligible:
        if need<=0: break
        q=min(s.quantity,need);out.append(Pick(s.lot,q,s.expiry));need-=q
    if need>0: raise ValueError('insufficient eligible stock')
    return out
