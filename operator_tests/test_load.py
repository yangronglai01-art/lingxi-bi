import httpx
from scripts.load_enterprise import attempt,summarize


def test_load_keeps_rejections_in_denominator():
    with httpx.Client(base_url='https://app.invalid',transport=httpx.MockTransport(lambda _:httpx.Response(429))) as c:
        r=attempt(c,'q',1)
    summary=summarize([r],1)
    assert summary['attempted']==1 and summary['successful']==0
    assert summary['all_enterprise_source'] is False
    assert summary['success_p95_seconds'] is None


def test_load_cancels_after_poll_failure():
    calls=[]
    def handle(r):
        calls.append(r.url.path)
        if r.url.path.endswith('/queries'):return httpx.Response(202,json={'id':'j'})
        if r.url.path.endswith('/cancel'):return httpx.Response(200)
        return httpx.Response(503)
    with httpx.Client(base_url='https://app.invalid',transport=httpx.MockTransport(handle)) as c:
        assert attempt(c,'q',1)['outcome']=='transport_error'
    assert calls[-1].endswith('/j/cancel')


def test_load_records_generation_path_without_exporting_answer():
    def handle(r):
        if r.url.path.endswith('/queries'):return httpx.Response(202,json={'id':'j'})
        return httpx.Response(200,json=dict(state='completed',result=dict(status='ok',generation_mode='local-chat',source_kind='enterprise',result={'secret':'not exported'})))
    with httpx.Client(base_url='https://app.invalid',transport=httpx.MockTransport(handle)) as c:
        result=attempt(c,'q',1)
    assert result['generation_mode']=='local-chat' and 'secret' not in str(result)
