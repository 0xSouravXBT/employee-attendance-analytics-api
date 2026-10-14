# Employee Attendance & Analytics API

FastAPI and MongoDB implementation for the HROne Software Engineer Trainee assignment. The full API contract and business rules are in `openapi.yaml`; storage shapes are in `DATA_MODEL.md`.

## Requirements

- Windows PowerShell
- Python 3.11 or newer
- MongoDB 6.0 or newer running locally (the grader uses MongoDB 7.0)

Start the MongoDB Windows service from Services, or start your local MongoDB server using the installation's configured `mongod.cfg`. Confirm it is listening on `localhost:27017` before starting the app. This project makes no application network calls other than MongoDB.

## PowerShell Setup

Run these commands from the repository root (`candidate_kit`):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_dev"

uvicorn app.main:app --port 8000 --reload
```

The app also reads `MONGO_URI` and `MONGO_DB` from a local `.env` file when present; process environment variables take precedence. Defaults are `mongodb://localhost:27017` and `attendance_db`. Use a database dedicated to this assignment, not a database containing data you need to preserve. The app creates its indexes at startup. Verify readiness at `http://127.0.0.1:8000/health`.

To deactivate the virtual environment, run `deactivate`.

## Sample Data Safety

**Warning: `sample_seed.py` is destructive to the configured database.** It deletes every document in both `employees` and `attendance_logs` before loading the samples. The equivalent `mongoimport` commands shown in the script use `--drop` and are also destructive. Never run the seed script or those commands against a database containing data you need.

If you intentionally need the sample data, first point the script at a disposable local database and verify the target name:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_sample_disposable"
python sample_seed.py
```

Do not use `attendance_db`, a production database, or the integration-test database for seeding. The provided samples show document shapes only; they do not establish correctness for the grader's larger dataset.

## Tests

For deterministic local tests, clear Mongo settings so no live integration test is enabled:

```powershell
Remove-Item Env:MONGO_URI -ErrorAction SilentlyContinue
Remove-Item Env:MONGO_DB -ErrorAction SilentlyContinue

# Focused contract tests
python -m pytest -q tests/test_contract_api.py

# Full suite (the guarded live integration test is skipped)
python -m pytest -q

# Standalone smoke test, backed by mongomock
python smoke_test.py
```

The live integration test is optional and requires a running local MongoDB server. It is guarded to accept only the dedicated `attendance_integration_test` database. Set both variables explicitly before running it:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_integration_test"
python -m pytest -v -s tests/test_real_mongo_integration.py
```

The integration test creates uniquely named test employees and deletes only those employees and their matching attendance records. It does not load the sample dataset. Its explain-plan assertion checks the attendance-list query on the current database contents; it is not a benchmark of a 100,000-record dataset.

## API Examples

With the server running at `http://127.0.0.1:8000`, these examples use PowerShell's `Invoke-RestMethod`:

```powershell
# Health
Invoke-RestMethod http://127.0.0.1:8000/health

# Create an employee
$employee = @{
    emp_code = "EMP0001"
    name = "Asha Rao"
    email = "asha@example.com"
    department = "Engineering"
    shift_start = "09:30"
    shift_end = "18:30"
    joined_on = "2026-01-05"
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
    -Uri http://127.0.0.1:8000/employees `
    -ContentType "application/json" `
    -Body $employee

# Punch in (epoch milliseconds)
$punchIn = @{
    emp_code = "EMP0001"
    punched_at = [DateTimeOffset]::Parse("2026-07-06T03:58:00Z").ToUnixTimeMilliseconds()
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
    -Uri http://127.0.0.1:8000/attendance/punch-in `
    -ContentType "application/json" `
    -Body $punchIn

# List attendance with filters and pagination
Invoke-RestMethod "http://127.0.0.1:8000/attendance?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=20"

# Employee monthly analytics
Invoke-RestMethod "http://127.0.0.1:8000/analytics/employees/EMP0001/monthly?month=2026-07"

# Department summary, late leaderboard, and department trend
Invoke-RestMethod "http://127.0.0.1:8000/analytics/departments/summary?month=2026-07"
Invoke-RestMethod "http://127.0.0.1:8000/analytics/leaderboard/late?month=2026-07&limit=10"
Invoke-RestMethod "http://127.0.0.1:8000/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07"

# Explain an endpoint query with MongoDB execution statistics
Invoke-RestMethod "http://127.0.0.1:8000/admin/explain/attendance_list?page=1&page_size=20"
```

## Submission

Submit a **public Git repository** containing `app/main.py`, `requirements.txt`, `README.md`, `REVIEW.md`, and `DECISIONS.md`, following the submission instructions in the assignment email. Do not commit `.env` files, secrets, a Dockerfile, a virtual environment, or data dumps. The assignment-provided `sample_data/` and `sample_seed.py` are local fixtures; do not treat them as the grader's dataset.
