from scripts.evaluate_enterprise import judge

def case(**changes):
    return dict(expected_status='ok',expected_columns=['name','amount'],expected_rows=[['A','12.03'],['B','2.00']],**changes)

def answer():return dict(status='ok',generation_mode='local-chat',result=dict(columns=['name','amount'],rows=[['B',2],['A','12.03']],truncated=False))

def test_exact_decimal_unordered():assert judge(case(),answer())=='passed_value'

def test_wrong_or_truncated_result_is_failure():
    a=answer();a['result']['rows'][0][1]=2.01
    assert judge(case(),a)=='failed_value'
    a=answer();a['result']['truncated']=True
    assert judge(case(),a)=='failed_truncated'

def test_template_is_not_model_quality():
    a=answer();a['generation_mode']='approved_template'
    assert judge(case(requires_model=True),a)=='failed_generation_path'
    assert judge({'expected_status':'denied'},{'status':'denied'})=='passed_behavior'


def test_model_is_not_counted_as_template():
    assert judge(case(requires_model=False),answer())=='failed_generation_path'
