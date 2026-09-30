from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from coldledger.inventory.fefo import Stock, reserve
from coldledger.transport.connections import Leg, choose
from coldledger.packaging.qualification_matrix import QualifiedPackage, candidates
from coldledger.customs.document_match import Requirement, required_documents

@dataclass(frozen=True)
class BookingRequest:
    shipment_id:str
    quantity:Decimal
    ship_date:object
    min_remaining_days:int
    transfer_minutes:int
    lane_class:str
    forecast_low:float
    forecast_high:float
    transit_hours:float
    payload_kg:float
    product_class:str
    origin_country:str
    destination_country:str

@dataclass(frozen=True)
class BookingPlan:
    shipment_id:str
    lots:tuple[str,...]
    connection:str
    package:str
    required_documents:frozenset[str]

class ControlTower:
    def plan_booking(self,req,stocks,inbound,connections,packages,doc_rules):
        picks=reserve(stocks,req.quantity,req.ship_date,req.min_remaining_days)
        conn=choose(inbound,connections,timedelta(minutes=req.transfer_minutes))
        if conn is None: raise ValueError('no feasible connection')
        packs=candidates(req.lane_class,req.forecast_low,req.forecast_high,req.transit_hours,req.payload_kg,packages)
        if not packs: raise ValueError('no qualified package')
        docs=required_documents(req.product_class,req.origin_country,req.destination_country,doc_rules)
        return BookingPlan(req.shipment_id,tuple(p.lot for p in picks),conn.leg_id,packs[0].package,frozenset(docs))
    def explain(self,plan):
        return {'shipment':plan.shipment_id,'lots':list(plan.lots),'connection':plan.connection,'package':plan.package,'documents':sorted(plan.required_documents)}
