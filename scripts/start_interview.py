"""Local-only interview launcher with per-run credentials and child cleanup."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT=Path(__file__).resolve().parents[1]


def build_environment(mode):
    if mode not in {'demo','llm'}: raise ValueError('mode must be demo or llm')
    if not (ROOT/'data/petkit500/current.json').exists():
        raise RuntimeError('请先执行 python scripts/build_petkit500.py')
    env=os.environ.copy()
    embedding_config=ROOT/'data/petkit500/embedding_runtime.json'
    if 'PETKIT_EMBEDDING_MODEL' not in env and embedding_config.exists():
        model=json.loads(embedding_config.read_text(encoding='utf-8'))['model']
        if not Path(model).is_dir(): raise RuntimeError('已配置的本地Embedding模型不存在，请重新构建向量或显式设置空PETKIT_EMBEDDING_MODEL')
        env['PETKIT_EMBEDDING_MODEL']=model
    if mode=='llm':
        from dotenv import dotenv_values
        key=env.get('API_KEY') or dotenv_values(ROOT/'.env').get('API_KEY','')
        if not key or key=='your_api_key_here': raise RuntimeError('llm模式需要在.env配置可用API_KEY')
    token=secrets.token_urlsafe(32)
    env.update(PROJECT_PROFILE='petkit',PETKIT_MODE=mode,
        PETKIT_API_TOKENS=json.dumps({token:{'subject':'local-interview','role':'analyst'}}),
        PETKIT_LOCAL_UI_TOKEN=token)
    return env,token


@contextmanager
def running(mode='demo',api_port=8000,ui_port=8501):
    if api_port==ui_port or not all(1<=p<=65535 for p in [api_port,ui_port]):
        raise ValueError('请使用两个不同的有效端口')
    env,token=build_environment(mode)
    children=[]
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    logs=ROOT/'data/petkit500/launcher_logs'
    logs.mkdir(parents=True,exist_ok=True)
    handles=[]
    try:
        for name,command in [
            ('api',[sys.executable,'-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port',str(api_port)]),
            ('ui',[sys.executable,'-m','streamlit','run','src/app.py','--server.address','127.0.0.1',
                   '--server.port',str(ui_port),'--server.headless','true'])]:
            log=(logs/(name+'-'+secrets.token_hex(4)+'.log')).open('w',encoding='utf-8')
            handles.append(log)
            children.append(subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags))
        api=f'http://127.0.0.1:{api_port}'
        ui=f'http://127.0.0.1:{ui_port}'
        for url in [api+'/health',ui+'/_stcore/health']:
            deadline=time.monotonic()+30
            while True:
                if any(p.poll() is not None for p in children):
                    raise RuntimeError('服务进程提前退出，请查看data/petkit500/launcher_logs')
                try:
                    with urlopen(url,timeout=1) as response:
                        if response.status==200: break
                except (URLError,TimeoutError,OSError): pass
                if time.monotonic()>=deadline: raise TimeoutError('本地服务启动超时，请检查日志或端口占用')
                time.sleep(.2)
        yield dict(api_url=api,ui_url=ui,token=token,processes=children)
    finally:
        for process in children:
            if process.poll() is None: process.terminate()
        for process in children:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for handle in handles: handle.close()


def main():
    parser=argparse.ArgumentParser(description='仅本机访问的灵犀面试演示')
    parser.add_argument('--mode',choices=['demo','llm'],default='demo')
    parser.add_argument('--api-port',type=int,default=8000)
    parser.add_argument('--ui-port',type=int,default=8501)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    try:
        with running(args.mode,args.api_port,args.ui_port) as services:
            print('工作台：'+services['ui_url'],flush=True)
            print('API文档：'+services['api_url']+'/docs',flush=True)
            print('模式：'+args.mode+'；本次临时演示身份已自动配置。Ctrl+C停止两项服务。',flush=True)
            if not args.no_browser: webbrowser.open(services['ui_url'])
            while all(p.poll() is None for p in services['processes']): time.sleep(.3)
    except KeyboardInterrupt:
        print('演示服务已停止。')


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
