"""One serialization contract shared by schema budgeting and generation."""
import json

GATEWAY_MESSAGE_CHARS = 32000
REPAIR_RESERVE_CHARS = 8000


def user_body(question, context, contract, previous=None):
    visible = {k:context[k] for k in ('version','tables','relationships','metric','data_as_of')}
    body = dict(question=question, metadata=visible, execution_contract=contract)
    if previous:
        repair = dict(previous)
        # A very long failed SQL must not displace pinned schemas/metric rules.
        # Omit it as a whole rather than feeding a misleading partial statement.
        if len(json.dumps(repair,ensure_ascii=False)) > REPAIR_RESERVE_CHARS - 32:
            repair = dict(feedback=repair['feedback'], previous_sql_omitted='exceeds reserved repair budget')
        body['repair'] = repair
    return body


def user_content(question, context, contract, previous=None):
    return json.dumps(user_body(question,context,contract,previous),ensure_ascii=False)
