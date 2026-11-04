"""Independent workers coordinated exclusively through PostgreSQL leases."""
import logging
import signal
import threading
import uuid
from .identity import Denied
from .store import Conflict,Store
from .settings import Settings
from .query import Query


def once(settings,store,query=None):
    job=store.claim(uuid.uuid4().hex,settings.tenant)
    if not job:return False
    stop=threading.Event()
    def renew():
        while not stop.wait(5):
            try:
                if not store.heartbeat(job):return
            except Exception:return
    thread=threading.Thread(target=renew,daemon=True);thread.start()
    try:
        p=store.principal(job['tenant'],job['subject'])
        if p.policy_version!=job['policy_version'] or p.scope!=job['scope']:raise Denied('Grant changed')
        result=(query or Query(settings,store)).run(job,p)
        store.finish(job,p,result)
    except Exception as exc:
        code=('AUTHORIZATION_CHANGED' if isinstance(exc,(Denied,PermissionError)) else
              'LEASE_LOST' if isinstance(exc,Conflict) else 'QUERY_DEADLINE' if isinstance(exc,TimeoutError) else 'QUERY_FAILED')
        store.fail(job,code)
        logging.getLogger(__name__).warning('analytics_job_failed code=%s type=%s request_id=%s',code,type(exc).__name__,job['id'])
    finally:stop.set();thread.join(6)
    return True


def main():
    settings=Settings.from_env();store=Store(settings.dsn);query=Query(settings,store);stop=threading.Event()
    if settings.environment in {'staging','production'}:store.assert_runtime_role()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        if not once(settings,store,query):stop.wait(.5)


if __name__=='__main__':main()
