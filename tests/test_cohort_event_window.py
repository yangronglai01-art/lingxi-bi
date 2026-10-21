import pytest
from test_simulated_rollout import org
from src.petkit.semantic import validate_metric,MetricError
from src.petkit.planning import cohort_sql


def context(metric):return dict(tables={},metric={'id':metric},data_as_of='2026-10-16')


@pytest.mark.parametrize('metric',['activation30','service30'])
def test_approved_cohort_event_window(metric):
    validate_metric(cohort_sql(metric),context(metric))


@pytest.mark.parametrize('window',[
    "a.activation_date<=DATE '2026-10-16'",
    "a.activation_date>=o.paid_date",
    "a.activation_date<=o.paid_date+INTERVAL '30 days'",
    "a.activation_date BETWEEN o.paid_date AND o.paid_date+INTERVAL '31 days'",
    "a.activation_date BETWEEN o.paid_date AND o.paid_date+INTERVAL '30 days' OR 1=1",
    "a.activation_date BETWEEN o.paid_date AND DATE '2026-10-16'",
])
def test_missing_or_ineffective_window_rejected(window):
    sql=f"""SELECT COUNT(DISTINCT o.order_id),COUNT(DISTINCT a.device_id)
    FROM fact_order o LEFT JOIN fact_device_activation a
    ON o.order_id=a.device_id AND ({window})
    WHERE o.paid_date<=DATE '2026-09-16'"""
    with pytest.raises(MetricError,match='分子'):validate_metric(sql,context('activation30'))


@pytest.mark.parametrize('reverse',[False,True])
def test_two_sided_window_in_left_join_on(reverse):
    window=("o.paid_date<=a.activation_date AND o.paid_date+INTERVAL '30 days'>=a.activation_date" if reverse else
            "a.activation_date>=o.paid_date AND a.activation_date<=o.paid_date+INTERVAL '30 days'")
    sql=f"""SELECT COUNT(DISTINCT o.order_id),COUNT(DISTINCT a.device_id)
    FROM fact_order o LEFT JOIN fact_device_activation a ON o.order_id=a.device_id AND {window}
    WHERE o.paid_date<=DATE '2026-09-16'"""
    validate_metric(sql,context('activation30'))


def test_actual_failed_model_response_is_rejected():
    # Reduced from the preserved first concurrency failure; no external report dependency.
    sql="""WITH cohort AS (
      SELECT b.device_id FROM fact_order o JOIN fact_order_item i ON o.order_id=i.order_id
      JOIN bridge_order_device b ON i.item_id=b.item_id
      WHERE o.paid_date>=DATE '2026-09-01' AND o.paid_date<=DATE '2026-09-16'
    ), activation AS (
      SELECT b.device_id,a.activation_date FROM bridge_order_device b
      JOIN fact_device_activation a ON b.device_id=a.device_id
      WHERE a.activation_date<=DATE '2026-10-16'
    ) SELECT COUNT(DISTINCT c.device_id),COUNT(DISTINCT a.device_id)
      FROM cohort c LEFT JOIN activation a ON c.device_id=a.device_id"""
    with pytest.raises(MetricError,match='分子'):validate_metric(sql,context('activation30'))


def joined_event_sql(metric,placement):
    table,column=(('fact_device_activation','activation_date') if metric=='activation30'
                  else ('fact_service_ticket','created_date'))
    window=f"e.{column} BETWEEN c.paid_date AND c.paid_date + INTERVAL '30 days'"
    prefix=cohort_sql(metric).split(', outcomes AS')[0]
    event_join=f'LEFT JOIN {table} e ON c.device_id=e.device_id'
    product_join='LEFT JOIN dim_product p ON c.product_id=p.product_id'
    where=''
    if placement=='later_left':product_join+=' AND '+window
    elif placement=='split':
        event_join+=f' AND e.{column}>=c.paid_date'
        product_join+=f" AND e.{column}<=c.paid_date + INTERVAL '30 days'"
    elif placement in {'right','full'}:
        event_join=event_join.replace('LEFT JOIN',placement.upper()+' JOIN')+' AND '+window
    elif placement=='preserved_from':
        event_join=f'{table} e LEFT JOIN cohort c ON c.device_id=e.device_id AND {window}'
    elif placement=='own_on':event_join+=' AND '+window
    elif placement=='where':
        event_join=event_join.replace('LEFT JOIN','JOIN')
        where=' WHERE '+window
    if placement=='preserved_from':sources=event_join+' '+product_join
    else:sources='cohort c '+event_join+' '+product_join
    return prefix+f''' SELECT c.product_id, COUNT(DISTINCT c.device_id) AS devices,
      COUNT(DISTINCT e.device_id) AS matched_devices,
      ROUND(100.0*COUNT(DISTINCT e.device_id)/NULLIF(COUNT(DISTINCT c.device_id),0),2) AS rate_percent
      FROM {sources} {where} GROUP BY c.product_id'''


