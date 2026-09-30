from dataclasses import dataclass
from decimal import Decimal
@dataclass(frozen=True)
class Transfer: transfer_id:str;expected:Decimal;arrived:Decimal=Decimal('0');dispatched:Decimal=Decimal('0');cancelled:Decimal=Decimal('0')
def arrival(t,qty):
    q=Decimal(str(qty))
    if q<=0 or t.arrived+q+t.cancelled>t.expected:raise ValueError('invalid arrival')
    return Transfer(t.transfer_id,t.expected,t.arrived+q,t.dispatched,t.cancelled)
def dispatch(t,qty):
    q=Decimal(str(qty))
    if q<=0 or t.dispatched+q>t.arrived:raise ValueError('invalid dispatch')
    return Transfer(t.transfer_id,t.expected,t.arrived,t.dispatched+q,t.cancelled)
def cancel(t,qty):
    q=Decimal(str(qty));open_expected=t.expected-t.arrived-t.cancelled
    if q<=0 or q>open_expected:raise ValueError('invalid cancellation')
    return Transfer(t.transfer_id,t.expected,t.arrived,t.dispatched,t.cancelled+q)
def status(t):
    live=t.expected-t.cancelled
    if t.arrived==live and t.dispatched==live:return 'complete'
    if t.arrived>0:return 'partial'
    if t.cancelled==t.expected:return 'cancelled'
    return 'planned'
