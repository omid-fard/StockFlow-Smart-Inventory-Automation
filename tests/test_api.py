import os
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from stockflow.api import app
from stockflow.service import StockFlow

class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self._old=os.environ.get('STOCKFLOW_DB')
        os.environ['STOCKFLOW_DB']=str(Path(self.tmp.name)/'api.db')
        db=StockFlow(os.environ['STOCKFLOW_DB'])
        db.create_org('demo','Demonstration','Retail')
        db.create_warehouse('demo','MAIN','Main')
        db.create_product('demo','PEN','Pen')
        db.record_movement('demo','MAIN','PEN','OPENING','10','open')
        self.client=TestClient(app)

    def tearDown(self):
        if self._old is None: os.environ.pop('STOCKFLOW_DB',None)
        else: os.environ['STOCKFLOW_DB']=self._old
        self.tmp.cleanup()

    def test_health_and_dashboard(self):
        self.assertEqual(self.client.get('/health').json()['status'],'ok')
        self.assertEqual(self.client.get('/dashboard').status_code,200)

    def test_post_sale_then_inventory(self):
        payload={'org':'demo','warehouse':'MAIN','sale_ref':'S-001',
                 'lines':[{'sku':'PEN','quantity':'3','unit_price_cents':199}]}
        r=self.client.post('/sales',json=payload)
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.json()['inserted'])
        self.assertFalse(self.client.post('/sales',json=payload).json()['inserted'])
        rows=self.client.get('/organizations/demo/inventory').json()
        self.assertEqual(rows[0]['on_hand'],'7')
        self.assertEqual(self.client.get('/organizations/demo/daily-report').json()['sales_orders'],1)

    def test_rejects_insufficient_stock(self):
        res=self.client.post('/sales',json={'org':'demo','warehouse':'MAIN','sale_ref':'S-FAIL',
            'lines':[{'sku':'PEN','quantity':'11','unit_price_cents':199}]})
        self.assertEqual(res.status_code,400)

if __name__=='__main__': unittest.main()
