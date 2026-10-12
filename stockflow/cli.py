"""CLI for launching and exploring the StockFlow MVP without Docker."""
import argparse
import json
from pathlib import Path
from .demo import seed
from .service import StockFlow, StockFlowError

def main():
    p=argparse.ArgumentParser(description="StockFlow MVP — synthetic cross-industry inventory analytics")
    p.add_argument("command",choices=["seed","organizations","inventory","report","reorder","orders","import-csv","demo"])
    p.add_argument("--db",default="data/stockflow_demo.db")
    p.add_argument("--org",default="online-retail")
    p.add_argument("--date",default=None)
    p.add_argument("--file",default=None,help="Input CSV path for import-csv")
    args=p.parse_args()
    try:
        if args.command=="seed":
            store=seed(args.db)
            result={"status":"seeded","database":args.db,"organizations":[x["id"] for x in store.organizations()]}
        else:
            store=StockFlow(args.db)
            if args.command=="organizations": result=store.organizations()
            elif args.command=="inventory": result=store.inventory(args.org)
            elif args.command=="report": result=store.daily_report(args.org,args.date)
            elif args.command=="reorder": result=store.reorder_suggestions(args.org,args.date)
            elif args.command=="orders": result=store.purchase_orders(args.org)
            elif args.command=="import-csv":
                if not args.file: p.error("--file is required for import-csv")
                result=store.import_movements_csv(args.org,args.file)
            elif args.command=="demo":
                result={"organizations":len(store.organizations()),"organization":args.org,
                        "daily_report":store.daily_report(args.org,args.date),
                        "inventory":store.inventory(args.org),
                        "reorders":store.reorder_suggestions(args.org,args.date),
                        "purchase_orders":store.purchase_orders(args.org)}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (StockFlowError,RuntimeError) as exc:
        p.exit(2,f"StockFlow error: {exc}\n")

if __name__=="__main__": main()
