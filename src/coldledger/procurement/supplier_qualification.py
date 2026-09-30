from dataclasses import dataclass
from datetime import date
@dataclass(frozen=True)
class Assessment:
    supplier:str;category:str;performed_on:date;expires_on:date;score:float;critical_findings:int;approved:bool
def active_assessment(supplier,category,on,rows):
    eligible=[r for r in rows if r.supplier==supplier and r.category in {category,'*'} and r.performed_on<=on<=r.expires_on]
    return max(eligible,key=lambda r:(r.category!='*',r.performed_on,r.score),default=None)
def qualified(supplier,category,on,rows,min_score=80.0,max_critical=0):
    r=active_assessment(supplier,category,on,rows)
    return r is not None and r.approved and r.score>=min_score and r.critical_findings<=max_critical
def renewals_due(rows,on,within_days):
    return sorted([r for r in rows if 0<=(r.expires_on-on).days<=within_days],key=lambda r:(r.expires_on,r.supplier,r.category))
def coverage(suppliers,category,on,rows):
    suppliers=set(suppliers)
    ok={s for s in suppliers if qualified(s,category,on,rows)}
    return {'qualified':ok,'missing':suppliers-ok,'coverage':0.0 if not suppliers else len(ok)/len(suppliers)}
