"""Exercise operator publish entry point without any real database or approval."""
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from src.enterprise import ops
from operator_tests.test_p0 import catalog,bundle


@pytest.mark.parametrize('record', ['missing','pending','valid'])
def test_production_publish_enforces_reviewed_bundle(tmp_path,monkeypatch,record):
    published=[]
    class Cursor:
        def execute(self,query,args):self.query=query
        def fetchone(self):return {'n':1} if 'count(*)' in self.query else {'catalog':catalog()}
    class Store:
        def __init__(self,*args):pass
        @contextmanager
        def db(self):yield Cursor()
        def publish(self,*args):published.append(args)
    settings=SimpleNamespace(dsn='not-a-connection',tenant='t',environment='production',enabled_metrics=('net_sales',),
        model_revision='m',embedding_revision='e')
    monkeypatch.setattr(ops.Settings,'from_env',lambda:settings)
    monkeypatch.setattr(ops,'Store',Store)
    monkeypatch.setattr(ops,'verify_release',lambda *a:dict(manifest_hash='independent-hash'))
    proof=tmp_path/'counts.json';proof.write_text(json.dumps(dict(source_counts={},independent_totals_passed=True)),encoding='utf-8')
    argv=['ops','publish','--file',str(proof),'--release','r','--expected','previous','--approval','reviewed-release']
    if record!='missing':
        b=bundle(tmp_path)
        if record=='pending':b['packages']['P0-05']['status']='pending'
        path=tmp_path/'acceptance.json';path.write_text(json.dumps(b),encoding='utf-8');argv+=['--acceptance',str(path)]
    monkeypatch.setattr('sys.argv',argv)
    if record=='valid':
        ops.main();assert len(published)==1
    else:
        with pytest.raises(ValueError,match='acceptance'):ops.main()
        assert not published
