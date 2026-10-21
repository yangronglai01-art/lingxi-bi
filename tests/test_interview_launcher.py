import json
import pytest
from scripts.start_interview import build_environment


def test_launcher_uses_temporary_local_credentials_and_no_persistent_config(monkeypatch):
    monkeypatch.setenv('PETKIT_API_TOKENS','existing-config-not-modified')
    first,key1=build_environment('demo')
    second,key2=build_environment('demo')
    assert key1!=key2 and len(key1)>30
    assert first['PETKIT_LOCAL_UI_TOKEN']==key1
    assert json.loads(first['PETKIT_API_TOKENS'])[key1]=={'subject':'local-interview','role':'analyst'}
    import os
    assert os.environ['PETKIT_API_TOKENS']=='existing-config-not-modified'
    assert first['PETKIT_MODE']=='demo' and first['PROJECT_PROFILE']=='petkit'


def test_workbench_accepts_launcher_identity(monkeypatch,tmp_path):
    from streamlit.testing.v1 import AppTest
    from src.petkit import history
    from pathlib import Path
    monkeypatch.setattr(history,'DATA',tmp_path)
    monkeypatch.setenv('PETKIT_LOCAL_UI_TOKEN','local-ui-test-key')
    monkeypatch.setenv('PETKIT_API_TOKENS',json.dumps({'local-ui-test-key':{'subject':'local-ui-test','role':'analyst'}}))
    at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/app.py')).run(timeout=30)
    assert not at.exception
    assert next(field for field in at.text_input if field.label.startswith('访问凭据')).value=='local-ui-test-key'
    assert [m.value for m in at.metric][:2]==['500','40']
