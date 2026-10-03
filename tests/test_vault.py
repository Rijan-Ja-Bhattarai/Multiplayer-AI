"""The credential store boundary, and the settings file's startup guards.

Vault is the only code in the project that talks to the OS keyring, and
every other test injects a fake instead, so its three error translations
had no coverage at all. A regression there surfaces as "the OS credential
store is unavailable" in front of a user and nowhere else.

The default data directory is here too, because a wrong value silently
relocates a user's credentials into their home directory rather than
failing, and no test ever exercised it: each one passes a temporary
directory.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import keyring.errors

from desktop_app.storage import (ABSENT, REMOVED, UNAVAILABLE, Storage, Vault,
                                  WorkspaceVault, default_data_directory)


class FakeBackend:
    """Stands in for the keyring module, with selectable failures.

    Exposes ``errors`` the way the real keyring module does, and raises
    ``PasswordDeleteError`` for an absent entry, so Vault.delete's
    distinction between "not there" and "store refused" is exercised
    against the same shapes the real backend produces.
    """

    def __init__(self, read_error=None, write_error=None, delete_error=None):
        self.values = {}
        self.errors = keyring.errors
        self.read_error = read_error
        self.write_error = write_error
        self.delete_error = delete_error

    def get_password(self, service, name):
        if self.read_error:
            raise self.read_error
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        if self.write_error:
            raise self.write_error
        self.values[(service, name)] = value

    def delete_password(self, service, name):
        if self.delete_error:
            raise self.delete_error
        if self.values.pop((service, name), None) is None:
            raise keyring.errors.PasswordDeleteError(service)


def vault_with(backend):
    """A Vault bound to a fake backend instead of the keyring module."""
    vault = Vault.__new__(Vault)
    vault.backend = backend
    return vault


class VaultTests(unittest.TestCase):
    def test_reads_and_writes_under_the_app_service(self) -> None:
        backend = FakeBackend()
        vault = vault_with(backend)

        vault.set("relay:device-1", "token")
        self.assertEqual(vault.get("relay:device-1"), "token")
        self.assertEqual(backend.values, {("MultiplayerAI", "relay:device-1"): "token"})

    def test_an_absent_entry_reads_as_none(self) -> None:
        """A missing token is recoverable; the caller replaces it."""
        self.assertIsNone(vault_with(FakeBackend()).get("relay:nothing"))

    def test_an_unavailable_store_says_so_on_read(self) -> None:
        """The message tells the user to unlock the keyring and restart."""
        vault = vault_with(FakeBackend(read_error=RuntimeError("locked")))

        with self.assertRaises(RuntimeError) as caught:
            vault.get("relay:device-1")

        self.assertIn("unavailable", str(caught.exception))
        self.assertIn("Unlock", str(caught.exception))

    def test_a_refused_write_says_so(self) -> None:
        vault = vault_with(FakeBackend(write_error=RuntimeError("denied")))

        with self.assertRaises(RuntimeError) as caught:
            vault.set("relay:device-1", "token")

        self.assertIn("Could not save credentials", str(caught.exception))

    def test_delete_distinguishes_removed_from_already_absent(self) -> None:
        """Both mean the entry is gone; a caller retrying needs both."""
        backend = FakeBackend()
        vault = vault_with(backend)
        vault.set("relay:device-1", "token")

        self.assertEqual(vault.delete("relay:device-1"), REMOVED)
        self.assertEqual(vault.delete("relay:device-1"), ABSENT)

    def test_an_absent_entry_is_not_the_same_as_a_locked_store(self) -> None:
        """The distinction a retry depends on.

        A boolean collapsed both into False, so a caller could not tell
        "there was nothing to remove" from "the store would not let me",
        and a superseded credential could outlive the deletion that was
        supposed to retire it.
        """
        self.assertEqual(vault_with(FakeBackend()).delete("relay:nothing"), ABSENT)
        self.assertEqual(
            vault_with(FakeBackend(delete_error=RuntimeError("locked"))).delete("relay:x"),
            UNAVAILABLE)

    def test_a_store_that_refuses_deletion_does_not_raise(self) -> None:
        """Cleanup must not raise, or a locked store breaks the caller.

        The caller decides what to do about it; the vault only reports.
        """
        vault = vault_with(FakeBackend(delete_error=RuntimeError("locked")))

        self.assertEqual(vault.delete("relay:device-1"), UNAVAILABLE)

    def test_the_backend_is_never_leaked_into_an_error(self) -> None:
        """keyring's own text can carry paths or a service name."""
        vault = vault_with(FakeBackend(read_error=RuntimeError("C:\\\\Users\\\\x")))

        with self.assertRaises(RuntimeError) as caught:
            vault.get("relay:device-1")

        self.assertIsNone(caught.exception.__cause__, "the original error must not chain")
        self.assertNotIn("Users", str(caught.exception))


