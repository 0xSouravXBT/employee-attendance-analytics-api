from __future__ import annotations

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def api_client(monkeypatch):
    original_client = getattr(main, "client", None)
    original_db = getattr(main, "db", None)

    mock_client = mongomock.MongoClient()
    main.client = mock_client
    main.db = mock_client["attendance_db"]
    main.db.employees.delete_many({})
    main.db.attendance_logs.delete_many({})

    with TestClient(main.app) as client:
        yield client

    main.client = original_client
    main.db = original_db
