"""
Tests for the authentication module.
"""
import base64

import pytest
from fastapi.security import HTTPBasicCredentials

from app import auth


@pytest.mark.unit
@pytest.mark.auth
class TestAuthenticateUser:
    """Tests for authenticate_user function."""

    def test_authenticate_valid_default_credentials(self):
        """Valid default admin credentials authenticate successfully."""
        creds = HTTPBasicCredentials(username="testadmin", password="testpass123")
        result = auth.authenticate_user(creds)

        assert result is not None
        assert result["username"] == "testadmin"
        assert result["is_admin"] == 1

    def test_authenticate_invalid_password(self):
        """Invalid password returns None."""
        creds = HTTPBasicCredentials(username="testadmin", password="wrongpassword")
        result = auth.authenticate_user(creds)

        assert result is None

    def test_authenticate_nonexistent_user(self):
        """Non-existent user returns None."""
        creds = HTTPBasicCredentials(username="nobody", password="anything")
        result = auth.authenticate_user(creds)

        assert result is None


@pytest.mark.integration
@pytest.mark.auth
class TestRequireBasicAuth:
    """Tests for require_basic dependency."""

    def test_endpoint_with_valid_auth_succeeds(self, client):
        """Endpoint with valid Basic Auth header succeeds."""
        credentials = base64.b64encode(b"testadmin:testpass123").decode()
        headers = {"Authorization": f"Basic {credentials}"}

        response = client.get("/opds", headers=headers)
        assert response.status_code == 200

    def test_endpoint_with_invalid_auth_returns_401(self, client):
        """Endpoint with invalid credentials returns 401."""
        credentials = base64.b64encode(b"testadmin:wrongpass").decode()
        headers = {"Authorization": f"Basic {credentials}"}

        response = client.get("/opds", headers=headers)
        assert response.status_code == 401

    def test_endpoint_without_auth_returns_403(self, client):
        """Endpoint without auth header returns 403."""
        response = client.get("/opds")
        # FastAPI raises 403 when required dependency fails
        assert response.status_code in [401, 403]
