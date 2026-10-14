from __future__ import annotations

from datetime import date, datetime, timezone

from bson.decimal128 import Decimal128
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def employee_payload():
    return {
        "emp_code": "EMP0001",
        "name": "Asha Rao",
        "email": "asha@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05",
    }


def test_health_check(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_indexes_propagates_index_creation_failure(monkeypatch):
    class IndexFailureCollection:
        def create_index(self, *args, **kwargs):
            raise main.PyMongoError("index creation failed")

    class Admin:
        def command(self, command):
            assert command == "ping"

    class IndexFailureClient:
        admin = Admin()

    class IndexFailureDatabase:
        employees = IndexFailureCollection()

    monkeypatch.setattr(main, "client", IndexFailureClient())
    monkeypatch.setattr(main, "db", IndexFailureDatabase())

    with pytest.raises(main.PyMongoError, match="index creation failed"):
        main.create_indexes()


def test_create_employee_and_list_employees(api_client, employee_payload):
    create_response = api_client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["emp_code"] == "EMP0001"
    assert isinstance(created["created_at"], int)

    list_response = api_client.get("/employees")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["emp_code"] == "EMP0001"


@pytest.mark.parametrize(
    "field,value",
    [
        ("emp_code", "EMP123"),
        ("emp_code", "EMP1234567"),
        ("name", ""),
        ("name", "N" * 101),
        ("email", "not-an-email"),
        ("email", f"{'a' * 109}@example.com"),
        ("department", ""),
        ("department", "D" * 51),
        ("shift_start", "24:00"),
        ("shift_end", "9:30"),
        ("joined_on", "2026-02-30"),
        ("joined_on", "2026/02/28"),
        ("shift_end", "09:30"),
    ],
)
def test_employee_creation_rejects_contract_invalid_fields(api_client, employee_payload, field, value):
    response = api_client.post("/employees", json={**employee_payload, field: value})

    assert response.status_code == 422


def test_employee_creation_accepts_contract_maximum_lengths(api_client, employee_payload):
    email_at_limit = f"{'a' * 108}@example.com"
    payload = {
        **employee_payload,
        "emp_code": "EMP123456",
        "name": "N" * 100,
        "email": email_at_limit,
        "department": "D" * 50,
        "shift_start": "00:00",
        "shift_end": "23:59",
        "joined_on": "2026-02-28",
    }

    response = api_client.post("/employees", json=payload)

    assert response.status_code == 201
    assert response.json()["emp_code"] == "EMP123456"


def test_duplicate_employee_code_is_rejected(api_client, employee_payload):
    api_client.post("/employees", json=employee_payload)
    second = api_client.post("/employees", json=employee_payload)
    assert second.status_code == 409
    assert second.json()["detail"] == "emp_code already exists"


def test_punch_in_and_out_match_contract(api_client, employee_payload):
    api_client.post("/employees", json=employee_payload)

    punch_in_ms = int(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc).timestamp() * 1000)
    punch_in = api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": punch_in_ms},
    )
    assert punch_in.status_code == 201
    pin_data = punch_in.json()
    assert pin_data["date"] == "2026-07-06"
    assert pin_data["status"] == "PRESENT"
    assert pin_data["late_minutes"] == 0

    punch_out_ms = int(datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc).timestamp() * 1000)
    punch_out = api_client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0001", "punched_at": punch_out_ms},
    )
    assert punch_out.status_code == 200
    pout_data = punch_out.json()
    assert pout_data["work_hours"] == 9.12
    assert pout_data["overtime_minutes"] == 0
    assert pout_data["half_day"] is False


def test_exact_late_boundary_and_one_second_past(api_client, employee_payload):
    api_client.post("/employees", json=employee_payload)

    exactly_on_boundary = int(datetime(2026, 7, 6, 4, 10, tzinfo=timezone.utc).timestamp() * 1000)
    on_boundary = api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": exactly_on_boundary},
    )
    assert on_boundary.status_code == 201
    assert on_boundary.json()["late_minutes"] == 0

    second_later = int(datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    second_employee = {**employee_payload, "emp_code": "EMP0002"}
    api_client.post("/employees", json=second_employee)

    late = api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0002", "punched_at": second_later},
    )
    assert late.status_code == 201
    assert late.json()["late_minutes"] == 10


def test_exact_overtime_boundary_and_one_second_below(api_client, employee_payload):
    api_client.post("/employees", json=employee_payload)

    punch_in_ms = int(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc).timestamp() * 1000)
    punch_out_exact = int(datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    punch_out_one_second_below = int(datetime(2026, 7, 6, 13, 29, 59, tzinfo=timezone.utc).timestamp() * 1000)

    api_client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": punch_in_ms})
    exact = api_client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0001", "punched_at": punch_out_exact},
    )
    assert exact.status_code == 200
    assert exact.json()["overtime_minutes"] == 30

    second_employee = {**employee_payload, "emp_code": "EMP0002", "shift_start": "09:30", "shift_end": "18:30"}
    api_client.post("/employees", json=second_employee)
    api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0002", "punched_at": punch_in_ms},
    )
    below = api_client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0002", "punched_at": punch_out_one_second_below},
    )
    assert below.status_code == 200
    assert below.json()["overtime_minutes"] == 0


