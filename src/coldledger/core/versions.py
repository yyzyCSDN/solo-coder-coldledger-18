from dataclasses import dataclass
from threading import RLock
class Conflict(RuntimeError): pass
@dataclass(frozen=True)
class Snapshot:
    value:object; version:int
class VersionedCell:
    def __init__(self,value): self._value=value; self._version=0; self._lock=RLock()
    def snapshot(self):
        with self._lock:return Snapshot(self._value,self._version)
    def compare_and_set(self,expected,value):
        with self._lock:
            if expected!=self._version: raise Conflict(f'expected {expected}, actual {self._version}')
            self._value=value;self._version+=1;return Snapshot(value,self._version)
    def update(self,fn):
        with self._lock:
            nxt=fn(self._value);self._value=nxt;self._version+=1;return Snapshot(nxt,self._version)
