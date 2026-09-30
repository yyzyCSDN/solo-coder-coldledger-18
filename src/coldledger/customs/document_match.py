from dataclasses import dataclass
@dataclass(frozen=True)
class Requirement:
    product_class:str;origin_country:str;destination_country:str;documents:frozenset;priority:int=0
def required_documents(product_class,origin,destination,rules):
    rows=[r for r in rules if r.product_class in {product_class,'*'} and r.origin_country in {origin,'*'} and r.destination_country in {destination,'*'}]
    docs=set()
    for r in sorted(rows,key=lambda x:(x.priority,x.product_class!='*',x.origin_country!='*',x.destination_country!='*')):
        docs.update(r.documents)
    return docs
def missing_documents(product_class,origin,destination,rules,provided):
    return required_documents(product_class,origin,destination,rules)-set(provided)
