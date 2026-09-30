from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from collections import defaultdict
@dataclass(frozen=True)
class Reading: device:str;event_time:datetime;value:float;sequence:int
class Buffer:
    def __init__(self,lateness):self.lateness=lateness;self.max={};self.pending=defaultdict(list);self.emitted=set()
    def add(self,r):
        t=r.event_time.astimezone(timezone.utc);m=self.max.get(r.device)
        if m is None or t>m:self.max[r.device]=t
        self.pending[r.device].append(r);wm=self.max[r.device]-self.lateness
        ready=sorted([x for x in self.pending[r.device] if x.event_time.astimezone(timezone.utc)<=wm],key=lambda x:(x.event_time,x.sequence));out=[]
        for x in ready:
            if x.sequence not in self.emitted:out.append(x);self.emitted.add(x.sequence)
        self.pending[r.device]=[x for x in self.pending[r.device] if x not in ready];return out
    def flush(self,device):
        rows=sorted(self.pending[device],key=lambda x:(x.event_time,x.sequence));self.pending[device]=[];out=[]
        for x in rows:
            if x.sequence not in self.emitted:out.append(x);self.emitted.add(x.sequence)
        return out
