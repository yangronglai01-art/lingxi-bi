import pytest
from src.petkit.planning import plan
from src.petkit.service import _messages

@pytest.mark.parametrize('phrase,metric',[
    ('30天售后设备率','service30'),('售出30天售后设备率','service30'),
    ('30天激活率','activation30'),('售出30天激活率','activation30'),
])
def test_registered_cohort_spelling_keeps_contract(phrase,metric):
    proposal=plan('2026年6月各型号'+phrase,demo=False)
    assert proposal['status']=='ready'
    assert proposal['metric']['id']==metric
    context=dict(version='test',tables={},relationships=[],metric=proposal['metric'],data_as_of='2026-10-16')
    import json
    message=json.loads(_messages(phrase,context,proposal)[1]['content'])
    assert message['execution_contract']['maturity_cutoff']=='2026-09-16'
    assert 'matched_devices' in message['execution_contract']['rule']

def test_short_service_phrase_still_clarifies_multi_metric():
    assert plan('30天售后设备率和净销售额',demo=False)['status']=='clarification'

def test_unspecified_failure_rate_still_needs_clarification():
    assert plan('各型号故障率',demo=False)['status']=='clarification'

def test_unregistered_rate_does_not_acquire_cohort_definition():
    assert plan('各型号60天售后设备率',demo=False)['metric'] is None
