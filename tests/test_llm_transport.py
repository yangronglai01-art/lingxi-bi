"""No-network transport regression for explicit JSON output and provider options."""
import json
import pytest
from src import config
from src.engine import llm_client


def test_explicit_json_and_thinking_options(monkeypatch):
    captured={}
    monkeypatch.setattr(config,'API_KEY','unit-test-not-real')
    class Response:
        status_code=200
        def json(self):
            return {'choices':[{'message':{'content':'{"status":"ready"}'},'finish_reason':'stop'}]}
    def fake_post(url,**kwargs):
        captured.update(json.loads(kwargs['data']))
        return Response()
    monkeypatch.setattr(llm_client.requests,'post',fake_post)
    result=llm_client.chat_completion([{'role':'user','content':'JSON'}],
        response_format={'type':'json_object'},thinking='disabled')
    assert json.loads(result)['status']=='ready'
    assert captured['response_format']=={'type':'json_object'}
    assert captured['thinking']=={'type':'disabled'}


def test_legacy_calls_do_not_add_provider_options(monkeypatch):
    captured={}
    monkeypatch.setattr(config,'API_KEY','unit-test-not-real')
    class Response:
        status_code=200
        def json(self): return {'choices':[{'message':{'content':'SELECT 1'},'finish_reason':'stop'}]}
    def fake_post(url,**kwargs):
        captured.update(json.loads(kwargs['data']))
        return Response()
    monkeypatch.setattr(llm_client.requests,'post',fake_post)
    assert llm_client.chat_completion([{'role':'user','content':'SQL'}])=='SELECT 1'
    assert 'thinking' not in captured and 'response_format' not in captured
