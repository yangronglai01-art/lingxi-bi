"""Explicit enterprise configuration; never reads the legacy .env."""
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, parse_qs


def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',value):
        raise ValueError('Invalid database/resource identifier')
    return value


@dataclass(frozen=True)
class Settings:
    environment: str
    dsn: str = field(repr=False)
    tenant: str
    issuer: str
    audience: str
    introspection_url: str
    client_id: str
    client_secret: str = field(repr=False)
    gateway_url: str
    gateway_key: str = field(repr=False)
    scopes: dict = field(repr=False)
    model_revision: str = ""
    embedding_revision: str = ""
    query_memory_bytes: int = 536870912
    query_seconds: int = 15
    max_rows: int = 1000
    max_bytes: int = 2000000
    max_context_chars: int = 34000
    model_seconds: int = 45
    approved_intent_routing: bool = True
    review_mode: str = 'disabled'
    review_alias: str = ''
    review_revision: str = ''
    review_seconds: int = 10
    review_approval: str = ''
    metrics_token: str = field(default='',repr=False)
    enabled_metrics: tuple = ('net_sales','tickets','inventory','received_units')

    def __post_init__(self):
        from .acceptance import confirmed
        if not isinstance(self.approved_intent_routing,bool):raise ValueError('Invalid intent routing policy')
        if self.review_mode not in {'disabled','shadow','enforce'} or not 1<=self.review_seconds<=20:raise ValueError('Invalid review policy')
        if self.review_mode!='disabled':
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',self.review_alias) or self.review_alias=='local-chat' or not confirmed(self.review_revision) or self.review_revision==self.model_revision:raise ValueError('Independent private review alias/revision required')
        if self.review_mode=='enforce' and not confirmed(self.review_approval):raise ValueError('Real ablation approval required before enforcing review')
        if self.environment not in {'development','integration','staging','production'}:
            raise ValueError('Unknown environment')
        if not self.dsn.startswith(('postgres://','postgresql://')) or not self.tenant:
            raise ValueError('PostgreSQL and tenant are required')
        if not 1<=self.query_seconds<=120 or not 1<=self.max_rows<=10000:
            raise ValueError('Invalid query resource budget')
        if not 16777216<=self.query_memory_bytes<=4294967296:raise ValueError('Query memory budget outside approved range')
        if not self.scopes:
            raise ValueError('At least one explicitly configured StarRocks scope is required')
        for scope,config in self.scopes.items():
            name(scope);name(config['database']);name(config['user'])
            if not isinstance(config.get('regions',[]),list):raise ValueError('Scope regions must be an explicit array')
            if config['user'].lower() in {'root','admin'}:
                raise ValueError('Runtime StarRocks account must be a restricted reader')
            if not config.get('password_env') or not config.get('host'):
                raise ValueError('StarRocks reader credentials must be explicit')
        if len({(c['database'],c['user']) for c in self.scopes.values()})!=len(self.scopes):
            raise ValueError('Each permission scope requires a distinct database reader')
        if self.environment in {'staging','production'}:
            if parse_qs(urlsplit(self.dsn).query).get('sslmode')!=['verify-full']:
                raise ValueError('Production PostgreSQL requires sslmode=verify-full')
            for endpoint in [self.gateway_url,self.introspection_url,self.issuer]:
                if urlsplit(endpoint).scheme!='https':
                    raise ValueError('Production endpoints require HTTPS')
            if not all([self.audience,self.client_id,self.client_secret,self.gateway_key]):
                raise ValueError('Enterprise identity and model credentials required')
            from .acceptance import confirmed
            if not confirmed(self.metrics_token) or not re.fullmatch(r'[!-~]{32,512}',self.metrics_token) or self.metrics_token in {self.gateway_key,self.client_secret}:raise ValueError('Dedicated monitoring-only secret required')
            if not confirmed(self.model_revision):raise ValueError('Production generation model immutable revision required')
            if not confirmed(self.embedding_revision):raise ValueError('Production BGE-M3 immutable revision required')
            if any(not c.get('ssl_ca') for c in self.scopes.values()):
                raise ValueError('StarRocks TLS CA required in production')

    @classmethod
    def from_env(cls):
        try:
            return cls(environment=os.environ.get('APP_ENV','development'),dsn=os.environ['LINGXI_DATABASE_URL'],
                tenant=os.environ['ENTERPRISE_TENANT_ID'],issuer=os.environ['OIDC_ISSUER'],
                audience=os.environ['OIDC_AUDIENCE'],introspection_url=os.environ['OIDC_INTROSPECTION_URL'],
                client_id=os.environ['OIDC_CLIENT_ID'],client_secret=os.environ['OIDC_CLIENT_SECRET'],
                gateway_url=os.environ['MODEL_GATEWAY_URL'],gateway_key=os.environ['MODEL_GATEWAY_KEY'],
                metrics_token=os.environ.get('LINGXI_METRICS_TOKEN',''),
                approved_intent_routing={'true':True,'false':False}[os.environ.get('LINGXI_APPROVED_INTENT_ROUTING','true').lower()],
                review_mode=os.environ.get('LINGXI_REVIEW_MODE','disabled'),review_alias=os.environ.get('LINGXI_REVIEW_ALIAS',''),review_revision=os.environ.get('LINGXI_REVIEW_REVISION',''),review_seconds=int(os.environ.get('LINGXI_REVIEW_SECONDS','10')),review_approval=os.environ.get('LINGXI_REVIEW_APPROVAL',''),
                scopes=json.loads(os.environ['STARROCKS_SCOPES_JSON']),model_revision=os.environ.get('MODEL_REVISION',''),embedding_revision=os.environ.get('EMBEDDING_REVISION',''),
                query_memory_bytes=int(os.environ.get('LINGXI_QUERY_MEMORY_BYTES','536870912')),query_seconds=int(os.environ.get('LINGXI_QUERY_SECONDS','15')),
                enabled_metrics=tuple(json.loads(os.environ.get('LINGXI_ENABLED_METRICS','["net_sales","tickets","inventory","received_units"]'))))
        except (KeyError,ValueError,TypeError):
            raise RuntimeError('Enterprise configuration missing or invalid; secret values omitted') from None