def test_attendance_list_filters_and_rejects_inverted_date_range(api_client, employee_payload):
    api_client.post("/employees", json=employee_payload)

    api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": int(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc).timestamp() * 1000)},
    )
    api_client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0001", "punched_at": int(datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc).timestamp() * 1000)},
    )

    filtered = api_client.get(
        "/attendance",
        params={"emp_code": "EMP0001", "date_from": "2026-07-05", "date_to": "2026-07-06", "status": "PRESENT"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1

    invalid = api_client.get(
        "/attendance",
        params={"date_from": "2026-07-07", "date_to": "2026-07-06"},
    )
    assert invalid.status_code == 422


def test_attendance_list_serializes_documented_defaults_for_legacy_documents(api_client):
    main.db.attendance_logs.insert_one(
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
        }
    )

    response = api_client.get("/attendance")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        }
    ]


def test_attendance_serializer_preserves_explicit_stored_values():
    history = [{"at": None, "by": "admin", "reason": "test", "changes": {}}]
    serialized = main.serialize_attendance(
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "WFH",
            "punch_in": None,
            "punch_out": None,
            "work_hours": 3.5,
            "late_minutes": 7,
            "overtime_minutes": 30,
            "half_day": True,
            "history": history,
        }
    )

    assert serialized["work_hours"] == 3.5
    assert serialized["late_minutes"] == 7
    assert serialized["overtime_minutes"] == 30
    assert serialized["half_day"] is True
    assert serialized["history"] == history


def test_regularized_attendance_writes_audit_history(api_client, employee_payload):
    api_client.post("/employees", json=employee_payload)
    punch_in_ms = int(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc).timestamp() * 1000)
    punch_out_ms = int(datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc).timestamp() * 1000)
    api_client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": punch_in_ms})
    api_client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": punch_out_ms})

    update = api_client.patch(
        "/attendance/EMP0001/2026-07-06",
        json={
            "status": "WFH",
            "reason": "Updated from office work",
            "regularized_by": "admin",
        },
    )
    assert update.status_code == 200
    payload = update.json()
    assert payload["status"] == "WFH"
    assert len(payload["history"]) == 1
    assert list(payload["history"][0]["changes"].keys()) == ["status"]


@pytest.mark.parametrize("invalid_date", ["not-a-date", "2026-02-30", "2026-7-06"])
def test_regularization_rejects_malformed_path_dates(api_client, invalid_date):
    response = api_client.patch(
        f"/attendance/EMP0001/{invalid_date}",
        json={"reason": "Corrected record", "regularized_by": "admin", "status": "LEAVE"},
    )

    assert response.status_code == 422


def capture_employee_monthly_aggregate(monkeypatch, result):
    captured_pipelines = []

    def fake_aggregate(pipeline):
        captured_pipelines.append(pipeline)
        return iter(result)

    monkeypatch.setattr(main.db.employees, "aggregate", fake_aggregate)
    return captured_pipelines


def test_monthly_employee_summary_matches_contract(api_client, monkeypatch):
    capture_employee_monthly_aggregate(
        monkeypatch,
        [
            {
                "emp_code": "EMP0001",
                "working_days": 23,
                "present_days": 1.0,
                "leave_days": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "total_overtime_minutes": 0,
                "attendance_pct": 4.3478,
            }
        ],
    )

    response = api_client.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-07"})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "emp_code",
        "month",
        "working_days",
        "present_days",
        "leave_days",
        "late_count",
        "total_late_minutes",
        "total_overtime_minutes",
        "attendance_pct",
    }
    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"
    assert data["working_days"] == 23
    assert data["present_days"] == 1.0
    assert data["attendance_pct"] == 4.3478


def test_monthly_pipeline_filters_month_and_aggregates_business_rules(api_client, monkeypatch):
    captured = capture_employee_monthly_aggregate(
        monkeypatch,
        [
            {
                "emp_code": "EMP0001",
                "working_days": 12,
                "present_days": 1.5,
                "leave_days": 2,
                "late_count": 1,
                "total_late_minutes": 10,
                "total_overtime_minutes": 30,
                "attendance_pct": 12.5,
            }
        ],
    )

    response = api_client.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-12"})

    assert response.status_code == 200
    assert response.json()["leave_days"] == 2
    pipeline = captured[0]
    assert pipeline[0] == {"$match": {"emp_code": "EMP0001"}}

    lookup = pipeline[1]["$lookup"]
    assert lookup["from"] == "attendance_logs"
    log_match = lookup["pipeline"][0]["$match"]["$expr"]["$and"]
    assert log_match == [
        {"$eq": ["$emp_code", "$$code"]},
        {"$gte": ["$date", "2026-12-01"]},
        {"$lt": ["$date", "2027-01-01"]},
    ]

    metrics = pipeline[2]["$set"]["metrics"]["$reduce"]
    assert metrics["input"] == "$logs"
    assert metrics["initialValue"] == {
        "present_half_units": 0,
        "leave_days": 0,
        "late_count": 0,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0,
    }
    leave_increment = metrics["in"]["leave_days"]["$add"][1]
    assert leave_increment == {"$cond": [{"$eq": ["$$this.status", "LEAVE"]}, 1, 0]}
    present_increment = metrics["in"]["present_half_units"]["$add"][1]["$cond"]
    assert present_increment[0]["$and"][0] == {
        "$in": ["$$this.status", ["PRESENT", "WFH", "ON_DUTY"]]
    }
    assert present_increment[0]["$and"][1]["$in"][1] == [2, 3, 4, 5, 6]
    assert present_increment[1] == {
        "$cond": [{"$ifNull": ["$$this.half_day", False]}, 1, 2]
    }
    working_days = pipeline[2]["$set"]["working_days"]
    assert all(operator in repr(working_days) for operator in ("$dateDiff", "$dateAdd", "$joined_date"))
    joined_date_condition = working_days["$let"]["in"]["$cond"]
    assert joined_date_condition[0] == {"$gt": ["$$joined_date", datetime(2026, 12, 31)]}
    period_start_condition = joined_date_condition[2]["$let"]["vars"]["period_start"]["$cond"]
    assert period_start_condition[0] == {"$gt": ["$$joined_date", datetime(2026, 12, 1)]}
    assert period_start_condition[2] == datetime(2026, 12, 1)
    assert pipeline[-1]["$project"]["attendance_pct"] == 1


