"""Reuses legacy storage schema, isolates databases by trusted principal and role."""
import hashlib
from src.history import HistoryStore
from .settings import DATA


def history_for(principal):
    scope=f"{principal.subject}:{principal.role}"
    if principal.managed:
        from .control import verify
        verify(principal)
        scope+=f":{principal.policy_version}:{principal.regions}"
    digest=hashlib.sha256(scope.encode()).hexdigest()
    return HistoryStore(DATA/"history"/f"{digest}.db")
