"""Nonsecret preferences on disk; tokens and API keys in the OS credential store."""
import json
import os
import re
import threading
from pathlib import Path


def default_data_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MultiplayerAI"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "multiplayer-ai"


# Outcome of a credential-store deletion. A caller retrying a cleanup
# needs to tell "it was never there" apart from "the store would not let
# me", and a bare boolean erases that difference.
REMOVED = "removed"
ABSENT = "absent"
UNAVAILABLE = "unavailable"

# The two outcomes that mean the entry is gone, and the caller has what it
# wanted. Only UNAVAILABLE is worth retrying or reporting.
GONE = (REMOVED, ABSENT)

# The only credential names this app mints: the bare legacy invitation
# token, and one provider or relay entry per agent id. Agent ids, profile
# ids and member names are all validated as [A-Za-z0-9_-]{1,64} when they
# are accepted, so matching that shape here cannot orphan an entry the
# code created.
CREDENTIAL_NAME = re.compile(r"(?:remote-token|(?:relay|provider):[A-Za-z0-9_-]{1,64})")

# Bounds a hand-edited or corrupt ledger from turning one launch into an
# unbounded number of credential-store round trips.
MAX_OWED_NAMES = 512


class Vault:
    def __init__(self):
        import keyring
        self.backend = keyring

    def get(self, name):
        try:
            return self.backend.get_password("MultiplayerAI", name)
        except Exception:
            raise RuntimeError("The OS credential store is unavailable. Unlock your desktop keyring and restart the app.") from None

    def set(self, name, value):
        try:
            self.backend.set_password("MultiplayerAI", name, value)
        except Exception:
            raise RuntimeError("Could not save credentials to the OS credential store.") from None

    def delete(self, name):
        """Report whether the entry is now gone, not merely that a call failed.

        Every backend raises ``PasswordDeleteError`` when there is nothing
        to delete, which is the result the caller wanted, so that counts as
        success. Anything else means the store refused the operation, which
        is the only case worth retrying or telling the user about.
        """
        try:
            self.backend.delete_password("MultiplayerAI", name)
            return REMOVED
        except self.backend.errors.PasswordDeleteError:
            return ABSENT
        except Exception:
            return UNAVAILABLE


class WorkspaceVault:
    """A view of the vault that namespaces entries by workspace.

    Lets several workspaces keep a ``provider`` entry under the same name
    without overwriting each other, while sharing one OS credential store.
    """

    def __init__(self, vault, workspace_id):
        self.vault = vault
        self.prefix = workspace_id + ":"

    def get(self, name):
        return self.vault.get(self.prefix + name)

    def set(self, name, value):
        self.vault.set(self.prefix + name, value)

    def delete(self, name):
        """Namespaced delete, reporting the underlying outcome.

        The inner result is passed through rather than dropped, so a caller
        cleaning up a workspace can tell a finished cleanup from a locked
        credential store.
        """
        if not hasattr(self.vault, "delete"):
            return ABSENT
        return self.vault.delete(self.prefix + name)