def test_monthly_missing_employee_logs_produce_zero_metrics(api_client, monkeypatch):
    capture_employee_monthly_aggregate(
        monkeypatch,
        [
            {
                "emp_code": "EMP0001",
                "working_days": 23,
                "present_days": 0.0,
                "leave_days": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "total_overtime_minutes": 0,
                "attendance_pct": 0.0,
            }
        ],
    )
    monkeypatch.setattr(
        main.db.attendance_logs,
        "find",
        lambda *args, **kwargs: pytest.fail("monthly analytics must aggregate logs in MongoDB"),
    )

    response = api_client.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-07"})

    assert response.status_code == 200
    assert response.json()["present_days"] == 0.0
    assert response.json()["leave_days"] == 0
    assert response.json()["attendance_pct"] == 0.0


def test_monthly_unknown_employee_returns_404_from_empty_aggregation(api_client, monkeypatch):
    captured = capture_employee_monthly_aggregate(monkeypatch, [])
    monkeypatch.setattr(
        main.db.employees,
        "find_one",
        lambda *args, **kwargs: pytest.fail("monthly employee existence must come from aggregation"),
    )

    response = api_client.get("/analytics/employees/EMP9999/monthly", params={"month": "2026-07"})

    assert response.status_code == 404
    assert len(captured) == 1
    assert captured[0][0] == {"$match": {"emp_code": "EMP9999"}}


def test_created_at_and_regularization_history_time_are_truncated(api_client, employee_payload, monkeypatch):
    fixed_instant = datetime(2026, 7, 6, 12, 34, 56, 987654, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_instant.astimezone(tz) if tz else fixed_instant.replace(tzinfo=None)

    with monkeypatch.context() as patch:
        patch.setattr(main, "datetime", FixedDateTime)
        created = api_client.post("/employees", json=employee_payload)

    assert created.status_code == 201
    stored_employee = main.db.employees.find_one({"emp_code": "EMP0001"})
    assert stored_employee["created_at"].microsecond == 0
    expected_created_ms = int(fixed_instant.replace(microsecond=0).timestamp() * 1000)
    assert created.json()["created_at"] == expected_created_ms

    punch_in_ms = int(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc).timestamp() * 1000)
    punched_in = api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": punch_in_ms},
    )
    assert punched_in.status_code == 201

    with monkeypatch.context() as patch:
        patch.setattr(main, "datetime", FixedDateTime)
        regularized = api_client.patch(
            "/attendance/EMP0001/2026-07-06",
            json={"status": "WFH", "reason": "Corrected status", "regularized_by": "admin"},
        )

    assert regularized.status_code == 200
    stored_attendance = main.db.attendance_logs.find_one(
        {"emp_code": "EMP0001", "date": "2026-07-06"}
    )
    assert stored_attendance["history"][0]["at"].microsecond == 0


