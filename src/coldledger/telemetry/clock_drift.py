from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from statistics import median
@dataclass(frozen=True)
class SyncPoint: device_time:datetime; trusted_time:datetime
@dataclass(frozen=True)
class Model: anchor:datetime; slope:float; intercept:float
def fit(points):
    if len(points)<3: raise ValueError('three sync points required')
    rows=sorted(points,key=lambda p:p.device_time); anchor=rows[0].device_time.astimezone(timezone.utc)
    xs=[(p.device_time.astimezone(timezone.utc)-anchor).total_seconds() for p in rows]
    ys=[(p.trusted_time.astimezone(timezone.utc)-anchor).total_seconds() for p in rows]
    slopes=[]
    for i in range(len(xs)-1):
        dx=xs[i+1]-xs[i]
        if dx: slopes.append((ys[i+1]-ys[i])/dx)
    slope=median(slopes); intercept=median([y-slope*x for x,y in zip(xs,ys)])
    if not .95<=slope<=1.05: raise ValueError('unsupported drift')
    return Model(anchor,slope,intercept)
def correct(model,at):
    x=(at.astimezone(timezone.utc)-model.anchor).total_seconds();return model.anchor+timedelta(seconds=model.intercept+model.slope*x)
def correct_series(sync,readings):
    m=fit(sync); out=[correct(m,r) for r in readings]
    if any(b<=a for a,b in zip(out,out[1:])): raise ValueError('non-monotone corrected times')
    return out
