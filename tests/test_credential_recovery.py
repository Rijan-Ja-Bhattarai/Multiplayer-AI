"""Recovering when the credential store no longer holds a saved token.

The behaviour being pinned down here is that a lost token degrades to a
fresh identity instead of making the app unstartable. It used to raise,
which left the user with no way back except deleting settings.json by
hand, and it happened whenever the credential store was cleared, locked
or moved to another machine.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage


class MemoryVault:
    """In-memory stand-in for the OS credential store."""

    def __init__(self, broken=False):
        self.values = {}
        self.broken = broken

    def get(self, name):
        if self.broken:
            raise RuntimeError("The OS credential store is unavailable.")
        return self.values.get(name)

    def set(self, name, value):
        if self.broken:
            raise RuntimeError("Could not save credentials to the OS credential store.")
        self.values[name] = value

    def delete(self, name):
        if self.broken:
            return False
        return self.values.pop(name, None) is not None


# The relay rejects tokens shorter than 32 characters, so a fixture that
# mints one would fail validation before reaching the code under test.
SAVED_TOKEN = "saved-" + "t" * 40


def storage_with_identity(vault, token=SAVED_TOKEN, store_token=True):
    """A settings file that already refers to one saved identity.

    ``store_token`` is False for the unavailable-store case: a broken
    vault refuses writes, so seeding the token would fail in the helper
    rather than in the behaviour under test.
    """
    directory = tempfile.mkdtemp()
    Path(directory, "settings.json").write_text(json.dumps({
        "device_id": "device-abc12345",
        "identities": {"device-abc12345": "workspace"},
    }), encoding="utf-8")
    if store_token:
        vault.set("relay:device-abc12345", token)
    return Storage(directory, vault)


class LostCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []

    def emit(self, kind, data):
        self.events.append((kind, data))

    async def test_missing_token_does_not_stop_startup(self):
        """A vanished token is replaced, and the app starts."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        # The token disappears, as a cleared credential store would do.
        del vault.values["relay:device-abc12345"]

        runtime = DesktopRuntime(storage, self.emit)
        await runtime.start()
        try:
            device_id = storage.settings["device_id"]
            self.assertIn(device_id, runtime.credentials,
                          "the device should have been given a new token")
            self.assertNotEqual(runtime.credentials[device_id]["token"], SAVED_TOKEN)
        finally:
            await runtime.close()

    async def test_the_user_is_told_the_identity_changed(self):
        """Losing an identity silently would look like a broken workspace."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        del vault.values["relay:device-abc12345"]

        runtime = DesktopRuntime(storage, self.emit)
        await runtime.start()
        try:
            notices = [data for kind, data in self.events if kind == "notice"]
            self.assertTrue(notices, "expected a notice about the new identity")
            self.assertIn("invitation", " ".join(notices))
        finally:
            await runtime.close()

    def test_the_dropped_identity_is_forgotten(self):
        """Settings stop referring to an identity that no longer exists."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        del vault.values["relay:device-abc12345"]

        storage.credentials()

        self.assertEqual(storage.dropped_identities, ["device-abc12345"])
        self.assertNotIn("device-abc12345", storage.settings["identities"])

    def test_intact_identities_are_untouched(self):
        """A healthy store loads exactly as before."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        credentials = storage.credentials()

        self.assertEqual(storage.dropped_identities, [])
        self.assertEqual(credentials["device-abc12345"]["token"], SAVED_TOKEN)

    def test_an_unavailable_store_is_still_fatal(self):
        """A broken store must not be silently treated as empty.

        The app cannot write a replacement either, so carrying on would
        give a device that silently fails on every relay instead of one
        that says what is wrong.
        """
        storage = storage_with_identity(MemoryVault(broken=True), store_token=False)

        with self.assertRaises(RuntimeError) as caught:
            storage.credentials()
        self.assertIn("unavailable", str(caught.exception))

    def test_only_the_missing_identity_is_dropped(self):
        """One bad identity does not discard the good ones."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        storage.settings["identities"]["device-good"] = "team"
        vault.set("relay:device-good", "good-token")
        # Only this one is lost; the other must survive.
        del vault.values["relay:device-abc12345"]

        credentials = storage.credentials()

        self.assertEqual(credentials["device-good"]["token"], "good-token")
        self.assertEqual(storage.dropped_identities, ["device-abc12345"])


class ResetIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []

    def emit(self, kind, data):
        self.events.append((kind, data))

    def test_reset_clears_settings_and_vault(self):
        """A reset leaves nothing that could restore the old identity."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        removed = storage.reset_identities()

        self.assertEqual(removed, ["device-abc12345"])
        self.assertEqual(storage.settings["identities"], {})
        self.assertNotIn("relay:device-abc12345", vault.values)

    def test_reset_survives_a_store_that_refuses_deletion(self):
        """Settings are cleared even if the token cannot be deleted."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        def refuse(name):
            return False
        vault.delete = refuse

        removed = storage.reset_identities()

        self.assertEqual(removed, ["device-abc12345"])
        self.assertEqual(storage.settings["identities"], {})

    def test_reset_keeps_the_device_id(self):
        """The token identifies the device, so a stable id is kept."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        storage.reset_identities()

        self.assertEqual(storage.settings["device_id"], "device-abc12345")

    async def test_runtime_reset_gives_a_new_token(self):
        """The runtime path mints a fresh token and tells the user."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        runtime = DesktopRuntime(storage, self.emit)
        await runtime.start()
        try:
            first = runtime.credentials[storage.settings["device_id"]]["token"]
            result = await runtime.reset_identity()
            second = runtime.credentials[storage.settings["device_id"]]["token"]

            self.assertEqual(result["device_id"], "device-abc12345")
            self.assertNotEqual(first, second)
            self.assertTrue([1 for kind, data in self.events
                             if kind == "notice" and "identity" in str(data)])
        finally:
            await runtime.close()


if __name__ == "__main__":
    unittest.main()
