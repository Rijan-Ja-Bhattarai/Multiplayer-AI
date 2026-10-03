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


def _fsync_directory(directory):
    """Persist a directory entry, so a completed rename survives a power cut.

    The rename is only durable once the directory entry naming the new file
    is on disk too. Windows will not open a directory for this, and does not
    need it, so there it is a no-op.

    Errors are not suppressed. The data is already written by the time this
    runs, so a failure means the filesystem cannot promise durability, and
    saying so is better than quietly continuing to claim it.
    """
    if os.name == "nt":
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def write_json_durably(path, text):
    """Write a document so a reader sees either the old one or the new one.

    A plain write truncates first, so a crash mid-write leaves a file that
    parses as nothing at all. Writing a temporary file and renaming over the
    target makes the swap atomic; flushing the temporary file to disk first
    makes its contents durable, and the directory flush afterwards makes the
    rename durable. All three are needed: without any one of them a power cut
    can leave the previous contents or no file at all, which for the cleanup
    ledger means credentials that nothing points at any more.

    Returns whether the directory was flushed. A failure *before* the rename
    raises, because nothing reached the file and the caller has to know. A
    failure *after* it is reported instead of raised, because from that point
    the new contents are already what every reader sees and they survive the
    process crashing; only the machine losing power could lose them. Raising
    there is not merely disproportionate, it is actively harmful to a caller
    mid-deletion, which would roll back a change that has already happened.

    Returns:
        ``True`` when the directory was flushed, ``False`` when the contents
        were replaced but that could not be confirmed.
    """
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    try:
        _fsync_directory(path.parent)
    except OSError:
        return False
    return True


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

# The id a saved workspace may carry. Kept here so the catalog, the cleanup
# ledger and the directory check cannot drift apart, since the value is
# validated in all three and used to build a path in one.
WORKSPACE_ID = re.compile(r"workspace-[0-9a-f]{32}")

# The local workspace shares the root directory and settings, so it has no
# id of its own and no directory to remove.
LOCAL_WORKSPACE = "local"


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
            write_json_durably(self.path, json.dumps(self.settings, indent=2))

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

    def forget_identities(self, agent_ids, source="reset"):
        """Delete these identities from the vault and the settings mapping.

        Called only once a replacement credential has been stored, so the
        superseded tokens are already unused and losing them costs nothing.
        The settings mapping is pruned either way, but the credential names
        that could not be deleted are both returned and recorded, so the
        caller can tell the user instead of assuming the cleanup finished.
        The device's own id is normally not passed in, because the vault entry
        for it now holds the new token.

        The record is written here, before returning, and not by the caller.
        Everything the caller does next can be cancelled: the reset replaces
        the identities and then awaits, so a write made after that await can
        be lost along with the notice that would have explained it, leaving a
        token that nothing on disk points at and nothing will ever retry.

        Args:
            agent_ids: the identities to forget.
            source: what asked for the removal, used to key the record so one
                removal does not displace another's.

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
        self.record_owed_credentials(["relay:" + agent_id for agent_id in undeleted], source)
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

    @staticmethod
    def _readable(record):
        """Whether a stored record is well formed enough to act on.

        Every field that reaches a pattern, a path or a credential-store call
        is type-checked first. ``re.fullmatch`` raises TypeError on anything
        that is not a string, and a TypeError escaping here would abort
        start-up, which is the opposite of how every other malformed field in
        this file is handled: dropped.
        """
        if not isinstance(record, dict) or record.get("type") not in ("workspace", "credentials"):
            return False
        if record["type"] == "workspace":
            workspace = record.get("id")
            if not (workspace == LOCAL_WORKSPACE
                    or (isinstance(workspace, str) and bool(WORKSPACE_ID.fullmatch(workspace)))):
                return False
            return isinstance(record.get("data_owed", False), bool)
        # None means the root vault, which is also what a missing key means.
        scope = record.get("workspace")
        return ((scope is None or (isinstance(scope, str)
                                   and bool(WORKSPACE_ID.fullmatch(scope))))
                and isinstance(record.get("source"), str))

    def read_pending_cleanup(self):
        """Outstanding cleanup, ignoring anything malformed.

        A record is a best-effort cleanup instruction, so a corrupt or
        hand-edited file is dropped rather than allowed to block startup.
        """
        if not self.ledger_path.exists():
            return []
        try:
            records = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(records, list):
            return []
        return [record for record in records if self._readable(record)]

    def write_pending_cleanup(self, records):
        """Written before the change it describes, and after each attempt."""
        write_json_durably(self.ledger_path, json.dumps(records, indent=2))

    def record_workspace_deletion(self, entry, credentials, root_credentials=(), data_owed=False):
        """Record a workspace's removal before anything is destroyed.

        The names are copied in because the settings that name them live in
        the directory the deletion is about to remove. Root names are kept
        apart from the workspace's own, since the pre-catalog layout kept
        the invitation token and provider key in the root settings and
        those are deleted through the root vault.

        ``data_owed`` records that the workspace directory itself is still
        there. Without it a record holding no names is ambiguous between a
        stranded chat archive and a catalog entry that could not be flushed,
        and those need different things said about them.
        """
        key = {"type": "workspace", "id": entry["id"]}
        records = [r for r in self.read_pending_cleanup() if not self._matches(r, key)]
        records.append({"type": "workspace", "id": entry["id"], "kind": entry["kind"],
                        "url": entry.get("url"), "credentials": list(credentials),
                        "root_credentials": list(root_credentials), "data_owed": bool(data_owed)})
        self.write_pending_cleanup(records)

    def record_owed_credentials(self, credentials, source):
        """Record names that could not be deleted, keyed by workspace and source.

        Merged into whatever is already outstanding for the same key rather
        than replacing it. Two removals that fail on different members share
        a key, so a second failure that displaced the first member's names
        would leave them out of the ledger for good, with nothing else
        pointing at them. Unrelated records are left untouched.

        This is the opposite of the two narrowing methods below, and
        deliberately so: those report what is still owed after an attempt,
        where merging would put back the names the attempt just deleted.
        """
        scope = self.vault_scope
        names = list(dict.fromkeys(credentials))
        if not names:
            return
        key = {"type": "credentials", "workspace": scope, "source": source}
        records = self.read_pending_cleanup()
        if not any(self._matches(r, key) for r in records):
            records.append({"type": "credentials", "workspace": scope, "source": source,
                            "credentials": names})
        else:
            # Re-read through _owed, so a name that could not have been minted
            # here is not carried forward by the merge. Every matching record
            # contributes, because a hand-edited file can hold the same key
            # twice and dropping the second must not drop what it owed.
            owed = []
            for record in records:
                if self._matches(record, key):
                    owed.extend(self._owed(record, "credentials"))
            union = list(dict.fromkeys(owed + names))[:MAX_OWED_NAMES]
            kept = False
            updated = []
            for record in records:
                if not self._matches(record, key):
                    updated.append(record)
                elif not kept:
                    # The union lands in the first and the duplicates go, so
                    # a name is never owed twice.
                    kept = True
                    updated.append(dict(record, credentials=union))
            records = updated
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
            # The directory flush matters here too: without it the unlink can
            # be lost, leaving a ledger that claims work which is finished and
            # so is retried for ever.
            self.ledger_path.unlink()
            _fsync_directory(self.ledger_path.parent)
