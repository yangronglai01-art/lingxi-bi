import pytest
from scripts.start_interview import build_environment


def test_launcher_uses_temporary_local_credentials_and_no_persistent_config(monkeypatch):
    monkeypatch.setenv('OIDC_ISSUER','existing-config-not-modified')
    first,key1,idp1=build_environment('demo')
    second,key2,idp2=build_environment('demo')
    try:
        assert key1!=key2 and len(key1)>30
        assert 'PETKIT_LOCAL_UI_TOKEN' not in first
        assert first['OIDC_ISSUER']==idp1.issuer
        assert first['OIDC_CLIENT_ID']=='lingxi'
        assert 'PETKIT_API_TOKENS' not in first
        import os
        assert os.environ['OIDC_ISSUER']=='existing-config-not-modified'
        assert first['PETKIT_MODE']=='demo' and first['PROJECT_PROFILE']=='petkit'
    finally:
        idp1.stop(); idp2.stop()
