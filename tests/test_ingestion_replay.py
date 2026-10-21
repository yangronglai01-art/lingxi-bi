import json
import shutil
from pathlib import Path
import pytest
from src.petkit import ingestion,control,releases
from test_ingestion import ledger,refund
from test_releases import publication,OLD,NEW

BASE='petkit-v1.3-aaaaaaaaaa'

@pytest.fixture
def feed(tmp_path,monkeypatch,ledger):
    ledger.execute('CHECKPOINT'); ledger.close()
    data=tmp_path/'data'; parent=data/BASE; parent.mkdir(parents=True)
    shutil.copy2(tmp_path/'tiny.duckdb',parent/'warehouse.duckdb')
    for module in [ingestion,control,releases]: monkeypatch.setattr(module,'DATA',data)
    import duckdb
    with duckdb.connect(str(parent/'warehouse.duckdb'),read_only=True) as c:
        names=[r[0] for r in c.execute('SHOW TABLES').fetchall()]
        tables={name:dict(columns={r[0]:dict(type=r[1]) for r in c.execute(f'DESCRIBE {name}').fetchall()}) for name in names}
        counts={name:c.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name in names}
    releases.write(parent/'catalog.json',dict(generation=BASE,tables=tables))
    releases.write(parent/'summary.json',dict(generation=BASE,counts=counts,total_rows=sum(counts.values())))
    releases.write(data/'current.json',dict(generation=BASE,version='petkit-v1.3'))
    incoming=tmp_path/'batch.json'
    return data,incoming

def payload(batch='b1',amount='10.00'):
    return dict(version=1,source='mock',batch_id=batch,events=[refund(amount=amount)])

def send(path,body): path.write_text(json.dumps(body),encoding='utf-8'); return ingestion.ingest(path)

def count(data,generation):
    import duckdb
    with duckdb.connect(str(data/generation/'warehouse.duckdb'),read_only=True) as c:
        return c.execute('SELECT count(*),SUM(amount) FROM fact_refund').fetchone()

def switch(data,generation): releases.write(data/'current.json',dict(generation=generation,version='petkit-v1.3'))

def test_received_is_not_applied_and_rollback_replay_is_not_lost(feed):
    data,path=feed
    first=send(path,payload())
    assert first['state']=='staged' and first['applied']==1
    second=send(path,payload())
    assert second['applied']==1 and second['candidate']!=first['candidate']
    assert count(data,BASE)[0]==0
    switch(data,first['candidate'])
    duplicate=send(path,payload())
    assert duplicate['state']=='already_applied' and duplicate['applied']==0
    assert count(data,duplicate['candidate'])==(1,10.0)
    switch(data,BASE)
    replay=send(path,payload())
    assert replay['applied']==1 and count(data,replay['candidate'])==(1,10.0)

@pytest.mark.parametrize('new_batch',['b1','b2'])
def test_conflicting_batch_or_event_identity_is_quarantined(feed,new_batch):
    data,path=feed
    send(path,payload())
    with pytest.raises(ValueError,match='冲突'): send(path,payload(new_batch,'20.00'))
    assert json.loads((data/'current.json').read_text())['generation']==BASE
    with control.db() as c: assert c.execute("SELECT count(*) FROM ingestion_attempts WHERE state='quarantined'").fetchone()[0]==1

def test_semantically_identical_json_formatting_is_idempotent(feed):
    data,path=feed
    first=send(path,payload()); switch(data,first['candidate'])
    path.write_text(json.dumps(payload(),indent=4),encoding='utf-8')
    repeated=ingestion.ingest(path)
    assert repeated['state']=='already_applied' and repeated['applied']==0

def test_whole_failed_candidate_keeps_parent_and_no_partial_refund(feed):
    data,path=feed
    body=payload(); body['events'].append(refund('e2','99.00'))
    with pytest.raises(ValueError,match='累计退款'): send(path,body)
    with control.db() as c: candidate=c.execute('SELECT candidate FROM ingestion_attempts').fetchone()[0]
    assert count(data,candidate)[0]==0 and count(data,BASE)[0]==0
    assert json.loads((data/'current.json').read_text())['generation']==BASE

def test_oversized_batch_rejected_before_storage(feed,monkeypatch):
    data,path=feed
    monkeypatch.setattr(ingestion,'MAX_BYTES',10)
    path.write_bytes(b'x'*11)
    with pytest.raises(ValueError,match='超过'): ingestion.ingest(path)
    assert not (data/'control.sqlite3').exists()

def test_stale_lineage_requires_parent_cas_or_explicit_historical_rollback(publication):
    releases.write(publication/NEW/'lineage.json',dict(parent_generation='another-parent'))
    with pytest.raises(ValueError,match='父版本'): releases.activate(NEW,expected_current=OLD)
    releases.activate(NEW,expected_current=OLD,allow_historical=True)
    assert json.loads((publication/'current.json').read_text())['generation']==NEW


@pytest.mark.parametrize('proposed',[None,{'event-key':'changed-payload'}])
def test_normal_publish_cannot_silently_drop_or_rewrite_ingested_events(publication,proposed):
    releases.write(publication/OLD/'lineage.json',dict(events={'event-key':'original-payload'}))
    if proposed is not None:
        releases.write(publication/NEW/'lineage.json',dict(parent_generation=OLD,events=proposed))
    with pytest.raises(ValueError,match='已应用事件'): releases.activate(NEW,expected_current=OLD)
    assert json.loads((publication/'current.json').read_text())['generation']==OLD
    releases.activate(NEW,expected_current=OLD,allow_historical=True)

def test_business_candidate_does_not_inherit_catalog_only_marker(feed):
    data,path=feed
    releases.write(data/BASE/'metadata.json',dict(kind='catalog-only',generation=BASE))
    result=send(path,payload())
    assert result['applied']==1
    assert (data/BASE/'metadata.json').exists()
    assert not (data/result['candidate']/'metadata.json').exists()
    assert json.loads((data/result['candidate']/'lineage.json').read_text(encoding='utf-8'))['last_attempt']==result['attempt']
