"""Introspected workforce identity intersected with authoritative PG grants."""
import math
import time
from dataclasses import dataclass
import httpx


class Denied(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    role: str
    regions: tuple
    scope: str
    policy_version: int
    managed: bool = True
    department: str = ''

    def allowed(self,table):
        from src.petkit.access import ROLES
        from src.petkit.access import Principal as BusinessPrincipal
        return BusinessPrincipal(self.subject,self.role,self.regions,self.department,self.policy_version,True).allowed(table)


class Identity:
    def __init__(self,settings,store,*,transport=None):
        self.settings,self.store,self.transport=settings,store,transport

    def resolve(self,token):
        if not token or len(token)>16384:
            raise Denied('Enterprise identity required')
        s=self.settings
        try:
            with httpx.Client(timeout=5,follow_redirects=False,transport=self.transport) as client:
                response=client.post(s.introspection_url,auth=(s.client_id,s.client_secret),data={'token':token})
            if response.status_code!=200 or len(response.content)>65536:
                raise ValueError()
            c=response.json();aud=c.get('aud',[]);aud=[aud] if isinstance(aud,str) else aud
            expiry=c.get('exp');subject=c.get('sub')
            roles=c.get('resource_access',{}).get(s.audience,{}).get('roles',[])
            if (c.get('active') is not True or c.get('iss')!=s.issuer or s.audience not in aud
                or c.get('tenant_id')!=s.tenant or not isinstance(subject,str) or not 1<=len(subject)<=128
                or type(expiry) not in {int,float} or not math.isfinite(expiry) or expiry<=time.time()
                or not isinstance(roles,list) or 'analytics_reader' not in roles):
                raise ValueError()
            grant=self.store.principal(s.tenant,subject)
            if grant.scope not in s.scopes or tuple(s.scopes[grant.scope].get('regions',[]))!=grant.regions:
                raise ValueError()
            return grant
        except Denied:
            raise
        except (httpx.HTTPError,ValueError,TypeError,AttributeError,KeyError):
            raise Denied('Invalid identity or identity provider unavailable') from None
