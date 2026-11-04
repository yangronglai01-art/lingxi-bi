"""测试隔离：每个测试前清空并重建共享 Postgres 控制面。

控制面从 SQLite 迁到 PG17 后，不再有「每个测试一个 SQLite 文件」的天然隔离
（tmp_path 只影响数据面工件，不影响 Postgres）。因此用一个 autouse fixture 在每个
测试前重置控制面，恢复与旧 SQLite 一致的「干净起点」语义。
"""
import os
import pytest
from src.petkit import control

def pytest_sessionstart(session):
    from src import config
    from src.storage import current_database
    database=current_database()
    if (config.PG_HOST not in {"localhost","127.0.0.1"} or not database.endswith("_qa")
        or os.getenv("LINGXI_TEST_ALLOW_RESET")!=database):
        raise pytest.UsageError("Legacy tests reset tables. Set an isolated local *_qa database and LINGXI_TEST_ALLOW_RESET to that exact name.")



@pytest.fixture(autouse=True)
def _fresh_control_store():
    control.reset()
    yield


@pytest.fixture()
def idp():
    """本地自签 RSA 的 OIDC 提供者（隔离集成用，非伪造外部 IdP）。"""
    from src.petkit import oidc
    provider = oidc.LocalOIDCProvider(client_id="lingxi")
    provider.start()
    yield provider
    provider.stop()
