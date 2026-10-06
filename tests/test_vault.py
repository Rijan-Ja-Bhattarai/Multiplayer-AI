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
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import keyring.errors

from desktop_app import storage as storage_module
from desktop_app.storage import (ABSENT, REMOVED, UNAVAILABLE, Storage, Vault,
                                  WorkspaceVault, default_data_directory,
                                  write_json_durably)


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


def leftovers(directory):
    """Temporary files a durable write should have renamed away.

    The name is no longer a fixed ``<name>.tmp`` because two writers must not
    share one, so this looks for the pattern rather than a single path.
    """
    return sorted(entry.name for entry in Path(directory).glob("*.tmp"))


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
            self.assertEqual(leftovers(directory), [],
                             "the temporary file must be renamed, not left behind")

    def test_two_saves_never_share_a_temporary_file(self) -> None:
        """A shared name let concurrent writers put stale contents on disk.

        Settings are written from the UI thread and from the runtime's own,
        so one could rename the other's half-written document into place and
        the caller would still believe its write landed.
        """
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())
            storage.settings["theme"] = "miku"
            seen = []
            real = Path.open

            def record(self, *args, **kwargs):
                if str(self).endswith(".tmp"):
                    seen.append(self.name)
                return real(self, *args, **kwargs)
            with mock.patch.object(Path, "open", record):
                storage.save()
                storage.save()

            self.assertEqual(len(seen), 2, "both saves should have used a temporary file")
            self.assertEqual(len(set(seen)), 2,
                             "two concurrent writers must not share one temporary name")
            self.assertEqual(leftovers(directory), [])


