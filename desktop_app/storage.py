"""Nonsecret preferences on disk; tokens and API keys in the OS credential store."""
import json
import os
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
    def __init__(self, directory=None, vault=None):
        self.directory = Path(directory) if directory else default_data_directory()
        self.vault = vault if vault is not None else Vault()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "settings.json"
        self.lock = threading.RLock()
        self.settings = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.settings, dict):
            raise ValueError("Desktop settings file must contain an object")
        # Identities whose token was missing from the credential store on
        # the last start, so the runtime can tell the user.
        self.dropped_identities = []

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
