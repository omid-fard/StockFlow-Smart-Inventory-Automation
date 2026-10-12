"""Transaction-safe inventory service using only Python's standard library.

Quantities are stored in thousandths, not binary floating point.
Example: 1.250 kg = 1250 milli-units. All writes are atomic.
"""
from __future__ import annotations
import csv
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

SCALE=1000
IN_TYPES={"OPENING","RECEIPT","RETURN","ADJUST_IN","TRANSFER_IN"}
OUT_TYPES={"SALE","DAMAGE","ADJUST_OUT","TRANSFER_OUT"}
PUBLIC_TYPES={"OPENING","RECEIPT","RETURN","ADJUST_IN","DAMAGE","ADJUST_OUT"}

class StockFlowError(ValueError):
    """Operation rejected due to a business rule or conflicting reference."""

def uid(): return uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def day(value=None):
    try: return date.fromisoformat(value or date.today().isoformat()).isoformat()
    except (TypeError,ValueError): raise StockFlowError("Date must be YYYY-MM-DD") from None

def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

def milli(value, precision=3):
    try:
        d=Decimal(str(value))
        v=d*SCALE
        if not d.is_finite() or v!=v.to_integral_value(): raise StockFlowError("Maximum precision: 3 decimal places")
        q=int(v)
        if q%(10**(3-precision))!=0: raise StockFlowError(f"Allowed precision: {precision} decimal places")
        return q
    except (InvalidOperation,TypeError,ValueError) as e:
        if isinstance(e,StockFlowError): raise
        raise StockFlowError(f"Invalid quantity: {value}") from None

def fmt(q): return format(Decimal(q)/SCALE,".3f").rstrip("0").rstrip(".") or "0"
def cents_from_scaled(q):
    return int((Decimal(q)/SCALE).quantize(Decimal("1"),rounding=ROUND_HALF_UP))

