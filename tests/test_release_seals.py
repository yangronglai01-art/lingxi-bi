import json
import pytest
from src.petkit import releases,control,builder
from src.petkit.settings import AS_OF

GEN='petkit-v1.3-3333333333'

@pytest.fixture
def sealed(tmp_path,monkeypatch):
    monkeypatch.setattr(releases,'DATA',tmp_path)
    monkeypatch.setattr(control,'DATA',tmp_path)
    folder=tmp_path/GEN; folder.mkdir()
    for name in ['warehouse.duckdb','catalog.json','summary.json']: (folder/name).write_text('original')
    monkeypatch.setattr(releases,'code_seal',lambda:{'gate':'v1'})
    monkeypatch.setattr(releases,'embedding_config',lambda root:{'model':'local','signature':'v1'})
    releases.write(tmp_path/'releases'/(GEN+'.json'),dict(status='validated',files=releases.file_seal(folder),code=releases.code_seal(),embedding=releases.embedding_config(tmp_path)))
    return tmp_path,folder

@pytest.mark.parametrize('name',['warehouse.duckdb','catalog.json','summary.json'])
def test_tampered_snapshot_rejects_publication(sealed,name):
    root,folder=sealed
    assert releases.check_receipt(GEN,root)['status']=='validated'
    (folder/name).write_text('changed after validation')
    with pytest.raises(ValueError,match='凭证'): releases.check_receipt(GEN,root)

@pytest.mark.parametrize('component',['code','embedding'])
def test_changed_code_or_model_invalidates_receipt(sealed,monkeypatch,component):
    root,_=sealed
    if component=='code': monkeypatch.setattr(releases,'code_seal',lambda:{'gate':'v2'})
    else: monkeypatch.setattr(releases,'embedding_config',lambda root:{'model':'local','signature':'v2'})
    with pytest.raises(ValueError): releases.check_receipt(GEN,root)

def test_bad_quality_records_rejection_and_preserves_pointer(tmp_path,monkeypatch):
    import duckdb
    monkeypatch.setattr(releases,'DATA',tmp_path)
    monkeypatch.setattr(control,'DATA',tmp_path)
    folder=tmp_path/GEN; folder.mkdir()
    c=duckdb.connect(str(folder/'warehouse.duckdb')); c.close()
    (folder/'catalog.json').write_text(json.dumps(dict(generation=GEN,as_of=AS_OF,tables={})))
    (folder/'summary.json').write_text(json.dumps(dict(generation=GEN)))
    releases.write(tmp_path/'current.json',{'generation':'unchanged'})
    with pytest.raises(ValueError,match='500'): releases.validate(GEN)
    assert json.loads((tmp_path/'current.json').read_text(encoding='utf-8'))['generation']=='unchanged'
    assert json.loads((tmp_path/'releases'/(GEN+'.json')).read_text(encoding='utf-8'))['status']=='rejected'
    assert len(list((tmp_path/'validation_runs').glob('*.json')))==1

def test_initialized_instance_cannot_bypass_release_pipeline(tmp_path,monkeypatch):
    monkeypatch.setattr(builder,'DATA',tmp_path)
    monkeypatch.setattr(builder,'CURRENT',tmp_path/'current.json')
    (tmp_path/'current.json').write_text('{}'); (tmp_path/'control.sqlite3').touch()
    with pytest.raises(ValueError,match='发布门禁'): builder.build()
