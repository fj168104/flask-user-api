"""配额记账服务（PRD v1.4 §20）。

- 首次登录成功 → 个人空间赠 1,000 次免费 OCR（benefit=FIRST_LOGIN）
- 企业首次认证通过 → 企业空间赠 1,000 次免费 OCR（benefit=FIRST_ENTERPRISE_VERIFIED）
- 创建 OCR 任务消耗 1 次免费任务；任务 FAILED/CANCELLED（未产出可用结果）返还
- 免费额度与付费套餐分开记账，免费优先、永不合并（§20.6）

发放幂等：grant_key 唯一约束（稳定身份：用户/企业，与会话无关）。
企业认证为人工 SQL 审核流（无状态翻转钩子），故企业赠送采用惰性补偿：
在配额查询与企业空间任务创建时检测「已 VERIFIED 且未发放」即补发。
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from errors import quota_exceeded
from extensions import db
from models import Enterprise, QuotaLedger
from plans import (
    DEFAULT_PLAN_CODE,
    FIRST_ENTERPRISE_VERIFIED_BONUS_TASKS,
    FIRST_LOGIN_BONUS_TASKS,
    get_plan,
)

logger = logging.getLogger(__name__)


# ---- 发放（幂等） ----


def ensure_user_bonus(user_id: int, *, commit: bool = True) -> bool:
    """首登赠 1,000 次到个人空间；已发放（含并发）返回 False。"""
    key = f"FIRST_LOGIN:U{user_id}"
    if QuotaLedger.query.filter_by(grant_key=key).first() is not None:
        return False
    db.session.add(
        QuotaLedger(
            grant_key=key,
            workspace_type="PERSONAL",
            enterprise_id=None,
            user_id=user_id,
            entry_type="GRANT",
            benefit_code="FIRST_LOGIN",
            quantity=FIRST_LOGIN_BONUS_TASKS,
        )
    )
    if not commit:
        return True
    try:
        db.session.commit()
    except IntegrityError:  # 并发首登：另一会话已发放
        db.session.rollback()
        return False
    logger.info("首登赠送 user=%s +%d", user_id, FIRST_LOGIN_BONUS_TASKS)
    return True


def ensure_enterprise_bonus(enterprise_id: int, *, commit: bool = True) -> bool:
    """企业首次 VERIFIED 赠 1,000 次到企业空间（惰性补偿，幂等）。"""
    enterprise = db.session.get(Enterprise, enterprise_id)
    if enterprise is None or enterprise.status != "VERIFIED":
        return False
    key = f"FIRST_ENT_VERIFIED:E{enterprise_id}"
    if QuotaLedger.query.filter_by(grant_key=key).first() is not None:
        return False
    db.session.add(
        QuotaLedger(
            grant_key=key,
            workspace_type="ENTERPRISE",
            enterprise_id=enterprise_id,
            user_id=None,  # 受益方为企业空间，不绑定个人
            entry_type="GRANT",
            benefit_code="FIRST_ENTERPRISE_VERIFIED",
            quantity=FIRST_ENTERPRISE_VERIFIED_BONUS_TASKS,
        )
    )
    if not commit:
        return True
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    logger.info("企业认证赠送 enterprise=%s +%d", enterprise_id,
                FIRST_ENTERPRISE_VERIFIED_BONUS_TASKS)
    return True


# ---- 余额与统计 ----


def workspace_balance(workspace_type: str, *, user_id: int | None = None,
                      enterprise_id: int | None = None) -> int:
    row = db.session.query(
        func.coalesce(func.sum(QuotaLedger.quantity), 0)
    ).filter(
        QuotaLedger.workspace_type == workspace_type
    )
    if workspace_type == "PERSONAL":
        row = row.filter(QuotaLedger.user_id == user_id)
    else:
        row = row.filter(QuotaLedger.enterprise_id == enterprise_id)
    return int(row.scalar() or 0)


def _granted_total(workspace_type: str, *, user_id: int | None = None,
                   enterprise_id: int | None = None) -> int:
    row = db.session.query(
        func.coalesce(func.sum(QuotaLedger.quantity), 0)
    ).filter(
        QuotaLedger.workspace_type == workspace_type,
        QuotaLedger.entry_type == "GRANT",
    )
    if workspace_type == "PERSONAL":
        row = row.filter(QuotaLedger.user_id == user_id)
    else:
        row = row.filter(QuotaLedger.enterprise_id == enterprise_id)
    return int(row.scalar() or 0)


# ---- 消耗与返还（随任务事务提交） ----


def check_and_consume(job) -> None:
    """创建任务时校验余额并消耗 1 次免费任务（同事务，未 commit）。"""
    if job.workspace_type == "ENTERPRISE":
        ensure_enterprise_bonus(job.enterprise_id, commit=False)
        balance = workspace_balance("ENTERPRISE", enterprise_id=job.enterprise_id)
    else:
        balance = workspace_balance("PERSONAL", user_id=job.created_by_user_id)
    if balance <= 0:
        raise quota_exceeded()
    db.session.add(
        QuotaLedger(
            workspace_type=job.workspace_type,
            enterprise_id=job.enterprise_id,
            user_id=job.created_by_user_id,
            entry_type="CONSUME",
            benefit_code="OCR_TASK",
            quantity=-1,
            job_id=job.id,
        )
    )


def refund_for_job(job_id: str, *, commit: bool = False) -> bool:
    """任务未产出可用结果（FAILED/CANCELLED）返还 1 次；幂等防重。"""
    consumed = QuotaLedger.query.filter_by(job_id=job_id, entry_type="CONSUME").first()
    if consumed is None:
        return False  # 功能上线前的历史任务：未消耗则不返还
    refunded = QuotaLedger.query.filter_by(job_id=job_id, entry_type="REFUND").first()
    if refunded is not None:
        return False
    db.session.add(
        QuotaLedger(
            workspace_type=consumed.workspace_type,
            enterprise_id=consumed.enterprise_id,
            user_id=consumed.user_id,
            entry_type="REFUND",
            benefit_code="OCR_TASK",
            quantity=1,
            job_id=job_id,
        )
    )
    if commit:
        db.session.commit()
    return True


# ---- 汇总（/me/quota） ----


def quota_summary(user) -> list[dict]:
    """当前有效工作空间的配额视图（含赠送自愈）。"""
    from workspace import list_workspaces

    ensure_user_bonus(user.id)
    plan = get_plan(DEFAULT_PLAN_CODE)
    items: list[dict] = []
    for ws in list_workspaces(user):
        if ws["workspace_type"] == "PERSONAL":
            total = _granted_total("PERSONAL", user_id=user.id)
            remaining = workspace_balance("PERSONAL", user_id=user.id)
        else:
            ensure_enterprise_bonus(ws["enterprise_id"])
            total = _granted_total("ENTERPRISE", enterprise_id=ws["enterprise_id"])
            remaining = workspace_balance("ENTERPRISE", enterprise_id=ws["enterprise_id"])
        items.append(
            {
                "workspace_type": ws["workspace_type"],
                "enterprise_id": ws["enterprise_id"],
                "enterprise_name": ws["enterprise_name"],
                "plan": {
                    "code": plan["code"],
                    "name": plan["name"],
                    "is_paid": False,
                    "free_unit": "OCR_TASK",
                    "paid_unit": "OCR_PAGE",
                },
                "free_tasks": {"total": total, "remaining": max(remaining, 0)},
                "paid_pages": {"monthly_total": 0, "remaining": 0},
            }
        )
    return items
