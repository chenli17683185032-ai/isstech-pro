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
    assert "未审批流程" in js.text
    assert "查看类目" in js.text
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
    assert "费用管理" in js.text
    assert "出差申请" in js.text
    assert "日常报销申请" in js.text
    assert "差旅报销申请" in js.text
    assert "差旅补助申请" in js.text
    assert "发起流程" in js.text
    assert "采购立项申请" in js.text
    assert "在 IPSA 中发起" in js.text
    assert "/WebTP/PurchaseRequisition/ProjectSelection" in js.text
    assert "/WebPMS/selector/selecttype" in js.text
    assert "bizcaseapply.list" in js.text
    assert "helpmenucode=92" in js.text
    assert "helpmenucode=90" in js.text
    assert "helpmenucode=93" in js.text
    assert "helpmenucode=112" in js.text
    assert "noopener noreferrer" in js.text
    assert "no-referrer" in js.text
    assert "同级业务系统" in js.text
    assert "费用管理子项" in js.text
    assert "查看本地详情" in js.text
    assert "申请/管理" not in js.text
    assert "个人相关范围" in js.text
    assert "账号可见范围" not in js.text
    assert "/v1/readonly-modules/payment" in js.text
    assert "/v1/readonly-modules/bizcases" in js.text
    assert "/v1/readonly-modules/travel-applications" in js.text
    assert "/v1/readonly-modules/daily-expenses" in js.text
    assert "/v1/readonly-modules/travel-reimbursements" in js.text
    assert "/v1/readonly-modules/travel-subsidies" in js.text
    assert "/v1/readonly-modules/sync" in js.text
    assert "催办助手" in js.text
    assert "当前偏好" in js.text
    assert "模型失败，已本地排序" in js.text
    assert "/v1/assistant/brief" in js.text
    assert "/v1/assistant/briefs" in js.text
    assert "/v1/assistant/preferences" in js.text
    assert "打开只读详情" not in js.text
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert ".assistant-panel" in css.text
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
