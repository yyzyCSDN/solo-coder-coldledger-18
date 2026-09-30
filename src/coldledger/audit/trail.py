from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib,json
@dataclass(frozen=True)
class Record:
    index:int; actor:str; action:str; subject:str; at:datetime; previous:str; digest:str
class AuditTrail:
    def __init__(self): self.records=[]
    @staticmethod
    def digest(index,actor,action,subject,at,previous):
        raw=json.dumps([index,actor,action,subject,at.astimezone(timezone.utc).isoformat(),previous],separators=(',',':')).encode()
        return hashlib.sha256(raw).hexdigest()
    def append(self,actor,action,subject,at=None):
        at=at or datetime.now(timezone.utc); prev=self.records[-1].digest if self.records else 'GENESIS'; i=len(self.records)+1
        r=Record(i,actor,action,subject,at,prev,self.digest(i,actor,action,subject,at,prev));self.records.append(r);return r
    def verify(self):
        prev='GENESIS'
        for r in self.records:
            if r.previous!=prev or r.digest!=self.digest(r.index,r.actor,r.action,r.subject,r.at,prev): return False
            prev=r.digest
        return True
