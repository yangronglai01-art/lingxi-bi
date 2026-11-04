"""OIDC 资源服务器 fail-closed 校验 + 本地隔离集成。

本地起一个自签 RSA 的 OIDC 提供者（LocalOIDCProvider），对资源服务器做真实的
签名 / iss / aud / exp 校验，验证 fail-closed：任何异常都拒绝，不降级为匿名。
"""
import json
import pytest

from src import config
from src.petkit import access, oidc
from src.petkit import settings as petkit_settings


@pytest.fixture()
def idp():
    provider = oidc.LocalOIDCProvider(client_id="lingxi")
    provider.start()
    yield provider
    provider.stop()


@pytest.fixture()
def server(idp):
    return oidc.ResourceServer(idp.issuer, idp.client_id)


def test_valid_token_maps_principal(server, idp):
    token = idp.issue_token("alice", role="analyst", regions=["R1", "R2"], department="销售一部")
    principal = server.authenticate(token)
    assert principal.subject == "alice"
    assert principal.role == "analyst"
    assert principal.regions == ("R1", "R2")
    assert principal.department == "销售一部"


def test_missing_token_rejected(server):
    with pytest.raises(oidc.OIDCError):
        server.authenticate("")
    with pytest.raises(oidc.OIDCError):
        server.authenticate("   ")


def test_expired_token_rejected(server, idp):
    # 过期需超过 leeway（默认 30s），故这里取 -60s
    token = idp.issue_token("bob", role="viewer", expires_in=-60)
    with pytest.raises(oidc.OIDCError, match="过期"):
        server.authenticate(token)


def test_wrong_audience_rejected(server, idp):
    token = idp.issue_token("bob", role="viewer", client_id="other-client")
    with pytest.raises(oidc.OIDCError):
        server.authenticate(token)


def test_wrong_issuer_rejected(idp):
    other = oidc.ResourceServer("http://127.0.0.1:9", "lingxi")
    token = idp.issue_token("bob", role="viewer")
    with pytest.raises(oidc.OIDCError):
        other.authenticate(token)


def test_tampered_signature_rejected(server, idp):
    token = idp.issue_token("bob", role="viewer")
    header, payload, _sig = token.split(".")
    # 篡改 subject 但不重签，签名必然失效
    forged = _b64_payload_replace(payload, "bob", "mallory")
    tampered = f"{header}.{forged}.{token.split('.')[2]}"
    with pytest.raises(oidc.OIDCError, match="无效"):
        server.authenticate(tampered)


def test_unknown_role_rejected(server, idp):
    token = idp.issue_token("bob", role="superadmin")
    with pytest.raises(oidc.OIDCError, match="角色"):
        server.authenticate(token)


def test_wiring_oidc_enabled(monkeypatch, idp):
    monkeypatch.setattr(config, "OIDC_ISSUER", idp.issuer)
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", idp.client_id)
    oidc._resource_server = None
    token = idp.issue_token("carol", role="viewer", regions=["R1"])
    principal = access.authenticate(token)
    assert principal.subject == "carol"
    assert principal.role == "viewer"


def test_wiring_fail_closed_without_oidc(monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", "")
    monkeypatch.delenv("PETKIT_DEPLOYMENT", raising=False)
    monkeypatch.setattr(petkit_settings, "MODE", "production")
    oidc._resource_server = None
    with pytest.raises(PermissionError):
        access.authenticate("some-opaque-token")


def test_auth_code_pkce_flow(server, idp):
    """本地 IdP 的授权码 + PKCE 完整流程（隔离集成，非伪造外部 IdP）。"""
    import base64
    import hashlib
    import secrets
    import urllib.parse
    import requests

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    redirect_uri = "http://localhost:5173/callback"

    auth_url = idp.issuer + "/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": idp.client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
    })
    resp = requests.get(auth_url, allow_redirects=False)
    assert resp.status_code == 302
    parsed = urllib.parse.urlparse(resp.headers["Location"])
    q = urllib.parse.parse_qs(parsed.query)
    code = q["code"][0]
    assert q["state"][0] == "xyz"

    resp2 = requests.post(idp.issuer + "/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": idp.client_id,
        "code_verifier": verifier,
    })
    assert resp2.status_code == 200
    access = resp2.json()["access_token"]
    principal = server.authenticate(access)
    assert principal.subject == "local-interview"
    assert principal.role == "analyst"


def test_auth_code_pkce_wrong_verifier_rejected(idp):
    import base64
    import hashlib
    import secrets
    import urllib.parse
    import requests

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    redirect_uri = "http://localhost:5173/callback"
    auth_url = idp.issuer + "/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": idp.client_id,
        "redirect_uri": redirect_uri, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    resp = requests.get(auth_url, allow_redirects=False)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["Location"]).query)["code"][0]
    resp2 = requests.post(idp.issuer + "/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri, "client_id": idp.client_id,
        "code_verifier": "wrong-verifier",
    })
    assert resp2.status_code == 400


def test_wiring_demo_mode_still_offline(monkeypatch):
    # demo 是明确标记的离线模板模式（无真实身份），不是「本地静态令牌身份」。
    monkeypatch.setattr(config, "OIDC_ISSUER", "")
    monkeypatch.delenv("PETKIT_DEPLOYMENT", raising=False)
    monkeypatch.setattr(petkit_settings, "MODE", "demo")
    oidc._resource_server = None
    principal = access.authenticate("")
    assert principal.subject == "local-demo"
    assert not principal.managed


def _b64_payload_replace(payload_b64, old, new):
    import base64
    raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
    data = json.loads(raw)
    data["sub"] = new
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
