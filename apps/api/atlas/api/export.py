"""CSV export of tabular analytics results."""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal
from typing import Any

from fastapi import Response

from atlas.api.batch import safe_cell


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return safe_cell(str(value))
    return safe_cell(value) if isinstance(value, str) else str(value)


def csv_response(rows: list[dict[str, Any]], filename: str) -> Response:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
