from dataclasses import dataclass
from threading import RLock
@dataclass(frozen=True)
class Event:
    stream:str; sequence:int; kind:str; payload:dict
class EventStore:
    def __init__(self): self._data={}; self._lock=RLock()
    def append(self,stream,expected,kind,payload):
        with self._lock:
            rows=self._data.setdefault(stream,[])
            if len(rows)!=expected: raise RuntimeError('stream conflict')
            e=Event(stream,len(rows)+1,kind,dict(payload));rows.append(e);return e
    def read(self,stream,after=0):
        with self._lock:return [e for e in self._data.get(stream,[]) if e.sequence>after]
    def all_streams(self):
        with self._lock:return sorted(self._data)
