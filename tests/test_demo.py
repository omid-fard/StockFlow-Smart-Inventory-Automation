import tempfile
from pathlib import Path
import unittest
from stockflow.demo import seed

class DemoFixtureTests(unittest.TestCase):
    def test_four_organizations_and_multi_industry_samples(self):
        with tempfile.TemporaryDirectory() as d:
            db=seed(Path(d)/'sample.db')
            orgs=db.organizations()
            self.assertEqual(len(orgs),4)
            self.assertEqual(len({x['sector'] for x in orgs}),4)
            for org in orgs:
                items=db.inventory(org['id'])
                self.assertEqual(len(items),6)  # 3 SKUs x 2 warehouses
                self.assertGreater(db.daily_report(org['id'])['sales_orders'],0)
                self.assertEqual(len(db.purchase_orders(org['id'])),1)
            self.assertEqual(db.purchase_orders('office-supplies')[0]['status'],'PARTIAL')
            with self.assertRaises(RuntimeError): seed(Path(d)/'sample.db')

    def test_sales_trend_has_full_calendar_window(self):
        with tempfile.TemporaryDirectory() as d:
            db=seed(Path(d)/'sample.db')
            trend=db.sales_trend('online-retail',days=14)
            self.assertEqual(len(trend),14)
            self.assertTrue(all(t['orders']>=0 for t in trend))
            self.assertTrue(all(t['gross_sales_cents']>=0 for t in trend))

if __name__=='__main__': unittest.main()
