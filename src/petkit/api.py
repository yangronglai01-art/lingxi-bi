"""New API. Principal comes only from trusted token mapping, not request role fields."""
import sqlite3
from dataclasses import asdict
from typing import Optional
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from src import config, schema
from src.engine.engine import Answer,QueryEngine
from src.history import HistoryStore
from .access import authenticate
from .history import history_for
from .planning import examples
from .retrieval import current_catalog,retrieve
from .settings import MODE

app=FastAPI(title="灵犀 · 小佩业务模拟问数",version="2.0.0",
    description="500表模拟目录；非小佩内部系统；demo为明确标记的离线模板模式")
engine=QueryEngine()


def identity(authorization: Optional[str]=Header(default=None)):
    token=(authorization or "").removeprefix("Bearer ")
    try: return authenticate(token)
    except sqlite3.Error as exc: raise HTTPException(503,"身份存储暂不可用") from exc
    except (PermissionError,ValueError,KeyError) as exc: raise HTTPException(401,str(exc)) from exc


def store(principal):
    if config.PROJECT_PROFILE=="legacy": return HistoryStore()
    return history_for(principal)


class AskRequest(BaseModel):
    question: str=Field(min_length=1,max_length=500)


@app.get("/health")
def health():
    from .settings import CURRENT
    return dict(status="ok",profile=config.PROJECT_PROFILE,mode=MODE,synthetic=True,
        warehouse_ready=config.WAREHOUSE_PATH.exists() if config.PROJECT_PROFILE=="legacy" else CURRENT.exists())


@app.get("/schema")
def get_schema(q:str="",core_only:bool=False,offset:int=0,limit:int=50,principal=Depends(identity)):
    if offset<0 or not 1<=limit<=100: raise HTTPException(422,"offset>=0，limit在1到100之间")
    if config.PROJECT_PROFILE=="legacy": return dict(tables=[asdict(t) for t in schema.TABLES.values()])
    try: cat=current_catalog()
    except FileNotFoundError as exc: raise HTTPException(503,str(exc)) from exc
    filtered=[t for t in cat["tables"].values() if principal.allowed(t) and
        (not core_only or t["core"]) and (not q or q.lower() in (t["name"]+t["description"]).lower())]
    return dict(total=len(filtered),version=cat["version"],generation=cat["generation"],
                tables=filtered[offset:offset+limit],synthetic=True)


@app.get("/metrics")
def metrics(principal=Depends(identity)):
    try: cat=current_catalog()
    except FileNotFoundError as exc: raise HTTPException(503,str(exc)) from exc
    allowed={n for n,t in cat["tables"].items() if principal.allowed(t)}
    return dict(metrics=[m for m in cat["metrics"] if set(m["dependencies"])<=allowed])


@app.get("/examples")
def get_examples(principal=Depends(identity)):
    # Only reveal approved examples for metrics the principal can use.
    try: cat=current_catalog()
    except FileNotFoundError as exc: raise HTTPException(503,str(exc)) from exc
    allowed={n for n,t in cat["tables"].items() if principal.allowed(t)}
    ids={m["id"] for m in cat["metrics"] if set(m["dependencies"])<=allowed}
    return dict(questions=[x["question"] for x in examples() if x["metric_id"] in ids])


@app.post("/retrieve")
def inspect_retrieval(req:AskRequest,principal=Depends(identity)):
    from .planning import plan
    try:
        proposal=plan(req.question,demo=False)
        return retrieve(req.question,principal,proposal.get("metric"))
    except PermissionError as exc: raise HTTPException(403,str(exc)) from exc
    except FileNotFoundError as exc: raise HTTPException(503,str(exc)) from exc


@app.post("/ask")
def ask(req:AskRequest,principal=Depends(identity)):
    import os
    if os.getenv("PETKIT_DEPLOYMENT")=="simulated":
        raise HTTPException(409,"模拟业务实例请通过POST /jobs提交持久任务")
    answer=engine.ask(req.question,principal=principal)
    history_id=store(principal).add(question=answer.question,sql=answer.sql,result=answer.result,
        chart=answer.chart,insights=answer.insights,error=answer.error,mock_used=answer.mock_used)
    return dict(**answer.model_dump(),history_id=history_id)


@app.get("/history")
def history(favorite_only:bool=False,limit:int=100,principal=Depends(identity)):
    if not 1<=limit<=100: raise HTTPException(422,"limit在1到100之间")
    return dict(items=store(principal).list(favorite_only=favorite_only,limit=limit))


@app.get("/history/{history_id}")
def get_history(history_id:int,principal=Depends(identity)):
    item=store(principal).get(history_id)
    if item is None: raise HTTPException(404,"记录不存在")
    return item


@app.post("/history/{history_id}/favorite")
def favorite(history_id:int,principal=Depends(identity)):
    value=store(principal).toggle_favorite(history_id)
    if value is None: raise HTTPException(404,"记录不存在")
    return dict(id=history_id,favorited=bool(value))


@app.delete("/history/{history_id}")
def delete(history_id:int,principal=Depends(identity)):
    if not store(principal).delete(history_id): raise HTTPException(404,"记录不存在")
    return dict(deleted=True)


from .operations_api import register
register(app,identity,AskRequest)
