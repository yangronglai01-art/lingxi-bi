"""Demo policy is trusted server configuration, never a user-supplied role header."""
import hmac
import json
import os
from dataclasses import dataclass

SHARED={"shared","product"}
ROLES={
 "analyst":set(["sales","device","service","supply","marketing","finance","customer","product","quality","operations","shared"]),
 "sales":SHARED|{"sales","customer"},
 "service":SHARED|{"device","service","sales"},
 "viewer":SHARED,
 "supply":SHARED|{"supply"},
}


@dataclass(frozen=True)
class Principal:
    subject: str = "local-demo"
    role: str = "analyst"
    regions: tuple = ()
    department: str = ""
    policy_version: int = 0
    managed: bool = False

    def allowed(self,table):
        if self.role not in ROLES: return False
        if self.regions:
            from .row_policy import eligible
            if not eligible(table): return False
        # Hide customer-level entities from service viewers; aggregate/device queries still work.
        if self.role=="service" and table["name"] in {"dim_customer","fact_device_binding"}: return False
        return table["domain"] in ROLES[self.role]


def authenticate(token=""):
    from .settings import MODE
    if os.getenv("PETKIT_DEPLOYMENT")=="simulated":
        from .control import authenticate as persistent_authenticate
        return persistent_authenticate(token)
    raw=os.getenv("PETKIT_API_TOKENS","")
    if raw:
        mapping=json.loads(raw)
        for secret, identity in mapping.items():
            if hmac.compare_digest(secret,token):
                if identity["role"] not in ROLES: raise PermissionError("角色配置无效")
                return Principal(identity["subject"],identity["role"])
        raise PermissionError("无效凭据")
    if MODE!="demo": raise PermissionError("非演示模式必须配置PETKIT_API_TOKENS")
    return Principal()
