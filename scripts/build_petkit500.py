"""Build a new immutable simulation snapshot, without running evaluation."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.petkit.builder import build

if __name__ == "__main__":
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser()
    parser.add_argument("--orders",type=int,default=3000)
    parser.add_argument("--extension-periods",type=int,default=12)
    args=parser.parse_args()
    summary=build(args.orders,args.extension_periods)
    print(json.dumps({k:v for k,v in summary.items() if k!="counts"},ensure_ascii=False,indent=2))
