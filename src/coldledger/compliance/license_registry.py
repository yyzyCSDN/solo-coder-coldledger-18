from dataclasses import dataclass
from datetime import date
@dataclass(frozen=True)
class License:
    holder:str; jurisdiction:str; product_class:str; valid_from:date; valid_to:date; suspended:bool=False
def valid_license(holder,jurisdiction,product_class,on,licenses):
    rows=[x for x in licenses if x.holder==holder and x.jurisdiction==jurisdiction and x.product_class in {product_class,'*'} and x.valid_from<=on<=x.valid_to and not x.suspended]
    return max(rows,key=lambda x:(x.product_class!='*',x.valid_to),default=None)
def gaps(holder,jurisdiction,product_class,start,end,licenses):
    if end<start: raise ValueError('bad range')
    missing=[];cur=start
    while cur<=end:
        if valid_license(holder,jurisdiction,product_class,cur,licenses) is None: missing.append(cur)
        cur=date.fromordinal(cur.toordinal()+1)
    return missing
