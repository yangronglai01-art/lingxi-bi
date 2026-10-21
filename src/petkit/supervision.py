"""Bounded process-exit supervision; interrupted jobs retain their existing leases."""
import json,sqlite3,threading,time

def heartbeat(data):
    with sqlite3.connect(data/'control.sqlite3',timeout=10) as c:
        row=c.execute("SELECT value,updated FROM runtime WHERE name='worker'").fetchone()
    if not row:return None
    try:value=json.loads(row[0])
    except (ValueError,TypeError):return None
    if not isinstance(value,dict):return None
    return dict(value,age=time.time()-row[1])

def persist(data,state,event=None):
    now=time.time();payload=json.dumps(state,ensure_ascii=False)
    with sqlite3.connect(data/'control.sqlite3',timeout=10) as c:
        c.execute("INSERT INTO runtime VALUES('supervisor',?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated=excluded.updated",(payload,now))
        if event:c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(now,'local-supervisor',event,payload))

class WorkerSupervisor:
    def __init__(self,current,launch,publish,read_heartbeat,stop_process,session,max_restarts=3,backoff=.5,now=time.monotonic):
        if type(max_restarts) is not int or not 0<=max_restarts<=10 or not .1<=backoff<=10:raise ValueError('无效重启预算或退避时间')
        self.current=current;self.launch=launch;self.publish=publish;self.read_heartbeat=read_heartbeat;self.stop_process=stop_process
        self.session=session;self.max_restarts=max_restarts;self.backoff=backoff;self.now=now
        self.phase='starting';self.restarts=0;self.reason='';self.due=0;self.started=now();self.last_emit=-100
        self.failed=threading.Event();self.stopping=threading.Event();self.thread=None

    def state(self):
        return dict(session=self.session,phase=self.phase,restarts=self.restarts,max_restarts=self.max_restarts,
                    worker_pid=self.current['worker'].pid if self.current.get('worker') else None,reason=self.reason)

    def emit(self,event=None):
        if event or self.now()-self.last_emit>=3:
            self.publish(self.state(),event);self.last_emit=self.now()

    def stop_owned(self):
        for name in ['ui','api','worker']:
            process=self.current.get(name)
            if process is not None:self.stop_process(process)

    def fail(self,reason):
        if self.failed.is_set():return
        self.reason=reason;self.phase='failed';self.failed.set();self.stopping.set()
        try:self.emit('supervisor_failed')
        except Exception:pass
        finally:self.stop_owned()

    def schedule(self,reason):
        if self.restarts>=self.max_restarts:self.fail('worker_restart_budget_exhausted');return
        self.restarts+=1;self.phase='backoff';self.reason=reason
        self.due=self.now()+min(10,self.backoff*2**(self.restarts-1));self.emit('worker_restart_scheduled')

    def tick(self):
        if self.stopping.is_set():return
        for name in ['api','ui']:
            if self.current[name].poll() is not None:self.fail(name+'_exited');return
        if self.phase=='backoff':
            if self.now()<self.due:self.emit();return
            try:self.current['worker']=self.launch()
            except Exception:self.schedule('worker_spawn_failed');return
            self.phase='starting';self.started=self.now();self.emit('worker_spawned')
        process=self.current['worker']
        if process.poll() is not None:self.schedule('worker_exited');return
        value=self.read_heartbeat()
        healthy=bool(value and value.get('session')==self.session and value.get('pid')==process.pid and 0<=value.get('age',999)<45)
        if healthy:
            if self.phase!='running':self.phase='running';self.reason='';self.emit('worker_ready')
        elif self.phase=='starting' and self.now()-self.started>45:self.fail('worker_start_timeout');return
        elif self.phase=='running':self.phase='unhealthy';self.reason='worker_heartbeat_stale';self.emit('worker_unhealthy')
        self.emit()

    def run(self):
        try:
            self.emit('supervisor_started')
            while not self.stopping.wait(.1):self.tick()
        except Exception:self.fail('supervisor_state_unavailable')

    def start(self):
        self.thread=threading.Thread(target=self.run,name='worker-supervisor',daemon=True);self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=20)
        self.stop_owned()
        if not self.failed.is_set():
            self.phase='stopped';self.reason='normal_shutdown'
            try:self.emit('supervisor_stopped')
            except Exception:self.failed.set();self.reason='shutdown_state_unavailable'
