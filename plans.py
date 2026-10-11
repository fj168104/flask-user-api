"""套餐目录（PRD v1.4 §20.1 / P15 定价页）。

P0 仅作展示目录：支付完全线下（联系销售开通/续费），无在线下单、
无回调、无自动续费。免费额度与付费套餐分开记账、互不合并
（免费按 OCR_TASK/次，付费按 OCR_PAGE/页）。
"""
from __future__ import annotations

# 免费赠送额度（PRD v1.4 §20.2）
FIRST_LOGIN_BONUS_TASKS = 1000
FIRST_ENTERPRISE_VERIFIED_BONUS_TASKS = 1000

# 默认套餐（未购买付费套餐时的工作空间档位）
DEFAULT_PLAN_CODE = "KINDER"

PLAN_CATALOG: list[dict] = [
    {
        "code": "KINDER",
        "name": "Kinder",
        "price": {
            "monthly_thb": None,
            "yearly_thb": 888,
            "display": "THB 888 / year",
        },
        "quota_pages_per_month": 1000,
        "seats": 1,
        "features": ["OCR + Excel export", "TH / EN documents"],
        "is_default": True,
    },
    {
        "code": "STARTER",
        "name": "Starter",
        "price": {
            "monthly_thb": 599,
            "yearly_thb": 5990,
            "display": "THB 599 / mo · THB 5,990 / yr",
        },
        "quota_pages_per_month": 5000,
        "seats": 2,
        "features": ["Approval flow", "Email notifications"],
        "is_default": False,
    },
    {
        "code": "INTERMEDIATE",
        "name": "Intermediate",
        "price": {
            "monthly_thb": None,
            "yearly_thb": None,
            "display": "Contact us",
        },
        "quota_pages_per_month": 10000,
        "seats": 5,
        "features": ["LINE/SMS notifications", "Integration"],
        "is_default": False,
    },
    {
        "code": "ADVANCE",
        "name": "Advance",
        "price": {
            "monthly_thb": None,
            "yearly_thb": None,
            "display": "Contact us",
        },
        "quota_pages_per_month": 100000,
        "seats": 10,
        "features": ["Custom dashboard", "API apps"],
        "is_default": False,
    },
]


def get_plan(code: str) -> dict:
    for plan in PLAN_CATALOG:
        if plan["code"] == code:
            return plan
    return PLAN_CATALOG[0]
