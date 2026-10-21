"""Single-machine application launcher with bounded worker process supervision."""
import argparse
from contextlib import contextmanager
import json,os,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]

def child_python(env):
    # Execute the real Windows interpreter while retaining the venv's prefix and packages.
    # This avoids supervising the venv redirector PID instead of the actual Python PID.
    if sys.platform=='win32':
        env['__PYVENV_LAUNCHER__']=sys.executable
        return sys._base_executable
    return sys.executable

def stop_process(process):
    if process.poll() is None:process.terminate()
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

@contextmanager
def running(mode='llm',api_port=18000,ui_port=18501,data_dir=None,supervise=True,max_worker_restarts=3,restart_backoff=.5):
    if mode not in {'demo','llm'} or api_port==ui_port or not all(1<=p<=65535 for p in [api_port,ui_port]):raise ValueError('无效模式或端口')
    if type(max_worker_restarts) is not int or not 0<=max_worker_restarts<=10 or not .1<=restart_backoff<=10:raise ValueError('无效重启预算或退避时间')
    from src.petkit.settings import DATA as DEFAULT_DATA
    from src.petkit.supervision import WorkerSupervisor,heartbeat,persist
    from filelock import FileLock,Timeout
    data=Path(data_dir).resolve() if data_dir else DEFAULT_DATA
    if not (data/'control.sqlite3').is_file():raise RuntimeError('先运行 scripts/provision_simulated.py')
    env=os.environ.copy();env.update(PROJECT_PROFILE='petkit',PETKIT_DEPLOYMENT='simulated',PETKIT_MODE=mode,PETKIT_DATA_DIR=str(data),PYTHONIOENCODING='utf-8')
    for key in ['PETKIT_LOCAL_UI_TOKEN','PETKIT_API_TOKENS','PETKIT_LAUNCH_SESSION']:env.pop(key,None)
    session=uuid.uuid4().hex
    if supervise:env['PETKIT_LAUNCH_SESSION']=session
    config=data/'embedding_runtime.json'
    if 'PETKIT_EMBEDDING_MODEL' not in env and config.exists():env['PETKIT_EMBEDDING_MODEL']=json.loads(config.read_text(encoding='utf-8'))['model']
    if env.get('PETKIT_EMBEDDING_MODEL') and not Path(env['PETKIT_EMBEDDING_MODEL']).is_dir():raise RuntimeError('配置的本地Embedding模型目录不存在')
    if mode=='llm':
        from dotenv import dotenv_values
        if not (env.get('API_KEY') or dotenv_values(ROOT/'.env').get('API_KEY')):raise RuntimeError('未配置API_KEY')
    interpreter=child_python(env)
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    logs=data/'simulated_logs';logs.mkdir(parents=True,exist_ok=True)
    children=[];handles=[];current={};guard=None
    lock=FileLock(str(data/'simulated_launcher.lock'),timeout=0)
    try:lock.acquire()
    except Timeout as exc:raise RuntimeError('该实例已有启动器运行，请勿重复启动') from exc
    def launch(name,command):
        handle=(logs/(name+'-'+uuid.uuid4().hex[:8]+'.log')).open('w',encoding='utf-8');handles.append(handle)
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=handle,stderr=subprocess.STDOUT,creationflags=flags)
        children.append(process);return process
    worker_command=[interpreter,'-m','src.petkit.worker']
    try:
        for name,command in [('worker',worker_command),('api',[interpreter,'-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port',str(api_port)]),
              ('ui',[interpreter,'-m','streamlit','run','src/app.py','--server.address','127.0.0.1','--server.port',str(ui_port),'--server.headless','true'])]:
            current[name]=launch(name,command)
        if supervise:
            guard=WorkerSupervisor(current,lambda:launch('worker',worker_command),lambda state,event:persist(data,state,event),lambda:heartbeat(data),stop_process,session,max_worker_restarts,restart_backoff)
            guard.start()
        api=f'http://127.0.0.1:{api_port}';ui=f'http://127.0.0.1:{ui_port}'
        for url in [api+'/ready',ui+'/_stcore/health']:
            deadline=time.monotonic()+45
            while True:
                if guard and guard.failed.is_set():raise RuntimeError('模拟守护启动失败：'+guard.reason)
                if not guard and any(p.poll() is not None for p in children):raise RuntimeError('子进程退出，请查看simulated_logs')
                try:
                    with urlopen(url,timeout=2) as response:
                        if response.status==200:
                            if url.endswith('/ready') and supervise:
                                if json.load(response).get('launch_session')==session:break
                            else:break
                except (OSError,TimeoutError,ValueError):pass
                if time.monotonic()>deadline:raise TimeoutError('模拟实例就绪超时')
                time.sleep(.2)
        yield dict(api_url=api,ui_url=ui,processes=children,current_processes=current,supervisor=guard,session=session)
    finally:
        if guard:guard.close()
        for name in ['ui','api','worker']:
            if name in current:stop_process(current[name])
        for process in children:stop_process(process)
        for handle in handles:handle.close()
        lock.release()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['demo','llm'],default='llm')
    parser.add_argument('--api-port',type=int,default=18000);parser.add_argument('--ui-port',type=int,default=18501)
    parser.add_argument('--data-dir',default=None);parser.add_argument('--no-supervise',action='store_true',help='仅用于人工故障验收，关闭自动工作进程守护')
    parser.add_argument('--max-worker-restarts',type=int,default=3);parser.add_argument('--restart-backoff',type=float,default=.5)
    args=parser.parse_args()
    try:
        with running(args.mode,args.api_port,args.ui_port,args.data_dir,not args.no_supervise,args.max_worker_restarts,args.restart_backoff) as services:
            print('模拟业务工作台：'+services['ui_url'],flush=True);print('API：'+services['api_url']+'/docs',flush=True)
            print('使用本机员工凭据登录；Ctrl+C停止。工作进程守护：'+('开启' if services['supervisor'] else '关闭'),flush=True)
            guard=services['supervisor']
            if guard:
                while not guard.failed.wait(.3):pass
                raise RuntimeError('守护停止服务：'+guard.reason)
            while all(p.poll() is None for p in services['processes']):time.sleep(.3)
    except KeyboardInterrupt:print('模拟实例已停止。未完成任务等待租约过期后标记中断。')

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0,str(ROOT));main()
