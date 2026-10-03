"""Nonsecret preferences on disk; tokens and API keys in the OS credential store."""
import json
import os
import threading
from pathlib import Path


def default_data_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MultiplayerAI"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "multiplayer-ai"


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
        """Best-effort removal; absent entries and locked stores are fine.

        Used when discarding an identity or a workspace. The point is to
        stop this device referring to it, and the caller removes its own
        bookkeeping regardless, so a store that refuses deletion must not
        abort the cleanup. Returns whether an entry was actually removed.
        """
        try:
            self.backend.delete_password("MultiplayerAI", name)
            return True
        except Exception:
            return False


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
        if hasattr(self.vault, "delete"):
            self.vault.delete(self.prefix + name)


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

        Called only once a replacement credential has been stored, so a
        store that refuses the deletion costs nothing: the ids are
        already unused. Vault deletion is best effort, so the mapping is
        pruned either way. The device's own id is normally not passed
        in, because the vault entry for it now holds the new token.
        """
        identities = self.settings.get("identities")
        removed = []
        for agent_id in agent_ids:
            self.vault.delete("relay:" + agent_id)
            if isinstance(identities, dict):
                identities.pop(agent_id, None)
            removed.append(agent_id)
        self.dropped_identities = []
        if removed:
            self.save()
        return removed

    def save_credentials(self, credentials):
        for agent_id, config in credentials.items():
            self.vault.set("relay:" + agent_id, config["token"])
        self.settings["identities"] = {agent_id: config["group"] for agent_id, config in credentials.items()}
        self.save()
