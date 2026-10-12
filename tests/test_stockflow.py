import csv
import tempfile
from datetime import date
from pathlib import Path
import unittest
from stockflow.service import StockFlow, StockFlowError

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=StockFlow(Path(self.tmp.name)/"test.db")
        self.db.create_org("a","Company A","Retail")
        self.db.create_org("b","Company B","Food")
        for org in ("a","b"):
            self.db.create_warehouse(org,"MAIN","Main")
            self.db.create_warehouse(org,"BRANCH","Branch")
            self.db.create_product(org,"SKU","Example",minimum="5",safety="2")
        self.db.create_product("a","OIL","Olive Oil",unit="L",minimum="2.500",safety="1.500")
        self.db.record_movement("a","MAIN","SKU","OPENING","20","open-a", "2026-10-01")
        self.db.record_movement("b","MAIN","SKU","OPENING","80","open-b", "2026-10-01")
        self.db.record_movement("a","MAIN","OIL","OPENING","10.500","open-oil", "2026-10-01")

    def tearDown(self): self.tmp.cleanup()
    def item(self,org="a",wh="MAIN",sku="SKU"):
        return next(r for r in self.db.inventory(org) if r["warehouse"]==wh and r["sku"]==sku)

    def test_multi_tenant_separation(self):
        self.assertEqual(self.item("a")["on_hand"],"20")
        self.assertEqual(self.item("b")["on_hand"],"80")
        with self.assertRaises(StockFlowError): self.db.record_movement("b","MAIN","OIL","RECEIPT","2","foreign")

    def test_stock_negative_prevented(self):
        with self.assertRaises(StockFlowError):
            self.db.record_movement("a","MAIN","SKU","DAMAGE","21","bad")
        self.assertEqual(self.item()["on_hand"],"20")

    def test_event_idempotency(self):
        self.assertTrue(self.db.record_movement("a","MAIN","SKU","RECEIPT","3","ev1"))
        self.assertFalse(self.db.record_movement("a","MAIN","SKU","RECEIPT","3","ev1"))
        self.assertEqual(self.item()["on_hand"],"23")
        with self.assertRaises(StockFlowError):
            self.db.record_movement("a","MAIN","SKU","RECEIPT","4","ev1")

    def test_transfer_balanced_and_repeat_safe(self):
        self.assertTrue(self.db.transfer("a","MAIN","BRANCH","SKU","4","tr1"))
        self.assertFalse(self.db.transfer("a","MAIN","BRANCH","SKU","4","tr1"))
        self.assertEqual(self.item()["on_hand"],"16")
        self.assertEqual(self.item(wh="BRANCH")["on_hand"],"4")

    def test_failed_transfer_rolls_back(self):
        with self.assertRaises(StockFlowError): self.db.transfer("a","MAIN","MISSING","SKU","4","tr-fail")
        self.assertEqual(self.item()["on_hand"],"20")

    def test_reservation_available_balance(self):
        self.db.reserve("a","MAIN","SKU","15","hold-1")
        self.assertEqual(self.item()["available"],"5")
        with self.assertRaises(StockFlowError): self.db.record_movement("a","MAIN","SKU","DAMAGE","6","damage-too-big")
        self.assertTrue(self.db.release_reservation("a","hold-1"))
        self.assertEqual(self.item()["available"],"20")

    def test_reserved_sale_consumption(self):
        self.db.reserve("a","MAIN","SKU","4","res-order")
        self.db.sell("a","MAIN","sale-1",[{"sku":"SKU","quantity":"4","unit_price_cents":250,"reservation_ref":"res-order"}],"2026-10-10")
        self.assertEqual(self.item()["on_hand"],"16")
        self.assertEqual(self.item()["reserved"],"0")

    def test_sales_idempotent_and_conflicting(self):
        lines=[{"sku":"SKU","quantity":"3","unit_price_cents":300}]
        self.assertTrue(self.db.sell("a","MAIN","S1",lines,"2026-10-10"))
        self.assertFalse(self.db.sell("a","MAIN","S1",lines,"2026-10-10"))
        with self.assertRaises(StockFlowError): self.db.sell("a","MAIN","S1",[{"sku":"SKU","quantity":"4","unit_price_cents":300}],"2026-10-10")
        self.assertEqual(self.item()["on_hand"],"17")

    def test_sales_atomic_rollback_on_second_line(self):
        with self.assertRaises(StockFlowError):
            self.db.sell("a","MAIN","sale-invalid",[
                {"sku":"SKU","quantity":"2","unit_price_cents":400},
                {"sku":"OIL","quantity":"999","unit_price_cents":200}],"2026-10-10")
        self.assertEqual(self.item()["on_hand"],"20")
        self.assertEqual(self.item(sku="OIL")["on_hand"],"10.5")
        self.assertEqual(self.db.daily_report("a","2026-10-10")["sales_orders"],0)

    def test_unit_precision_validation(self):
        self.db.record_movement("a","MAIN","OIL","RECEIPT","1.375","oil-receipt")
        self.assertEqual(self.item(sku="OIL")["on_hand"],"11.875")
        with self.assertRaises(StockFlowError): self.db.record_movement("a","MAIN","SKU","RECEIPT","1.25","fractional-pcs")
        with self.assertRaises(StockFlowError): self.db.record_movement("a","MAIN","OIL","RECEIPT","0.0001","too-precise")

    def test_po_pending_not_on_hand(self):
        self.db.create_po("a","MAIN","P1","Supplier","2026-10-20",[{"sku":"SKU","quantity":"10"}])
        self.assertEqual(self.item()["on_hand"],"20")
        self.assertEqual(self.item()["incoming_po"],"10")

    def test_partial_purchase_receipts(self):
        self.db.create_po("a","MAIN","P1","Supplier","2026-10-20",[{"sku":"SKU","quantity":"10"}])
        self.assertTrue(self.db.receive_po("a","P1","DEL1",[{"sku":"SKU","quantity":"4"}],"2026-10-15"))
        self.assertFalse(self.db.receive_po("a","P1","DEL1",[{"sku":"SKU","quantity":"4"}],"2026-10-15"))
        self.assertEqual(self.item()["on_hand"],"24")
        self.assertEqual(self.item()["incoming_po"],"6")
        self.assertEqual(self.db.purchase_orders("a")[0]["status"],"PARTIAL")
        with self.assertRaises(StockFlowError): self.db.receive_po("a","P1","DEL-EXCESS",[{"sku":"SKU","quantity":"7"}])
        self.assertEqual(self.item()["on_hand"],"24")
        self.db.receive_po("a","P1","DEL2",[{"sku":"SKU","quantity":"6"}],"2026-10-16")
        self.assertEqual(self.db.purchase_orders("a")[0]["status"],"RECEIVED")

    def test_daily_report_exact(self):
        self.db.sell("a","MAIN","DAY-1",[{"sku":"SKU","quantity":"2","unit_price_cents":1299}],"2026-10-11")
        report=self.db.daily_report("a","2026-10-11")
        self.assertEqual(report["sales_orders"],1)
        self.assertEqual(report["quantity_sold"],"2")
        self.assertEqual(report["gross_sales_cents"],2598)

    def test_fractional_unit_sales_rounds_currency_half_up(self):
        self.db.sell("a","MAIN","sale-liter",[{"sku":"OIL","quantity":"1.250","unit_price_cents":950}],"2026-10-10")
        self.assertEqual(self.db.daily_report("a","2026-10-10")["gross_sales_cents"],1188)

    def test_reorder_alert(self):
        self.db.record_movement("a","MAIN","SKU","DAMAGE","18","damage-1")
        result=self.db.reorder_suggestions("a","2026-10-12")
        sku=next(x for x in result if x["sku"]=="SKU" and x["warehouse"]=="MAIN")
        self.assertEqual(sku["available"],"2")
        self.assertEqual(sku["reorder_point"],"5")
        self.assertEqual(sku["suggested_order"],"3")  # minimum stock is respected, even with no recent demand

    def test_csv_atomic_and_safe_to_rerun(self):
        file=Path(self.tmp.name)/"import.csv"
        file.write_text("warehouse,sku,event_type,quantity,event_key,event_date,note\nMAIN,SKU,RECEIPT,2,csv-1,2026-10-08,test\nMAIN,OIL,RECEIPT,1.250,csv-2,2026-10-08,test\n")
        self.assertEqual(self.db.import_movements_csv("a",file)["inserted"],2)
        self.assertEqual(self.db.import_movements_csv("a",file)["skipped_as_duplicate"],2)
        self.assertEqual(self.item()["on_hand"],"22")
        file.write_text("warehouse,sku,event_type,quantity,event_key,event_date,note\nMAIN,SKU,RECEIPT,2,csv-x,2026-10-08,test\nMAIN,MISSING,RECEIPT,1,csv-y,2026-10-08,test\n")
        with self.assertRaises(StockFlowError): self.db.import_movements_csv("a",file)
        self.assertEqual(self.item()["on_hand"],"22")

    def test_cannot_bypass_sale_or_transfer_records(self):
        with self.assertRaises(StockFlowError):
            self.db.record_movement("a","MAIN","SKU","SALE","1","fake-sale")
        with self.assertRaises(StockFlowError):
            self.db.record_movement("a","MAIN","SKU","TRANSFER_OUT","1","fake-transfer")

    def test_cross_org_same_reference_is_allowed(self):
        self.db.record_movement("a","MAIN","SKU","RECEIPT","1","shared-ref")
        self.db.record_movement("b","MAIN","SKU","RECEIPT","1","shared-ref")
        self.assertEqual(self.item("a")["on_hand"],"21")
        self.assertEqual(self.item("b")["on_hand"],"81")

if __name__=="__main__": unittest.main()
