"""Deterministic, synthetic demo data for four unrelated industries."""
from datetime import date, timedelta
from random import Random
from .service import StockFlow

BUSINESSES = [
 ("online-retail","Nova Commerce","E-commerce","MAIN","Central Fulfillment","EAST","East Hub",[
  ("HEADPHONES","Wireless Headphones","pcs",150,12,20,5,7,3999),
  ("USB-CABLE","USB-C Charging Cable","pcs",220,22,30,5,7,999),
  ("LAPTOP-STAND","Adjustable Laptop Stand","pcs",90,8,15,9,7,2999)]),
 ("food-wholesale","Harvest Foods","Food wholesale","MAIN","Cold Storage","NORTH","North Storage",[
  ("RICE-BULK","Basmati Rice","kg",175,15,30,5,7,350),
  ("OLIVE-OIL","Olive Oil","L",115,12,25,9,7,950),
  ("CANNED-TOMATO","Canned Tomatoes","pcs",180,20,40,4,7,220)]),
 ("auto-parts","Motion Parts","Auto spare parts","MAIN","Parts Center","SOUTH","South Depot",[
  ("OIL-FILTER","Oil Filter","pcs",100,8,15,8,7,1299),
  ("BRAKE-PAD","Brake Pad Set","pcs",100,10,15,12,7,4500),
  ("BELT-6PK","Drive Belt","pcs",70,5,12,14,7,2200)]),
 ("office-supplies","PaperBridge","Office supplies","MAIN","City Warehouse","WEST","West Warehouse",[
  ("A4-PAPER","A4 Paper Ream","pcs",210,18,40,4,7,650),
  ("PEN-BLUE","Blue Ballpoint Pen","pcs",250,26,45,6,7,99),
  ("TONER-BLK","Black Printer Toner","pcs",75,6,12,10,7,3900)])
]

def seed(db_path="data/stockflow_demo.db"):
    store=StockFlow(db_path)
    if store.organizations():
        raise RuntimeError("Database is not empty. Use a new --db path for seed to avoid mixing data.")
    rng=Random(20261012)
    today=date.today()
    for org,name,sector,wh,wname,other,oname,products in BUSINESSES:
        store.create_org(org,name,sector)
        store.create_warehouse(org,wh,wname)
        store.create_warehouse(org,other,oname)
        for sku,title,unit,opening,safety,minimum,lead,review,price in products:
            store.create_product(org,sku,title,unit,safety=str(safety),minimum=str(minimum),lead_days=lead,review_days=review)
            qty=str(opening)
            store.record_movement(org,wh,sku,"OPENING",qty,f"opening:{sku}:main",(today-timedelta(days=32)).isoformat(),"Synthetic starting stock")
            store.record_movement(org,other,sku,"OPENING",str(opening//3),f"opening:{sku}:branch",(today-timedelta(days=32)).isoformat(),"Synthetic branch stock")
        # Daily sales across 21 days, including today; multi-line sales orders.
        for n in range(20,-1,-1):
            sales_day=(today-timedelta(days=n)).isoformat()
            items=[]
            for ix,(sku,_,unit,_,_,_,_,_,price) in enumerate(products):
                q = rng.randint(1,3) if ix==2 else rng.randint(2,5)
                if unit in ("kg","L"):
                    quantity=f"{q}.250"
                else: quantity=str(q)
                items.append({"sku":sku,"quantity":quantity,"unit_price_cents":price})
            store.sell(org,wh,f"SALE-{org}-{sales_day}",items,sales_day)
        # A transfer, a reservation, and a purchase order in each organization.
        p0=products[0][0]
        store.transfer(org,wh,other,p0,"2",f"transfer-{org}-{today.isoformat()}",today.isoformat())
        store.reserve(org,other,p0,"1",f"reserve-{org}-001")
        p1=products[1][0]
        store.create_po(org,wh,f"PO-{org}-001",f"Sample Supplier for {name}",
                        (today+timedelta(days=4)).isoformat(),[{"sku":p1,"quantity":"50"}])
        # Half-delivery to illustrate partial receipts (except online retail).
        if org=="office-supplies":
            store.receive_po(org,f"PO-{org}-001",f"DEL-{org}-A",[{"sku":p1,"quantity":"20"}],today.isoformat())
    return store
