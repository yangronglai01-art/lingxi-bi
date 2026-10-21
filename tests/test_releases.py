import json
from pathlib import Path
import time
import pytest
from src.petkit import control,releases

OLD='petkit-v1.3-1111111111'; NEW='petkit-v1.3-2222222222'

@pytest.fixture
def publication(tmp_path,monkeypatch):
    monkeypatch.setattr(releases,'DATA',tmp_path)
    monkeypatch.setattr(control,'DATA',tmp_path)
    control.initialize()
    for generation in [OLD,NEW]:
        folder=tmp_path/generation; folder.mkdir()
        (folder/'catalog.json').write_text(json.dumps({'version':'petkit-v1.3'}))
    releases.write(tmp_path/'current.json',dict(generation=OLD,version='petkit-v1.3'))
    monkeypatch.setattr(releases,'check_receipt',lambda *args:{'status':'validated'})
    monkeypatch.setattr(releases,'check_regions',lambda conn:None)
    import duckdb
    # Minimal warehouse is enough to exercise publication transactions; full500 gates have separate integration.
    for generation in [OLD,NEW]:
        conn=duckdb.connect(str(tmp_path/generation/'warehouse.duckdb')); conn.close()
    return tmp_path

def job(root,state='queued',generation=OLD):
    with control.db() as c:
        c.execute('INSERT INTO jobs(id,subject,policy_version,generation,question,state,created,updated,idempotency) VALUES(?,?,?,?,?,?,?,?,?)',
            ('test','actor',1,generation,'question',state,time.time(),time.time(),'key'))

def test_publish_supersedes_queued_jobs_and_audits(publication):
    job(publication)
    result=releases.activate(NEW,expected_current=OLD)
    assert result['state']=='committed'
    assert json.loads((publication/'current.json').read_text())['generation']==NEW
    with control.db() as c:
        assert c.execute("SELECT state FROM jobs WHERE id='test'").fetchone()[0]=='superseded'
        assert c.execute("SELECT count(*) FROM audit WHERE event='data_release'").fetchone()[0]==1
    with releases.guard(str(publication)): releases.reconcile_locked(publication)
    with control.db() as c: assert c.execute("SELECT count(*) FROM audit WHERE event='data_release'").fetchone()[0]==1

def test_running_job_blocks_publish_and_expected_current_protects(publication):
    job(publication,'running')
    with pytest.raises(ValueError,match='执行中'): releases.activate(NEW)
    with pytest.raises(ValueError,match='当前版本'): releases.activate(NEW,expected_current=NEW)
    assert json.loads((publication/'current.json').read_text())['generation']==OLD

def test_crash_after_pointer_reconciles_exactly_one_audit(publication,monkeypatch):
    job(publication)
    original=releases.write
    def fail_journal(path,value):
        if Path(path).parent.name=='release_history': raise OSError('simulated crash after control commit')
        return original(path,value)
    monkeypatch.setattr(releases,'write',fail_journal)
    with pytest.raises(OSError): releases.activate(NEW)
    assert json.loads((publication/'release_journal.json').read_text())['state']=='prepared'
    monkeypatch.setattr(releases,'write',original)
    with releases.guard(str(publication)): result=releases.reconcile_locked(publication)
    assert result['state']=='committed'
    with control.db() as c: assert c.execute("SELECT count(*) FROM audit WHERE event='data_release'").fetchone()[0]==1

def test_prepared_before_pointer_switch_aborts(publication):
    releases.write(publication/'release_journal.json',dict(id='pending',state='prepared',previous=dict(generation=OLD,version='petkit-v1.3'),target=dict(generation=NEW,version='petkit-v1.3')))
    assert releases.reconcile_locked(publication)['state']=='aborted'

def test_failed_receipt_leaves_current_untouched(publication,monkeypatch):
    def fail(*args): raise ValueError('invalid seal')
    monkeypatch.setattr(releases,'check_receipt',fail)
    with pytest.raises(ValueError): releases.activate(NEW)
    assert json.loads((publication/'current.json').read_text())['generation']==OLD

def test_rollback_uses_identical_checks(publication):
    releases.activate(NEW)
    releases.activate(OLD,expected_current=NEW)
    assert json.loads((publication/'current.json').read_text())['generation']==OLD

@pytest.mark.parametrize('name',['../outside','D:/outside','petkit-v1.3-invalid',''])
def test_snapshot_path_rejected(publication,name):
    with pytest.raises(ValueError): releases.folder_for(name)


from test_simulated_rollout import org

def test_idempotency_is_scoped_to_data_generation(org,monkeypatch):
    from src.petkit import retrieval
    principals,_,_=org
    monkeypatch.setattr(retrieval,'current_catalog',lambda:{'generation':OLD})
    before=control.submit(principals['east'],'各型号净销售额','request-key')
    assert control.submit(principals['east'],'各型号净销售额','request-key')==before
    monkeypatch.setattr(retrieval,'current_catalog',lambda:{'generation':NEW})
    after=control.submit(principals['east'],'各型号净销售额','request-key')
    assert before!=after
