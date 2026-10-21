"""Prepare offline embeddings; configuration is enabled only after successful build."""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.petkit.retrieval import Index,current_catalog
from src.petkit.settings import DATA

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',required=True)
    args=parser.parse_args()
    start=time.monotonic()
    index=Index(current_catalog(),str(Path(args.model).resolve()))
    allowed=set(index.catalog['tables'])
    hits=index.search('2026年5月每种产品型号的净销售额',allowed)
    report=dict(generation=index.catalog['generation'],model=index.model_path,
        device='cpu',max_seq_length=256,unique_vectors=len(index.dense),
        dimensions=index.dense.shape[1],elapsed_seconds=round(time.monotonic()-start,2),
        sample_hits=hits)
    (ROOT/'reports/petkit500_embedding_build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (DATA/'embedding_runtime.json').write_text(json.dumps({'model':index.model_path},ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sample_hits'},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
