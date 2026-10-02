"""Nonsecret preferences on disk; tokens and API keys in the OS credential store."""
import json
import os
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


class Storage:
    def __init__(self, directory=None, vault=None):
        self.directory = Path(directory) if directory else default_data_directory()
        self.vault = vault if vault is not None else Vault()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "settings.json"
        self.settings = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.settings, dict):
            raise ValueError("Desktop settings file must contain an object")

    def save(self):
        temporary = self.directory / "settings.json.tmp"
        temporary.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def credentials(self):
        result = {}
        for agent_id, group in self.settings.get("identities", {}).items():
            token = self.vault.get("relay:" + agent_id)
            if not token:
                raise RuntimeError("A saved workspace token is missing from the OS credential store")
            result[agent_id] = {"token": token, "group": group}
        return result

    def save_credentials(self, credentials):
        for agent_id, config in credentials.items():
            self.vault.set("relay:" + agent_id, config["token"])
        self.settings["identities"] = {agent_id: config["group"] for agent_id, config in credentials.items()}
        self.save()
