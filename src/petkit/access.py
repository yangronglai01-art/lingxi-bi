"""Demo policy is trusted server configuration, never a user-supplied role header."""
import os
from dataclasses import dataclass
from src import config

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
    # OIDC 资源服务器（fail-closed）：配置了 OIDC_ISSUER 即校验 JWT，取代本地静态令牌身份。
    if config.OIDC_ISSUER:
        from . import oidc
        return oidc.resource_server().authenticate(token)
    if MODE!="demo": raise PermissionError("未配置 OIDC 资源服务器（OIDC_ISSUER）")
    return Principal()
