import unittest
from datetime import datetime,timezone,timedelta,date,time
from decimal import Decimal
from coldledger.api import ColdLedgerService
from coldledger.inventory.fefo import Stock
from coldledger.quality.release import Policy,Case
from coldledger.transport.connections import Leg
from coldledger.audit.trail import AuditTrail
from coldledger.storage.event_store import EventStore
from coldledger.transport.route_selection import Route,select
from coldledger.compliance.license_registry import License,valid_license
UTC=timezone.utc
class PublicTests(unittest.TestCase):
    def test_health(self): self.assertEqual(ColdLedgerService().health()['runtime_dependencies'],0)
    def test_import_every_module(self):
        import coldledger, pkgutil, importlib
        names=[m.name for m in pkgutil.walk_packages(coldledger.__path__, coldledger.__name__+'.')]
        for name in names: importlib.import_module(name)
        self.assertGreater(len(names),100)
    def test_fefo(self):
        rows=[Stock('A',date(2027,1,1),Decimal('5')),Stock('B',date(2027,2,1),Decimal('10'))]
        picks=ColdLedgerService().reserve_inventory(rows,8,date(2026,10,1),30)
        self.assertEqual([p.lot for p in picks],['A','B'])
    def test_release(self):
        p=[Policy(1,2.0,frozenset({'pod'}),datetime(2026,1,1,tzinfo=UTC))]
        self.assertTrue(ColdLedgerService().release_decision(Case(datetime(2026,2,1,tzinfo=UTC),1,frozenset({'pod'})),p))
    def test_connection(self):
        i=Leg('i',datetime(2026,1,1,8,tzinfo=UTC),datetime(2026,1,1,7,tzinfo=UTC),'FRA')
        o=Leg('o',datetime(2026,1,1,12,tzinfo=UTC),datetime(2026,1,1,9,tzinfo=UTC),'FRA')
        self.assertEqual(ColdLedgerService().next_connection(i,[o],30).leg_id,'o')
    def test_audit(self):
        a=AuditTrail();a.append('u','create','S');a.append('q','release','S');self.assertTrue(a.verify())
    def test_store_conflict(self):
        s=EventStore();s.append('x',0,'created',{})
        with self.assertRaises(RuntimeError):s.append('x',0,'again',{})
    def test_route(self):
        r=select([Route('r',8,100,10,.1,1,50)],20,.2);self.assertEqual(r.route_id,'r')
    def test_license(self):
        l=License('H','DE','BIO',date(2026,1,1),date(2026,12,31));self.assertIsNotNone(valid_license('H','DE','BIO',date(2026,5,1),[l]))
if __name__=='__main__':unittest.main()