class DurableWriteTests(unittest.TestCase):
    """Renaming into place is only half of surviving a power cut.

    The other half is that the bytes and the directory entry reach the disk.
    Without it a rename can be lost, leaving the previous contents or no file
    at all, which for the cleanup ledger means credentials that nothing on
    disk points at any more.

    The directory flush is stubbed rather than driven through a real POSIX
    filesystem. Pretending ``os.name`` is ``posix`` on Windows looks
    harmless and is not: ``pathlib`` chooses its class from ``os.name`` at
    construction, so every ``Path(...)`` inside the writer starts raising
    UnsupportedOperation. The contract worth checking is that the flush is
    attempted after the rename and that its failure is reported rather than
    raised, and that is stub-independent.
    """

    def test_the_temporary_file_is_flushed_to_disk_before_the_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "ledger.json")
            flushed = []
            real_fsync = os.fsync

            def fsync(handle):
                flushed.append(os.fstat(handle).st_mode)
                real_fsync(handle)
            with mock.patch.object(os, "fsync", fsync):
                self.assertTrue(write_json_durably(target, "{}"))

            self.assertTrue([mode for mode in flushed if mode & stat.S_IFREG],
                            "the temporary file's bytes must be fsynced")
            self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_the_parent_directory_is_flushed_after_the_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "ledger.json")
            order = []
            real_replace = Path.replace

            def replace(self, other):
                order.append("rename")
                return real_replace(self, other)

            def flush(path):
                order.append("flush")
            with mock.patch.object(Path, "replace", replace), \
                    mock.patch("desktop_app.storage._fsync_directory", flush):
                write_json_durably(target, "{}")

            self.assertEqual(order, ["rename", "flush"],
                             "the directory entry is only durable after the rename")

    def test_the_directory_flush_is_asked_for_on_every_platform(self) -> None:
        """Windows skips it inside the helper, but the writer still asks."""
        for platform in ("posix", "nt"):
            with self.subTest(platform=platform):
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory, "ledger.json")
                    asked = []
                    with mock.patch("desktop_app.storage._fsync_directory",
                                    side_effect=lambda path: asked.append(path)):
                        write_json_durably(target, "{}")

                    self.assertEqual(asked, [Path(directory)])

    def test_an_unflushed_directory_is_reported_rather_than_raised(self) -> None:
        """The rename has already happened, so raising would undo it.

        A caller mid-deletion that treats this as a failed write rolls back a
        change that is on disk, leaving memory and the file disagreeing.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "ledger.json")
            written = []

            with mock.patch("desktop_app.storage._fsync_directory",
                            side_effect=OSError("no space left on device")):
                written.append(write_json_durably(target, "{}"))

            self.assertEqual(written, [False], "False, not an exception")
            self.assertEqual(target.read_text(encoding="utf-8"), "{}",
                             "the contents are on disk either way")

    def test_a_failure_before_the_rename_still_raises(self) -> None:
        """Nothing reached the file, so the caller has to be told loudly."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "ledger.json")

            def refuse(handle):
                raise OSError("no space left on device")
            with mock.patch.object(os, "fsync", refuse):
                with self.assertRaises(OSError):
                    write_json_durably(target, "{}")

            self.assertFalse(target.exists())

    def test_a_refused_rename_is_retried_rather_than_reported(self) -> None:
        """Windows refuses the replace whenever anything else holds the file.

        Replacing a file goes through MoveFileEx, which takes DELETE access
        on the target, and CPython opens files without FILE_SHARE_DELETE.
        So any other handle on the same path loses, and settings are
        written from the UI thread and from the runtime's own. Measured on
        this machine, six concurrent writers had four refused before the
        retry existed. Nothing has reached the target at this point, so the
        write has not happened yet rather than having gone wrong, and the
        content is the same on every attempt.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "settings.json")
            calls = []
            real_replace = Path.replace
            refusals = [PermissionError("Access is denied")] * 3

            def replace(self, other):
                calls.append(1)
                if refusals:
                    raise refusals.pop(0)
                return real_replace(self, other)

            with mock.patch.object(Path, "replace", replace):
                write_json_durably(target, '{"written": true}')

            self.assertEqual(len(calls), 4, "three refusals then the real move")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                             {"written": True})

    def test_a_rename_that_is_always_refused_is_given_up_on(self) -> None:
        """Retrying forever would hang a caller who needs to know.

        If something holds the file for good, the write genuinely cannot
        land, and silence would leave the settings and the file disagreeing.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "settings.json")
            calls = []

            def replace(self, other):
                calls.append(1)
                raise PermissionError("Access is denied")

            with mock.patch.object(Path, "replace", replace), \
                    mock.patch("desktop_app.storage._REPLACE_PAUSE", 0):
                with self.assertRaises(PermissionError):
                    write_json_durably(target, "{}")

            self.assertEqual(len(calls), storage_module._REPLACE_ATTEMPTS,
                             "it should stop at the attempt limit, not sooner")
            self.assertFalse(target.exists())

    def test_concurrent_durable_writes_all_land(self) -> None:
        """The case that produced the flake: two threads, one settings file.

        A reader that holds the file for good cannot be satisfied, but two
        writers each hold it for a moment, and neither should lose.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "settings.json")
            target.write_text("{}", encoding="utf-8")
            writers = 4
            rounds = 20
            refused = []
            barrier = threading.Barrier(writers)

            def writer(number):
                barrier.wait()
                for _ in range(rounds):
                    try:
                        write_json_durably(target, json.dumps({"writer": number}))
                    except PermissionError as exc:
                        refused.append(exc)

            threads = [threading.Thread(target=writer, args=(n,))
                       for n in range(writers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

            self.assertEqual(refused, [], f"{len(refused)} writes were refused")
            json.loads(target.read_text(encoding="utf-8"))

    def test_removing_the_final_record_flushes_the_directory(self) -> None:
        """A lost unlink would resurrect a ledger of finished work for ever."""
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())
            storage.record_owed_credentials(["relay:guest"], "member")
            self.assertTrue(storage.ledger_path.exists())
            asked = []

            with mock.patch("desktop_app.storage._fsync_directory",
                            side_effect=lambda path: asked.append(path)):
                storage.discard_pending_cleanup(
                    {"type": "credentials", "workspace": None, "source": "member"})

            self.assertFalse(storage.ledger_path.exists())
            self.assertEqual(asked, [Path(directory)])


class PendingCleanupRecordTests(unittest.TestCase):
    """The ledger is read before anything else, so it has to be survivable."""

    def write(self, storage, records):
        storage.ledger_path.write_text(json.dumps(records), encoding="utf-8")

    def test_a_scope_that_is_not_a_string_is_dropped(self) -> None:
        """re.fullmatch raises TypeError on one, and that aborts start-up.

        Every other malformed field here is dropped rather than acted on, so
        a scope of the wrong type must be too.
        """
        for scope in (5, ["x"], {"a": 1}, True, 1.5):
            with self.subTest(scope=scope):
                with tempfile.TemporaryDirectory() as directory:
                    storage = Storage(directory, FakeBackend())
                    self.write(storage, [{"type": "credentials", "workspace": scope,
                                          "source": "reset", "credentials": ["relay:guest"]}])

                    self.assertEqual(storage.read_pending_cleanup(), [])

    def test_a_workspace_id_that_is_not_a_string_is_dropped(self) -> None:
        for bad in (5, ["x"], {"a": 1}, True):
            with self.subTest(id=bad):
                with tempfile.TemporaryDirectory() as directory:
                    storage = Storage(directory, FakeBackend())
                    self.write(storage, [{"type": "workspace", "id": bad, "kind": "local",
                                          "credentials": ["remote-token"]}])

                    self.assertEqual(storage.read_pending_cleanup(), [])

    def test_a_string_scope_still_has_to_match_the_id_pattern(self) -> None:
        for scope in ("workspace-../../etc", "C:/Windows", "workspace-" + "z" * 32, ""):
            with self.subTest(scope=scope):
                with tempfile.TemporaryDirectory() as directory:
                    storage = Storage(directory, FakeBackend())
                    self.write(storage, [{"type": "credentials", "workspace": scope,
                                          "source": "reset", "credentials": ["relay:guest"]}])

                    self.assertEqual(storage.read_pending_cleanup(), [])

    def test_the_local_workspace_is_a_valid_scope_and_id(self) -> None:
        """It shares the root, so it has no id of its own but is legitimate."""
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())
            self.write(storage, [
                {"type": "workspace", "id": "local", "kind": "local",
                 "credentials": ["remote-token"]},
                {"type": "credentials", "workspace": None, "source": "reset",
                 "credentials": ["relay:guest"]},
                {"type": "credentials", "workspace": "workspace-" + "a" * 32,
                 "source": "member", "credentials": ["relay:writer"]}])

            self.assertEqual(len(storage.read_pending_cleanup()), 3)

    def test_a_second_failure_merges_instead_of_displacing_the_first(self) -> None:
        """Two removals share a key, so replacing would strand the first."""
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())

            storage.record_owed_credentials(["relay:guest1", "provider:guest1"], "member")
            storage.record_owed_credentials(["relay:guest2", "provider:guest2"], "member")

            records = storage.read_pending_cleanup()
            self.assertEqual(len(records), 1)
            self.assertEqual(sorted(records[0]["credentials"]),
                             ["provider:guest1", "provider:guest2",
                              "relay:guest1", "relay:guest2"])

    def test_merging_leaves_other_workspaces_and_sources_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())

            storage.record_owed_credentials(["relay:writer"], "reset")
            storage.record_owed_credentials(["relay:guest1"], "member")
            storage.record_owed_credentials(["relay:guest2"], "member")

            records = {r["source"]: sorted(r["credentials"]) for r in storage.read_pending_cleanup()}
            self.assertEqual(records, {"reset": ["relay:writer"],
                                       "member": ["relay:guest1", "relay:guest2"]})

    def test_merging_does_not_repeat_a_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())

            storage.record_owed_credentials(["relay:guest"], "member")
            storage.record_owed_credentials(["relay:guest"], "member")

            self.assertEqual(storage.read_pending_cleanup()[0]["credentials"], ["relay:guest"])

    def test_a_duplicate_record_from_a_hand_edited_file_is_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())
            self.write(storage, [
                {"type": "credentials", "workspace": None, "source": "member",
                 "credentials": ["relay:guest1"]},
                {"type": "credentials", "workspace": None, "source": "member",
                 "credentials": ["relay:guest2"]}])

            storage.record_owed_credentials(["relay:guest3"], "member")

            records = storage.read_pending_cleanup()
            self.assertEqual(len(records), 1)
            self.assertEqual(sorted(records[0]["credentials"]),
                             ["relay:guest1", "relay:guest2", "relay:guest3"])

    def test_narrowing_still_wins_over_merging(self) -> None:
        """The retry reports what is left; merging there would restore the rest.

        If narrowing merged, every pass would put back the names it had just
        deleted and a record could never be finished.
        """
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, FakeBackend())
            storage.record_owed_credentials(["relay:guest1", "relay:guest2"], "member")

            storage.replace_owed_credentials(None, "member", ["relay:guest2"])

            self.assertEqual(storage.read_pending_cleanup()[0]["credentials"], ["relay:guest2"])
            storage.replace_owed_credentials(None, "member", [])
            self.assertEqual(storage.read_pending_cleanup(), [])


if __name__ == "__main__":
    unittest.main()