from dataclasses import dataclass,replace
from decimal import Decimal
@dataclass(frozen=True)
class Bin: sku:str;quantity:Decimal;version:int
def reconcile(current,counts,versions):
    if set(counts)!=set(versions):raise ValueError('key mismatch')
    adjustments={}
    for sku,counted in counts.items():
        if counted<0:raise ValueError('negative count')
        state=current[sku]
        if state.version!=versions[sku]:raise RuntimeError('version conflict')
        adjustments[sku]=counted-state.quantity
    nxt=dict(current)
    for sku,delta in adjustments.items():
        state=current[sku];nxt[sku]=replace(state,quantity=state.quantity+delta,version=state.version+1)
    return nxt,adjustments
