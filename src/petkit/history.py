"""Reuses legacy storage schema, isolates scopes by trusted principal and role."""
import hashlib
from src.history import HistoryStore
from .settings import DATA  # noqa: F401  历史已迁至 Postgres（scope 隔离）；保留 DATA 供旧调用方/测试 monkeypatch


def history_for(principal):
    scope=f"{principal.subject}:{principal.role}"
    if principal.managed:
        from .control import verify
        verify(principal)
        scope+=f":{principal.policy_version}:{principal.regions}"
    digest=hashlib.sha256(scope.encode()).hexdigest()
    return HistoryStore(scope=digest)
