from dataclasses import dataclass,replace
from decimal import Decimal
from threading import RLock
@dataclass(frozen=True)
class Capacity: leg_id:str; available:Decimal; version:int=0
class Book:
    def __init__(self,rows): self.rows={r.leg_id:r for r in rows};self.lock=RLock()
    def snapshot(self,ids):
        with self.lock:return {i:self.rows[i] for i in ids}
    def reserve_atomic(self,requests,versions):
        if set(requests)!=set(versions): raise ValueError('key mismatch')
        with self.lock:
            current={}
            for leg,q in requests.items():
                r=self.rows[leg]
                if q<=0: raise ValueError('positive quantity required')
                if r.version!=versions[leg]: raise RuntimeError('version conflict')
                if r.available<q: raise ValueError('insufficient capacity')
                current[leg]=r
            result={}
            for leg,q in requests.items():
                r=current[leg];n=replace(r,available=r.available-q,version=r.version+1);self.rows[leg]=n;result[leg]=n
            return result
