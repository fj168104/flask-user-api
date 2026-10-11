"""OCR 后台 Worker（PRD §9：自有持久队列，不依赖厂商回调）。

单线程轮询：QUEUED → PREPROCESSING → PROCESSING → POSTPROCESSING →
SUCCEEDED / PARTIAL_SUCCESS / FAILED；CANCEL_REQUESTED → CANCELLED。
进程重启后把中断的非终态任务重新排队（不丢任务、不重复计费——
恢复时仅在任务仍处于 QUEUED/PREPROCESSING 时继续；PROCESSING 中
厂商是否已接收无法对账，按 PRD §12.4 标记 FAILED(uncertain) 保守处理）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from flask import Flask

logger = logging.getLogger(__name__)

TERMINAL = {"SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"}
MAX_VENDOR_RETRIES = 3  # 限流类错误的有界指数退避（PRD §12.4）


class OcrWorker:
    def __init__(self, app: Flask, poll_seconds: float = 2.0) -> None:
        self.app = app
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="ocr-worker", daemon=True)
        self._thread.start()
        logger.info("OCR worker started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        from datetime import datetime, timezone

        from extensions import db
        from models import OcrJob
        import quota

        with self.app.app_context():
            # 重启恢复：中断的任务重新入队；已请求取消的直接落终态（不再计费）
            stuck = OcrJob.query.filter(
                OcrJob.status.in_(["PREPROCESSING", "PROCESSING", "POSTPROCESSING"])
            ).all()
            for job in stuck:
                job.status = "QUEUED"
                job.progress = 0
            cancelling = OcrJob.query.filter_by(status="CANCEL_REQUESTED").all()
            for job in cancelling:
                job.status = "CANCELLED"
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                quota.refund_for_job(job.id)
            db.session.commit()

        while not self._stop.is_set():
            try:
                with self.app.app_context():
                    job = (
                        OcrJob.query.filter_by(status="QUEUED")
                        .order_by(OcrJob.created_at.asc())
                        .first()
                    )
                    if job is not None:
                        self._process(job.id)
            except Exception:  # noqa: BLE001 worker 永不退出
                logger.exception("OCR worker 轮询异常")
            self._stop.wait(self.poll_seconds)

    # ---- 状态推进（独立上下文，提交即持久） ----

    def _transition(self, job_id: str, status: str, *, progress: int | None = None,
                    error_code: str = "", error_message: str = "") -> None:
        from extensions import db
        from models import OcrJob
        import quota

        job = db.session.get(OcrJob, job_id)
        if job is None:
            return
        job.status = status
        if progress is not None:
            job.progress = progress
        if error_code:
            job.error_code = error_code
        if error_message:
            job.error_message = error_message[:512]
        if status in ("FAILED", "CANCELLED"):
            # 未产出可用结果：返还本次免费任务消耗（幂等）
            try:
                quota.refund_for_job(job_id)
            except Exception:  # noqa: BLE001 返还不阻塞状态落库
                logger.exception("配额返还失败 job=%s", job_id)
        db.session.commit()

    def _cancel_requested(self, job_id: str) -> bool:
        from extensions import db
        from models import OcrJob

        job = db.session.get(OcrJob, job_id)
        return job is not None and job.status == "CANCEL_REQUESTED"

    # ---- 任务处理 ----

    def _process(self, job_id: str) -> None:
        from datetime import datetime, timezone

        from extensions import db
        from models import File, OcrJob, PageResult, ResultVersion

        with self.app.app_context():
            self._process_inner(job_id)

    def _process_inner(self, job_id: str) -> None:
        from datetime import datetime, timezone

        from extensions import db
        from models import File, OcrJob, PageResult, ResultVersion

        job = db.session.get(OcrJob, job_id)
        if job is None or job.status != "QUEUED":
            return

        self._transition(job_id, "PREPROCESSING", progress=10)
        if self._cancel_requested(job_id):
            self._transition(job_id, "CANCELLED")
            return

        file = db.session.get(File, job.file_id)
        if file is None or file.status != "READY":
            self._transition(job_id, "FAILED", error_code="FILE_NOT_READY",
                             error_message="source file missing or not READY")
            return
        import storage

        if not storage.exists(file.storage_path):
            self._transition(job_id, "FAILED", error_code="FILE_MISSING",
                             error_message="source file bytes missing")
            return

        client = self.app.extensions.get("textin_client")
        if client is None:
            self._transition(job_id, "FAILED", error_code="OCR_VENDOR_UNAVAILABLE",
                             error_message="TextIn client not configured")
            return

        self._transition(job_id, "PROCESSING", progress=30)

        content = storage.get_bytes(file.storage_path)
        params = dict(job.params or {})
        field_names = params.pop("field_names", None)
        started = time.monotonic()

        result: dict[str, Any] | None = None
        last_err: Exception | None = None
        for attempt in range(MAX_VENDOR_RETRIES):
            try:
                from textin.adapter import run_extraction

                result = run_extraction(
                    client,
                    filename=file.original_name or f"{file.id}",
                    content=content,
                    content_type=file.mime_type or "application/octet-stream",
                    params=params,
                    field_names=field_names,
                )
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if self._cancel_requested(job_id):
                    self._transition(job_id, "CANCELLED")
                    return
                if _is_retryable(e) and attempt < MAX_VENDOR_RETRIES - 1:
                    delay = 2 ** (attempt + 1)
                    logger.warning("TextIn 可重试错误（%s），%ds 后第 %d 次重试", e, delay, attempt + 2)
                    time.sleep(delay)
                else:
                    break

        if result is None:
            err = last_err or RuntimeError("unknown")
            self._transition(job_id, "FAILED", error_code=_error_code(err),
                             error_message=str(err)[:512])
            return

        if self._cancel_requested(job_id):
            self._transition(job_id, "CANCELLED")
            return

        self._transition(job_id, "POSTPROCESSING", progress=80)

        with self.app.app_context():
            job = db.session.get(OcrJob, job_id)
            duration_ms = int((time.monotonic() - started) * 1000)
            version = ResultVersion(
                job_id=job_id, version=1, is_current=True, created_by_user_id=None
            )
            db.session.add(version)
            db.session.flush()
            for page in result["pages"]:
                db.session.add(
                    PageResult(
                        result_version_id=version.id,
                        page_number=page["page_number"],
                        text=page["text"],
                        tables=page["tables"],
                        fields=page["fields"],
                    )
                )
            job.duration_ms = result["duration_ms"] or duration_ms
            job.page_count = result["page_count"]
            job.table_count = result["table_count"]
            job.field_count = result["field_count"]
            job.missing_pages = list(result.get("missing_pages") or [])
            job.capability = {"vendor": "textin", **(job.capability or {}),
                              "field_source": result.get("field_source", "heuristic")}
            job.status = "PARTIAL_SUCCESS" if result["missing_pages"] else "SUCCEEDED"
            job.progress = 100
            job.started_at = job.started_at or datetime.now(timezone.utc).replace(tzinfo=None)
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()

        # 审计副本 + 完成通知
        try:
            import json
            from datetime import datetime, timezone

            storage.put_bytes(
                "audit",
                f"{job_id}.json",
                json.dumps(
                    {
                        "job_id": job_id,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "summary": {k: result[k] for k in
                                    ("page_count", "table_count", "field_count", "missing_pages",
                                     "duration_ms", "field_source", "vendor")},
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
                "application/json",
            )
        except Exception:  # noqa: BLE001
            logger.warning("审计副本写入失败 job=%s", job_id)

        from auth import notify
        from models import User

        with self.app.app_context():
            job = db.session.get(OcrJob, job_id)
            user = db.session.get(User, job.created_by_user_id)
            if user is not None:
                notify(
                    user.id, "OCR_DONE",
                    "OCR task finished",
                    f"job {job_id} -> {job.status}",
                )


def _is_retryable(err: Exception) -> bool:
    from textin.errors import TextInAPIError

    if isinstance(err, TextInAPIError):
        return str(err.code) in {"429", "458", "459"} or "rate" in err.message.lower()
    import httpx

    return isinstance(err, (httpx.TransportError, httpx.TimeoutException))


def _error_code(err: Exception) -> str:
    from textin.errors import TextInAPIError

    if isinstance(err, TextInAPIError):
        return f"TEXTIN_ERROR_{err.code}"
    import httpx

    if isinstance(err, httpx.TimeoutException):
        return "VENDOR_TIMEOUT"
    if isinstance(err, httpx.TransportError):
        return "VENDOR_TRANSPORT"
    return "OCR_FAILED"


_worker: OcrWorker | None = None


def start_worker(app: Flask) -> None:
    """应用启动时拉起 worker（debug reloader 下仅主进程启动一次）。"""
    global _worker
    if _worker is not None:
        return
    import os

    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return  # reloader 子进程未起时跳过，实际请求进程才启动
    _worker = OcrWorker(app)
    _worker.start()
