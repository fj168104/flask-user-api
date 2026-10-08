"""启发式字段名/值提取。TextIn parse/sync 不返回原生 KV 字段，
因此从两个来源拼接：(a) 每个 Table 元素按行 cells[0]=name、cells[1..]=value；
(b) result.markdown 中以 中/英文冒号 开头的行。

不去重：同名出现在不同来源（或同源不同位置）时全部保留。
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

# 中/英文冒号 + 可选空白
_KV_LINE = re.compile(r"^(?P<name>.+?)[\uFF1A:]\s*(?P<value>.+?)$")


class ExtractedField(TypedDict):
    page_number: int
    name: str
    value: str
    source: str  # "table" | "markdown"


def _extract_markdown_fields(markdown: str | None) -> list[ExtractedField]:
    if not markdown:
        return []
    out: list[ExtractedField] = []
    for line in markdown.splitlines():
        m = _KV_LINE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        value = m.group("value").strip()
        if name and value:
            out.append({"page_number": 0, "name": name, "value": value, "source": "markdown"})
    return out


def _row_value_cells(cells: list[dict[str, Any]]) -> str:
    parts = [c.get("text", "").strip() for c in cells[1:]]
    parts = [p for p in parts if p]
    return " / ".join(parts)


def _extract_table_fields(elements: list[dict[str, Any]]) -> list[ExtractedField]:
    out: list[ExtractedField] = []
    for elem in elements:
        if elem.get("type") != "Table":
            continue
        ts = elem.get("table_structure") or {}
        cells = ts.get("cells") or []
        if not cells:
            continue
        page_number = int(elem.get("page_number") or 0)
        by_row: dict[int, list[dict[str, Any]]] = {}
        for c in cells:
            row = c.get("row")
            if row is None:
                continue
            by_row.setdefault(int(row), []).append(c)
        for row_idx, row_cells in sorted(by_row.items()):
            if row_idx == 0:  # 第一行约定是表头
                continue
            row_cells.sort(key=lambda c: c.get("col", 0))
            if not row_cells:
                continue
            name = (row_cells[0].get("text") or "").strip()
            value = _row_value_cells(row_cells)
            if name and value:
                out.append(
                    {
                        "page_number": page_number,
                        "name": name,
                        "value": value,
                        "source": "table",
                    }
                )
    return out


def extract_fields(textin_response: dict[str, Any]) -> list[ExtractedField]:
    """从 parse/sync 响应提取字段列表，按 markdown → table 顺序返回，不去重。"""
    elements = textin_response.get("elements") or []
    markdown = textin_response.get("markdown")
    return [*_extract_markdown_fields(markdown), *_extract_table_fields(elements)]