class Storage:
    def __init__(self, directory=None, vault=None, root=None):
        self.directory = Path(directory) if directory else default_data_directory()
        self.vault = vault if vault is not None else Vault()
        # The local root. A workspace-scoped Storage points back at it so the
        # cleanup ledger is always written beside the settings that outlive
        # any one workspace, rather than into a directory that is about to be
        # deleted along with the record itself.
        self.root = root if root is not None else self
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "settings.json"
        self.lock = threading.RLock()
        self.settings = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.settings, dict):
            raise ValueError("Desktop settings file must contain an object")
        # Identities whose token was missing from the credential store on
        # the last start, so the runtime can tell the user.
        self.dropped_identities = []

    @property
    def vault_scope(self):
        """The workspace whose names this vault holds, or None for the root.

        Recorded alongside owed credentials so a later attempt knows which
        vault to delete from: the same name means a different entry under a
        ``WorkspaceVault``.
        """
        prefix = getattr(self.vault, "prefix", None)
        return prefix[:-1] if isinstance(prefix, str) and prefix.endswith(":") else None

    @property
    def ledger_path(self):
        return self.root.directory / "pending-deletions.json"

    def save(self):
        with self.lock:
            temporary = self.directory / "settings.json.tmp"
            temporary.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            temporary.replace(self.path)

    def credentials(self):
        """Tokens for every saved identity.

        An identity whose token has gone from the credential store is
        dropped and forgotten rather than raising. The token cannot be
        recovered, so refusing to start left the user with no way forward
        except deleting this settings file by hand. Dropping it lets the
        runtime mint a replacement for the device identity, which is what
        makes the recovery automatic.

        A credential store that is *unavailable* still raises, from
        ``Vault.get``: a replacement token could not be written either,
        so the app genuinely cannot continue.

        Dropped ids are recorded on ``dropped_identities`` for the caller
        to report, because a workspace joined with one of them needs a new
        invitation.
        """
        result = {}
        dropped = []
        for agent_id, group in self.settings.get("identities", {}).items():
            token = self.vault.get("relay:" + agent_id)
            if not token:
                dropped.append(agent_id)
                continue
            result[agent_id] = {"token": token, "group": group}
        self.dropped_identities = dropped
        if dropped:
            identities = self.settings.get("identities")
            if isinstance(identities, dict):
                for agent_id in dropped:
                    identities.pop(agent_id, None)
            self.save()
        return result

    def forget_identities(self, agent_ids):
        """Delete these identities from the vault and the settings mapping.

        Called only once a replacement credential has been stored, so the
        superseded tokens are already unused and losing them costs nothing.
        The settings mapping is pruned either way, but the credential names
        that could not be deleted are returned so the caller can tell the
        user instead of assuming the cleanup finished. The device's own id
        is normally not passed in, because the vault entry for it now
        holds the new token.

        Returns:
            ``(removed, undeleted)`` - the ids pruned from settings, and
            those whose vault entry is still present.
        """
        identities = self.settings.get("identities")
        removed = []
        undeleted = []
        for agent_id in agent_ids:
            if self.vault.delete("relay:" + agent_id) == UNAVAILABLE:
                undeleted.append(agent_id)
            else:
                removed.append(agent_id)
            if isinstance(identities, dict):
                identities.pop(agent_id, None)
        self.dropped_identities = []
        if removed:
            self.save()
        return removed, undeleted

    def save_credentials(self, credentials):
        for agent_id, config in credentials.items():
            self.vault.set("relay:" + agent_id, config["token"])
        self.settings["identities"] = {agent_id: config["group"] for agent_id, config in credentials.items()}
        self.save()

    # --- credentials that could not be deleted ---------------------------
    #
    # Removing an identity, a member or a workspace destroys a relay token
    # and its chat archive. If the credential store is locked at that
    # moment, the entry survives with nothing on disk pointing at it, so a
    # ledger records the exact names still owed. It lives beside the root
    # settings rather than inside any workspace, because a workspace
    # deletion removes its own directory.
    #
    # The ledger is a best-effort cleanup instruction, so it is written
    # atomically and read defensively: a corrupt file is dropped rather
    # than allowed to block startup. It is not trusted to name an arbitrary
    # entry, because that would let a hand-edited file delete a credential
    # the app is still using.

    def _owed(self, record, key):
        """The names a record claims, kept only if they could be minted here."""
        if not isinstance(record, dict):
            return []
        names = record.get(key, [])
        if not isinstance(names, list):
            return []
        wanted = (name for name in names
                  if isinstance(name, str) and CREDENTIAL_NAME.fullmatch(name))
        return list(dict.fromkeys(wanted))[:MAX_OWED_NAMES]

    @staticmethod
    def _matches(record, key):
        """Whether a stored record is the one a key names.

        A workspace deletion is identified by the workspace, and a
        credential cleanup by the workspace plus what asked for it.
        """
        if record.get("type") != key["type"]:
            return False
        if key["type"] == "workspace":
            return record.get("id") == key["id"]
        return record.get("workspace") == key["workspace"] and record.get("source") == key["source"]

    def read_pending_cleanup(self):
        """Outstanding cleanup, ignoring anything malformed."""
        if not self.ledger_path.exists():
            return []
        try:
            records = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(records, list):
            return []
        return [record for record in records
                if isinstance(record, dict) and record.get("type") in ("workspace", "credentials")]

    def write_pending_cleanup(self, records):
        """Written before the change it describes, and after each attempt."""
        temporary = self.ledger_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.replace(self.ledger_path)

    def record_workspace_deletion(self, entry, credentials, root_credentials=()):
        """Record a workspace's removal before anything is destroyed.

        The names are copied in because the settings that name them live in
        the directory the deletion is about to remove. Root names are kept
        apart from the workspace's own, since the pre-catalog layout kept
        the invitation token and provider key in the root settings and
        those are deleted through the root vault.
        """
        key = {"type": "workspace", "id": entry["id"]}
        records = [r for r in self.read_pending_cleanup() if not self._matches(r, key)]
        records.append({"type": "workspace", "id": entry["id"], "kind": entry["kind"],
                        "url": entry.get("url"), "credentials": list(credentials),
                        "root_credentials": list(root_credentials)})
        self.write_pending_cleanup(records)

    def record_owed_credentials(self, credentials, source):
        """Record names that could not be deleted, keyed by workspace and source.

        One record per source per workspace, so repeated failures do not
        accumulate duplicates for the same removal.
        """
        scope = self.vault_scope
        names = list(dict.fromkeys(credentials))
        if not names:
            return
        key = {"type": "credentials", "workspace": scope, "source": source}
        records = [r for r in self.read_pending_cleanup() if not self._matches(r, key)]
        records.append({"type": "credentials", "workspace": scope, "source": source,
                        "credentials": names})
        self.write_pending_cleanup(records)

    def replace_owed_credentials(self, scope, source, credentials):
        """Narrow an existing record to what is still owed, or drop it."""
        key = {"type": "credentials", "workspace": scope, "source": source}
        if not credentials:
            self.discard_pending_cleanup(key)
            return
        records = [dict(r, credentials=list(credentials)) if self._matches(r, key) else r
                   for r in self.read_pending_cleanup()]
        self.write_pending_cleanup(records)

    def discard_pending_cleanup(self, key):
        records = self.read_pending_cleanup()
        remaining = [r for r in records if not self._matches(r, key)]
        if len(remaining) == len(records):
            return
        if remaining:
            self.write_pending_cleanup(remaining)
        elif self.ledger_path.exists():
            self.ledger_path.unlink()