@pytest.mark.parametrize('metric',['activation30','service30'])
@pytest.mark.parametrize('placement',['later_left','split','right','full','preserved_from'])
def test_window_on_preserved_or_unrelated_join_is_rejected(metric,placement):
    with pytest.raises(MetricError,match='分子'):
        validate_metric(joined_event_sql(metric,placement),context(metric))


@pytest.mark.parametrize('metric',['activation30','service30'])
@pytest.mark.parametrize('placement',['own_on','where'])
def test_effective_event_window_still_accepted(metric,placement):
    validate_metric(joined_event_sql(metric,placement),context(metric))


@pytest.mark.parametrize('metric',['activation30','service30'])
def test_misplaced_window_can_change_counts_without_breaking_result_contract(metric):
    import duckdb
    from types import SimpleNamespace
    from src.petkit.result_contract import validate
    table,column=(('fact_device_activation','activation_date') if metric=='activation30'
                  else ('fact_service_ticket','created_date'))
    # Include on-time, late and absent events; all three devices stay in cohort.
    with duckdb.connect() as conn:
        conn.execute("CREATE TABLE fact_order(order_id VARCHAR, paid_date DATE)")
        conn.execute("INSERT INTO fact_order VALUES ('o',DATE '2026-08-01')")
        conn.execute("CREATE TABLE fact_order_item(item_id VARCHAR,order_id VARCHAR)")
        conn.execute("INSERT INTO fact_order_item VALUES ('i','o')")
        conn.execute("CREATE TABLE bridge_order_device(device_id VARCHAR,item_id VARCHAR)")
        conn.execute("INSERT INTO bridge_order_device VALUES ('on-time','i'),('late','i'),('absent','i')")
        conn.execute("CREATE TABLE dim_device(device_id VARCHAR,product_id VARCHAR)")
        conn.execute("INSERT INTO dim_device VALUES ('on-time','P'),('late','P'),('absent','P')")
        conn.execute("CREATE TABLE dim_product(product_id VARCHAR)")
        conn.execute("INSERT INTO dim_product VALUES ('P')")
        conn.execute(f'CREATE TABLE {table}(device_id VARCHAR,{column} DATE)')
        conn.execute(f"INSERT INTO {table} VALUES ('on-time',DATE '2026-08-11'),('late',DATE '2026-09-01')")
        bad=conn.execute(joined_event_sql(metric,'later_left')).fetchall()
        good=conn.execute(joined_event_sql(metric,'own_on')).fetchall()
    assert bad==[('P',3,2,66.67)]
    assert good==[('P',3,1,33.33)]
    validate({'id':metric},SimpleNamespace(
        columns=['product_id','devices','matched_devices','rate_percent'],rows=bad))
    with pytest.raises(MetricError,match='分子'):
        validate_metric(joined_event_sql(metric,'later_left'),context(metric))


@pytest.mark.parametrize('metric',['activation30','service30'])
def test_service_repairs_misplaced_window_before_execution(org,monkeypatch,metric):
    import json
    from src import config
    from src.engine import llm_client
    from src.petkit import service
    principals,_,_=org
    monkeypatch.setattr(service,'MODE','llm')
    monkeypatch.setattr(config,'API_KEY','unit-test-not-real')
    responses=iter([joined_event_sql(metric,'later_left'),cohort_sql(metric)])
    messages=[]
    def generate(request_messages,**kwargs):
        messages.append(request_messages)
        return json.dumps(dict(status='ready',sql=next(responses),plan=dict(
            metric=metric,dimensions=[],time_range=None,filters=[],joins=[],grain='device',assumptions=[])))
    monkeypatch.setattr(llm_client,'chat_completion',generate)
    executed=[]
    original_execute=service.execute
    def tracked_execute(checked,*args):
        executed.append(checked.sql)
        return original_execute(checked,*args)
    monkeypatch.setattr(service,'execute',tracked_execute)
    question='各型号售出30天'+('激活率' if metric=='activation30' else '售后设备率')
    answer=service.ask(question,principals['global'])
    assert answer.status=='success',answer.error
    assert len(messages)==2 and len(executed)==1
    previous=json.loads(messages[1][1]['content'])['previous_error']
    assert '分子' in previous['error']
    assert answer.result.row_count>0
