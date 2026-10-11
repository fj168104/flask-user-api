"""套餐目录路由（PRD v1.4 §20.1 / §14）。

需登录（无匿名）；仅返回目录数据，支付完全线下（联系销售），
无下单、无回调、无在线支付。
"""
from __future__ import annotations

from flask import Blueprint

from errors import ok
from plans import PLAN_CATALOG

bp = Blueprint("plans", __name__)


@bp.get("/plans")
def list_plans():
    return ok(
        {
            "currency": "THB",
            "payment": "OFFLINE",
            "items": PLAN_CATALOG,
        }
    )
