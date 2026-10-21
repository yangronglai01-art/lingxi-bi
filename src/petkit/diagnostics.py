"""Read-only bounded operational snapshots; no raw questions, SQL, results or errors."""
from datetime import datetime,timezone
import math,os,time
from . import control
from .health_checks import evaluate,runtime_value,age

LIMIT=200
TERMINAL={'completed','failed','interrupted','cancelled','superseded'}
ANSWERS={'success','denied','clarification','unsupported','error','timeout','not_ready'}

def percentile(values,p):
    if not values:return None
    values=sorted(values);return round(values[max(0,math.ceil(len(values)*p)-1)],3)

def snapshot(principal,window_minutes=15):
    control.verify(principal)
    if principal.role!='analyst' or principal.regions:raise PermissionError('仅全局分析账号可查看运维诊断')
    if type(window_minutes) is not int or not 1<=window_minutes<=1440:raise ValueError('时间窗必须为1至1440分钟')
    now=time.time();cutoff=now-window_minutes*60
    with control.db() as c:
        c.execute('BEGIN')
        runtime={r['name']:dict(r) for r in c.execute("SELECT name,value,updated FROM runtime WHERE name IN ('worker','supervisor')")}
        users=c.execute('SELECT count(*) FROM users WHERE enabled=1').fetchone()[0]
        counts={r['state']:r['n'] for r in c.execute('SELECT state,count(*) n FROM jobs GROUP BY state')}
        oldest=c.execute("SELECT MIN(created) FROM jobs WHERE state='queued'").fetchone()[0]
        expired=c.execute("SELECT count(*) FROM jobs WHERE state='running' AND (lease IS NULL OR lease<=?)",(now,)).fetchone()[0]
        max_employee=c.execute("SELECT MAX(n) FROM (SELECT count(*) n FROM jobs WHERE state IN ('queued','running') GROUP BY subject)").fetchone()[0] or 0
        total=c.execute("SELECT count(*) FROM jobs WHERE updated BETWEEN ? AND ? AND state IN ('completed','failed','interrupted','cancelled','superseded')",(cutoff,now)).fetchone()[0]
        rows=[dict(r) for r in c.execute("""SELECT id,state,created,updated,
          CASE WHEN json_valid(result) THEN json_extract(result,'$.status') ELSE NULL END answer_status
          FROM jobs WHERE updated BETWEEN ? AND ? AND state IN ('completed','failed','interrupted','cancelled','superseded') ORDER BY updated DESC,id LIMIT ?""",(cutoff,now,LIMIT))]
    health=evaluate(now,runtime,users);worker_age=age(runtime.get('worker'),now);supervisor_age=age(runtime.get('supervisor'),now)
    state=runtime_value(runtime.get('supervisor'));session=os.getenv('PETKIT_LAUNCH_SESSION')
    bound=bool(session and state.get('session')==session)
    phase=state.get('phase','unknown') if bound else 'manual' if not session else 'unknown'
    restarts=state.get('restarts') if bound else None;budget=state.get('max_restarts') if bound else None
    outcomes={};durations=[];failures=[]
    for row in rows:
        outcome=row['answer_status'] if row['state']=='completed' and row['answer_status'] in ANSWERS else 'unknown' if row['state']=='completed' else row['state']
        outcomes[outcome]=outcomes.get(outcome,0)+1
        duration=row['updated']-row['created']
        if outcome=='success' and math.isfinite(duration) and duration>=0:durations.append(duration)
        if outcome in {'error','timeout','not_ready','failed','interrupted','unknown'} and len(failures)<10:
            failures.append(dict(id=row['id'],state=row['state'],outcome=outcome,updated=row['updated']))
    execution_success=outcomes.get('success',0);execution_failed=sum(outcomes.get(k,0) for k in ['error','timeout','not_ready','failed','unknown']);evaluated=execution_success+execution_failed
    queued=counts.get('queued',0);running=counts.get('running',0);pending=queued+running
    oldest_age=max(0,now-oldest) if oldest is not None else None;alerts=[]
    def alert(code,message,action):alerts.append(dict(code=code,message=message,action=action))
    if not health['checks'].get('warehouse',False):alert('warehouse_unavailable','业务快照文件不可用','检查当前版本及备份，不要直接重建覆盖')
    if health['checks'].get('publication') is False:alert('publication_pending','发布尚未完成或日志异常','使用受控发布reconcile流程检查')
    if not health['checks'].get('identity_store',True):alert('identity_unavailable','持久身份库不可用','检查本机控制库和磁盘')
    if not health['checks'].get('worker',True):alert('worker_not_ready','工作进程心跳或会话尚未就绪','查看守护状态和对应进程日志')
    if health['checks'].get('supervisor') is False:alert('supervisor_not_ready','本次启动会话尚未处于健康运行状态','等待退避恢复，若预算耗尽则排查后重新启动')
    if oldest_age is not None and oldest_age>=60:alert('queue_wait_high','最早排队任务已等待至少60秒','检查工作进程和慢查询，避免重复提交新键')
    if pending>=.8*control.GLOBAL_QUEUE_LIMIT:alert('global_queue_high','全局待处理任务达到配额80%','降低提交速率，按429重试提示退避')
    if max_employee>=.8*control.USER_QUEUE_LIMIT:alert('employee_queue_high','至少一个员工接近待处理配额','等待完成或取消不需要的任务')
    if expired:alert('expired_running_leases','存在过期运行租约','等待工作进程恢复扫描；中断任务使用新键重新提交')
    if isinstance(restarts,int) and isinstance(budget,int) and budget>0 and restarts>=budget:alert('restart_budget_used','本次启动的工作进程重启预算已用完','排查故障；再次退出将停止服务')
    if outcomes.get('interrupted'):alert('recent_interrupted_jobs','近期样本中存在中断任务','确认工作进程恢复后使用新幂等键重新提交')
    if execution_failed:alert('recent_execution_failures','近期样本中存在执行失败','按任务ID核查状态及受控日志，勿把权限拒绝算作执行失败')
    unavailable=not health['checks'].get('warehouse',False) or health['checks'].get('publication') is False or not health['checks'].get('identity_store',True)
    status='unavailable' if unavailable else 'degraded' if alerts or not health['ready'] else 'healthy'
    result=dict(observed_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),generation=health['generation'],status=status,ready=health['ready'],checks=health['checks'],
        worker=dict(heartbeat_age_seconds=worker_age),supervisor=dict(enabled=bool(session),phase=phase,heartbeat_age_seconds=supervisor_age if bound else None,restarts=restarts,max_restarts=budget),
        queue=dict(states=counts,pending=pending,queued=queued,running=running,oldest_wait_seconds=round(oldest_age,3) if oldest_age is not None else None,expired_running_leases=expired,max_employee_pending=max_employee,employee_limit=control.USER_QUEUE_LIMIT,global_limit=control.GLOBAL_QUEUE_LIMIT),
        recent=dict(window_minutes=window_minutes,terminal_count=total,sample_count=len(rows),sample_limit=LIMIT,truncated=total>len(rows),outcomes=outcomes,execution_evaluated=evaluated,execution_successes=execution_success,execution_failures=execution_failed,execution_success_rate=round(execution_success/evaluated,4) if evaluated else None,successful_end_to_end_seconds=dict(count=len(durations),p50=percentile(durations,.5),p95=percentile(durations,.95)),failures=failures),alerts=alerts)
    control.verify(principal)
    return result
