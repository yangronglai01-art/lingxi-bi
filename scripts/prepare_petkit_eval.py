"""Write evaluation inputs only. Does not run queries or produce scores."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.petkit.catalog import METRICS
from src.petkit.planning import examples

def main():
    out=Path(__file__).resolve().parents[1]/"data"/"petkit500"/"eval_cases.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    metrics={m["id"]:m for m in METRICS}
    cases=[dict(id=f"P{i:03}",question=x["question"],reference_sql=x["sql"],
        required_tables=metrics[x["metric_id"]]["dependencies"],metric_id=x["metric_id"],
        split="development",review_status="模板派生参考SQL，需独立审核后建立盲测，不是准确率证明")
        for i,x in enumerate(examples(),1)]
    for q,status in [("各型号激活率","clarification"),("各型号故障率","clarification"),
                     ("删除所有订单","unsupported"),("随便猜一个收入数字","unsupported")]:
        cases.append(dict(question=q,expected_demo_status=status,split="development"))
    out.write_text(json.dumps(dict(evaluated=False,cases=cases),ensure_ascii=False,indent=2),encoding="utf-8")
    print(out)

if __name__=="__main__": main()
