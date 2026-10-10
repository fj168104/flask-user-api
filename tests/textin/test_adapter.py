"""adapter 归一化测试：元素文本按页聚合（PDF/图片模式的 NarrativeText 等类型）。"""

from __future__ import annotations

from textin.adapter import normalize_response


def test_text_elements_grouped_by_page() -> None:
    response = {
        "metadata": {"page_count": 2},
        "elements": [
            {"type": "TableCaption", "page_number": 1, "text": "**SAIFLOW TEST INVOICE**"},
            {"type": "Table", "page_number": 1, "text": "<table>...</table>"},
            {"type": "NarrativeText", "page_number": 2, "text": "Page two note"},
            {"type": "NarrativeText", "page_number": 2, "text": "Total due 536.50 THB"},
        ],
    }
    out = normalize_response(response, fields=[])
    texts = {p["page_number"]: p["text"] for p in out["pages"]}
    assert texts[1] == "SAIFLOW TEST INVOICE"  # ** 标记已去除，Table 的 HTML 不混入
    assert texts[2] == "Page two note\nTotal due 536.50 THB"
    assert out["page_count"] == 2


def test_text_elements_type_text_still_works() -> None:
    response = {
        "elements": [
            {"type": "text", "page_number": 1, "text": "legacy text element"},
        ],
    }
    out = normalize_response(response, fields=[])
    assert out["pages"][0]["text"] == "legacy text element"
