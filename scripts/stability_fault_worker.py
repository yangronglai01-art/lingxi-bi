"""Acceptance-only child: block after real worker claim, before query execution."""
import argparse,sys,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.petkit import service,worker

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--marker',required=True);args=parser.parse_args()
    marker=Path(args.marker)
    original=service.ask
    def blocked(*a,**kw):
        marker.write_text('claimed-before-query',encoding='utf-8')
        threading.Event().wait(120)
        return original(*a,**kw)
    service.ask=blocked
    worker.main()

if __name__=='__main__':main()
