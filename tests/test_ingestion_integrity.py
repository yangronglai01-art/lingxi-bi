import json
import pytest
from src.petkit import ingestion,control
from test_ingestion_replay import feed,ledger,send,payload
from test_simulated_rollout import org

@pytest.mark.parametrize('fault',['missing_row','changed_row','changed_archive'])
def test_claimed_applied_event_requires_actual_source_and_business_row(feed,fault):
    data,path=feed
    result=send(path,payload()); folder=data/result['candidate']
    history=json.loads((folder/'lineage.json').read_text(encoding='utf-8'))
    import duckdb
    with duckdb.connect(str(folder/'warehouse.duckdb')) as conn:
        assert ingestion.validate_lineage(conn,history)['source_and_rows_verified']
        if fault=='missing_row': conn.execute('DELETE FROM fact_refund')
        elif fault=='changed_row': conn.execute('UPDATE fact_refund SET amount=11')
        else:
            with control.db() as c: archive=c.execute('SELECT archive FROM ingestion_batches').fetchone()[0]
            from pathlib import Path
            body=json.loads(Path(archive).read_text(encoding='utf-8')); body['events'][0]['amount']='11.00'
            Path(archive).write_text(json.dumps(body),encoding='utf-8')
        with pytest.raises(ValueError): ingestion.validate_lineage(conn,history)

def test_ingestion_status_api_is_global_operator_only(org,monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from src.petkit.api import app
    principals,keys,_=org
    monkeypatch.setattr(ingestion,'DATA',tmp_path)
    ingestion.initialize()
    with TestClient(app) as client:
        east={'Authorization':'Bearer '+keys['east']}; global_headers={'Authorization':'Bearer '+keys['global']}
        assert client.get('/ops/ingestion',headers=east).status_code==403
        assert client.get('/ops/ingestion',headers=global_headers).json()=={'items':[]}
        assert client.get('/ops/ingestion?limit=1000',headers=global_headers).status_code==422


from test_release_seals import sealed,GEN

def test_source_archive_tampering_after_validation_rejects_publication(sealed):
    from src.petkit import releases
    root,_=sealed
    archive=root/'ingestion/archive/mock/b1/original.json'
    archive.parent.mkdir(parents=True); archive.write_bytes(b'original source')
    receipt_path=root/'releases'/(GEN+'.json')
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['source_files']={str(archive.relative_to(root)):releases.sha(archive)}
    releases.write(receipt_path,receipt)
    assert releases.check_receipt(GEN,root)['status']=='validated'
    archive.write_bytes(b'changed after validation')
    with pytest.raises(ValueError,match='来源批次'): releases.check_receipt(GEN,root)
