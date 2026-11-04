"""OIDC 资源服务器 + 本地开发用 OIDC 提供者。

资源服务器（``ResourceServer``）按 fail-closed 原则校验访问令牌：
- 令牌缺失 / 签名无效 / 过期 / 签发者(iss)或受众(aud)不符 / 未携带已授权角色 → 一律拒绝。
- 身份只来自可信 IdP 签发的 JWT，绝不信任请求头里自报的身份字段（参考 WrenAI /
  Dataherald 的资源服务器网关）。

本地开发（本机无真实 IdP）用 ``LocalOIDCProvider`` 自签 RSA 令牌 + 本地 JWKS 端点：
这是「本地隔离集成」，不是伪造接通外部 IdP。
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from jwt import PyJWKClient

from src import config
from .access import Principal, ROLES

# 角色优先级：令牌可能授予多个角色时，按此顺序取第一个（analyst 最宽 → viewer 最窄）。
_ROLE_PRIORITY = ("analyst", "sales", "service", "supply", "viewer")


class OIDCError(PermissionError):
    """OIDC 校验失败（对外表现为 401）。"""


def _s256(verifier: str) -> str:
    """PKCE S256：base64url(sha256(code_verifier))。"""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _claim_path(claims: dict, path: str):
    """按点号路径取嵌套 claim（例如 ``realm_access.roles``）。"""
    node = claims
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class ResourceServer:
    """OIDC 资源服务器：校验访问令牌并映射为内部 Principal。"""

    def __init__(self, issuer, client_id, jwks_uri=None,
                 role_claim=None, regions_claim=None, department_claim=None,
                 leeway=30):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._jwks_uri = jwks_uri or None
        self.role_claim = role_claim or config.OIDC_ROLE_CLAIM
        self.regions_claim = regions_claim or config.OIDC_REGIONS_CLAIM
        self.department_claim = department_claim or config.OIDC_DEPARTMENT_CLAIM
        self.leeway = leeway
        self._jwks_client = None

    def _get_jwks_client(self):
        if self._jwks_client is None:
            uri = self._jwks_uri or self._discover_jwks_uri()
            self._jwks_client = PyJWKClient(uri, lifespan=300)
        return self._jwks_client

    def _discover_jwks_uri(self):
        import requests
        url = self.issuer + "/.well-known/openid-configuration"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()["jwks_uri"]

    def authenticate(self, token):
        if not token or not token.strip():
            raise OIDCError("缺少访问令牌")
        try:
            key = self._get_jwks_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except OIDCError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise OIDCError("访问令牌已过期") from exc
        except jwt.InvalidAudienceError as exc:
            raise OIDCError("访问令牌受众不符") from exc
        except jwt.InvalidIssuerError as exc:
            raise OIDCError("访问令牌签发者不受信任") from exc
        except Exception as exc:
            raise OIDCError("访问令牌无效") from exc
        return self._principal(claims)

    def _principal(self, claims):
        role = self._pick_role(claims)
        regions = tuple(_claim_path(claims, self.regions_claim) or ())
        department = _claim_path(claims, self.department_claim) or ""
        return Principal(subject=claims["sub"], role=role, regions=regions, department=department)

    def _pick_role(self, claims):
        roles = _claim_path(claims, self.role_claim)
        if isinstance(roles, str):
            roles = [roles]
        granted = {r for r in (roles or []) if isinstance(r, str)}
        for role in _ROLE_PRIORITY:
            if role in granted:
                return role
        raise OIDCError("访问令牌未携带已授权角色")


_resource_server = None


def resource_server():
    """按当前配置惰性构造（配置变化时重建）的资源服务器单例。"""
    global _resource_server
    issuer = config.OIDC_ISSUER.rstrip("/")
    if _resource_server is None or _resource_server.issuer != issuer:
        _resource_server = ResourceServer(
            issuer,
            config.OIDC_CLIENT_ID,
            jwks_uri=config.OIDC_JWKS_URI or None,
        )
    return _resource_server


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalOIDCProvider:
    """本地开发 / 隔离集成用 OIDC 提供者。

    生成 RSA 密钥对、在本地端口服务 ``/.well-known/openid-configuration`` 与
    ``/jwks``、并签发 RS256 访问令牌。资源服务器可对其做真实的签名 / iss / aud /
    exp 校验（非伪造外部 IdP）。
    """

    def __init__(self, client_id="lingxi", host="127.0.0.1", port=None):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.client_id = client_id
        self.host = host
        self.port = port or _free_port()
        self.kid = "local-dev-key-1"
        self.issuer = f"http://{host}:{self.port}"
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = self._private_key.public_key()
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        self._jwks = {"keys": [jwk]}
        self._server = None
        self._thread = None
        self._codes = {}   # auth-code 流程（PKCE）临时授权码
        self._code_lock = threading.Lock()

    def start(self):
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status, obj, extra_headers=None):
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                # 本地 IdP 需被浏览器 SPA 跨源调用（token/jwks 端点），
                # 与真实 IdP（Keycloak/Auth0）一致地放行 CORS。
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                for k, v in (extra_headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(self.path)
                route = parsed.path
                query = parse_qs(parsed.query)
                if route == "/.well-known/openid-configuration":
                    self._json(200, {
                        "issuer": provider.issuer,
                        "authorization_endpoint": provider.issuer + "/authorize",
                        "token_endpoint": provider.issuer + "/token",
                        "jwks_uri": provider.issuer + "/jwks",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256", "plain"],
                    })
                elif route == "/jwks":
                    self._json(200, provider._jwks)
                elif route == "/authorize":
                    provider._authorize(self, query)
                else:
                    self._json(404, {"error": "not_found"})

            def do_POST(self):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(self.path)
                if parsed.path == "/token":
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    body = self.rfile.read(length).decode()
                    provider._token(self, parse_qs(body))
                else:
                    self._json(404, {"error": "not_found"})

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def _authorize(self, handler, query):
        from urllib.parse import quote, urlencode
        redirect_uri = (query.get("redirect_uri") or [""])[0]
        state = (query.get("state") or [""])[0]
        code_challenge = (query.get("code_challenge") or [""])[0]
        method = (query.get("code_challenge_method") or ["S256"])[0]
        client_id = (query.get("client_id") or [""])[0]
        # 本地 IdP：固定演示身份 local-interview（analyst）
        subject = (query.get("login_hint") or ["local-interview"])[0]
        role = (query.get("role") or ["analyst"])[0]
        if not redirect_uri or not code_challenge:
            handler._json(400, {"error": "invalid_request"})
            return
        code = uuid.uuid4().hex
        with self._code_lock:
            self._codes[code] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": method,
                "subject": subject,
                "role": role,
                "expires_at": time.time() + 120,
            }
        sep = "&" if "?" in redirect_uri else "?"
        url = f"{redirect_uri}{sep}code={quote(code)}"
        if state:
            url += f"&state={quote(state)}"
        handler.send_response(302)
        handler.send_header("Location", url)
        handler.end_headers()

    def _token(self, handler, params):
        from urllib.parse import parse_qs
        grant_type = (params.get("grant_type") or [""])[0]
        code = (params.get("code") or [""])[0]
        verifier = (params.get("code_verifier") or [""])[0]
        client_id = (params.get("client_id") or [""])[0]
        redirect_uri = (params.get("redirect_uri") or [""])[0]
        if grant_type != "authorization_code":
            handler._json(400, {"error": "unsupported_grant_type"}); return
        with self._code_lock:
            entry = self._codes.pop(code, None)
        if not entry or entry["expires_at"] < time.time():
            handler._json(400, {"error": "invalid_grant"}); return
        if entry["client_id"] and entry["client_id"] != client_id:
            handler._json(400, {"error": "invalid_client"}); return
        if redirect_uri and entry["redirect_uri"] != redirect_uri:
            handler._json(400, {"error": "invalid_grant"}); return
        if entry["code_challenge_method"] == "S256":
            ok = _s256(verifier) == entry["code_challenge"]
        else:
            ok = verifier == entry["code_challenge"]
        if not ok:
            handler._json(400, {"error": "invalid_grant"}); return
        token = self.issue_token(entry["subject"], role=entry["role"])
        handler._json(200, {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": token,
        })

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def issue_token(self, subject, role="analyst", regions=(), department="",
                    expires_in=3600, client_id=None):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": subject,
            "iss": self.issuer,
            "aud": client_id or self.client_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
            "jti": uuid.uuid4().hex,          # 保证同秒内签发的令牌仍唯一
            "roles": [role],
            "regions": list(regions),
            "department": department,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256",
                          headers={"kid": self.kid})

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
