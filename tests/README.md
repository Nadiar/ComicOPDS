# ComicOPDS Test Suite

This directory contains the comprehensive test suite for ComicOPDS, covering authentication, database operations, OPDS feed generation, search, pagination, and API endpoints.

## Running Tests

### Run All Tests

```bash
pytest
```

### Run Tests with Coverage Report

```bash
pytest --cov=app --cov-report=html --cov-report=term-missing
```

The coverage report will be generated in `htmlcov/index.html`.

### Run Specific Test File

```bash
pytest tests/test_auth.py
```

### Run Specific Test Class

```bash
pytest tests/test_auth.py::TestAuthenticateUser
```

### Run Specific Test Method

```bash
pytest tests/test_auth.py::TestAuthenticateUser::test_authenticate_valid_default_credentials
```

### Run Tests by Marker

```bash
# Run only OPDS 1.2 tests
pytest -m opds1

# Run only OPDS 2.0 tests
pytest -m opds2

# Run only integration tests
pytest -m integration

# Run only unit tests
pytest -m unit

# Run only auth tests
pytest -m auth

# Run only pagination tests
pytest -m pagination
```

### Run Tests Verbosely

```bash
pytest -v
pytest -vv  # Even more verbose
```

### Run Tests in Parallel (requires pytest-xdist)

```bash
pytest -n auto
```

### Stop on First Failure

```bash
pytest -x
```

### Run Last Failed Tests Only

```bash
pytest --lf
```

## Test Structure

### Test Files

- **test_auth.py** - Authentication and authorization tests
- **test_db_functions.py** - Database function tests (queries, calculations)
- **test_opds_browse.py** - OPDS 1.2 feed browsing tests
- **test_opds_browse_2.py** - OPDS 2.0 feed browsing tests
- **test_opds_pagination.py** - Pagination tests for both OPDS versions
- **test_opds_utils.py** - OPDS utility function tests
- **test_opds_search.py** - Search functionality tests (OPDS 1.2 and 2.0)
- **test_opds_smartlists.py** - Smart list endpoint tests
- **test_download.py** - Download endpoint and HTTP Range support tests
- **test_stream.py** - Page streaming endpoint tests
- **test_api_users.py** - User management API tests
- **test_api_stats.py** - Statistics endpoint tests
- **test_admin.py** - Admin endpoint tests
- **test_error_handling.py** - Error handling and HTTP status code tests

### Test Fixtures (conftest.py)

Common fixtures available to all tests:

- **client** - FastAPI TestClient instance
- **auth_headers** - HTTP Basic Auth headers for authenticated requests
- **opds1_headers** - Headers requesting OPDS 1.2 format
- **opds2_headers** - Headers requesting OPDS 2.0 format
- **test_library_dir** - Temporary directory for test comic files
- **test_db** - Temporary SQLite database with schema
- **client_with_data** - Client with test data indexed (specific to each test)

### Test Data (fixtures/test_data.py)

Test data generation functions:

- **index_test_data()** - Indexes test data into the database
- **create_test_cbz()** - Creates a test CBZ file with sample content

## Test Markers

Markers are used to categorize and filter tests:

| Marker | Purpose | Example |
|--------|---------|---------|
| `@pytest.mark.opds1` | OPDS 1.2 specification tests | Atom XML feed tests |
| `@pytest.mark.opds2` | OPDS 2.0 specification tests | JSON feed tests |
| `@pytest.mark.integration` | Full end-to-end integration tests | API endpoint tests |
| `@pytest.mark.unit` | Unit tests for isolated functions | Database query tests |
| `@pytest.mark.auth` | Authentication and authorization tests | Login, permissions |
| `@pytest.mark.pagination` | Pagination functionality tests | Page parameter handling |

## Coverage Reports

### Terminal Report

After running tests with coverage, the terminal shows:

