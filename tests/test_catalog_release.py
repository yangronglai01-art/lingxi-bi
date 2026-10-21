import json,shutil
import pytest
from src.petkit import catalog_release as catalog,control,releases
from src.petkit.catalog import METRICS

BASE='petkit-v1.3-aaaaaaaaaa'

@pytest.fixture
def parent(tmp_path,monkeypatch):
    data=tmp_path/'instance';data.mkdir()
    for module in [catalog,control,releases]:monkeypatch.setattr(module,'DATA',data)
    control.initialize();folder=data/BASE;folder.mkdir()
    (folder/'warehouse.duckdb').write_bytes(b'immutable business bytes')
    releases.write(folder/'catalog.json',dict(generation=BASE,metrics=[],version='petkit-v1.3',tables={'T':{'columns':{'id':{'type':'VARCHAR'}}}}))
    releases.write(folder/'summary.json',dict(generation=BASE,counts={'T':1},orders=1,total_rows=1))
    releases.write(folder/'lineage.json',dict(version=1,generation=BASE,events={'event1':'hash1'},batches={'batch1':'hash2'},last_attempt='source-attempt'))
    releases.write(data/'current.json',dict(generation=BASE,version='petkit-v1.3'))
    return data

def test_catalog_stage_keeps_business_bytes_history_and_active_pointer(parent):
    before=releases.file_seal(parent/BASE);result=catalog.stage();folder=parent/result['candidate']
    gate=catalog.validate(folder)
    assert gate['business_bytes_unchanged'] and gate['source_events_preserved']==1
    assert releases.file_seal(parent/BASE)==before
    assert json.loads((parent/'current.json').read_text(encoding='utf-8'))['generation']==BASE
    assert json.loads((folder/'catalog.json').read_text(encoding='utf-8'))['metrics']==METRICS
    assert 'metadata.json' in releases.file_seal(folder)

@pytest.mark.parametrize('kind',['database','marker','structure','summary','history','audit'])
def test_catalog_validation_rejects_tampered_candidate(parent,kind):
    result=catalog.stage();folder=parent/result['candidate']
    if kind=='database':(folder/'warehouse.duckdb').write_bytes(b'changed')
    elif kind=='audit':
        with control.db() as c:c.execute("DELETE FROM audit WHERE event='catalog_staged'")
    else:
        path=folder/{'marker':'metadata.json','structure':'catalog.json','summary':'summary.json','history':'lineage.json'}[kind]
        value=json.loads(path.read_text(encoding='utf-8'))
        if kind=='marker':value['parent']='fake'
        elif kind=='structure':value['tables']['T']['columns']['id']['type']='INTEGER'
        elif kind=='summary':value['total_rows']=2
        else:value['events']={}
        releases.write(path,value)
    with pytest.raises(ValueError):catalog.validate(folder)

def test_catalog_evidence_validates_in_restore_without_parent_folder(parent,tmp_path,monkeypatch):
    result=catalog.stage();restored=tmp_path/'restored';restored.mkdir()
    shutil.copytree(parent/result['candidate'],restored/result['candidate'])
    # SQLite is closed here, so a file copy creates the independent fixture.
    shutil.copy2(parent/'control.sqlite3',restored/'control.sqlite3')
    monkeypatch.setattr(control,'DATA',restored)
    assert not (restored/BASE).exists()
    assert catalog.validate(restored/result['candidate'])['source_events_preserved']==1

def test_catalog_candidate_requires_expected_parent_before_activation(parent,monkeypatch):
    result=catalog.stage();monkeypatch.setattr(releases,'check_receipt',lambda *args:{'status':'validated'})
    with pytest.raises(ValueError,match='指标目录候选'):releases.activate(result['candidate'])

