"""Optional FastAPI interface. Install requirements-api.txt to run.

Demo-only: no authentication. Bind to localhost; do not expose publicly.
"""
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, Field
from .service import StockFlow, StockFlowError

app=FastAPI(title="StockFlow MVP API",version="0.2.0",description="Local demo API. No auth; do not expose to the internet.")

def store(): return StockFlow(os.environ.get("STOCKFLOW_DB","data/stockflow_demo.db"))

class Movement(BaseModel):
    org:str; warehouse:str; sku:str; event_type:str
    quantity:str; event_key:str; event_date:str|None=None; note:str=""
class SaleLine(BaseModel):
    sku:str; quantity:str; unit_price_cents:int=Field(ge=0); reservation_ref:str|None=None
class Sale(BaseModel):
    org:str; warehouse:str; sale_ref:str; lines:list[SaleLine]; sold_date:str|None=None
class Reservation(BaseModel):
    org:str; warehouse:str; sku:str; quantity:str; reservation_ref:str
class Transfer(BaseModel):
    org:str; source:str; destination:str; sku:str; quantity:str; reference:str; event_date:str|None=None
class PurchaseOrder(BaseModel):
    org:str; warehouse:str; po_ref:str; supplier:str; expected_date:str; lines:list[dict]
class Receipt(BaseModel):
    org:str; po_ref:str; delivery_ref:str; lines:list[dict]; received_date:str|None=None

@app.get("/health")
def health(): return {"status":"ok","demo_only":True}
@app.get("/organizations")
def organizations(): return store().organizations()
@app.get("/organizations/{org}/inventory")
def inventory(org:str):
    try: return store().inventory(org)
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.get("/organizations/{org}/daily-report")
def report(org:str,on_date:str|None=None):
    try: return store().daily_report(org,on_date)
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.get("/organizations/{org}/reorder-suggestions")
def reorder(org:str,as_of:str|None=None,window_days:int=14):
    try: return store().reorder_suggestions(org,as_of,window_days)
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.get("/organizations/{org}/purchase-orders")
def purchase_orders(org:str): return store().purchase_orders(org)

@app.get("/organizations/{org}/movements")
def recent_movements(org:str,limit:int=12):
    try: return store().recent_movements(org,limit)
    except StockFlowError as e: raise HTTPException(400,str(e))

@app.post("/movements")
def post_movement(x:Movement):
    try: return {"inserted":store().record_movement(x.org,x.warehouse,x.sku,x.event_type,x.quantity,x.event_key,x.event_date,x.note)}
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.post("/sales")
def post_sale(x:Sale):
    try: return {"inserted":store().sell(x.org,x.warehouse,x.sale_ref,[line.model_dump() for line in x.lines],x.sold_date)}
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.post("/reservations")
def post_reservation(x:Reservation):
    try: return {"inserted":store().reserve(x.org,x.warehouse,x.sku,x.quantity,x.reservation_ref)}
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.post("/transfers")
def post_transfer(x:Transfer):
    try: return {"inserted":store().transfer(x.org,x.source,x.destination,x.sku,x.quantity,x.reference,x.event_date)}
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.post("/purchase-orders")
def post_po(x:PurchaseOrder):
    try: return {"inserted":store().create_po(x.org,x.warehouse,x.po_ref,x.supplier,x.expected_date,x.lines)}
    except StockFlowError as e: raise HTTPException(400,str(e))
@app.post("/purchase-receipts")
def post_receipt(x:Receipt):
    try: return {"inserted":store().receive_po(x.org,x.po_ref,x.delivery_ref,x.lines,x.received_date)}
    except StockFlowError as e: raise HTTPException(400,str(e))

@app.get("/dashboard",include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")

@app.get("/organizations/{org}/sales-trend")
def trend(org:str,as_of:str|None=None,days:int=14):
    try: return store().sales_trend(org,as_of,days)
    except StockFlowError as e: raise HTTPException(400,str(e))