class NoDeleteVault:
    """An inner vault that predates deletion, like a test double."""

    def __init__(self, inner):
        self.inner = inner

    def get(self, name):
        return self.inner.get(name)

    def set(self, name, value):
        self.inner.set(name, value)


class WorkspaceVaultTests(unittest.TestCase):
    """Namespacing so two workspaces can each hold a "provider" key."""

    def test_namespaces_reads_and_writes(self) -> None:
        backend = FakeBackend()
        scoped = WorkspaceVault(vault_with(backend), "workspace-abc")

        scoped.set("provider:writer", "key")
        scoped.get("provider:writer")

        self.assertEqual(list(backend.values), [("MultiplayerAI", "workspace-abc:provider:writer")])

    def test_workspaces_do_not_collide(self) -> None:
        backend = FakeBackend()
        first = WorkspaceVault(vault_with(backend), "workspace-one")
        second = WorkspaceVault(vault_with(backend), "workspace-two")

        first.set("provider:writer", "first-key")
        second.set("provider:writer", "second-key")

        self.assertEqual(first.get("provider:writer"), "first-key")
        self.assertEqual(second.get("provider:writer"), "second-key")

    def test_a_vault_without_delete_is_tolerated(self) -> None:
        """An inner vault may not implement deletion at all."""
        backend = FakeBackend()
        scoped = WorkspaceVault(NoDeleteVault(vault_with(backend)), "workspace-abc")

        scoped.set("provider:writer", "key")
        scoped.delete("provider:writer")          # must not raise

        self.assertEqual(scoped.get("provider:writer"), "key")


class DataDirectoryTests(unittest.TestCase):
    """Where the app looks for its settings by default.

    Only this platform's branch is exercised. pathlib binds WindowsPath or
    PosixPath from os.name when it is imported, so patching os.name to
    reach the other branch makes Path() try to build a foreign flavour and
    raise instead. Each branch therefore runs where it is real.
    """

    @unittest.skipUnless(os.name == "nt", "the Windows branch of the path lookup")
    def test_windows_uses_local_app_data(self) -> None:
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}, clear=False):
            resolved = default_data_directory()

        self.assertEqual(resolved, Path(r"C:\Users\test\AppData\Local") / "MultiplayerAI")

    @unittest.skipUnless(os.name == "nt", "the Windows branch of the path lookup")
    def test_windows_falls_back_to_the_home_directory(self) -> None:
        """An unset LOCALAPPDATA must still resolve inside the home directory."""
        environment = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with mock.patch.dict(os.environ, environment, clear=True):
            resolved = default_data_directory()

        self.assertEqual(resolved, Path.home() / "MultiplayerAI")
        self.assertTrue(str(resolved).startswith(str(Path.home())))

    @unittest.skipIf(os.name == "nt", "the POSIX branch of the path lookup")
    def test_posix_uses_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/home/test/.config"}, clear=False):
            resolved = default_data_directory()

        self.assertEqual(resolved, Path("/home/test/.config") / "multiplayer-ai")

    @unittest.skipIf(os.name == "nt", "the POSIX branch of the path lookup")
    def test_posix_falls_back_to_dot_config(self) -> None:
        environment = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, environment, clear=True):
            resolved = default_data_directory()

        self.assertEqual(resolved, Path.home() / ".config" / "multiplayer-ai")
        self.assertNotIn("MultiplayerAI", str(resolved), "the Windows directory name must not leak")


class SettingsFileTests(unittest.TestCase):
    """The settings file is read before any UI exists, so it must be sane."""

    def test_a_settings_file_that_is_not_an_object_is_rejected(self) -> None:
        """A list or scalar would break every settings access silently."""
        for content in ("[]", '"dark"', "42", "null"):
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as directory:
                    Path(directory, "settings.json").write_text(content, encoding="utf-8")

                    with self.assertRaisesRegex(ValueError, "must contain an object"):
                        Storage(directory, FakeBackend())

    def test_a_missing_settings_file_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())

            self.assertEqual(storage.settings, {})

    def test_settings_are_written_atomically(self) -> None:
        """A half-written file would lose every preference on a crash."""
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())
            storage.settings["theme"] = "miku"
            storage.save()

            self.assertEqual(json.loads(Path(directory, "settings.json").read_text(encoding="utf-8")),
                             {"theme": "miku"})
            self.assertFalse(Path(directory, "settings.json.tmp").exists(),
                             "the temporary file must be renamed, not left behind")


if __name__ == "__main__":
    unittest.main()