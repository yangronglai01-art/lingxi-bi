"""Local demo resource boundary. No claims of native RLS or full process isolation."""
import json
import threading
from .settings import DATA, MAX_BYTES, MAX_ROWS, QUERY_SECONDS

SLOTS=threading.BoundedSemaphore(3)


def execute(checked,generation,principal=None):
    import os
    if principal is not None and principal.managed:
        from .control import verify
        verify(principal)
    elif os.getenv("PETKIT_DEPLOYMENT")=="simulated":
        raise PermissionError("模拟落地执行必须使用持久身份")
    import duckdb
    from src.engine.executor import QueryResult, _to_native
    folder=DATA/generation
    if folder.parent.resolve()!=DATA.resolve(): raise ValueError("无效快照")
    if not SLOTS.acquire(timeout=1): raise TimeoutError("查询并发已满，请稍后重试")
    conn=None; timer=None; timed_out=threading.Event()
    try:
        conn=duckdb.connect(str(folder/"warehouse.duckdb"),read_only=True,
            config={"enable_external_access":"false","memory_limit":"512MB","threads":"2",
                    "max_temp_directory_size":"0B"})
        if principal is not None:
            from .retrieval import load_catalog
            catalog=load_catalog(generation)
            if any(not principal.allowed(catalog["tables"][name]) for name in checked.tables):
                raise PermissionError("执行表超出当前授权")
            from .row_policy import install
            install(conn,principal,checked.tables)
        def cancel():
            timed_out.set()
            conn.interrupt()
        timer=threading.Timer(QUERY_SECONDS,cancel); timer.daemon=True; timer.start()
        cursor=conn.execute(checked.sql)
        values=cursor.fetchmany(MAX_ROWS+1)
        truncated=len(values)>MAX_ROWS; values=values[:MAX_ROWS]
        rows=[[_to_native(v) for v in row] for row in values]
        if len(json.dumps(rows,ensure_ascii=False).encode())>MAX_BYTES:
            raise ValueError("查询结果超过字节预算，请缩小范围")
        if principal is not None and principal.managed:
            from .control import verify
            verify(principal)
        return QueryResult(columns=[d[0] for d in cursor.description],rows=rows,row_count=len(rows)),truncated
    except Exception as exc:
        if timed_out.is_set(): raise TimeoutError("查询超过执行时间预算，已请求中断") from exc
        raise
    finally:
        if timer:
            timer.cancel(); timer.join()
        if conn: conn.close()
        SLOTS.release()
