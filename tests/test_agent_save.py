"""Failure and restart coverage for journaled model and search credential saves."""
import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage, UNAVAILABLE, WorkspaceVault
from desktop_app.workspace_runtime import WorkspaceRuntime
from tests.test_desktop_runtime import MemoryVault


class AgentSaveTests(unittest.TestCase):
    """Exercise persistence boundaries without starting model or relay connections."""

    def setUp(self):
        """Create a private storage directory and a controllable credential store."""
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.storage = Storage(self.directory.name, self.vault)
        self.profile = {"id": "researcher", "provider": "bionic", "model": "test-model",
                        "base_url": "https://model.example/v1", "web_search": "auto",
                        "search_provider": "ollama", "autostart": False}
        self.credentials = {"researcher": {"token": "private-relay-token", "group": "workspace"}}
        self.storage.settings = {"workspace_name": "Research"}
        self.storage.save()

    def tearDown(self):
        """Remove test preferences and journals after each isolated failure case."""
        self.directory.cleanup()

    def save(self, profile=None, **kwargs):
        """Save both independent API keys with the model's relay ownership metadata."""
        return self.storage.save_agent_profile(profile or self.profile, self.credentials,
                                               "private-model-key", "private-search-key", **kwargs)

    def reopen(self):
        """Load persisted preferences independently of the original in-memory state."""
        return Storage(self.directory.name, self.vault)

    def test_each_new_agent_vault_failure_rolls_back_all_keys_and_profile(self):
        """Backup, model, search, and relay failures leave no unowned credentials."""
        original = self.vault.set
        for failed in ("agent-save:", "provider:researcher", "search:researcher", "relay:researcher"):
            with self.subTest(failed=failed):
                fired = False

                def fail_once(name, value):
                    """Fail one selected write after storing it, modeling an ambiguous backend error."""
                    nonlocal fired
                    if not fired and name.startswith(failed):
                        fired = True
                        original(name, value)
                        raise RuntimeError("Credential store refused the update")
                    original(name, value)

                with patch.object(self.vault, "set", fail_once), self.assertRaises(RuntimeError):
                    self.save()
                self.assertTrue(fired)
                self.assertEqual(self.vault.values, {})
                self.assertEqual(self.reopen().settings, {"workspace_name": "Research"})
                self.assertFalse(self.storage.agent_save_path.exists())
                self.assertEqual(self.storage.read_pending_cleanup(), [])

    def test_profile_persistence_failure_restores_credentials_and_memory(self):
        """A refused settings write restores an existing profile and both original keys."""
        self.save()
        before = self.reopen().settings
        keys = dict(self.vault.values)
        updated = {**self.profile, "model": "changed-model"}
        with patch.object(self.storage, "save", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.storage.save_agent_profile(updated, self.credentials, "new-model-key", "new-search-key")
        self.assertEqual(self.storage.settings, before)
        self.assertEqual(self.reopen().settings, before)
        self.assertEqual(self.vault.values, keys)

    def test_edit_search_write_failure_restores_the_previous_model_key(self):
        """The second API-key failure cannot pair a changed model key with old settings."""
        self.save()
        before = self.reopen().settings
        original = self.vault.set

        def reject_search(name, value):
            """Reject the new search key while permitting compensating writes of old values."""
            if name == "search:researcher" and value == "new-search-key":
                raise RuntimeError("Search key could not be stored")
            original(name, value)

        with patch.object(self.vault, "set", reject_search), self.assertRaises(RuntimeError):
            self.storage.save_agent_profile({**self.profile, "model": "changed-model"}, self.credentials,
                                            "new-model-key", "new-search-key")
        self.assertEqual(self.vault.get("provider:researcher"), "private-model-key")
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")
        self.assertEqual(self.reopen().settings, before)

    def test_interrupted_save_before_profile_persistence_is_recovered_after_restart(self):
        """A retained journal names new keys even when no identity or profile was committed."""
        original = self.vault.set

        def reject_relay_and_lock_rollback(name, value):
            """Fail new relay creation after the search write and prevent immediate cleanup."""
            if name == "relay:researcher":
                raise RuntimeError("Relay key unavailable")
            original(name, value)

        with patch.object(self.vault, "set", reject_relay_and_lock_rollback), patch.object(
                self.vault, "delete", return_value=UNAVAILABLE):
            with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
                self.save()
        restarted = self.reopen()
        self.assertNotIn("identities", restarted.settings)
        self.assertNotIn("agents", restarted.settings)
        self.assertIn("search:researcher", restarted.agent_save_credential_names())
        restarted.recover_agent_save()
        self.assertEqual(self.vault.values, {})
        self.assertFalse(restarted.agent_save_path.exists())

    def test_interrupted_edit_replays_rollback_before_loading_the_profile(self):
        """Locked compensating writes preserve the backup until a later launch can restore it."""
        self.save()
        before = self.reopen().settings
        original = self.vault.set

        def lock_after_model_write(name, value):
            """Allow one changed model key, then refuse search and rollback writes."""
            if name == "search:researcher" or (name == "provider:researcher" and value == "private-model-key"):
                raise RuntimeError("Credential store locked")
            original(name, value)

        with patch.object(self.vault, "set", lock_after_model_write), self.assertRaisesRegex(
                RuntimeError, "recovery is pending"):
            self.storage.save_agent_profile({**self.profile, "model": "changed-model"}, self.credentials,
                                            "new-model-key", "new-search-key")
        restarted = self.reopen()
        restarted.recover_agent_save()
        self.assertEqual(restarted.settings, before)
        self.assertEqual(self.vault.get("provider:researcher"), "private-model-key")
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")
        self.assertFalse(restarted.agent_save_path.exists())

    def test_restart_after_profile_commit_keeps_the_new_credentials(self):
        """A crash after the settings swap must not roll back a committed key pair."""
        with patch.object(self.storage, "_finish_agent_save", side_effect=SystemExit("Interrupted")):
            with self.assertRaises(SystemExit):
                self.save()
        restarted = self.reopen()
        restarted.recover_agent_save()
        self.assertEqual(restarted.settings["agents"], [self.profile])
        self.assertEqual(self.vault.get("provider:researcher"), "private-model-key")
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")
        self.assertEqual(set(self.vault.values), {"provider:researcher", "search:researcher", "relay:researcher"})

    def test_backup_deletion_failure_is_owned_by_the_root_cleanup_ledger(self):
        """A scoped backup survives directory loss with an exact namespace for cleanup retry."""
        scope = "workspace-" + "a" * 32
        root = self.storage
        self.storage = Storage(Path(self.directory.name) / "workspaces" / scope,
                               WorkspaceVault(self.vault, scope), root=root)
        original = self.vault.delete

        def lock_backup(name):
            """Refuse backup deletion while permitting normal canonical-key operations."""
            return UNAVAILABLE if ":agent-save:" in name else original(name)

        with patch.object(self.vault, "delete", lock_backup):
            self.save()
        records = root.read_pending_cleanup()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["workspace"], scope)
        self.assertEqual(len(records[0]["credentials"]), 1)
        backup = records[0]["credentials"][0]
        self.assertTrue(backup.startswith("agent-save:"))
        self.assertFalse(self.storage.agent_save_path.exists())
        runtime = DesktopRuntime(root)
        runtime.retry_pending_cleanup()
        self.assertIsNone(self.vault.get(scope + ":" + backup))
        self.assertEqual(root.read_pending_cleanup(), [])
        self.assertEqual(self.storage.vault.get("search:researcher"), "private-search-key")

    def test_journal_failure_prevents_all_vault_writes(self):
        """The ownership record must be durable before even a backup is stored."""
        with patch("desktop_app.storage.write_json_durably", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(self.vault.values, {})

    def test_pending_journal_and_settings_never_contain_secrets(self):
        """Only the OS vault holds the old keys used for recovery."""
        with patch.object(self.storage, "_finish_agent_save", side_effect=SystemExit("Interrupted")):
            with self.assertRaises(SystemExit):
                self.save()
        text = self.storage.path.read_text() + self.storage.agent_save_path.read_text()
        for secret in ("private-model-key", "private-search-key", "private-relay-token"):
            self.assertNotIn(secret, text)

    def test_corrupt_journal_cannot_name_another_agents_credentials(self):
        """Recovery must refuse a modified journal instead of deleting unrelated vault entries."""
        with patch.object(self.storage, "_finish_agent_save", side_effect=SystemExit("Interrupted")):
            with self.assertRaises(SystemExit):
                self.save()
        record = json.loads(self.storage.agent_save_path.read_text())
        record["credentials"] = ["search:another-agent"]
        self.storage.agent_save_path.write_text(json.dumps(record))
        self.vault.set("search:another-agent", "unrelated-key")
        with self.assertRaisesRegex(RuntimeError, "pending model save"):
            self.reopen().recover_agent_save()
        self.assertEqual(self.vault.get("search:another-agent"), "unrelated-key")

    def test_locked_recovery_blocks_startup_before_connections_are_created(self):
        """An inconsistent key pair must never be loaded into a running model adapter."""
        self.save()
        original = self.vault.set

        def lock_after_model_write(name, value):
            """Permit a partial edit but refuse the writes needed to complete or undo it."""
            if name == "search:researcher" or (name == "provider:researcher" and value == "private-model-key"):
                raise RuntimeError("Credential store locked")
            original(name, value)

        with patch.object(self.vault, "set", lock_after_model_write):
            with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
                self.storage.save_agent_profile(self.profile, self.credentials, "new-model-key", "new-search-key")
            runtime = WorkspaceRuntime(self.reopen())
            with self.assertRaisesRegex(RuntimeError, "locked"):
                asyncio.run(runtime.start())
        self.assertIsNone(runtime.http)
        self.assertIsNone(runtime.app)
        self.assertIsNone(runtime.server)

    def test_unconfirmed_settings_commit_retains_a_recovery_backup(self):
        """Unconfirmed directory durability preserves the old keys until restart resolves it."""
        original = self.storage.save

        def unconfirmed_save():
            """Persist the new settings but simulate failure to confirm directory durability."""
            original()
            return False

        with patch.object(self.storage, "save", unconfirmed_save):
            self.assertFalse(self.save())
        self.assertTrue(self.storage.agent_save_path.exists())
        restarted = self.reopen()
        restarted.recover_agent_save()
        self.assertEqual(restarted.settings["agents"], [self.profile])
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")

    def test_save_with_no_key_updates_does_not_write_to_the_vault(self):
        """Changing only a profile avoids adding an unnecessary credential-store failure."""
        self.save()
        updated = {**self.profile, "model": "changed-model"}
        with patch.object(self.vault, "set", side_effect=RuntimeError("Locked")):
            self.storage.save_agent_profile(updated, self.credentials)
        self.assertEqual(self.reopen().settings["agents"], [updated])

    def test_cleanup_failure_keeps_the_committed_save_successful(self):
        """Post-commit cleanup errors cannot cause the live relay to retain stale ownership."""
        with patch.object(self.storage, "_finish_agent_save", side_effect=OSError("Ledger unavailable")):
            self.assertFalse(self.save())
        self.assertEqual(self.storage.settings["agents"], [self.profile])
        self.assertIn("researcher", self.reopen().settings["identities"])
        self.reopen().recover_agent_save()
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")

    def test_a_later_relay_save_cannot_lose_pending_agent_recovery(self):
        """Invitations and identity resets must recover old model writes before new tokens."""
        original = self.vault.set

        def reject_relay(name, value):
            """Fail the first model's relay write, leaving its partial API keys journaled."""
            if name == "relay:researcher":
                raise RuntimeError("Relay unavailable")
            original(name, value)

        with patch.object(self.vault, "set", reject_relay), patch.object(self.vault, "delete", return_value=UNAVAILABLE):
            with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
                self.save()
        self.storage.save_credentials(self.credentials)
        self.assertEqual(self.vault.get("relay:researcher"), "private-relay-token")
        self.assertIsNone(self.vault.get("search:researcher"))
        self.assertFalse(self.storage.agent_save_path.exists())

    def test_process_exit_at_each_vault_write_is_recovered_from_durable_ownership(self):
        """Restart cleans every possible prefix of a new agent's credential writes."""
        original = self.vault.set
        for interrupted in ("agent-save:", "provider:researcher", "search:researcher", "relay:researcher"):
            with self.subTest(interrupted=interrupted):

                def exit_after_write(name, value):
                    """Terminate after one selected write, before profile ownership is persisted."""
                    original(name, value)
                    if name.startswith(interrupted):
                        raise SystemExit("Process stopped")

                with patch.object(self.vault, "set", exit_after_write), patch.object(
                        self.storage, "recover_agent_save", side_effect=[None, SystemExit("No more execution")]):
                    with self.assertRaises(SystemExit):
                        self.save()
                restarted = self.reopen()
                self.assertEqual(restarted.settings, {"workspace_name": "Research"})
                restarted.recover_agent_save()
                self.assertEqual(self.vault.values, {})
                self.assertFalse(restarted.agent_save_path.exists())

    def test_remote_profile_failure_restores_keys_and_keeps_invitation_token(self):
        """Joined models receive the same rollback guarantees without owning local relay tokens."""
        self.vault.set("remote-token", "invitation-token")
        self.save(remote=True)
        before = self.reopen().settings
        with patch.object(self.storage, "save", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.storage.save_agent_profile({**self.profile, "model": "changed"}, self.credentials,
                                                "new-model-key", "new-search-key", remote=True)
        self.assertEqual(self.reopen().settings, before)
        self.assertEqual(self.vault.get("remote-token"), "invitation-token")
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")
        self.assertIsNone(self.vault.get("relay:researcher"))

    def test_unconfirmed_backup_cleanup_keeps_the_journal_owned(self):
        """Failure to durably hand off a backup cannot leave it outside both cleanup records."""
        with patch.object(self.storage, "record_owed_credentials", return_value=False):
            self.assertFalse(self.save())
        self.assertTrue(self.storage.agent_save_path.exists())
        self.assertEqual(self.storage.read_agent_save()["state"], "finished")
        self.reopen().recover_agent_save()
        self.assertFalse(self.storage.agent_save_path.exists())
        self.assertEqual(self.vault.get("search:researcher"), "private-search-key")

    def test_missing_backup_after_key_writes_blocks_recovery(self):
        """A lost backup must not be mistaken for a save interrupted before any key write."""
        original = self.vault.set

        def exit_after_search(name, value):
            """Stop after a search credential write, leaving the updating journal intact."""
            original(name, value)
            if name == "search:researcher":
                raise SystemExit("Interrupted")

        with patch.object(self.vault, "set", exit_after_search), patch.object(
                self.storage, "recover_agent_save", side_effect=[None, SystemExit("No more execution")]):
            with self.assertRaises(SystemExit):
                self.save()
        self.vault.delete(self.storage.read_agent_save()["backup"])
        restarted = self.reopen()
        with self.assertRaisesRegex(RuntimeError, "missing its credential backup"):
            restarted.recover_agent_save()
        self.assertTrue(restarted.agent_save_path.exists())