def test_department_summary_and_late_leaderboard(api_client, monkeypatch):
    employees = [
        {
            "emp_code": "EMP0001",
            "name": "A",
            "email": "a@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
        {
            "emp_code": "EMP0002",
            "name": "B",
            "email": "b@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
        {
            "emp_code": "EMP0003",
            "name": "C",
            "email": "c@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    ]
    for employee in employees:
        create = api_client.post("/employees", json=employee)
        assert create.status_code == 201

    # Employee 1 late by 35 minutes on a workday
    punch_in_1 = int(datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc).timestamp() * 1000)
    punch_out_1 = int(datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    api_client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": punch_in_1})
    api_client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": punch_out_1})

    # Employee 2 late by 35 minutes, same total as employee 1
    punch_in_2 = int(datetime(2026, 7, 7, 4, 35, tzinfo=timezone.utc).timestamp() * 1000)
    punch_out_2 = int(datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    api_client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": punch_in_2})
    api_client.post("/attendance/punch-out", json={"emp_code": "EMP0002", "punched_at": punch_out_2})

    # Employee 3 has no late minutes
    monkeypatch.setattr(
        main.db.employees,
        "aggregate",
        lambda pipeline: iter(
            [
                {
                    "department": "Engineering",
                    "headcount": 3,
                    "present_days": 2.0,
                    "avg_work_hours": 9.08,
                    "late_count": 2,
                    "total_late_minutes": 70,
                    "leave_count": 0,
                    "on_duty_count": 0,
                }
            ]
        ),
    )
    summary = api_client.get("/analytics/departments/summary", params={"month": "2026-07"})
    assert summary.status_code == 200
    summary_data = summary.json()
    assert summary_data["month"] == "2026-07"
    assert any(item["department"] == "Engineering" and item["headcount"] == 3 for item in summary_data["items"])

    monkeypatch.setattr(
        main.db.attendance_logs,
        "aggregate",
        lambda pipeline: iter(
            [
                {
                    "rank": 1,
                    "emp_code": "EMP0001",
                    "name": "A",
                    "department": "Engineering",
                    "total_late_minutes": 35,
                    "late_count": 1,
                },
                {
                    "rank": 1,
                    "emp_code": "EMP0002",
                    "name": "B",
                    "department": "Engineering",
                    "total_late_minutes": 35,
                    "late_count": 1,
                },
            ]
        ),
    )
    leaderboard = api_client.get("/analytics/leaderboard/late", params={"month": "2026-07", "limit": 2})
    assert leaderboard.status_code == 200
    leaderboard_data = leaderboard.json()
    assert leaderboard_data["items"][0]["emp_code"] in {"EMP0001", "EMP0002"}
    assert leaderboard_data["items"][1]["emp_code"] in {"EMP0001", "EMP0002"}
    assert leaderboard_data["items"][0]["rank"] == 1
    assert leaderboard_data["items"][1]["rank"] == 1


def capture_late_leaderboard_aggregate(monkeypatch, result):
    captured_pipelines = []

    def fake_aggregate(pipeline):
        captured_pipelines.append(pipeline)
        return iter(result)

    monkeypatch.setattr(main.db.attendance_logs, "aggregate", fake_aggregate)
    return captured_pipelines


def test_late_leaderboard_pipeline_applies_rank_cutoff_after_grouping(api_client, monkeypatch):
    captured = capture_late_leaderboard_aggregate(
        monkeypatch,
        [
            {
                "rank": 1,
                "emp_code": "EMP0001",
                "name": "A",
                "department": "Engineering",
                "total_late_minutes": 70,
                "late_count": 2,
            },
            {
                "rank": 1,
                "emp_code": "EMP0002",
                "name": "B",
                "department": "Engineering",
                "total_late_minutes": 70,
                "late_count": 2,
            },
            {
                "rank": 3,
                "emp_code": "EMP0003",
                "name": "C",
                "department": "Engineering",
                "total_late_minutes": 35,
                "late_count": 1,
            },
        ],
    )

    response = api_client.get(
        "/analytics/leaderboard/late",
        params={"month": "2026-12", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json() == {
        "month": "2026-12",
        "items": [
            {
                "rank": 1,
                "emp_code": "EMP0001",
                "name": "A",
                "department": "Engineering",
                "total_late_minutes": 70,
                "late_count": 2,
            },
            {
                "rank": 1,
                "emp_code": "EMP0002",
                "name": "B",
                "department": "Engineering",
                "total_late_minutes": 70,
                "late_count": 2,
            },
            {
                "rank": 3,
                "emp_code": "EMP0003",
                "name": "C",
                "department": "Engineering",
                "total_late_minutes": 35,
                "late_count": 1,
            },
        ],
    }

    pipeline = captured[0]
    assert pipeline[0] == {
        "$match": {
            "date": {"$gte": "2026-12-01", "$lt": "2027-01-01"},
            "late_minutes": {"$gt": 0},
        }
    }
    assert pipeline[1]["$group"] == {
        "_id": "$emp_code",
        "total_late_minutes": {"$sum": "$late_minutes"},
        "late_count": {"$sum": 1},
    }
    lookup = pipeline[2]["$lookup"]
    assert lookup == {
        "from": "employees",
        "localField": "_id",
        "foreignField": "emp_code",
        "as": "employee",
    }
    assert pipeline[3] == {"$unwind": "$employee"}
    assert pipeline[4]["$set"]["emp_code"] == "$_id"
    window = pipeline[5]["$setWindowFields"]
    assert window["sortBy"] == {"total_late_minutes": -1}
    assert window["output"] == {"rank": {"$rank": {}}}
    assert pipeline[6] == {"$match": {"rank": {"$lte": 1}}}
    assert pipeline[7] == {"$sort": {"total_late_minutes": -1, "emp_code": 1}}


def test_late_leaderboard_department_filter_precedes_ranking(api_client, monkeypatch):
    captured = capture_late_leaderboard_aggregate(monkeypatch, [])

    response = api_client.get(
        "/analytics/leaderboard/late",
        params={"month": "2026-07", "department": "Engineering", "limit": 4},
    )

    assert response.status_code == 200
    assert response.json() == {"month": "2026-07", "items": []}
    pipeline = captured[0]
    assert pipeline[4]["$set"]["department"] == "$employee.department"
    assert pipeline[5] == {"$match": {"department": "Engineering"}}
    assert "$setWindowFields" in pipeline[6]
    assert pipeline[7] == {"$match": {"rank": {"$lte": 4}}}


def capture_department_summary_aggregate(monkeypatch, result):
    captured_pipelines = []

    def fake_aggregate(pipeline):
        captured_pipelines.append(pipeline)
        return iter(result)

    def fail_find(*args, **kwargs):
        pytest.fail("department summary must not fetch employees or attendance in Python")

    monkeypatch.setattr(main.db.employees, "aggregate", fake_aggregate)
    monkeypatch.setattr(main.db.employees, "find", fail_find)
    monkeypatch.setattr(main.db.attendance_logs, "find", fail_find)
    return captured_pipelines


def test_department_summary_aggregates_metrics_and_maps_response(api_client, monkeypatch):
    captured = capture_department_summary_aggregate(
        monkeypatch,
        [
            {
                "department": "Engineering",
                "headcount": 3,
                "present_days": 1.5,
                "avg_work_hours": 8.75,
                "late_count": 2,
                "total_late_minutes": 42,
                "leave_count": 1,
                "on_duty_count": 2,
            }
        ],
    )

    response = api_client.get(
        "/analytics/departments/summary",
        params={"month": "2026-07", "department": "Engineering"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "month": "2026-07",
        "items": [
            {
                "department": "Engineering",
                "headcount": 3,
                "present_days": 1.5,
                "avg_work_hours": 8.75,
                "late_count": 2,
                "total_late_minutes": 42,
                "leave_count": 1,
                "on_duty_count": 2,
            }
        ],
    }

    pipeline = captured[0]
    assert pipeline[0] == {
        "$match": {
            "joined_on": {"$lte": "2026-07-31"},
            "department": "Engineering",
        }
    }
    lookup = pipeline[1]["$lookup"]
    assert lookup["from"] == "attendance_logs"
    assert lookup["pipeline"][0]["$match"]["$expr"]["$and"] == [
        {"$eq": ["$emp_code", "$$code"]},
        {"$gte": ["$date", "2026-07-01"]},
        {"$lt": ["$date", "2026-08-01"]},
    ]
    assert pipeline[2] == {
        "$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}
    }

    employee_group = pipeline[3]["$group"]
    assert employee_group["_id"] == {
        "department": "$department",
        "emp_code": "$emp_code",
    }
    assert employee_group["headcount"] == {"$first": 1}
    present_days_expression = employee_group["present_days"]["$sum"]
    assert "PRESENT" in repr(present_days_expression)
    assert "WFH" in repr(present_days_expression)
    assert "ON_DUTY" in repr(present_days_expression)
    assert "$dayOfWeek" in repr(present_days_expression)
    assert "half_day" in repr(present_days_expression)
    assert "LEAVE" in repr(employee_group["leave_count"])
    assert "ON_DUTY" in repr(employee_group["on_duty_count"])
    assert "$ifNull" in repr(employee_group["total_late_minutes"])

    department_group = pipeline[4]["$group"]
    assert department_group["_id"] == "$_id.department"
    assert department_group["headcount"] == {"$sum": "$headcount"}
    assert department_group["work_hours_sum"] == {"$sum": "$work_hours_sum"}
    assert department_group["work_hours_count"] == {"$sum": "$work_hours_count"}
    assert pipeline[5]["$set"]["avg_work_hours"]["$cond"][0] == {
        "$eq": ["$work_hours_count", 0]
    }
    assert pipeline[6] == {"$sort": {"_id": 1}}
    assert pipeline[7]["$project"]["department"] == "$_id"


def test_department_summary_includes_employee_without_logs(api_client, monkeypatch):
    capture_department_summary_aggregate(
        monkeypatch,
        [
            {
                "department": "Operations",
                "headcount": 1,
                "present_days": 0.0,
                "avg_work_hours": None,
                "late_count": 0,
                "total_late_minutes": 0,
                "leave_count": 0,
                "on_duty_count": 0,
            }
        ],
    )

    response = api_client.get(
        "/analytics/departments/summary",
        params={"month": "2026-07", "department": "Operations"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["headcount"] == 1
    assert item["present_days"] == 0.0
    assert item["avg_work_hours"] is None


def test_department_summary_returns_empty_items_for_empty_department(api_client, monkeypatch):
    captured = capture_department_summary_aggregate(monkeypatch, [])

    response = api_client.get(
        "/analytics/departments/summary",
        params={"month": "2026-07", "department": "NoSuchDepartment"},
    )

    assert response.status_code == 200
    assert response.json() == {"month": "2026-07", "items": []}
    assert captured[0][0]["$match"]["department"] == "NoSuchDepartment"


def capture_department_trend_aggregate(monkeypatch, result):
    captured_pipelines = []

    def fake_aggregate(pipeline):
        captured_pipelines.append(pipeline)
        return iter(result)

    def fail_find(*args, **kwargs):
        pytest.fail("department trend must not fetch employees or attendance in Python")

    monkeypatch.setattr(main.db.employees, "aggregate", fake_aggregate)
    monkeypatch.setattr(main.db.employees, "find", fail_find)
    monkeypatch.setattr(main.db.attendance_logs, "find", fail_find)
    return captured_pipelines


def test_department_trend_pipeline_maps_weekends_gaps_and_moving_average(api_client, monkeypatch):
    rows = [
        {
            "date": "2026-07-03",
            "is_working_day": True,
            "headcount": 1,
            "present_count": 1.0,
            "late_count": 0,
            "attendance_rate": 1.0,
            "moving_avg_7d": 1.0,
        },
        {
            "date": "2026-07-04",
            "is_working_day": False,
            "headcount": 1,
            "present_count": 0.0,
            "late_count": 0,
            "attendance_rate": None,
            "moving_avg_7d": 1.0,
        },
        {
            "date": "2026-07-05",
            "is_working_day": False,
            "headcount": 1,
            "present_count": 0.0,
            "late_count": 0,
            "attendance_rate": None,
            "moving_avg_7d": 1.0,
        },
        {
            "date": "2026-07-06",
            "is_working_day": True,
            "headcount": 1,
            "present_count": 0.0,
            "late_count": 0,
            "attendance_rate": 0.0,
            "moving_avg_7d": 0.5,
        },
    ]
    captured = capture_department_trend_aggregate(monkeypatch, rows)

    response = api_client.get(
        "/analytics/departments/Engineering/trend",
        params={"from": "2026-07-03", "to": "2026-07-06"},
    )

    assert response.status_code == 200
    assert response.json() == {"department": "Engineering", "items": rows}

    pipeline = captured[0]
    assert pipeline[0] == {"$match": {"department": "Engineering"}}
    # The group carries a harmless accumulator so the plan is an IXSCAN (not a
    # DISTINCT_SCAN); it is dropped by the final $project, so the response is unchanged.
    assert pipeline[1]["$group"]["_id"] == "$department"
    assert pipeline[1]["$group"]["row_count"] == {"$sum": 1}
    calendar = pipeline[2]["$set"]["calendar"]["$map"]["input"]["$range"]
    assert calendar[0] == 0
    assert calendar[1] == {
        "$add": [
            {
                "$dateDiff": {
                    "startDate": datetime(2026, 7, 3, tzinfo=timezone.utc),
                    "endDate": datetime(2026, 7, 6, tzinfo=timezone.utc),
                    "unit": "day",
                }
            },
            1,
        ]
    }
    assert pipeline[3] == {"$unwind": "$calendar"}

    employee_lookup = pipeline[5]["$lookup"]
    assert employee_lookup["from"] == "employees"
    assert employee_lookup["let"] == {
        "day_string": "$date_string",
        "department": "$_id",
    }
    assert employee_lookup["pipeline"][0]["$match"]["$expr"]["$and"] == [
        {"$eq": ["$department", "$$department"]},
        {"$lte": ["$joined_on", "$$day_string"]},
    ]
    assert employee_lookup["pipeline"][1] == {"$project": {"_id": 0, "emp_code": 1}}

    attendance_lookup = pipeline[7]["$lookup"]
    assert attendance_lookup["from"] == "attendance_logs"
    assert attendance_lookup["pipeline"][0]["$match"]["$expr"]["$and"] == [
        {"$eq": ["$date", "$$day_string"]},
        {"$in": ["$emp_code", "$$employee_codes"]},
    ]
    assert attendance_lookup["pipeline"][1] == {
        "$project": {
            "_id": 0,
            "status": 1,
            "half_day": 1,
            "late_minutes": 1,
        }
    }

    metrics = pipeline[8]["$set"]
    assert "$reduce" in repr(metrics["present_count"])
    assert "PRESENT" in repr(metrics["present_count"])
    assert "WFH" in repr(metrics["present_count"])
    assert "ON_DUTY" in repr(metrics["present_count"])
    assert "$ifNull" in repr(metrics["present_count"])
    assert "$ifNull" in repr(metrics["late_count"])
    window = pipeline[10]["$setWindowFields"]
    assert window["sortBy"] == {"calendar": 1}
    assert window["output"]["moving_avg_raw"]["window"] == {"documents": [-6, 0]}
    assert pipeline[-2] == {"$sort": {"calendar": 1}}


def test_department_trend_empty_aggregation_returns_unknown_department(api_client, monkeypatch):
    captured = capture_department_trend_aggregate(monkeypatch, [])

    response = api_client.get(
        "/analytics/departments/NoSuchDepartment/trend",
        params={"from": "2026-07-01", "to": "2026-07-02"},
    )

    assert response.status_code == 404
    assert captured[0][0] == {"$match": {"department": "NoSuchDepartment"}}


@pytest.mark.parametrize(
    "to_date,expected_status",
    [("2026-04-01", 404), ("2026-04-02", 404), ("2026-04-03", 422)],
)
def test_department_trend_enforces_inclusive_92_day_limit(
    api_client, monkeypatch, to_date, expected_status
):
    captured = capture_department_trend_aggregate(monkeypatch, [])

    response = api_client.get(
        "/analytics/departments/Engineering/trend",
        params={"from": "2026-01-01", "to": to_date},
    )

    assert response.status_code == expected_status
    assert len(captured) == (0 if expected_status == 422 else 1)


def test_department_trend_rejects_reversed_dates_before_aggregation(api_client, monkeypatch):
    captured = capture_department_trend_aggregate(monkeypatch, [])

    response = api_client.get(
        "/analytics/departments/Engineering/trend",
        params={"from": "2026-07-02", "to": "2026-07-01"},
    )

    assert response.status_code == 422
    assert captured == []


def test_admin_explain_returns_explain_payload(api_client, monkeypatch):
    explain_payload = {
        "queryPlanner": {"winningPlan": {"stage": "IXSCAN"}},
        "executionStats": {"executionSuccess": True, "nReturned": 1},
    }
    command = {}

    def fake_command(document):
        command.update(document)
        return explain_payload

    monkeypatch.setattr(main.db, "command", fake_command)

    response = api_client.get(
        "/admin/explain/attendance_list",
        params={
            "emp_code": "EMP-EXPLAIN-001",
            "status": "PRESENT",
            "date_from": "2026-07-01",
            "date_to": "2026-07-31",
            "page": 3,
            "page_size": 10,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoint"] == "attendance_list"
    assert payload["collection"] == "attendance_logs"
    assert payload["explain"]["queryPlanner"]["winningPlan"]["stage"] == "IXSCAN"
    assert payload["explain"]["executionStats"]["executionSuccess"] is True
    assert command["verbosity"] == "executionStats"
    assert command["explain"] == {
        "find": "attendance_logs",
        "filter": {
            "emp_code": "EMP-EXPLAIN-001",
            "status": "PRESENT",
            "date": {"$gte": "2026-07-01", "$lte": "2026-07-31"},
        },
        "sort": {"date": -1, "emp_code": 1},
        "skip": 20,
        "limit": 10,
    }


def test_admin_explain_serializes_decimal128_from_mongodb(api_client, monkeypatch):
    monkeypatch.setattr(
        main.db,
        "command",
        lambda document: {
            "executionStats": {
                "executionSuccess": True,
                "sampleDecimal": Decimal128("0.1250"),
            }
        },
    )

    response = api_client.get("/admin/explain/attendance_list")

    assert response.status_code == 200
    assert response.json()["explain"]["executionStats"]["sampleDecimal"] == 0.125


def mock_explain_command(monkeypatch):
    explain_payload = {
        "queryPlanner": {"winningPlan": {"stage": "IXSCAN"}},
        "executionStats": {"executionSuccess": True, "nReturned": 2},
    }
    commands = []

    def fake_command(document):
        commands.append(document)
        return explain_payload

    monkeypatch.setattr(main.db, "command", fake_command)
    return commands, explain_payload


@pytest.mark.parametrize(
    "production_path,params,explain_params,endpoint,collection,mocked_result,production_status",
    [
        (
            "/analytics/employees/EMP0001/monthly",
            {"month": "2026-07"},
            {"emp_code": "EMP0001", "month": "2026-07"},
            "employee_monthly",
            "employees",
            [{"emp_code": "EMP0001"}],
            200,
        ),
        (
            "/analytics/departments/summary",
            {"month": "2026-07", "department": "Engineering"},
            {"month": "2026-07", "department": "Engineering"},
            "department_summary",
            "employees",
            [],
            200,
        ),
        (
            "/analytics/leaderboard/late",
            {"month": "2026-07", "department": "Engineering", "limit": 3},
            {"month": "2026-07", "department": "Engineering", "limit": 3},
            "late_leaderboard",
            "attendance_logs",
            [],
            200,
        ),
        (
            "/analytics/departments/Engineering/trend",
            {"from": "2026-07-01", "to": "2026-07-03"},
            {
                "department": "Engineering",
                "from": "2026-07-01",
                "to": "2026-07-03",
            },
            "department_trend",
            "employees",
            [],
            404,
        ),
    ],
)
def test_admin_explain_pipeline_equals_pipeline_run_by_production_route(
    api_client,
    monkeypatch,
    production_path,
    params,
    explain_params,
    endpoint,
    collection,
    mocked_result,
    production_status,
):
    production_pipelines = []

    def fake_aggregate(pipeline):
        production_pipelines.append(pipeline)
        return iter(mocked_result)

    aggregate_collection = getattr(main.db, collection)
    monkeypatch.setattr(aggregate_collection, "aggregate", fake_aggregate)
    production_response = api_client.get(production_path, params=params)
    assert production_response.status_code == production_status
    assert len(production_pipelines) == 1

    commands, _ = mock_explain_command(monkeypatch)
    explain_response = api_client.get(
        f"/admin/explain/{endpoint}", params=explain_params
    )

    assert explain_response.status_code == 200
    assert commands[0]["verbosity"] == "executionStats"
    assert commands[0]["explain"] == {
        "aggregate": collection,
        "pipeline": production_pipelines[0],
        "cursor": {},
    }


def test_admin_explain_attendance_list_matches_production_find(api_client, monkeypatch):
    captured_find = {}

    class CursorCapture:
        def sort(self, sort_spec):
            captured_find["sort"] = sort_spec
            return self

        def skip(self, skip_count):
            captured_find["skip"] = skip_count
            return self

        def limit(self, limit_count):
            captured_find["limit"] = limit_count
            return self

        def __iter__(self):
            return iter(())

    def fake_find(query, projection):
        captured_find["filter"] = query
        captured_find["projection"] = projection
        return CursorCapture()

    monkeypatch.setattr(main.db.attendance_logs, "count_documents", lambda query: 0)
    monkeypatch.setattr(main.db.attendance_logs, "find", fake_find)
    params = {
        "emp_code": "EMP0001",
        "status": "PRESENT",
        "date_from": "2026-07-01",
        "date_to": "2026-07-31",
        "page": 2,
        "page_size": 7,
    }
    production_response = api_client.get("/attendance", params=params)
    assert production_response.status_code == 200

    commands, _ = mock_explain_command(monkeypatch)
    explain_response = api_client.get(
        "/admin/explain/attendance_list", params=params
    )

    assert explain_response.status_code == 200
    assert commands[0]["verbosity"] == "executionStats"
    assert commands[0]["explain"] == {
        "find": "attendance_logs",
        "filter": captured_find["filter"],
        "sort": dict(captured_find["sort"]),
        "skip": captured_find["skip"],
        "limit": captured_find["limit"],
    }


def test_admin_explain_employee_monthly_uses_aggregate_command(api_client, monkeypatch):
    commands, explain_payload = mock_explain_command(monkeypatch)

    response = api_client.get(
        "/admin/explain/employee_monthly",
        params={"emp_code": "EMP0001", "month": "2026-07"},
    )

    assert response.status_code == 200
    assert response.json()["collection"] == "employees"
    assert response.json()["explain"] == explain_payload
    command = commands[0]
    assert command["verbosity"] == "executionStats"
    operation = command["explain"]
    assert operation["aggregate"] == "employees"
    assert operation["cursor"] == {}
    assert operation["pipeline"] == main._build_employee_monthly_pipeline(
        "EMP0001", "2026-07"
    )


def test_admin_explain_department_summary_uses_filtered_aggregate(api_client, monkeypatch):
    commands, explain_payload = mock_explain_command(monkeypatch)

    response = api_client.get(
        "/admin/explain/department_summary",
        params={"month": "2026-12", "department": "Engineering"},
    )

    assert response.status_code == 200
    assert response.json()["collection"] == "employees"
    assert response.json()["explain"] == explain_payload
    command = commands[0]
    assert command["verbosity"] == "executionStats"
    operation = command["explain"]
    assert operation["aggregate"] == "employees"
    assert operation["cursor"] == {}
    assert operation["pipeline"] == main._build_department_summary_pipeline(
        "2026-12", "Engineering"
    )


def test_admin_explain_late_leaderboard_preserves_filters_sort_and_limit(
    api_client, monkeypatch
):
    commands, explain_payload = mock_explain_command(monkeypatch)

    response = api_client.get(
        "/admin/explain/late_leaderboard",
        params={"month": "2026-07", "department": "Engineering", "limit": 3},
    )

    assert response.status_code == 200
    assert response.json()["collection"] == "attendance_logs"
    assert response.json()["explain"] == explain_payload
    command = commands[0]
    assert command["verbosity"] == "executionStats"
    assert command["explain"] == {
        "aggregate": "attendance_logs",
        "pipeline": main._build_late_leaderboard_pipeline(
            "2026-07", 3, "Engineering"
        ),
        "cursor": {},
    }


def test_admin_explain_department_trend_uses_date_filtered_aggregate(api_client, monkeypatch):
    commands, explain_payload = mock_explain_command(monkeypatch)

    response = api_client.get(
        "/admin/explain/department_trend",
        params={
            "department": "Engineering",
            "from": "2026-07-01",
            "to": "2026-07-03",
        },
    )

    assert response.status_code == 200
    assert response.json()["collection"] == "employees"
    assert response.json()["explain"] == explain_payload
    command = commands[0]
    assert command["verbosity"] == "executionStats"
    assert command["explain"] == {
        "aggregate": "employees",
        "pipeline": main._build_department_trend_pipeline(
            "Engineering", date(2026, 7, 1), date(2026, 7, 3)
        ),
        "cursor": {},
    }


@pytest.mark.parametrize(
    "endpoint,params",
    [
        ("unknown", {}),
        ("attendance_list", {"page": 0}),
        ("attendance_list", {"date_from": "not-a-date"}),
        ("attendance_list", {"date_from": "2026-07-02", "date_to": "2026-07-01"}),
        ("employee_monthly", {"emp_code": "EMP0001", "month": "2026-13"}),
        ("late_leaderboard", {"month": "2026-07", "limit": 0}),
        (
            "department_trend",
            {"department": "Engineering", "from": "2026-07-01", "to": "2026-10-02"},
        ),
    ],
)
def test_admin_explain_rejects_invalid_endpoint_filters_and_pagination(
    api_client, monkeypatch, endpoint, params
):
    commands, _ = mock_explain_command(monkeypatch)

    response = api_client.get(f"/admin/explain/{endpoint}", params=params)

    assert response.status_code == 422
    assert commands == []


@pytest.mark.parametrize(
    "endpoint,params",
    [
        ("employee_monthly", {"emp_code": "EMP0001"}),
        ("department_summary", {}),
        ("late_leaderboard", {}),
        ("department_trend", {"department": "Engineering", "from": "2026-07-01"}),
    ],
)
def test_admin_explain_rejects_missing_required_filters(api_client, monkeypatch, endpoint, params):
    commands, _ = mock_explain_command(monkeypatch)

    response = api_client.get(f"/admin/explain/{endpoint}", params=params)

    assert response.status_code == 422
    assert commands == []


def test_invalid_timestamp_and_pagination_validation(api_client):
    api_client.post(
        "/employees",
        json={
            "emp_code": "EMP0001",
            "name": "A",
            "email": "a@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )
    invalid_stamp = api_client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": 12345},
    )
    assert invalid_stamp.status_code == 422

    invalid_page = api_client.get("/employees", params={"page": 0})
    assert invalid_page.status_code == 422

    invalid_month = api_client.get("/analytics/employees/EMP0001/monthly", params={"month": "2026/07"})
    assert invalid_month.status_code == 422
