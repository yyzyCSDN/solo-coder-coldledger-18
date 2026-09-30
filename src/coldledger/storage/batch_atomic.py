from dataclasses import dataclass,replace
from threading import RLock
@dataclass(frozen=True)
class Record:key:str;value:int;version:int=0
class Table:
    def __init__(self,rows):self.rows={r.key:r for r in rows};self.lock=RLock()
    def batch_increment(self,deltas,versions):
        if set(deltas)!=set(versions):raise ValueError('key mismatch')
        with self.lock:
            current={}
            for k,d in deltas.items():
                if d==0:raise ValueError('zero delta')
                r=self.rows[k]
                if r.version!=versions[k]:raise RuntimeError('version conflict')
                current[k]=r
            out={}
            for k,d in deltas.items():
                r=current[k];n=replace(r,value=r.value+d,version=r.version+1);self.rows[k]=n;out[k]=n
            return out