```
tests/test_auth.py::TestAuthenticateUser::test_authenticate_valid_default_credentials PASSED
tests/test_db_functions.py::TestLastModified::test_last_modified_empty_database PASSED

---------- coverage: platform win32, pytest-cov 7.0.0 ----------
Name                    Stmts   Miss  Cover   Missing
---------------------------------------------------------
app/__init__.py              0      0   100%
app/auth.py                 15      3    80%   42-44,78
app/db.py                  180     45    75%   92-110,201-205
app/main.py               210     90    57%   120-135,180-200
TOTAL                      405     138   66%
```

### HTML Report

Open `htmlcov/index.html` in a browser for interactive coverage visualization with:

- File-level coverage statistics
- Line-by-line highlighting (green = covered, red = uncovered)
- Coverage trends
- Missing line details

## Test Configuration (pyproject.toml)

Configuration in `[tool.pytest.ini_options]`:

```toml
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers --cov=app --cov-report=term-missing --cov-report=html"
```

Configuration in `[tool.coverage.run]`:

```toml
source = ["app"]
omit = ["*/tests/*"]
```

Exclusions in `[tool.coverage.report]`:

- `pragma: no cover` - Lines marked to skip coverage
- `def __repr__` - Representation methods
- Exception raising
- Type checking imports
- `if __name__ == .__main__.:` - Script entry points

## Writing New Tests

### Basic Test Pattern

```python
import pytest

@pytest.mark.integration
class TestNewFeature:
    """Tests for new feature."""

    def test_basic_functionality(self, client, auth_headers):
        """Test basic functionality."""
        response = client.get("/endpoint", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "expected_field" in data
```

### Using Fixtures

```python
def test_with_data(self, client_with_data, auth_headers):
    """Test with indexed test data."""
    response = client_with_data.get("/opds", headers=auth_headers)

    assert response.status_code == 200
```

### Mocking/Patching

```python
def test_with_mock(self, client, auth_headers, monkeypatch):
    """Test with mocked dependencies."""
    from app import db

    def mock_connect():
        # Return mock connection
        pass

    monkeypatch.setattr(db, "connect", mock_connect)

    response = client.get("/stats.json", headers=auth_headers)
    assert response.status_code == 200
```

## Continuous Integration

The test suite is configured to run in CI/CD pipelines:

```bash
pytest --cov=app --cov-report=xml --cov-report=term-missing
```

The `--cov-report=xml` generates `coverage.xml` for tools like Codecov.

## Troubleshooting

### Tests Failing Due to Database Lock

The test fixtures handle database cleanup, but in rare cases:

```bash
# Remove stray test database files
rm -rf /tmp/comicopds_test_*
rm -rf /tmp/comicopds_data_*
```

### Import Errors

Ensure the project is installed in development mode:

```bash
pip install -e .
```

### Missing Test Dependencies

Install test dependencies:

```bash
pip install -e ".[test]"
```

## Test Statistics

Current test suite:

- **Total Tests**: 50+ (across 14 test files)
- **Test Classes**: 20+
- **Coverage Target**: >80% of app module
- **Execution Time**: ~10-15 seconds on typical hardware

### Test Breakdown by Category

| Category | Count | Files |
|----------|-------|-------|
| Authentication | 6 | test_auth.py |
| Database | 6 | test_db_functions.py |
| OPDS 1.2 Browse | 4 | test_opds_browse.py |
| OPDS 2.0 Browse | 4 | test_opds_browse_2.py |
| Pagination | 4 | test_opds_pagination.py |
| OPDS Utils | 1 | test_opds_utils.py |
| Search | 6 | test_opds_search.py |
| Smart Lists | 3 | test_opds_smartlists.py |
| Download | 3 | test_download.py |
| Streaming | 3 | test_stream.py |
| Users | 3 | test_api_users.py |
| Statistics | 2 | test_api_stats.py |
| Admin | 3 | test_admin.py |
| Error Handling | 4 | test_error_handling.py |

## Additional Notes

- Tests use temporary directories for isolation and cleanup
- Database is recreated fresh for each test
- Authentication is tested with default credentials from environment
- OPDS format negotiation is tested via Accept headers
- Both successful and error cases are tested
