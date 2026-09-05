"""Every series carries a per-column summary computed over the FULL
row set; summary_only drops the rows and says so. Column 0
is the timestamp and is skipped. Non-numeric cells are skipped and are not
counted in `samples`."""
from __future__ import annotations

from conftest import FakeClient, default_routes, load
from tools import stats as S


def test_column_summary_math():
    cols, units = ["Recorded At", "Transmit", "Receive"], ["unix_mins", "percent", "percent"]
    rows = [[1, 10.0, 1.0], [2, 30.0, None], [3, 20.0, 3.0]]
    s = S.column_summary(cols, units, rows)
    assert s == [
        {"column": "Transmit", "unit": "percent", "samples": 3, "min": 10.0, "max": 30.0, "avg": 20.0, "last": 20.0},
        {"column": "Receive", "unit": "percent", "samples": 2, "min": 1.0, "max": 3.0, "avg": 2.0, "last": 3.0},
    ]


def test_column_summary_no_data_reports_zero_samples():
    s = S.column_summary(["Recorded At", "Percent"], ["unix_mins", "percent"], [])
    assert s == [{"column": "Percent", "unit": "percent", "samples": 0, "min": 0.0, "max": 0.0, "avg": 0.0, "last": 0.0}]
    assert S.column_summary(["Recorded At"], ["unix_mins"], [[1]]) == []


def test_summary_is_over_full_rows_even_when_rows_are_capped():
    item = load("stat_device.json")["data"][0]  # 24 rows, Percent = 10..33
    p = S.project_series(item, limit=5)
    ser = p["series"][0]
    assert ser["row_count"] == 24 and len(ser["rows"]) == 5 and ser["truncated"] is True
    assert ser["summary"][0] == {"column": "Percent", "unit": "percent", "samples": 24, "min": 10.0, "max": 33.0, "avg": 21.5, "last": 33.0}


def test_summary_only_drops_rows_keeps_counts_and_explains():
    fake = FakeClient(default_routes())
    out = S.device_stats(fake, ["T1"], "cpuUtilization", "DEV-1", summary_only=True)
    ser = out["series_for"][0]["series"][0]
    assert ser["rows"] == [] and ser["row_count"] == 24 and ser["truncated"] is False
    assert ser["summary"][0]["samples"] == 24
    assert "rows omitted" in out["detail"] and "summary_only" in out["detail"]
    assert out["series_with_data"] == 1  # an omitted-row series still counts as having data


def test_default_keeps_rows_and_detail_is_unchanged():
    fake = FakeClient(default_routes())
    out = S.device_stats(fake, ["T1"], "cpuUtilization", "DEV-1")
    assert len(out["series_for"][0]["series"][0]["rows"]) == 24 and out["detail"] == ""
