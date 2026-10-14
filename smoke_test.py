from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
import mongomock

from app import main

client = mongomock.MongoClient()
main.client = client
main.db = client['attendance_db']
main.db.employees.delete_many({})
main.db.attendance_logs.delete_many({})

with TestClient(main.app) as tc:
    create = tc.post('/employees', json={
        'emp_code': 'EMP0001',
        'name': 'Asha Rao',
        'email': 'asha@example.com',
        'department': 'Engineering',
        'shift_start': '09:30',
        'shift_end': '18:30',
        'joined_on': '2026-01-05',
    })
    assert create.status_code == 201, create.text

    punch_in_ms = int(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc).timestamp() * 1000)
    pin = tc.post('/attendance/punch-in', json={'emp_code': 'EMP0001', 'punched_at': punch_in_ms})
    assert pin.status_code == 201, pin.text
    payload = pin.json()
    assert payload['date'] == '2026-07-06', payload
    assert payload['late_minutes'] == 0, payload

    punch_out_ms = int(datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc).timestamp() * 1000)
    pout = tc.post('/attendance/punch-out', json={'emp_code': 'EMP0001', 'punched_at': punch_out_ms})
    assert pout.status_code == 200, pout.text
    out = pout.json()
    assert out['work_hours'] == 9.12, out
    assert out['overtime_minutes'] == 0, out

    # mongomock cannot execute the correlated $lookup; the contract tests inspect the pipeline.
    monthly_result = {
        'emp_code': 'EMP0001',
        'working_days': 23,
        'present_days': 1.0,
        'leave_days': 0,
        'late_count': 0,
        'total_late_minutes': 0,
        'total_overtime_minutes': 0,
        'attendance_pct': 4.3478,
    }
    with patch.object(main.db.employees, 'aggregate', return_value=iter([monthly_result])):
        monthly = tc.get('/analytics/employees/EMP0001/monthly', params={'month': '2026-07'})
    assert monthly.status_code == 200, monthly.text
    m = monthly.json()
    assert m['working_days'] == 23, m
    assert m['present_days'] == 1.0, m
    assert m['attendance_pct'] == 4.3478, m

    reg = tc.patch('/attendance/EMP0001/2026-07-06', json={
        'status': 'WFH',
        'punch_in': punch_in_ms,
        'punch_out': punch_out_ms,
        'reason': 'Updated from office work',
        'regularized_by': 'admin',
    })
    assert reg.status_code == 200, reg.text
    saved = reg.json()
    assert saved['status'] == 'WFH', saved
    assert len(saved['history']) == 1, saved
    assert list(saved['history'][0]['changes'].keys()) == ['status'], saved

print('SMOKE_OK')
