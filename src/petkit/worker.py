"""Single-worker durable query processing, with leases and cooperative cancellation."""
import os
import threading
import uuid
from . import control

def run_once(owner=None):
    owner=owner or uuid.uuid4().hex
    control.tick(owner)
    control.recover()
    job=control.claim(owner)
    if not job: return False
    stop=threading.Event()
    def pulse():
        while not stop.wait(15): control.heartbeat(job['id'],owner)
    thread=threading.Thread(target=pulse,daemon=True); thread.start()
    try:
        principal=control.principal_for(job['subject'])
        if principal.policy_version!=job['policy_version']: raise PermissionError('任务提交后授权已变更')
        from .retrieval import current_catalog
        if current_catalog()['generation']!=job['generation']: raise ValueError('数据版本已变化，请重新提交任务')
        from .service import ask
        answer=ask(job['question'],principal)
        control.verify(principal)
        if answer.generation and answer.generation!=job['generation']: raise ValueError('数据版本已变化，结果不发布')
        control.finish(job['id'],owner,answer.model_dump())
        control.audit(dict(subject=principal.subject,job_id=job['id'],answer_status=answer.status,request_id=answer.request_id,generation=answer.generation,elapsed_ms=answer.elapsed_ms),'job_finished')
    except Exception as exc:
        # Secrets and provider internals are not persisted in worker exception messages.
        control.finish(job['id'],owner,error='任务未完成：'+type(exc).__name__)
        control.audit(dict(subject=job['subject'],job_id=job['id'],error_type=type(exc).__name__),'job_failed')
    finally:
        stop.set(); thread.join(timeout=2)
    return True

def main():
    from filelock import FileLock
    from .settings import DATA
    control.initialize()
    with FileLock(str(DATA/'query_worker.lock'),timeout=0):
        owner=uuid.uuid4().hex
        print('持久化问数工作进程已启动；Ctrl+C停止。',flush=True)
        while True:
            if not run_once(owner): threading.Event().wait(.3)

if __name__=='__main__': main()
