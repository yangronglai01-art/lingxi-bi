import pytest
from src.petkit.result_contract import validate
from src.petkit.semantic import MetricError
from src.engine.executor import QueryResult

@pytest.mark.parametrize('den,num,rate,valid',[(105,63,60,True),(105,63,.6,False),(0,0,None,True),(0,0,0,False),(5,6,120,False),(5,2,40,True),(5,2,float("nan"),False)])
def test_cohort_units_and_counts(den,num,rate,valid):
    result=QueryResult(columns=['devices','matched_devices','rate_percent'],rows=[[den,num,rate]],row_count=1)
    if valid: validate({'id':'activation30'},result)
    else:
        with pytest.raises(MetricError): validate({'id':'activation30'},result)

def test_unknown_rate_alias_not_silently_guessed():
    result=QueryResult(columns=['den','num','rate'],rows=[[10,6,.6]],row_count=1)
    with pytest.raises(MetricError): validate({'id':'activation30'},result)
