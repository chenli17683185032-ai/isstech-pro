"""The built workflow-center SPA is served locally without a Node runtime."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from isstech_replay.api import WEB_DIST, create_app
from isstech_replay.session_store import SessionStore


def test_built_root_ui_and_hashed_assets_are_served() -> None:
    assert WEB_DIST.is_dir()
    app = create_app(session_store=SessionStore())
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "统一流程中心" in response.text
        assert '<div id="root"></div>' in response.text
        script = re.search(r'src="([^"]+\.js)"', response.text)
        stylesheet = re.search(r'href="([^"]+\.css)"', response.text)
        favicon = re.search(r'rel="icon"[^>]+href="([^"]+)"', response.text)
        assert script is not None
        assert stylesheet is not None
        assert favicon is not None
        js = client.get(script.group(1))
        css = client.get(stylesheet.group(1))
        icon = client.get(favicon.group(1))

    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "待处理" in js.text
    assert "已完成" in js.text
    assert "范围：我申请、我的项目与我管理的" in js.text
    assert "全部相关" in js.text
    assert "我的项目" in js.text
    assert "我提交的" in js.text
    assert "我管理的" in js.text
    assert "全部流程" in js.text
    assert "审批人" in js.text
    assert "localStorage" in js.text
    assert "查看本地详情" in js.text
    assert "审批轨迹" in js.text
    assert "上游未返回审批轨迹" in js.text
    assert "筛选流程类型" in js.text
    assert "业务查询" in js.text
    assert "付款申请" in js.text
    assert "BizCase查询" in js.text
    assert "申请/管理" in js.text
    assert "submitted_or_managed_count" in js.text
    assert "个人相关范围" in js.text
    assert "账号可见范围" not in js.text
    assert "/v1/readonly-modules/payment" in js.text
    assert "/v1/readonly-modules/bizcases" in js.text
    assert "/v1/readonly-modules/sync" in js.text
    assert "打开只读详情" not in js.text
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert "http://" not in response.text
    assert "https://" not in response.text


def test_api_routes_take_precedence_over_static_mount() -> None:
    app = create_app(session_store=SessionStore())
    with TestClient(app) as client:
        health = client.get("/health")
        docs = client.get("/docs")
        protected = client.get("/v1/drafts")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert docs.status_code == 200
    assert protected.status_code == 401
    assert protected.json()["detail"]["code"] == "AUTH_EXPIRED"
