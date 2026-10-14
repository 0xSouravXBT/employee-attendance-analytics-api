# DECISIONS.md

1. **Indexes.** At startup I create a unique index on `employees.emp_code` and a unique `(emp_code, date)` on `attendance_logs` (this also enforces one record per employee per day). I add `(department, emp_code)` and `(department, joined_on)` for department lookups, a standalone `joined_on` index so the department-summary headcount match (`joined_on <= month end`) is an IXSCAN rather than a COLLSCAN, and `(date desc, emp_code)` plus `(status, date)` on `attendance_logs` for the list/leaderboard filters. I avoided a text index because the assignment is exact-match filtering.

2. **Punch-in race.** There is no "check, then insert." Both punch-in and employee-create lean on a database unique index: the first write wins and a duplicate raises a duplicate-key error, which the endpoint turns into a 409. Two simultaneous punch-ins for the same employee and date therefore give exactly one 201 and the rest 409. Punch-out is similar: it updates with a filter of `punch_out: null`, so only one concurrent call succeeds.

3. **Ties.** The leaderboard ranks by total late minutes using MongoDB's `$rank`, so tied totals share a competition rank and the next rank is skipped (1, 2, 2, 4). `limit` is applied after ranking by keeping every row whose rank is at or below the cutoff, so a tie at the boundary can return more rows than `limit`. Rows are then ordered by late minutes desc, then `emp_code` asc.

4. **Headcount.** The department summary starts from `employees` and keeps only those with `joined_on <= end of month`, then left-joins the logs. Employees with zero log rows still appear, so they count toward headcount (R9); people who join after the period are excluded.

5. **At 100x data.** The analytics are already MongoDB aggregation pipelines, so the Python layer stays a thin serializer. For a much larger dataset I would pre-aggregate daily rollups, shard `attendance_logs` by date, and rely on the covering date/status indexes so the explain plans stay index-only instead of scanning.
