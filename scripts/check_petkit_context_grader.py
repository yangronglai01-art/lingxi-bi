import copy
import importlib.util
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('context_validation',root/'scripts/validate_petkit_schema_context.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from src.petkit.access import Principal
from src.petkit.retrieval import current_catalog,retrieve

module.CATALOG=current_catalog()
case=next(c for c in json.loads((root/'evals/petkit_schema_context_v1.json').read_text(encoding='utf-8'))['cases'] if c['metric_id']=='received_units')
metric=next(m for m in module.CATALOG['metrics'] if m['id']=='received_units')
principal=Principal('grader-check','supply',('R1',),managed=True)
base=retrieve(case['question'],principal,metric)
assert module.inspect(base,case,principal)['passed']
checks=[]
broken=copy.deepcopy(base)
del broken['tables']['fact_receipt']['columns']['quantity']
result=module.inspect(broken,case,principal)
assert not result['passed'] and 'fact_receipt.quantity' in result['missing_columns']
checks.append('missing_quantity_rejected')
broken=copy.deepcopy(base)
broken['relationships']=[]
result=module.inspect(broken,case,principal)
assert not result['passed'] and result['disconnected_tables']
checks.append('disconnected_join_rejected')
broken=copy.deepcopy(base)
broken['tables']['fact_order']=module.CATALOG['tables']['fact_order']
result=module.inspect(broken,case,principal)
assert not result['passed'] and 'fact_order' in result['unauthorized_tables']
checks.append('unauthorized_table_rejected')
broken=copy.deepcopy(base)
broken['evidence'].append(dict(table='fact_order'))
result=module.inspect(broken,case,principal)
assert not result['passed'] and 'fact_order' in result['unauthorized_evidence']
checks.append('unauthorized_evidence_rejected')
broken=copy.deepcopy(base)
broken['relationships'].append(dict(left='fact_receipt',right='dim_warehouse',left_key='quantity',right_key='warehouse_id',approved=False))
result=module.inspect(broken,case,principal)
assert not result['passed'] and result['bad_relationships']
checks.append('unapproved_edge_rejected')
report=dict(checks=checks,passed=len(checks),production_data_modified=False)
(root/'reports/petkit500_context_grader_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
