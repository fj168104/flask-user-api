"""PRD v1.2 API 集成测试：门禁 / 文件 / OCR / 结果 / 导出 / 企业。

约定（见 conftest docstring）：每个用户一个专属 client（Cookie jar 即身份），
headers 只带 X-CSRF-Token；成功信封 {code:"OK", data}，错误信封扁平
{code, message_key, request_id, retryable, field_errors}。
"""
from __future__ import annotations

from datetime import datetime, timezone

from extensions import db
from models import Enterprise
from tests.conftest import PNG_1PX

COMPANY = {
    "legal_name": "Acme Co",
    "registration_no": "REG-1",
    "contact_phone": "0812345678",
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _login(app, dev_id):
    c = app.test_client()
    r = c.post("/api/v1/auth/line", json={"dev_id": dev_id})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    return c, {"X-CSRF-Token": data["csrf_token"]}, data["user"]


def _make_ready_file(c, h, name="doc.png"):
    r = c.post("/api/v1/uploads", json={"file_name": name, "size_bytes": len(PNG_1PX)}, headers=h)
    assert r.status_code == 201, r.get_json()
    f = r.get_json()["data"]["file"]
    r = c.put(f"/api/v1/uploads/{f['id']}/content", data=PNG_1PX,
              headers={"Content-Type": "image/png", **h})
    assert r.status_code == 200, r.get_json()
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["file"]


def _verified_enterprise(app, c, h):
    """添加公司 + 模拟后台人工审核（UPDATE enterprises SET status='VERIFIED'）；返回企业 dict。"""
    r = c.post("/api/v1/enterprises", json=COMPANY, headers=h)
    assert r.status_code == 201, r.get_json()
    ent = r.get_json()["data"]["enterprise"]
    with app.app_context():
        e = db.session.get(Enterprise, ent["id"])
        e.status = "VERIFIED"
        db.session.commit()
    return ent


# ---------- 门禁 ----------

def test_health(client):
    assert client.get("/api/health").status_code == 200


def test_anonymous_gate(client):
    for path in ["/api/v1/me", "/api/v1/me/workspaces", "/api/v1/files/x",
                 "/api/v1/ocr/jobs", "/api/v1/exports/x", "/api/v1/enterprises/mine"]:
        assert client.get(path).status_code == 401, path


def test_unknown_version_404(client):
    assert client.get("/api/v0/me").status_code == 404
    assert client.get("/api/v2/me").status_code == 404
    assert client.get("/api/me").status_code == 404


def test_unknown_endpoint_404(auth, client):
    c, h, _ = auth
    assert c.get("/api/v1/nonexistent", headers=h).status_code == 404
    # 匿名先过会话门禁（401），路由在鉴权之后
    assert client.post("/api/v1/auth/line/callback").status_code == 401


# ---------- 认证 ----------

def test_auth_flow(app):
    c, h, user = _login(app, "dev-alice")
    r = c.get("/api/v1/me", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["display_name"] == user["display_name"]

    r = c.get("/api/v1/me/workspaces", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["workspaces"][0]["workspace_type"] == "PERSONAL"

    r = c.post("/api/v1/auth/logout", headers=h)
    assert r.status_code == 200
    assert c.get("/api/v1/me", headers=h).status_code == 401


def test_write_without_csrf_rejected(auth):
    c, _h, _ = auth
    r = c.post("/api/v1/uploads", json={"file_name": "x.png", "size_bytes": 1}, headers={})
    assert r.status_code == 403
    assert r.get_json()["code"] == "CSRF_INVALID"


def test_error_envelope_shape(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    body = r.get_json()
    assert body["code"] == "AUTH_REQUIRED"
    assert body["message_key"] == "auth.login_required"
    assert body["request_id"]
    assert body["retryable"] is False


# ---------- 文件 ----------

def test_uploads_happy_path(auth):
    c, h, _ = auth
    f = _make_ready_file(c, h, "ok.png")
    assert f["status"] == "READY"
    assert f["size_bytes"] == len(PNG_1PX)

    r = c.get(f"/api/v1/files/{f['id']}", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["file"]["status"] == "READY"


def test_uploads_rejected_and_repair(auth):
    c, h, _ = auth
    r = c.post("/api/v1/uploads", json={"file_name": "bad.png", "size_bytes": len(PNG_1PX)}, headers=h)
    f = r.get_json()["data"]["file"]
    r = c.put(f"/api/v1/uploads/{f['id']}/content", data=b"not-an-image",
              headers={"Content-Type": "image/png", **h})
    assert r.status_code == 400  # 魔数校验当场拒绝并置 REJECTED
    r = c.get(f"/api/v1/files/{f['id']}", headers=h)
    assert r.get_json()["data"]["file"]["status"] == "REJECTED"

    r = c.put(f"/api/v1/uploads/{f['id']}/content", data=PNG_1PX,
              headers={"Content-Type": "image/png", **h})
    assert r.status_code == 200  # REJECTED 允许重传内容
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["file"]["status"] == "READY"


def test_files_stranger_404(auth, app):
    c1, h1, _ = auth
    f = _make_ready_file(c1, h1, "priv.png")
    c2, h2, _ = _login(app, "dev-bob")
    r = c2.get(f"/api/v1/files/{f['id']}", headers=h2)
    assert r.status_code == 404


def _pdf_bytes(pages: int = 2) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 72 + i * 24), f"saiflow pdf test page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_upload_complete_and_page_preview(auth):
    c, h, _ = auth
    pdf = _pdf_bytes(2)
    r = c.post("/api/v1/uploads", json={"file_name": "doc.pdf", "size_bytes": len(pdf)}, headers=h)
    f = r.get_json()["data"]["file"]
    r = c.put(f"/api/v1/uploads/{f['id']}/content", data=pdf,
              headers={"Content-Type": "application/pdf", **h})
    assert r.status_code == 200
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 200
    f = r.get_json()["data"]["file"]
    assert f["status"] == "READY"
    assert f["page_count"] == 2
    assert f["mime_type"] == "application/pdf"

    # PDF 页预览渲染为 PNG，命中缓存后再取仍 200
    for _ in range(2):
        r = c.get(f"/api/v1/files/{f['id']}/pages/1/preview", headers=h)
        assert r.status_code == 200
        assert r.mimetype == "image/png"
        assert r.data[:8] == b"\x89PNG\r\n\x1a\n"
    r = c.get(f"/api/v1/files/{f['id']}/pages/2/preview", headers=h)
    assert r.status_code == 200
    r = c.get(f"/api/v1/files/{f['id']}/pages/3/preview", headers=h)
    assert r.status_code == 404  # 超出页数


def test_pdf_corrupt_rejected_at_complete(auth):
    c, h, _ = auth
    r = c.post("/api/v1/uploads", json={"file_name": "broken.pdf", "size_bytes": 64}, headers=h)
    f = r.get_json()["data"]["file"]
    r = c.put(f"/api/v1/uploads/{f['id']}/content", data=b"%PDF garbage not really a pdf",
              headers={"Content-Type": "application/pdf", **h})
    assert r.status_code == 200  # 魔数通过，结构性损坏在 complete 阶段拒绝
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 400
    assert r.get_json()["message_key"] == "files.corrupt_or_encrypted"
    r = c.get(f"/api/v1/files/{f['id']}", headers=h)
    assert r.get_json()["data"]["file"]["status"] == "REJECTED"


# ---------- OCR 任务 ----------

def test_job_lifecycle(succeeded_job):
    job_id, c, h = succeeded_job
    r = c.get(f"/api/v1/ocr/jobs/{job_id}", headers=h)
    assert r.status_code == 200
    job = r.get_json()["data"]
    assert job["status"] == "SUCCEEDED"
    assert job["page_count"] == 1
    assert job["table_count"] == 1
    assert job["field_count"] == 1
    assert job["review_status"] == "UNREVIEWED"

    r = c.get(f"/api/v1/ocr/jobs/{job_id}/results", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["pages"][0]["tables"]


def test_job_create_validation(auth, ready_file):
    c, h, f = ready_file
    r = c.post("/api/v1/ocr/jobs", json={"file_id": f["id"], "field_names": "not-a-list"},
               headers=h)
    assert r.status_code == 400
    assert any(fe["field"] == "field_names" for fe in r.get_json()["field_errors"])

    r = c.post("/api/v1/ocr/jobs", json={"file_id": "no-such"}, headers=h)
    assert r.status_code == 404


def test_job_idempotency(auth, ready_file):
    c, h, f = ready_file
    payload = {"file_id": f["id"], "params": {}, "idempotency_key": "abc-123"}
    r1 = c.post("/api/v1/ocr/jobs", json=payload, headers=h)
    r2 = c.post("/api/v1/ocr/jobs", json=payload, headers=h)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.get_json()["data"]["id"] == r2.get_json()["data"]["id"]


def test_job_cancel_and_rerun(auth, ready_file):
    c, h, f = ready_file
    r = c.post("/api/v1/ocr/jobs", json={"file_id": f["id"], "params": {}}, headers=h)
    job_id = r.get_json()["data"]["id"]
    r = c.post(f"/api/v1/ocr/jobs/{job_id}/cancel", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "CANCELLED"

    r = c.post(f"/api/v1/ocr/jobs/{job_id}/rerun", headers=h)
    assert r.status_code == 201
    assert r.get_json()["data"]["parent_job_id"] == job_id

    r = c.post(f"/api/v1/ocr/jobs/{job_id}/cancel", headers=h)
    assert r.status_code == 409


def test_job_delete(auth, succeeded_job):
    job_id, c, h = succeeded_job
    r = c.delete(f"/api/v1/ocr/jobs/{job_id}", headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["results_deleted"] is True
    assert c.get(f"/api/v1/ocr/jobs/{job_id}/results", headers=h).status_code == 404


# ---------- 结果与校对 ----------

def _first_cell(c, h, job_id):
    r = c.get(f"/api/v1/ocr/jobs/{job_id}/results", headers=h)
    data = r.get_json()["data"]
    page = data["pages"][0]
    table = page["tables"][0]
    return data, page, table, table["cells"][0]


def test_results_and_review(succeeded_job):
    job_id, c, h = succeeded_job
    data, page, table, cell = _first_cell(c, h, job_id)
    assert data["version"] == 1
    assert cell["review_status"] == "UNREVIEWED"

    r = c.patch(f"/api/v1/ocr/jobs/{job_id}/results", headers=h, json={
        "expected_version": 1,
        "edits": [{
            "page_number": page["page_number"],
            "table_id": table["table_id"],
            "row_index": cell["row_index"],
            "column_index": cell["column_index"],
            "current_text": "EDITED",
        }],
    })
    assert r.status_code == 200
    assert r.get_json()["data"]["version"] == 2
    assert r.get_json()["data"]["review_status"] == "EDITED"

    r = c.patch(f"/api/v1/ocr/jobs/{job_id}/results", headers=h,
                json={"expected_version": 1, "confirm": True})
    assert r.status_code == 409
    assert r.get_json()["code"] == "VERSION_CONFLICT"

    _data, _p, _t, edited = _first_cell(c, h, job_id)
    assert edited["current_text"] == "EDITED"
    assert edited["raw_text"] != "EDITED"
    assert edited["review_status"] == "EDITED"


def test_results_bad_cell_ref(succeeded_job):
    job_id, c, h = succeeded_job
    _data, page, table, _cell = _first_cell(c, h, job_id)
    r = c.patch(f"/api/v1/ocr/jobs/{job_id}/results", headers=h, json={
        "expected_version": 1,
        "edits": [{
            "page_number": page["page_number"],
            "table_id": table["table_id"],
            "row_index": 9,
            "column_index": 9,
            "current_text": "x",
        }],
    })
    assert r.status_code == 400
    assert r.get_json()["field_errors"][0]["reason"] == "cell_not_found"


def test_results_confirm_marks_cells(succeeded_job):
    job_id, c, h = succeeded_job
    r = c.patch(f"/api/v1/ocr/jobs/{job_id}/results", headers=h,
                json={"expected_version": 1, "confirm": True})
    assert r.status_code == 200
    assert r.get_json()["data"]["review_status"] == "CONFIRMED"
    _data, _p, table, _cell = _first_cell(c, h, job_id)
    assert table["cells"] and all(x["review_status"] == "CONFIRMED" for x in table["cells"])


def test_results_no_csrf_blocked(succeeded_job):
    job_id, c, _h = succeeded_job
    r = c.patch(f"/api/v1/ocr/jobs/{job_id}/results", headers={},
                json={"expected_version": 1, "confirm": True})
    assert r.status_code == 403
    assert r.get_json()["code"] == "CSRF_INVALID"


def test_results_stranger_404(succeeded_job, app):
    job_id, _c1, _h1 = succeeded_job
    c2, h2, _ = _login(app, "dev-carol")
    r = c2.get(f"/api/v1/ocr/jobs/{job_id}/results", headers=h2)
    assert r.status_code == 404


def test_job_list_excludes_other_users_personal_jobs(succeeded_job, app):
    job_id, _c1, _h1 = succeeded_job
    c2, h2, _ = _login(app, "dev-erin")
    for url in ("/api/v1/ocr/jobs", "/api/v1/ocr/jobs?workspace_type=PERSONAL"):
        r = c2.get(url, headers=h2)
        assert r.status_code == 200, r.get_json()
        ids = [j["id"] for j in r.get_json()["data"]["items"]]
        assert job_id not in ids


# ---------- 导出 ----------

def test_exports_roundtrip(succeeded_job):
    job_id, c, h = succeeded_job
    for fmt in ("xlsx", "csv", "txt"):
        r = c.post(f"/api/v1/ocr/jobs/{job_id}/exports", json={"format": fmt}, headers=h)
        assert r.status_code == 201, r.get_json()
        exp = r.get_json()["data"]
        assert exp["format"] == fmt.upper()
        r = c.get(f"/api/v1/exports/{exp['id']}", headers=h)
        assert r.status_code == 200
        dl = c.get(f"/api/v1/exports/{exp['id']}/download", headers=h)
        assert dl.status_code == 200
        assert len(dl.data) > 0


def test_export_expired(succeeded_job, app):
    job_id, c, h = succeeded_job
    r = c.post(f"/api/v1/ocr/jobs/{job_id}/exports", json={"format": "csv"}, headers=h)
    exp = r.get_json()["data"]
    from models import Export
    with app.app_context():
        row = db.session.get(Export, exp["id"])
        row.expires_at = row.created_at
        db.session.commit()
    r = c.get(f"/api/v1/exports/{exp['id']}/download", headers=h)
    assert r.status_code == 409
    assert r.get_json()["message_key"] == "export.expired"


def test_exports_stranger_404(succeeded_job, app):
    job_id, c1, h1 = succeeded_job
    c2, h2, _ = _login(app, "dev-dan")
    r = c1.post(f"/api/v1/ocr/jobs/{job_id}/exports", json={"format": "csv"}, headers=h1)
    exp = r.get_json()["data"]
    assert c2.get(f"/api/v1/exports/{exp['id']}", headers=h2).status_code == 404
    assert c2.get(f"/api/v1/exports/{exp['id']}/download", headers=h2).status_code == 404


# ---------- 公司（添加/修改/编号绑定/解绑 + 人工审核） ----------

def test_company_create_pending_then_manual_review(auth, app):
    """添加公司 → 待审核（不可绑定/不进工作空间）→ 人工 VERIFIED → 生效。"""
    c, h, _ = auth
    r = c.post("/api/v1/enterprises", json=COMPANY, headers=h)
    assert r.status_code == 201, r.get_json()
    ent = r.get_json()["data"]["enterprise"]
    assert ent["status"] == "PENDING_VERIFICATION"
    assert ent["contact_phone"] == COMPANY["contact_phone"]
    assert ent["id"] and ent["code"]  # 编号自增生成

    # 未过审：不出现在工作空间
    r = c.get("/api/v1/me/workspaces", headers=h)
    assert all(w["workspace_type"] == "PERSONAL"
               for w in r.get_json()["data"]["workspaces"])
    # 未过审：他人按代码绑定 404（不泄露存在性）
    c2, h2, _ = _login(app, "dev-binder")
    r = c2.post("/api/v1/enterprises/bind", json={"code": ent["code"]}, headers=h2)
    assert r.status_code == 404

    # 我的公司可见：OWNER / ACTIVE / 待审核
    r = c.get("/api/v1/enterprises/mine", headers=h)
    items = r.get_json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == ent["id"]
    assert items[0]["my_role"] == "OWNER"
    assert items[0]["membership_status"] == "ACTIVE"

    # 后台人工审核：UPDATE enterprises SET status='VERIFIED' WHERE id=…
    with app.app_context():
        db.session.get(Enterprise, ent["id"]).status = "VERIFIED"
        db.session.commit()

    r = c.get("/api/v1/me/workspaces", headers=h)
    ent_ws = [w for w in r.get_json()["data"]["workspaces"]
              if w["workspace_type"] == "ENTERPRISE"]
    assert len(ent_ws) == 1
    assert ent_ws[0]["enterprise_name"] == COMPANY["legal_name"]

    r = c2.post("/api/v1/enterprises/bind", json={"code": ent["code"]}, headers=h2)
    assert r.status_code == 200
    assert r.get_json()["data"]["role"] == "MEMBER"


def test_company_create_validation(auth):
    c, h, _ = auth
    r = c.post("/api/v1/enterprises",
               json={"legal_name": "", "registration_no": "R"}, headers=h)
    assert r.status_code == 400
    assert any(fe["field"] == "legal_name" for fe in r.get_json()["field_errors"])
    r = c.post("/api/v1/enterprises",
               json={"legal_name": "X", "registration_no": ""}, headers=h)
    assert r.status_code == 400
    r = c.post("/api/v1/enterprises",
               json={"legal_name": "X", "registration_no": "R",
                     "contact_phone": "x" * 33}, headers=h)
    assert r.status_code == 400
    assert any(fe["field"] == "contact_phone" for fe in r.get_json()["field_errors"])


def test_company_edit_rules(auth, app):
    c, h, _ = auth
    r = c.post("/api/v1/enterprises", json=COMPANY, headers=h)
    ent_id = r.get_json()["data"]["enterprise"]["id"]

    # 非 OWNER 不可改
    c2, h2, _ = _login(app, "dev-editor")
    r = c2.patch(f"/api/v1/enterprises/{ent_id}", json=COMPANY, headers=h2)
    assert r.status_code == 403

    # OWNER 修改：仍处待审核
    upd = {**COMPANY, "legal_name": "Acme Renamed", "contact_phone": "099"}
    r = c.patch(f"/api/v1/enterprises/{ent_id}", json=upd, headers=h)
    assert r.status_code == 200
    ent = r.get_json()["data"]["enterprise"]
    assert ent["legal_name"] == "Acme Renamed"
    assert ent["contact_phone"] == "099"
    assert ent["status"] == "PENDING_VERIFICATION"

    # 过审后再改 → 回到待审核（重新审核后才生效）
    with app.app_context():
        db.session.get(Enterprise, ent_id).status = "VERIFIED"
        db.session.commit()
    r = c.patch(f"/api/v1/enterprises/{ent_id}", json=upd, headers=h)
    assert r.get_json()["data"]["enterprise"]["status"] == "PENDING_VERIFICATION"


def test_company_bind_multi_and_unbind(auth, app):
    c1, h1, _ = auth
    e1 = _verified_enterprise(app, c1, h1)
    e2 = _verified_enterprise(app, c1, h1)
    c2, h2, _ = _login(app, "dev-multi")

    # 一人可绑定多个公司（仅按公司代码）
    for e in (e1, e2):
        r = c2.post("/api/v1/enterprises/bind", json={"code": e["code"]}, headers=h2)
        assert r.status_code == 200
    r = c2.get("/api/v1/me/workspaces", headers=h2)
    ent_ws = [w for w in r.get_json()["data"]["workspaces"]
              if w["workspace_type"] == "ENTERPRISE"]
    assert len(ent_ws) == 2

    # 重复绑定 409
    r = c2.post("/api/v1/enterprises/bind", json={"code": e1["code"]}, headers=h2)
    assert r.status_code == 409

    # 解绑一个 → 工作空间只剩另一个；mine 不再列出
    r = c2.post(f"/api/v1/enterprises/{e1['id']}/unbind", headers=h2)
    assert r.status_code == 200
    r = c2.get("/api/v1/me/workspaces", headers=h2)
    ent_ws = [w for w in r.get_json()["data"]["workspaces"]
              if w["workspace_type"] == "ENTERPRISE"]
    assert len(ent_ws) == 1 and ent_ws[0]["enterprise_id"] == e2["id"]
    r = c2.get("/api/v1/enterprises/mine", headers=h2)
    assert all(i["id"] != e1["id"] for i in r.get_json()["data"]["items"])

    # 解绑后可重新绑定
    r = c2.post("/api/v1/enterprises/bind", json={"code": e1["code"]}, headers=h2)
    assert r.status_code == 200


def test_company_bind_by_code(auth, app):
    """绑定只认公司代码（ent-…）；数字编号被拒；未知代码 404 不泄露存在性。"""
    c1, h1, _ = auth
    r = c1.post("/api/v1/enterprises", json=COMPANY, headers=h1)
    ent = r.get_json()["data"]["enterprise"]
    with app.app_context():
        db.session.get(Enterprise, ent["id"]).status = "VERIFIED"
        db.session.commit()

    c2, h2, _ = _login(app, "dev-code-binder")
    r = c2.post("/api/v1/enterprises/bind", json={"code": ent["code"]}, headers=h2)
    assert r.status_code == 200
    assert r.get_json()["data"]["enterprise"]["id"] == ent["id"]
    assert r.get_json()["data"]["role"] == "MEMBER"

    # 已绑定后按代码再绑 → 409；未知代码 → 404
    r = c2.post("/api/v1/enterprises/bind", json={"code": ent["code"]}, headers=h2)
    assert r.status_code == 409
    r = c2.post("/api/v1/enterprises/bind", json={"code": "ent-000000-deadbe"}, headers=h2)
    assert r.status_code == 404

    # 只按代码：数字编号与空 body → 400
    r = c2.post("/api/v1/enterprises/bind", json={"enterprise_id": ent["id"]}, headers=h2)
    assert r.status_code == 400
    r = c2.post("/api/v1/enterprises/bind", json={}, headers=h2)
    assert r.status_code == 400


def test_company_cert_photo(auth, app):
    c, h, _ = auth
    r = c.post("/api/v1/uploads",
               json={"file_name": "cert.png", "size_bytes": len(PNG_1PX),
                     "purpose": "APPLICATION_MATERIAL"}, headers=h)
    assert r.status_code == 201, r.get_json()
    f = r.get_json()["data"]["file"]
    r = c.put(f"/api/v1/uploads/{f['id']}/content", data=PNG_1PX,
              headers={"Content-Type": "image/png", **h})
    assert r.status_code == 200
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 200

    r = c.post("/api/v1/enterprises", json={**COMPANY, "cert_file_id": f["id"]}, headers=h)
    assert r.status_code == 201
    assert r.get_json()["data"]["enterprise"]["cert_file_id"] == f["id"]

    # 他人的文件不能作为证明材料
    c2, h2, _ = _login(app, "dev-cert")
    r = c2.post("/api/v1/enterprises", json={**COMPANY, "cert_file_id": f["id"]}, headers=h2)
    assert r.status_code == 400


def test_invitation_full_cycle(auth, app):
    c1, h1, u1 = auth
    ent_id = _verified_enterprise(app, c1, h1)["id"]

    c2, h2, _ = _login(app, "dev-erin")
    r = c2.post(f"/api/v1/enterprises/{ent_id}/invitations", headers=h2, json={})
    assert r.status_code == 403

    r = c1.post(f"/api/v1/enterprises/{ent_id}/invitations", headers=h1, json={})
    assert r.status_code == 201
    inv = r.get_json()["data"]
    token = inv["token"]

    anon = app.test_client()
    r = anon.post("/api/v1/invitations/redeem", json={"token": token})
    assert r.status_code == 401

    r = c2.post("/api/v1/invitations/redeem", headers=h2, json={"token": token})
    assert r.status_code == 200
    assert r.get_json()["data"]["role"] == "MEMBER"

    r = c2.get("/api/v1/me/workspaces", headers=h2)
    ent_ws = [w for w in r.get_json()["data"]["workspaces"] if w["workspace_type"] == "ENTERPRISE"]
    assert len(ent_ws) == 1
    assert ent_ws[0]["enterprise_id"] == ent_id

    r = c1.post("/api/v1/invitations/redeem", headers=h1, json={"token": token})
    assert r.status_code == 404  # 额度耗尽，不区分不存在/失效


def test_invitation_revoke(auth, app):
    c1, h1, u1 = auth
    ent_id = _verified_enterprise(app, c1, h1)["id"]
    r = c1.post(f"/api/v1/enterprises/{ent_id}/invitations", headers=h1, json={})
    inv = r.get_json()["data"]
    c2, h2, _ = _login(app, "dev-finn")
    r = c1.delete(f"/api/v1/invitations/{inv['id']}", headers=h1)
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "REVOKED"
    r = c2.post("/api/v1/invitations/redeem", headers=h2, json={"token": inv["token"]})
    assert r.status_code == 404


def test_enterprise_file_access(auth, app):
    c1, h1, u1 = auth
    ent_id = _verified_enterprise(app, c1, h1)["id"]
    r = c1.post("/api/v1/uploads", headers=h1,
                json={"file_name": "ent.png", "size_bytes": len(PNG_1PX),
                      "workspace_type": "ENTERPRISE", "enterprise_id": ent_id})
    assert r.status_code == 201
    f = r.get_json()["data"]["file"]

    c2, h2, _ = _login(app, "dev-gail")
    assert c2.get(f"/api/v1/files/{f['id']}", headers=h2).status_code == 404

    c3, h3, _ = _login(app, "dev-hank")
    r = c1.post(f"/api/v1/enterprises/{ent_id}/invitations", headers=h1, json={})
    token = r.get_json()["data"]["token"]
    r = c3.post("/api/v1/invitations/redeem", headers=h3, json={"token": token})
    assert r.status_code == 200
    assert c3.get(f"/api/v1/files/{f['id']}", headers=h3).status_code == 200


# ---------- 配额（PRD v1.4 §20） ----------


def _quota_of(c, h, ws_type="PERSONAL"):
    r = c.get("/api/v1/me/quota", headers=h)
    assert r.status_code == 200, r.get_json()
    return next(
        w for w in r.get_json()["data"]["workspaces"] if w["workspace_type"] == ws_type
    )


def test_first_login_bonus_no_double_grant(app, auth):
    c, h, _ = auth
    ws = _quota_of(c, h)
    assert ws["plan"]["code"] == "KINDER"
    assert ws["plan"]["free_unit"] == "OCR_TASK"
    assert ws["free_tasks"] == {"total": 1000, "remaining": 1000}

    # 再次登录（新会话）不重复发放
    r = c.post("/api/v1/auth/line", json={"dev_id": "tester"})
    assert r.status_code == 200, r.get_json()
    h2 = {"X-CSRF-Token": r.get_json()["data"]["csrf_token"]}
    assert _quota_of(c, h2)["free_tasks"]["total"] == 1000


def test_quota_endpoints_require_auth(client):
    assert client.get("/api/v1/me/quota").status_code == 401
    assert client.get("/api/v1/plans").status_code == 401


def test_plans_catalog(auth):
    c, h, _ = auth
    r = c.get("/api/v1/plans", headers=h)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["currency"] == "THB"
    assert data["payment"] == "OFFLINE"
    assert [p["code"] for p in data["items"]] == [
        "KINDER", "STARTER", "INTERMEDIATE", "ADVANCE"
    ]
    starter = data["items"][1]
    assert starter["price"]["monthly_thb"] == 599
    assert starter["price"]["yearly_thb"] == 5990
    assert starter["quota_pages_per_month"] == 5000
    assert starter["seats"] == 2
    assert data["items"][0]["is_default"] is True


def test_job_consumes_and_cancel_refunds(auth, ready_file):
    c, h, f = ready_file
    r = c.post("/api/v1/ocr/jobs", json={"file_id": f["id"], "params": {}}, headers=h)
    assert r.status_code == 201, r.get_json()
    assert _quota_of(c, h)["free_tasks"]["remaining"] == 999

    # QUEUED 直接取消：未产出结果，返还
    job_id = r.get_json()["data"]["id"]
    r = c.post(f"/api/v1/ocr/jobs/{job_id}/cancel", headers=h)
    assert r.status_code == 200
    assert _quota_of(c, h)["free_tasks"]["remaining"] == 1000


def test_failed_job_refunds(app, auth, ready_file):
    c, h, f = ready_file
    r = c.post("/api/v1/ocr/jobs", json={"file_id": f["id"], "params": {}}, headers=h)
    assert r.status_code == 201, r.get_json()
    job_id = r.get_json()["data"]["id"]
    assert _quota_of(c, h)["free_tasks"]["remaining"] == 999

    # 源文件失效 → worker FAILED → 返还
    from models import File
    from worker import OcrWorker

    with app.app_context():
        db.session.get(File, f["id"]).status = "VALIDATING"
        db.session.commit()
    OcrWorker(app)._process(job_id)
    r = c.get(f"/api/v1/ocr/jobs/{job_id}", headers=h)
    assert r.get_json()["data"]["status"] == "FAILED"
    assert _quota_of(c, h)["free_tasks"]["remaining"] == 1000


def test_quota_exceeded_rejects_job(app, auth, ready_file):
    c, h, f = ready_file
    from models import QuotaLedger

    with app.app_context():
        grant = QuotaLedger.query.filter_by(benefit_code="FIRST_LOGIN").first()
        assert grant is not None
        db.session.add(
            QuotaLedger(
                workspace_type="PERSONAL",
                user_id=grant.user_id,
                entry_type="CONSUME",
                benefit_code="OCR_TASK",
                quantity=-1000,
                job_id="synthetic-burn",
            )
        )
        db.session.commit()

    r = c.post("/api/v1/ocr/jobs", json={"file_id": f["id"], "params": {}}, headers=h)
    assert r.status_code == 402
    assert r.get_json()["code"] == "QUOTA_EXCEEDED"


def test_enterprise_bonus_separate_ledger(app, auth):
    c, h, _ = auth
    ent = _verified_enterprise(app, c, h)

    # 惰性发放：/me/quota 检测到 VERIFIED 即补发企业 1,000 次
    ws = _quota_of(c, h, "ENTERPRISE")
    assert ws["enterprise_id"] == ent["id"]
    assert ws["free_tasks"] == {"total": 1000, "remaining": 1000}
    # 个人空间与企业空间分开记账
    assert _quota_of(c, h)["free_tasks"]["remaining"] == 1000

    # 企业空间任务消耗企业额度，不影响个人额度
    r = c.post(
        "/api/v1/uploads",
        headers=h,
        json={
            "file_name": "ent.png",
            "size_bytes": len(PNG_1PX),
            "workspace_type": "ENTERPRISE",
            "enterprise_id": ent["id"],
        },
    )
    assert r.status_code == 201, r.get_json()
    f = r.get_json()["data"]["file"]
    r = c.put(
        f"/api/v1/uploads/{f['id']}/content",
        data=PNG_1PX,
        headers={"Content-Type": "image/png", **h},
    )
    assert r.status_code == 200, r.get_json()
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 200, r.get_json()

    r = c.post("/api/v1/ocr/jobs", json={"file_id": f["id"], "params": {}}, headers=h)
    assert r.status_code == 201, r.get_json()
    assert _quota_of(c, h, "ENTERPRISE")["free_tasks"]["remaining"] == 999
    assert _quota_of(c, h)["free_tasks"]["remaining"] == 1000
