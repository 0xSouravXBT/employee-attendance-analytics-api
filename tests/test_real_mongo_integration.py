from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest


EXPECTED_TEST_DB = "attendance_integration_test"


def _require_dedicated_test_database() -> str:
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        pytest.skip(
            "requires MONGO_URI to be set to a live MongoDB instance",
            allow_module_level=True,
        )

    db_name = os.getenv("MONGO_DB")
    if not db_name:
        pytest.skip(
            f"requires MONGO_DB={EXPECTED_TEST_DB} to point at a dedicated test database",
            allow_module_level=True,
        )

    normalized = db_name.strip()
    if normalized != EXPECTED_TEST_DB:
        pytest.skip(
            f"refusing to run against non-dedicated database {db_name!r}; "
            f"set MONGO_DB={EXPECTED_TEST_DB} before running the live integration test",
            allow_module_level=True,
        )

    return normalized


# Import the app only after the test database guard has validated the environment.
_require_dedicated_test_database()

from fastapi.testclient import TestClient

from app import main


def _collect_stage_nodes(plan, stage_name):
    matches = []
    stack = [plan]

    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("stage") == stage_name:
                matches.append(current)
            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    stack.append(value)

    return matches


def _collect_execution_stats(explain):
    stats = []
    stack = [explain]

    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "executionStats" and isinstance(value, dict):
                    stats.append(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(value for value in current if isinstance(value, (dict, list)))

    return stats


def _cleanup_test_records(db, emp_code: str, attendance_date: str) -> None:
    db.employees.delete_many({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code, "date": attendance_date})


pytestmark = pytest.mark.integration


def test_live_mongo_explain_and_analytics_requirements():
    db = main.db

    unique_emp_code = f"IT_EMP_{uuid.uuid4().hex[:12].upper()}"
    second_emp_code = f"IT_EMP_{uuid.uuid4().hex[:12].upper()}"
    no_log_emp_code = f"IT_EMP_{uuid.uuid4().hex[:12].upper()}"
    test_department = f"IT_DEPT_{uuid.uuid4().hex[:10].upper()}"
    leaderboard_emp_codes = [
        f"IT_EMP_{uuid.uuid4().hex[:12].upper()}",
        f"IT_EMP_{uuid.uuid4().hex[:12].upper()}",
    ]
    leaderboard_department = f"IT_DEPT_{uuid.uuid4().hex[:10].upper()}"
    attendance_date = "2026-07-06"
    second_attendance_date = "2026-07-07"
    leaderboard_dates = ("2026-07-08", "2026-07-09")

    try:
        with TestClient(main.app) as client:
            db.employees.insert_one(
                {
                    "emp_code": unique_emp_code,
                    "name": "A",
                    "email": f"{uuid.uuid4().hex[:8]}@example.com",
                    "department": "Engineering",
                    "shift_start": "09:30",
                    "shift_end": "18:30",
                    "joined_on": "2026-01-05",
                    "created_at": datetime.now(timezone.utc),
                }
            )
            db.employees.insert_many(
                [
                    {
                        "emp_code": second_emp_code,
                        "name": "B",
                        "email": f"{uuid.uuid4().hex[:8]}@example.com",
                        "department": test_department,
                        "shift_start": "09:30",
                        "shift_end": "18:30",
                        "joined_on": "2026-01-05",
                        "created_at": datetime.now(timezone.utc),
                    },
                    {
                        "emp_code": no_log_emp_code,
                        "name": "C",
                        "email": f"{uuid.uuid4().hex[:8]}@example.com",
                        "department": test_department,
                        "shift_start": "09:30",
                        "shift_end": "18:30",
                        "joined_on": "2026-01-05",
                        "created_at": datetime.now(timezone.utc),
                    },
                ]
            )
            db.employees.insert_many(
                [
                    {
                        "emp_code": emp_code,
                        "name": f"Tie {index}",
                        "email": f"{uuid.uuid4().hex[:8]}@example.com",
                        "department": leaderboard_department,
                        "shift_start": "09:30",
                        "shift_end": "18:30",
                        "joined_on": "2026-01-05",
                        "created_at": datetime.now(timezone.utc),
                    }
                    for index, emp_code in enumerate(leaderboard_emp_codes, start=1)
                ]
            )

            ts1 = datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc)
            ts2 = datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc)
            db.attendance_logs.insert_one(
                {
                    "emp_code": unique_emp_code,
                    "date": attendance_date,
                    "status": "PRESENT",
                    "punch_in": ts1,
                    "punch_out": ts2,
                    "work_hours": 9.12,
                    "late_minutes": 0,
                    "overtime_minutes": 0,
                    "half_day": False,
                    "history": [],
                }
            )
            db.attendance_logs.insert_one(
                {
                    "emp_code": second_emp_code,
                    "date": second_attendance_date,
                    "status": "WFH",
                    "punch_in": ts1,
                    "punch_out": ts2,
                    "work_hours": 7.25,
                    "late_minutes": 12,
                    "overtime_minutes": 0,
                    "half_day": False,
                    "history": [],
                }
            )
            db.attendance_logs.insert_many(
                [
                    {
                        "emp_code": emp_code,
                        "date": log_date,
                        "status": "PRESENT",
                        "late_minutes": 12,
                    }
                    for emp_code, log_date in zip(leaderboard_emp_codes, leaderboard_dates)
                ]
            )

            monthly = client.get(
                f"/analytics/employees/{unique_emp_code}/monthly",
                params={"month": "2026-07"},
            )
            assert monthly.status_code == 200

            department_summary = client.get(
                "/analytics/departments/summary",
                params={"month": "2026-07", "department": test_department},
            )
            assert department_summary.status_code == 200
            summary_item = department_summary.json()["items"][0]
            assert summary_item == {
                "department": test_department,
                "headcount": 2,
                "present_days": 1.0,
                "avg_work_hours": 7.25,
                "late_count": 1,
                "total_late_minutes": 12,
                "leave_count": 0,
                "on_duty_count": 0,
            }

            trend = client.get(
                f"/analytics/departments/{test_department}/trend",
                params={"from": "2026-07-06", "to": "2026-07-08"},
            )
            assert trend.status_code == 200
            assert trend.json() == {
                "department": test_department,
                "items": [
                    {
                        "date": "2026-07-06",
                        "is_working_day": True,
                        "headcount": 2,
                        "present_count": 0.0,
                        "late_count": 0,
                        "attendance_rate": 0.0,
                        "moving_avg_7d": 0.0,
                    },
                    {
                        "date": "2026-07-07",
                        "is_working_day": True,
                        "headcount": 2,
                        "present_count": 1.0,
                        "late_count": 1,
                        "attendance_rate": 0.5,
                        "moving_avg_7d": 0.25,
                    },
                    {
                        "date": "2026-07-08",
                        "is_working_day": True,
                        "headcount": 2,
                        "present_count": 0.0,
                        "late_count": 0,
                        "attendance_rate": 0.0,
                        "moving_avg_7d": 0.1667,
                    },
                ],
            }

            leaderboard = client.get(
                "/analytics/leaderboard/late",
                params={
                    "month": "2026-07",
                    "department": leaderboard_department,
                    "limit": 1,
                },
            )
            assert leaderboard.status_code == 200
            assert leaderboard.json()["items"] == [
                {
                    "rank": 1,
                    "emp_code": emp_code,
                    "name": f"Tie {index}",
                    "department": leaderboard_department,
                    "total_late_minutes": 12,
                    "late_count": 1,
                }
                for index, emp_code in sorted(
                    enumerate(leaderboard_emp_codes, start=1), key=lambda row: row[1]
                )
            ]

            explain = client.get(
                "/admin/explain/attendance_list",
                params={"emp_code": unique_emp_code, "page": 1, "page_size": 20},
            )
            assert explain.status_code == 200

            explain_payload = explain.json()["explain"]
            assert explain_payload["executionStats"]["executionSuccess"] is True
            plan = explain_payload.get("queryPlanner", {}).get("winningPlan")
            ixscan_nodes = _collect_stage_nodes(plan, "IXSCAN")

            assert ixscan_nodes, (
                "Expected the attendance_list explain plan to use an IXSCAN against one of "
                "the attendance indexes, but the winning plan was: "
                f"{explain_payload}"
            )

            ixscan_index_names = {
                node.get("indexName")
                for node in ixscan_nodes
                if isinstance(node, dict) and node.get("indexName")
            }
            assert ixscan_index_names, (
                "Expected the IXSCAN stage to include an index name; "
                f"got: {explain_payload}"
            )
            assert ixscan_index_names & {"uq_attendance_employee_date", "idx_attendance_date_employee"}, (
                "The actual explain plan did not use the attendance index expected by the API. "
                f"Plan: {explain_payload}"
            )

            assert not _collect_stage_nodes(plan, "COLLSCAN"), (
                "COLLSCAN was used for attendance_list; the query should be covered by an index. "
                f"Plan: {explain_payload}"
            )

            live_explain_requests = [
                (
                    "employee_monthly",
                    {"emp_code": unique_emp_code, "month": "2026-07"},
                ),
                (
                    "department_summary",
                    {"month": "2026-07", "department": test_department},
                ),
                (
                    "late_leaderboard",
                    {
                        "month": "2026-07",
                        "department": leaderboard_department,
                        "limit": 1,
                    },
                ),
                (
                    "department_trend",
                    {
                        "department": test_department,
                        "from": "2026-07-06",
                        "to": "2026-07-08",
                    },
                ),
            ]
            for endpoint, params in live_explain_requests:
                explain_response = client.get(
                    f"/admin/explain/{endpoint}", params=params
                )
                assert explain_response.status_code == 200, explain_response.text
                live_explain = explain_response.json()["explain"]
                execution_stats = _collect_execution_stats(live_explain)
                assert execution_stats, f"No executionStats found in explain: {live_explain}"
                assert any(
                    stats.get("executionSuccess") is True for stats in execution_stats
                ), f"No successful executionStats found in explain: {live_explain}"
    finally:
        _cleanup_test_records(db, unique_emp_code, attendance_date)
        _cleanup_test_records(db, second_emp_code, second_attendance_date)
        db.employees.delete_many({"emp_code": no_log_emp_code})
        for emp_code, log_date in zip(leaderboard_emp_codes, leaderboard_dates):
            _cleanup_test_records(db, emp_code, log_date)
