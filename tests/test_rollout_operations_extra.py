import pytest
from test_simulated_rollout import org
from src.petkit import control,service

def test_same_policy_provisioning_is_idempotent(org):
    principals,keys,regions=org
    control.upsert_user('east','销售一部','analyst',[regions[0]])
    assert control.principal_for('east')==principals['east']
    assert control.authenticate(keys['east'])==principals['east']

def test_user_queue_quota(org):
    principals,_,_=org
    for _ in range(20): control.submit(principals['east'],'各型号净销售额')
    with pytest.raises(ValueError,match='配额'): control.submit(principals['east'],'各型号净销售额')

def test_disabled_queued_user_is_not_executed(org,monkeypatch):
    from src.petkit import worker
    principals,_,regions=org
    job=control.submit(principals['east'],'各型号净销售额')
    control.upsert_user('east','销售一部','analyst',[regions[0]],False)
    def forbidden(*args,**kwargs): raise AssertionError('已禁用账号不应进入模型或SQL执行')
    monkeypatch.setattr(service,'ask',forbidden)
    assert worker.run_once('disabled-user')
    with control.db() as c: result=c.execute('SELECT state,result FROM jobs WHERE id=?',(job,)).fetchone()
    assert result['state']=='failed' and result['result'] is None

def test_managed_audit_failure_suppresses_result(org,monkeypatch):
    principals,_,_=org
    def fail(*args,**kwargs): raise OSError('disk unavailable')
    monkeypatch.setattr(control,'audit',fail)
    answer=service.ask('各型号净销售额',principals['east'])
    assert answer.status=='error' and answer.result is None and '审计' in answer.error

def test_operations_workbench_auth_and_persistent_recent_jobs(org,monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    principals,keys,_=org
    control.submit(principals['east'],'各型号净销售额')
    monkeypatch.setenv('PETKIT_LOCAL_UI_TOKEN',keys['east'])
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/app.py')).run(timeout=30)
    assert not app.exception
    assert any('模拟业务工作台' in x.value for x in app.subheader)
    recent=next(x for x in app.selectbox if x.label=='最近提交的任务')
    assert len(recent.options)==1


def test_managed_cohort_result_has_versioned_unit_contract(org):
    principals,_,_=org
    answer=service.ask('各型号售出30天激活率',principals['east'])
    assert answer.status=='success',answer.error
    assert answer.context['output_contract']=='cohort-percent-v1'

def test_managed_freeform_query_without_registered_metric(org,monkeypatch):
    import json
    from src import config
    from src.engine import llm_client
    principals,_,_=org
    monkeypatch.setattr(service,'MODE','llm')
    monkeypatch.setattr(config,'API_KEY','test-key-never-sent')
    plan=dict(metric=None,dimensions=['product_name'],time_range=None,filters=[],joins=[],grain='product',assumptions=[])
    monkeypatch.setattr(llm_client,'chat_completion',lambda *a,**k:json.dumps(dict(status='ready',plan=plan,sql='SELECT product_name FROM dim_product')))
    answer=service.ask('列出产品型号名称',principals['east'])
    assert answer.status=='success',answer.error
    assert answer.result.row_count==12


def test_rotation_skips_disabled_accounts_and_preserves_others(org,monkeypatch,tmp_path):
    import json
    import sys
    from scripts import provision_simulated
    principals,_,regions=org
    control.upsert_user('regional_service','区域售后','service',[regions[1]])
    old=control.issue_token('regional_service')
    control.upsert_user('regional_service','区域售后','service',[regions[1]],False)
    monkeypatch.setattr(provision_simulated,'DATA',tmp_path)
    (tmp_path/'simulated_credentials.json').write_text(json.dumps({'regional_service':old}),encoding='utf-8')
    monkeypatch.setattr(sys,'argv',['provision_simulated.py','--rotate'])
    provision_simulated.main()
    credentials=json.loads((tmp_path/'simulated_credentials.json').read_text(encoding='utf-8'))
    assert credentials['regional_service']==old
    assert control.authenticate(credentials['global_analyst']).managed
    with pytest.raises(PermissionError): control.authenticate(old)
