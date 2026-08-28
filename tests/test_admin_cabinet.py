"""R2 admin cabinet: auth, sessions, provider config CRUD."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.auth import verify_password


class TestPasswordHashing(unittest.TestCase):
    def test_roundtrip(self):
        from app.auth import hash_password

        h = hash_password("hunter2secret")
        self.assertTrue(verify_password("hunter2secret", h))
        self.assertFalse(verify_password("wrong", h))

    def test_salt_unique(self):
        from app.auth import hash_password

        self.assertNotEqual(hash_password("x"), hash_password("x"))

    def test_malformed_stored_hash(self):
        self.assertFalse(verify_password("x", "not-a-hash"))
        self.assertFalse(verify_password("x", ""))


class TestSessionsAndProviderConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile as tf
        from app.store import Store

        cls._tmpdir = tf.TemporaryDirectory()
        cls.store = Store(str(Path(cls._tmpdir.name) / "t.db"))

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_user_session_crud(self):
        store = self.store
        self.assertEqual(store.count_users(), 0)
        uid = store.create_user("admin", hash_password_safe("password123"))
        self.assertIsNotNone(store.get_user("admin"))
        self.assertIsNone(store.get_user("nobody"))

        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        store.create_session("tokhash", uid, future)
        session = store.get_session("tokhash")
        assert session is not None
        self.assertEqual(session["username"], "admin")

        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        store.create_session("stale", uid, expired)
        self.assertIsNone(store.get_session("stale"))

        store.delete_session("tokhash")
        self.assertIsNone(store.get_session("tokhash"))

    def test_provider_config_crud(self):
        store = self.store
        store.upsert_provider_config("zai", "Z.AI", True, {"api_key": "k1", "base_url": ""})
        store.upsert_provider_config("zai", "Z.AI", False, {"api_key": "k2"})
        rows = store.list_provider_configs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slug"], "zai")
        self.assertFalse(rows[0]["enabled"])
        self.assertEqual(rows[0]["config"]["api_key"], "k2")
        self.assertTrue(store.delete_provider_config("zai"))
        self.assertFalse(store.delete_provider_config("zai"))


def hash_password_safe(pw: str) -> str:
    from app.auth import hash_password

    return hash_password(pw)


class TestAdminApi(unittest.TestCase):
    """End-to-end through the FastAPI app with an isolated database."""

    API = None  # populated lazily below

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        import os

        os.environ["DATABASE_PATH"] = str(Path(cls._tmpdir.name) / "admin.db")
        os.environ["DEMO_MODE"] = "false"
        from app.main import app

        cls.app = app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    @property
    def store(self):
        return self.app.state.store

    def _setup_account(self):
        res = self.client.post(
            "/api/auth/setup", json={"username": "admin", "password": "password123"}
        )
        if res.status_code == 409:  # account created by another test in class
            # ensure this test's cookie jar holds a valid session again
            res = self.client.post(
                "/api/auth/login", json={"username": "admin", "password": "password123"}
            )
            self.assertEqual(res.status_code, 200, res.text)
            return
        self.assertEqual(res.status_code, 200, res.text)

    def test_01_setup_login_logout_flow(self):
        self.assertTrue(self.client.get("/api/auth/session").json()["needs_setup"])
        self._setup_account()
        session = self.client.get("/api/auth/session").json()
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["username"], "admin")

        res = self.client.post("/api/auth/setup", json={"username": "x", "password": "y" * 10})
        self.assertEqual(res.status_code, 409)

        # weak password rejected
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": "short"})
        self.assertEqual(res.status_code, 401)

        self.client.post("/api/auth/logout")
        self.assertFalse(self.client.get("/api/auth/session").json()["authenticated"])

    def test_02_wrong_credentials_rejected(self):
        self._setup_account()
        res = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong-pass"}
        )
        self.assertEqual(res.status_code, 401)
        res = self.client.post(
            "/api/auth/login", json={"username": "ghost", "password": "wrong-pass"}
        )
        self.assertEqual(res.status_code, 401)

    def test_03_admin_endpoints_require_auth(self):
        client = TestClient(self.app)  # fresh cookie jar, unauthenticated
        res = client.get("/api/admin/providers")
        self.assertEqual(res.status_code, 401)
        res = client.put("/api/admin/providers/zai", json={})
        self.assertEqual(res.status_code, 401)
        res = client.delete("/api/admin/providers/zai")
        self.assertEqual(res.status_code, 401)
        res = client.post("/api/admin/change-password", json={})
        self.assertEqual(res.status_code, 401)

    def test_04_provider_update_stored_and_masked(self):
        self._setup_account()
        res = self.client.put(
            "/api/admin/providers/deepseek",
            json={"api_key": "sk-test-123", "base_url": "", "enabled": True},
        )
        self.assertEqual(res.status_code, 200, res.text)
        row = next(r for r in self.store.list_provider_configs() if r["slug"] == "deepseek")
        self.assertEqual(row["config"]["api_key"], "sk-test-123")

        listed = self.client.get("/api/admin/providers").json()["providers"]
        ds = next(p for p in listed if p["slug"] == "deepseek")
        api_key_field = next(f for f in ds["fields"] if f["name"] == "api_key")
        self.assertTrue(api_key_field["is_set"])
        self.assertNotIn("value", api_key_field, "secrets must never be returned to the browser")
        self.assertTrue(ds["overridden"])

    def test_05_secret_untouched_when_empty_input(self):
        self._setup_account()
        self.client.put("/api/admin/providers/deepseek", json={"api_key": "sk-one"})
        self.client.put("/api/admin/providers/deepseek", json={"api_key": ""})
        row = next(r for r in self.store.list_provider_configs() if r["slug"] == "deepseek")
        self.assertEqual(row["config"]["api_key"], "sk-one")

    def test_06_disable_reset_falls_back_to_env(self):
        self._setup_account()
        self.client.put("/api/admin/providers/openrouter", json={"enabled": False})
        row = next(r for r in self.store.list_provider_configs() if r["slug"] == "openrouter")
        self.assertFalse(row["enabled"])
        self.assertTrue(self.client.delete("/api/admin/providers/openrouter").status_code == 200)
        slugs = [r["slug"] for r in self.store.list_provider_configs()]
        self.assertNotIn("openrouter", slugs)

    def test_07_unknown_provider_rejected(self):
        self._setup_account()
        res = self.client.put("/api/admin/providers/unknown", json={})
        self.assertEqual(res.status_code, 404)

    def test_08_change_password(self):
        self._setup_account()
        res = self.client.post(
            "/api/admin/change-password",
            json={"old_password": "wrong", "new_password": "newpassword9"},
        )
        self.assertEqual(res.status_code, 403)
        res = self.client.post(
            "/api/admin/change-password",
            json={"old_password": "password123", "new_password": "newpassword9"},
        )
        self.assertEqual(res.status_code, 200)
        user = self.store.get_user("admin")
        self.assertTrue(verify_password("newpassword9", user["password_hash"]))

    def test_09_dashboard_open_without_auth(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