class StockFlow:
    def __init__(self,db_path="stockflow.db"):
        self.db_path=str(db_path)
        if self.db_path==":memory:": raise StockFlowError("Use a file-backed SQLite DB for transaction-safe multi-connection access")
        Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
        with self.read() as c:
            c.executescript((Path(__file__).with_name("schema.sql")).read_text(encoding="utf8"))

    def _connect(self):
        c=sqlite3.connect(self.db_path,timeout=30)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    @contextmanager
    def read(self):
        c=self._connect()
        try: yield c
        finally: c.close()

    @contextmanager
    def tx(self):
        c=self._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally: c.close()

    @staticmethod
    def one(c,sql,args=()):
        x=c.execute(sql,args).fetchone()
        if x is None: raise StockFlowError("Record not found in the selected organization")
        return x

    def organizations(self):
        with self.read() as c: return [dict(x) for x in c.execute("SELECT * FROM organizations ORDER BY id")]

    def create_org(self,slug,name,sector):
        if not (slug and name and sector): raise StockFlowError("Organization fields cannot be blank")
        with self.tx() as c: c.execute("INSERT INTO organizations VALUES(?,?,?,?)",(slug,name,sector,now()))

    def create_warehouse(self,org,code,name):
        with self.tx() as c:
            self.one(c,"SELECT id FROM organizations WHERE id=?",(org,))
            c.execute("INSERT INTO warehouses VALUES(?,?,?,?)",(uid(),org,code,name))

    def create_product(self,org,sku,name,unit="pcs",safety="0",minimum="0",lead_days=7,review_days=7):
        prec={"pcs":0,"kg":3,"L":3,"m":3}
        if unit not in prec: raise StockFlowError("Supported units: pcs, kg, L, m")
        s,t=milli(safety,prec[unit]),milli(minimum,prec[unit])
        if s<0 or t<0 or lead_days<1 or review_days<1: raise StockFlowError("Invalid replenishment parameters")
        with self.tx() as c:
            self.one(c,"SELECT id FROM organizations WHERE id=?",(org,))
            c.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?)",(uid(),org,sku,name,unit,prec[unit],s,t,lead_days,review_days))

    @classmethod
    def lookup(cls,c,org,wh,sku):
        w=cls.one(c,"SELECT * FROM warehouses WHERE org_id=? AND code=?",(org,wh))
        p=cls.one(c,"SELECT * FROM products WHERE org_id=? AND sku=?",(org,sku))
        return w,p

    @staticmethod
    def balance(c,org,wid,pid):
        on=c.execute("SELECT COALESCE(SUM(delta_milli),0) FROM stock_events WHERE org_id=? AND warehouse_id=? AND product_id=?",(org,wid,pid)).fetchone()[0]
        reserved=c.execute("SELECT COALESCE(SUM(quantity_milli),0) FROM reservations WHERE org_id=? AND warehouse_id=? AND product_id=? AND status='ACTIVE'",(org,wid,pid)).fetchone()[0]
        return on,reserved,on-reserved

    @classmethod
    def apply_movement(cls,c,org,wh,sku,event_type,quantity,event_key,event_date=None,note=""):
        if not event_key: raise StockFlowError("An idempotency event_key is required")
        if event_type not in IN_TYPES|OUT_TYPES: raise StockFlowError("Unknown movement type")
        w,p=cls.lookup(c,org,wh,sku)
        q=milli(quantity,p["precision_digits"])
        if q<=0: raise StockFlowError("Quantity must be positive")
        delta=q if event_type in IN_TYPES else -q
        d=day(event_date)
        fp=fingerprint([wh,sku,event_type,q,d,note])
        prior=c.execute("SELECT fingerprint FROM stock_events WHERE org_id=? AND event_key=?",(org,event_key)).fetchone()
        if prior:
            if prior["fingerprint"]!=fp: raise StockFlowError("event_key already used with different content")
            return False
        if delta<0 and cls.balance(c,org,w["id"],p["id"])[2]<-delta:
            raise StockFlowError(f"Insufficient available stock: {sku} at {wh}")
        c.execute("INSERT INTO stock_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uid(),org,w["id"],p["id"],event_key,event_type,delta,d,note,fp,now()))
        return True

    def record_movement(self,org,wh,sku,event_type,quantity,event_key,event_date=None,note=""):
        if event_type not in PUBLIC_TYPES: raise StockFlowError("Use sell() or transfer() for this event type")
        with self.tx() as c: return self.apply_movement(c,org,wh,sku,event_type,quantity,event_key,event_date,note)

    def import_movements_csv(self,org,path):
        """Atomic import; bad row rolls back the complete CSV. Re-import is idempotent."""
        with open(path,encoding="utf-8-sig",newline="") as f:
            r=csv.DictReader(f)
            fields={"warehouse","sku","event_type","quantity","event_key","event_date"}
            if not fields.issubset(set(r.fieldnames or [])): raise StockFlowError("CSV missing: "+", ".join(sorted(fields)))
            rows=list(r)
        added=0
        with self.tx() as c:
            for i,row in enumerate(rows,2):
                try:
                    if row["event_type"] not in PUBLIC_TYPES: raise StockFlowError("Use sale/transfer operations for this movement type")
                    added+=int(self.apply_movement(c,org,row["warehouse"],row["sku"],row["event_type"],row["quantity"],row["event_key"],row["event_date"],row.get("note","") or ""))
                except (StockFlowError,sqlite3.IntegrityError) as e: raise StockFlowError(f"CSV row {i}: {e}") from e
        return {"rows":len(rows),"inserted":added,"skipped_as_duplicate":len(rows)-added}

    def transfer(self,org,source,destination,sku,quantity,reference,event_date=None):
        if source==destination: raise StockFlowError("Source and destination must differ")
        with self.tx() as c:
            o=self.apply_movement(c,org,source,sku,"TRANSFER_OUT",quantity,f"transfer:{reference}:out",event_date,reference)
            i=self.apply_movement(c,org,destination,sku,"TRANSFER_IN",quantity,f"transfer:{reference}:in",event_date,reference)
            if o!=i: raise StockFlowError("Incomplete transfer reference")
            return o

    def reserve(self,org,wh,sku,quantity,ref):
        with self.tx() as c:
            w,p=self.lookup(c,org,wh,sku)
            q=milli(quantity,p["precision_digits"])
            if q<=0: raise StockFlowError("Quantity must be positive")
            prior=c.execute("SELECT * FROM reservations WHERE org_id=? AND reservation_ref=?",(org,ref)).fetchone()
            if prior:
                if (prior["warehouse_id"],prior["product_id"],prior["quantity_milli"],prior["status"])!=(w["id"],p["id"],q,"ACTIVE"):
                    raise StockFlowError("Conflicting reservation reference")
                return False
            if self.balance(c,org,w["id"],p["id"])[2]<q: raise StockFlowError("Insufficient stock to reserve")
            c.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?,?,?)",(uid(),org,ref,w["id"],p["id"],q,"ACTIVE",now()))
            return True

    def release_reservation(self,org,ref):
        with self.tx() as c:
            r=self.one(c,"SELECT * FROM reservations WHERE org_id=? AND reservation_ref=?",(org,ref))
            if r["status"]=="RELEASED": return False
            if r["status"]!="ACTIVE": raise StockFlowError("Reservation already consumed")
            c.execute("UPDATE reservations SET status='RELEASED' WHERE id=?",(r["id"],))
            return True

    @staticmethod
    def normalized(lines,sale=False):
        if not lines: raise StockFlowError("Order must contain at least one line")
        seen=set(); items=[]
        for line in lines:
            sku=str(line["sku"])
            if sku in seen: raise StockFlowError("Duplicate SKU in one order")
            seen.add(sku)
            d={"sku":sku,"quantity":str(line["quantity"])}
            if sale:
                d["unit_price_cents"]=int(line["unit_price_cents"])
                d["reservation_ref"]=line.get("reservation_ref") or ""
                if d["unit_price_cents"]<0: raise StockFlowError("Price cannot be negative")
            items.append(d)
        return sorted(items,key=lambda x:x["sku"])

    def sell(self,org,wh,reference,lines,sold_date=None):
        items=self.normalized(lines,True); d=day(sold_date)
        fp=fingerprint([wh,d,items])
        with self.tx() as c:
            prior=c.execute("SELECT fingerprint FROM sales WHERE org_id=? AND sale_ref=?",(org,reference)).fetchone()
            if prior:
                if prior["fingerprint"]!=fp: raise StockFlowError("Conflicting sale reference")
                return False
            w=self.one(c,"SELECT id FROM warehouses WHERE org_id=? AND code=?",(org,wh))
            sid=uid()
            c.execute("INSERT INTO sales VALUES(?,?,?,?,?,?,?)",(sid,org,reference,w["id"],d,fp,now()))
            for line in items:
                p=self.one(c,"SELECT * FROM products WHERE org_id=? AND sku=?",(org,line["sku"]))
                q=milli(line["quantity"],p["precision_digits"])
                if q<=0: raise StockFlowError("Quantity must be positive")
                if line["reservation_ref"]:
                    r=self.one(c,"SELECT * FROM reservations WHERE org_id=? AND reservation_ref=?",(org,line["reservation_ref"]))
                    if (r["status"],r["warehouse_id"],r["product_id"],r["quantity_milli"])!=("ACTIVE",w["id"],p["id"],q):
                        raise StockFlowError("Reservation does not match sale line")
                    c.execute("UPDATE reservations SET status='CONSUMED' WHERE id=?",(r["id"],))
                c.execute("INSERT INTO sale_lines VALUES(?,?,?,?,?)",(uid(),sid,p["id"],q,line["unit_price_cents"]))
                self.apply_movement(c,org,wh,line["sku"],"SALE",line["quantity"],f"sale:{reference}:{line['sku']}",d,reference)
        return True

    def create_po(self,org,wh,reference,supplier,expected_date,lines):
        items=self.normalized(lines); exp=day(expected_date); fp=fingerprint([wh,supplier,exp,items])
        with self.tx() as c:
            prior=c.execute("SELECT fingerprint FROM purchase_orders WHERE org_id=? AND po_ref=?",(org,reference)).fetchone()
            if prior:
                if prior["fingerprint"]!=fp: raise StockFlowError("Conflicting PO reference")
                return False
            w=self.one(c,"SELECT id FROM warehouses WHERE org_id=? AND code=?",(org,wh)); pid=uid()
            c.execute("INSERT INTO purchase_orders VALUES(?,?,?,?,?,?,?,?,?)",(pid,org,reference,w["id"],supplier,exp,"ORDERED",fp,now()))
            for line in items:
                p=self.one(c,"SELECT * FROM products WHERE org_id=? AND sku=?",(org,line["sku"]))
                q=milli(line["quantity"],p["precision_digits"])
                if q<=0: raise StockFlowError("Quantity must be positive")
                c.execute("INSERT INTO po_lines VALUES(?,?,?,?,?)",(uid(),pid,p["id"],q,0))
        return True

    def receive_po(self,org,reference,delivery_ref,lines,received_date=None):
        items=self.normalized(lines); d=day(received_date); fp=fingerprint([reference,d,items])
        with self.tx() as c:
            prior=c.execute("SELECT fingerprint FROM purchase_receipts WHERE org_id=? AND delivery_ref=?",(org,delivery_ref)).fetchone()
            if prior:
                if prior["fingerprint"]!=fp: raise StockFlowError("Conflicting delivery reference")
                return False
            po=self.one(c,"SELECT po.*,w.code AS warehouse FROM purchase_orders po JOIN warehouses w ON w.id=po.warehouse_id WHERE po.org_id=? AND po.po_ref=?",(org,reference))
            c.execute("INSERT INTO purchase_receipts VALUES(?,?,?,?,?,?)",(uid(),org,po["id"],delivery_ref,fp,now()))
            for item in items:
                p=self.one(c,"SELECT * FROM products WHERE org_id=? AND sku=?",(org,item["sku"]))
                q=milli(item["quantity"],p["precision_digits"])
                if q<=0: raise StockFlowError("Quantity must be positive")
                line=self.one(c,"SELECT * FROM po_lines WHERE po_id=? AND product_id=?",(po["id"],p["id"]))
                if line["received_milli"]+q>line["ordered_milli"]: raise StockFlowError("Delivery exceeds ordered quantity")
                c.execute("UPDATE po_lines SET received_milli=received_milli+? WHERE id=?",(q,line["id"]))
                self.apply_movement(c,org,po["warehouse"],item["sku"],"RECEIPT",item["quantity"],f"delivery:{delivery_ref}:{item['sku']}",d,reference)
            pending=c.execute("SELECT SUM(ordered_milli-received_milli),SUM(received_milli) FROM po_lines WHERE po_id=?",(po["id"],)).fetchone()
            status="RECEIVED" if pending[0]==0 else "PARTIAL" if pending[1]>0 else "ORDERED"
            c.execute("UPDATE purchase_orders SET status=? WHERE id=?",(status,po["id"]))
        return True

    def inventory(self,org):
        with self.read() as c:
            self.one(c,"SELECT id FROM organizations WHERE id=?",(org,))
            rows=c.execute('''
                SELECT w.code AS warehouse, p.sku, p.name, p.unit, p.minimum_milli,
                  COALESCE((SELECT SUM(e.delta_milli) FROM stock_events e WHERE e.org_id=p.org_id AND e.warehouse_id=w.id AND e.product_id=p.id),0) AS on_hand,
                  COALESCE((SELECT SUM(r.quantity_milli) FROM reservations r WHERE r.org_id=p.org_id AND r.warehouse_id=w.id AND r.product_id=p.id AND r.status='ACTIVE'),0) AS reserved,
                  COALESCE((SELECT SUM(l.ordered_milli-l.received_milli) FROM po_lines l JOIN purchase_orders po ON po.id=l.po_id WHERE po.org_id=p.org_id AND po.warehouse_id=w.id AND l.product_id=p.id),0) AS inbound
                FROM products p JOIN warehouses w ON w.org_id=p.org_id
                WHERE p.org_id=? ORDER BY w.code,p.sku
            ''',(org,)).fetchall()
            return [{"warehouse":r["warehouse"],"sku":r["sku"],"product":r["name"],"unit":r["unit"],
                     "on_hand":fmt(r["on_hand"]),"reserved":fmt(r["reserved"]),
                     "available":fmt(r["on_hand"]-r["reserved"]),"incoming_po":fmt(r["inbound"]),
                     "minimum":fmt(r["minimum_milli"])} for r in rows]

    def reorder_suggestions(self,org,as_of=None,window_days=14):
        if window_days<1: raise StockFlowError("Window must be positive")
        end=day(as_of); start=(date.fromisoformat(end)-timedelta(days=window_days-1)).isoformat()
        with self.read() as c:
            report=[]
            for row in self.inventory(org):
                p=self.one(c,"SELECT * FROM products WHERE org_id=? AND sku=?",(org,row["sku"]))
                wh=self.one(c,"SELECT id FROM warehouses WHERE org_id=? AND code=?",(org,row["warehouse"]))
                total=c.execute("SELECT COALESCE(SUM(sl.quantity_milli),0) FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.org_id=? AND s.warehouse_id=? AND sl.product_id=? AND s.sold_date BETWEEN ? AND ?",(org,wh["id"],p["id"],start,end)).fetchone()[0]
                daily=Decimal(total)/window_days
                threshold=max(Decimal(p["minimum_milli"]),daily*p["lead_days"]+p["safety_milli"])
                pos=milli(row["on_hand"])-milli(row["reserved"])+milli(row["incoming_po"])
                target=max(Decimal(p["minimum_milli"]),daily*(p["lead_days"]+p["review_days"])+p["safety_milli"])
                inc=10**(3-p["precision_digits"])
                needed=int((max(Decimal(0),target-Decimal(pos))/inc).to_integral_value(rounding=ROUND_CEILING))*inc
                if pos<=threshold:
                    report.append({"warehouse":row["warehouse"],"sku":row["sku"],"unit":row["unit"],"available":row["available"],
                                   "incoming_po":row["incoming_po"],"average_daily_sales":str((daily/SCALE).quantize(Decimal('0.001'))),
                                   "reorder_point":fmt(int(threshold)),"inventory_position":fmt(pos),
                                   "suggested_order":fmt(needed),"severity":"OUT_OF_STOCK" if milli(row["available"])==0 else "REORDER"})
            return report

    def daily_report(self,org,on_date=None):
        d=day(on_date)
        with self.read() as c:
            self.one(c,"SELECT id FROM organizations WHERE id=?",(org,))
            sales=c.execute('''SELECT COUNT(DISTINCT s.id) AS cnt,COALESCE(SUM(sl.quantity_milli),0) AS qty,
                              COALESCE(SUM(sl.quantity_milli*sl.unit_price_cents),0) AS gross_scaled
                              FROM sales s LEFT JOIN sale_lines sl ON sl.sale_id=s.id WHERE s.org_id=? AND s.sold_date=?''',(org,d)).fetchone()
            movements=c.execute("SELECT event_type,COUNT(*) AS events,SUM(delta_milli) AS delta FROM stock_events WHERE org_id=? AND event_date=? GROUP BY event_type ORDER BY event_type",(org,d)).fetchall()
            return {"organization":org,"date":d,"sales_orders":sales["cnt"],"quantity_sold":fmt(sales["qty"]),
                    "gross_sales_cents":cents_from_scaled(sales["gross_scaled"]),
                    "movements":[{"type":x["event_type"],"events":x["events"],"net_quantity":fmt(x["delta"])} for x in movements]}

    def sales_trend(self,org,as_of=None,days=14):
        if not 1<=days<=90: raise StockFlowError("Days must be between 1 and 90")
        last=date.fromisoformat(day(as_of))
        first=last-timedelta(days=days-1)
        with self.read() as c:
            self.one(c,"SELECT id FROM organizations WHERE id=?",(org,))
            rows=c.execute("""SELECT s.sold_date, COUNT(DISTINCT s.id) AS orders,
                   COALESCE(SUM(sl.quantity_milli),0) AS qty,
                   COALESCE(SUM(sl.quantity_milli*sl.unit_price_cents),0) AS gross_scaled
                   FROM sales s JOIN sale_lines sl ON sl.sale_id=s.id
                   WHERE s.org_id=? AND s.sold_date BETWEEN ? AND ?
                   GROUP BY s.sold_date""",(org,first.isoformat(),last.isoformat()))
            by_day={x["sold_date"]:x for x in rows}
            result=[]
            for i in range(days):
                d=(first+timedelta(days=i)).isoformat()
                r=by_day.get(d)
                result.append({"date":d,"orders":r["orders"] if r else 0,
                    "quantity":fmt(r["qty"]) if r else "0",
                    "gross_sales_cents":cents_from_scaled(r["gross_scaled"]) if r else 0})
            return result

    def recent_movements(self,org,limit=12):
        if not 1 <= limit <= 50:
            raise StockFlowError("Limit must be between 1 and 50")
        with self.read() as c:
            self.one(c,"SELECT id FROM organizations WHERE id=?",(org,))
            records=c.execute("""SELECT e.event_date, e.created_at, e.event_type,
                      e.delta_milli, e.note, e.event_key, w.code AS warehouse,
                      p.name AS product, p.sku, p.unit
                      FROM stock_events e JOIN warehouses w ON e.warehouse_id=w.id
                      JOIN products p ON e.product_id=p.id
                      WHERE e.org_id=? ORDER BY e.created_at DESC,e.rowid DESC LIMIT ?""",(org,limit)).fetchall()
            return [{"date":r["event_date"],"type":r["event_type"],"delta":fmt(r["delta_milli"]),
                     "warehouse":r["warehouse"],"sku":r["sku"],"product":r["product"],
                     "unit":r["unit"],"reference":r["event_key"]} for r in records]

    def purchase_orders(self,org):
        with self.read() as c:
            return [dict(r) for r in c.execute("SELECT po.po_ref,po.supplier,po.expected_date,po.status,w.code AS warehouse FROM purchase_orders po JOIN warehouses w ON w.id=po.warehouse_id WHERE po.org_id=? ORDER BY po.expected_date",(org,))]
