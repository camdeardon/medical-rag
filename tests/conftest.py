import os
import tempfile
import pytest
from fastapi.testclient import TestClient

# Override DB path for tests before importing the app
fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ["SQLITE_DB"] = temp_db_path

from app.main import app, get_current_user
from app.database import init_db, _conn

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    # Initialize the database schema for the test session
    init_db()
    yield
    # Cleanup after session
    os.close(fd)
    os.remove(temp_db_path)

@pytest.fixture
def db_conn():
    conn = _conn()
    yield conn
    conn.close()

@pytest.fixture
def mock_user():
    return {
        "id": 1,
        "email": "test@example.com",
        "is_approved": True,
        "is_admin": False
    }

@pytest.fixture
def mock_admin():
    return {
        "id": 2,
        "email": "admin@example.com",
        "is_approved": True,
        "is_admin": True
    }

@pytest.fixture
def client(mock_user):
    # Override dependency
    app.dependency_overrides[get_current_user] = lambda: mock_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def admin_client(mock_admin):
    # Override dependency for admin routes
    app.dependency_overrides[get_current_user] = lambda: mock_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
