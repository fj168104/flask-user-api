"""启发式字段抽取单元测试。"""

from __future__ import annotations

from textin.field_extractor import extract_fields


def test_empty_response() -> None:
    assert extract_fields({}) == []


def test_markdown_chinese_colon() -> None:
    resp = {"markdown": "发票号: 12345\n金额: 100.00"}
    out = extract_fields(resp)
    assert out == [
        {"page_number": 0, "name": "发票号", "value": "12345", "source": "markdown"},
        {"page_number": 0, "name": "金额", "value": "100.00", "source": "markdown"},
    ]


def test_markdown_english_colon() -> None:
    resp = {"markdown": "Invoice: INV-001\nDate: 2026-01-01"}
    out = extract_fields(resp)
    assert out == [
        {"page_number": 0, "name": "Invoice", "value": "INV-001", "source": "markdown"},
        {"page_number": 0, "name": "Date", "value": "2026-01-01", "source": "markdown"},
    ]


def test_markdown_lines_without_colon_skipped() -> None:
    resp = {"markdown": "无冒号行\n发票号: 12345\n另一无冒号行"}
    out = extract_fields(resp)
    assert len(out) == 1
    assert out[0]["name"] == "发票号"


def test_markdown_empty_value_skipped() -> None:
    resp = {"markdown": "空字段:\n正常: 值"}
    out = extract_fields(resp)
    assert len(out) == 1
    assert out[0]["name"] == "正常"


def test_table_basic_kv() -> None:
    resp = {
        "elements": [
            {
                "type": "Table",
                "page_number": 1,
                "table_structure": {
                    "cells": [
                        {"row": 0, "col": 0, "text": "字段"},
                        {"row": 0, "col": 1, "text": "值"},
                        {"row": 1, "col": 0, "text": "发票号"},
                        {"row": 1, "col": 1, "text": "INV-001"},
                    ]
                },
            }
        ]
    }
    out = extract_fields(resp)
    assert out == [
        {"page_number": 1, "name": "发票号", "value": "INV-001", "source": "table"},
    ]


def test_table_multi_col_value_joined() -> None:
    resp = {
        "elements": [
            {
                "type": "Table",
                "page_number": 2,
                "table_structure": {
                    "cells": [
                        {"row": 0, "col": 0, "text": "K"},
                        {"row": 0, "col": 1, "text": "V1"},
                        {"row": 0, "col": 2, "text": "V2"},
                        {"row": 1, "col": 0, "text": "金额"},
                        {"row": 1, "col": 1, "text": "100"},
                        {"row": 1, "col": 2, "text": "元"},
                    ]
                },
            }
        ]
    }
    out = extract_fields(resp)
    assert out[0]["value"] == "100 / 元"


def test_table_skips_row_zero_as_header() -> None:
    """约定 row 0 是表头：即使 cells[0] 有 text 也跳过整行。"""
    resp = {
        "elements": [
            {
                "type": "Table",
                "page_number": 1,
                "table_structure": {
                    "cells": [
                        {"row": 0, "col": 0, "text": "字段"},
                        {"row": 0, "col": 1, "text": "值"},
                    ]
                },
            }
        ]
    }
    assert extract_fields(resp) == []


def test_non_table_elements_ignored() -> None:
    resp = {
        "elements": [
            {"type": "text", "page_number": 1, "text": "foo: bar"},
            {"type": "Table", "page_number": 1, "table_structure": {"cells": []}},
        ]
    }
    # text 元素不会进入 markdown 路径；空 table 跳过
    assert extract_fields(resp) == []


def test_mixed_markdown_and_table_no_dedup() -> None:
    resp = {
        "markdown": "发票号: MD-1",
        "elements": [
            {
                "type": "Table",
                "page_number": 1,
                "table_structure": {
                    "cells": [
                        {"row": 0, "col": 0, "text": "K"},
                        {"row": 0, "col": 1, "text": "V"},
                        {"row": 1, "col": 0, "text": "发票号"},
                        {"row": 1, "col": 1, "text": "TBL-1"},
                    ]
                },
            }
        ],
    }
    out = extract_fields(resp)
    assert len(out) == 2
    assert [f["source"] for f in out] == ["markdown", "table"]