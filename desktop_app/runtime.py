"""Own saved local and joined workspaces and dispatch commands to the selected one."""
import asyncio
import json
import re
from uuid import uuid4

import httpx

from .storage import Storage, WorkspaceVault
from .workspace_runtime import WorkspaceRuntime, relay_http_url


class DesktopRuntime:
    def __init__(self, storage, emit=lambda event, data: None):
        self.storage = storage
        self.emit = emit
        self.catalog_path = storage.directory / "workspaces.json"
        self.catalog = json.loads(self.catalog_path.read_text(encoding="utf-8")) if self.catalog_path.exists() else {
            "active": "local", "workspaces": [{"id": "local", "name": "My workspace", "kind": "local"}]}
        for entry in self.catalog["workspaces"]:
            if entry["id"] != "local" and not re.fullmatch(r"workspace-[0-9a-f]{32}", entry["id"]):
                raise ValueError("Invalid saved workspace identity")
            if entry["kind"] not in ("local", "remote"):
                raise ValueError("Invalid saved workspace type")
        self.active_workspace_id = self.catalog["active"]
        self.engines = {}
        self.cache = {}
        self.ready = False
        self.closed = False
        self.mutation = asyncio.Lock()

    @property
    def engine(self):
        return self.engines[self.active_workspace_id]

    @property
    def http(self):
        return self.engine.http

    @http.setter
    def http(self, value):
        self.engine.http = value

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.engine, name)

    def entry(self, workspace_id=None):
        return next(item for item in self.catalog["workspaces"] if item["id"] == (workspace_id or self.active_workspace_id))

    def scoped_storage(self, entry):
        if entry["id"] == "local":
            return self.storage
        return Storage(self.storage.directory / "workspaces" / entry["id"], WorkspaceVault(self.storage.vault, entry["id"]))

    def save_catalog(self):
        temporary = self.catalog_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.catalog, indent=2), encoding="utf-8")
        temporary.replace(self.catalog_path)

    def publish_catalog(self):
        self.emit("workspaces", {"workspaces": [dict(entry) for entry in self.catalog["workspaces"]],
                                 "active": self.active_workspace_id})

    def forward(self, workspace_id, event, data):
        self.cache.setdefault(workspace_id, {})[event] = data
        if event == "workspace_info":
            entry = self.entry(workspace_id)
            if entry["kind"] == "remote" and self.engines[workspace_id].remote and entry["name"] != data["name"]:
                entry["name"] = data["name"]
                self.save_catalog()
                if self.ready:
                    self.publish_catalog()
        if workspace_id != self.active_workspace_id:
            if event in ("incoming", "incoming_reply"):
                store = self.engines[workspace_id].history_store
                state = store.load("ui").get("state", {})
                chat = state.setdefault("chats", {}).setdefault(data["from"], {"messages": [], "history": [], "pending": False})
                role = "peer" if event == "incoming" else "error" if data.get("error") else "local_agent"
                chat["messages"].append([role, data["text"] if event == "incoming" else data["to"] + ":\n" + data["text"]])
                chat["unread"] = chat.get("unread", 0) + (event == "incoming")
                store.save("ui", "state", state)
            elif event == "conversations" and self.engines[workspace_id].remote == (self.entry(workspace_id)["kind"] == "remote"):
                engine = self.engines[workspace_id]
                state = engine.history_store.load("ui").get("state", {})
                previous = state.get("conversations", {})
                state["conversations"] = {room["id"]: room for room in data}
                for room in data:
                    chat = state.setdefault("chats", {}).setdefault(room["id"], {"messages": [], "history": []})
                    old_ids = {message["id"] for message in previous.get(room["id"], {}).get("messages", [])}
                    chat["unread"] = chat.get("unread", 0) + len({message["id"] for message in room["messages"]} - old_ids)
                    chat["revision"] = room["revision"]
                    chat["pending"] = room["pending"]
                    chat["messages"] = [["user" if message["role"] == "user" and message["from"] == engine.active_id
                        else "member:" + message["from"] if message["role"] == "user" else message["role"], message["content"]]
                        for message in room["messages"]]
                engine.history_store.save("ui", "state", state)
            return
        if not self.ready or event == "ready":
            return
        if event == "workspace":
            entry = self.entry()
            if entry["kind"] == "remote" and self.engine.remote:
                entry["identity"] = data["self"]
                self.save_catalog()
                self.emit("workspace", {**data, "id": workspace_id, "name": entry["name"], "port": self.engine.port,
                    "history_directory": str(self.engine.storage.directory)})
            return
        if self.entry()["kind"] == "remote" and not self.engine.remote and event in ("agents", "conversations", "workspace_info"):
            return
        self.emit(event, data)

    async def ensure_engine(self, entry):
        if entry["id"] in self.engines:
            return self.engines[entry["id"]]
        scoped = self.scoped_storage(entry)
        scoped.settings["workspace_name"] = entry["name"]
        scoped.settings["workspace_id"] = entry["id"]
        scoped.save()
        engine = WorkspaceRuntime(scoped, lambda event, data: self.forward(entry["id"], event, data), restore_remote=entry["kind"] == "remote")
        self.engines[entry["id"]] = engine
        try:
            await engine.start()
        except BaseException:
            await engine.close()
            self.engines.pop(entry["id"], None)
            raise
        return engine

    async def snapshot(self):
        entry = self.entry()
        engine = self.engine
        cached = self.cache.get(entry["id"], {})
        self.emit("workspace", {"id": entry["id"], "name": entry["name"], "remote": entry["kind"] == "remote",
            "self": entry.get("identity", engine.active_id), "url": engine.active_url, "port": engine.port,
            "history_directory": str(engine.storage.directory)})
        self.publish_catalog()
        if entry["kind"] == "remote" and not engine.remote:
            self.emit("offline", "Workspace is offline. Saved conversations remain available; reconnection will be retried.")
            return
        for event in ("agents", "conversations", "workspace_info"):
            if event in cached:
                self.emit(event, cached[event])
        try:
            await engine.refresh()
        except Exception:
            self.emit("offline", "Workspace is offline. Your saved history is still available.")

    async def start(self):
        # Migrate the single saved remote connection without changing old tokens.
        saved = self.storage.settings.get("remote")
        if not self.catalog_path.exists() and saved and self.storage.vault.get("remote-token"):
            entry = {"id": "workspace-" + uuid4().hex, "name": "Shared workspace", "kind": "remote", "url": saved["url"]}
            scoped = self.scoped_storage(entry)
            scoped.settings["remote"] = saved
            scoped.vault.set("remote-token", self.storage.vault.get("remote-token"))
            profile = self.storage.settings.get("remote_agent")
            if profile:
                scoped.settings["remote_agent"] = profile
                key = self.storage.vault.get("provider:" + profile["id"])
                if key:
                    scoped.vault.set("provider:" + profile["id"], key)
            scoped.save()
            self.catalog["workspaces"].append(entry)
            self.active_workspace_id = entry["id"]
        if not self.catalog["workspaces"]:
            self.catalog["workspaces"].append({"id": "local", "name": "My workspace", "kind": "local"})
        if not any(entry["id"] == self.active_workspace_id for entry in self.catalog["workspaces"]):
            self.active_workspace_id = self.catalog["workspaces"][0]["id"]
        self.catalog["active"] = self.active_workspace_id
        self.save_catalog()
        entries = [entry for entry in self.catalog["workspaces"] if entry["kind"] == "local" or entry["id"] == self.active_workspace_id]
        await asyncio.gather(*(self.ensure_engine(entry) for entry in entries))
        self.ready = True
        await self.snapshot()
        self.emit("ready", {"port": self.engine.port, "device_id": self.engine.active_id})

    async def switch_workspace(self, workspace_id):
        async with self.mutation:
            entry = self.entry(workspace_id)
            await self.ensure_engine(entry)
            self.active_workspace_id = workspace_id
            self.catalog["active"] = workspace_id
            self.save_catalog()
            await self.snapshot()

    async def create_workspace(self, name):
        name = self.valid_name(name)
        async with self.mutation:
            entry = {"id": "workspace-" + uuid4().hex, "name": name, "kind": "local"}
            self.catalog["workspaces"].append(entry)
            try:
                await self.ensure_engine(entry)
            except BaseException:
                self.catalog["workspaces"].remove(entry)
                raise
            self.active_workspace_id = entry["id"]
            self.catalog["active"] = entry["id"]
            self.save_catalog()
            await self.snapshot()
            return dict(entry)

    @staticmethod
    def valid_name(name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Use a workspace name with 1–80 characters")
        return name.strip()

    async def rename_workspace(self, name):
        name = self.valid_name(name)
        async with self.mutation:
            if self.entry()["kind"] != "local":
                raise ValueError("Only the workspace owner can rename it")
            self.entry()["name"] = name
            self.engine.storage.settings["workspace_name"] = name
            self.engine.storage.save()
            self.save_catalog()
            await self.snapshot()

    async def use_local(self, forget_remote=True):
        local = next((entry for entry in self.catalog["workspaces"] if entry["kind"] == "local"), None)
        if not local:
            return await self.create_workspace("My workspace")
        if forget_remote:
            self.storage.settings.pop("remote", None)
            self.storage.save()
        await self.switch_workspace(local["id"])

    async def reset_identity(self):
        """Replace this device's identity in every local workspace.

        Each local workspace owns its own relay and credential set, so
        the reset is delegated to every engine rather than only the
        visible one. Otherwise a workspace the user was not looking at
        would keep advertising the superseded token.
        """
        results = []
        async with self.mutation:
            for entry in self.catalog["workspaces"]:
                if entry["kind"] != "local":
                    continue
                results.append(await self.ensure_engine(entry).reset_identity())
        await self.snapshot()
        return results

    async def join(self, url, token, allow_insecure=False, save=True, conversation_id=None):
        relay_http_url(url, allow_insecure)
        async with self.mutation:
            existing = next((entry for entry in self.catalog["workspaces"] if entry["kind"] == "remote"
                and entry.get("url") == url and self.scoped_storage(entry).vault.get("remote-token") == token), None)
            entry = existing or {"id": "workspace-" + uuid4().hex, "name": "Shared workspace", "kind": "remote", "url": url}
            if not existing:
                self.catalog["workspaces"].append(entry)
            previous = self.active_workspace_id
            try:
                inspector = self.engines.get(existing["id"]) if existing else None
                await (inspector or self.engine).inspect_invitation(url, token, allow_insecure, conversation_id)
                engine = await self.ensure_engine(entry)
                await engine.join(url, token, allow_insecure, save=True, conversation_id=conversation_id)
            except BaseException:
                if not existing:
                    self.catalog["workspaces"].remove(entry)
                    if entry["id"] in self.engines:
                        await self.engines.pop(entry["id"]).close()
                    self.cache.pop(entry["id"], None)
                self.active_workspace_id = previous
                raise
            entry["identity"] = engine.active_id
            entry["name"] = self.cache.get(entry["id"], {}).get("workspace_info", {}).get("name", entry["name"])
            self.active_workspace_id = entry["id"]
            self.catalog["active"] = entry["id"]
            if save:
                self.storage.settings["remote"] = {"url": url, "allow_insecure": allow_insecure}
                self.storage.vault.set("remote-token", token)
                self.storage.save()
            self.save_catalog()
            await self.snapshot()
            if conversation_id:
                self.emit("conversation_joined", conversation_id)

    def connected_engine(self):
        if self.entry()["kind"] == "remote" and not self.engine.remote:
            raise ConnectionError("This workspace is offline. Keep its host app open to reconnect.")
        return self.engine

    async def send(self, target, payload):
        return await self.connected_engine().send(target, payload)

    async def send_conversation(self, conversation_id, text):
        return await self.connected_engine().send_conversation(conversation_id, text)

    async def save_agent(self, profile, key=None):
        return await self.connected_engine().save_agent(profile, key)

    async def stop_agent(self, agent_id):
        return await self.connected_engine().stop_agent(agent_id)

    async def invite(self, *args, **kwargs):
        return await self.connected_engine().invite(*args, **kwargs)

    async def remove_member(self, member):
        if self.entry()["kind"] != "local":
            raise ValueError("Only the workspace owner can remove members")
        await self.engine.remove_member(member)

    async def delete_workspace(self):
        async with self.mutation:
            entry = self.entry()
            engine = self.engine
            if entry["kind"] == "remote" and engine.remote:
                base = relay_http_url(engine.active_url, True)
                try:
                    response = await engine.http.post(base + "/workspace/leave", headers={"Authorization": "Bearer " + engine.active_token}, timeout=10)
                except httpx.HTTPError:
                    # Offline hosts cannot revoke their token; forget it locally.
                    response = None
                if response is not None and response.status_code not in (200, 401, 404, 501):
                    raise ValueError("The host could not remove this membership. Ask the workspace owner to remove your device.")
            await engine.close()
            for identity in engine.storage.settings.get("identities", {}):
                if hasattr(engine.storage.vault, "delete"):
                    engine.storage.vault.delete("relay:" + identity)
                    engine.storage.vault.delete("provider:" + identity)
            profile_ids = {profile["id"] for profile in engine.storage.settings.get("agents", [])}
            if engine.storage.settings.get("remote_agent"):
                profile_ids.add(engine.storage.settings["remote_agent"]["id"])
            for identity in profile_ids:
                if hasattr(engine.storage.vault, "delete"):
                    engine.storage.vault.delete("provider:" + identity)
            if hasattr(engine.storage.vault, "delete"):
                engine.storage.vault.delete("remote-token")
            engine.history_store.clear()
            if entry["id"] == "local":
                for key in ("identities", "device_id", "agents", "remote", "remote_agent", "share_lan", "relay_port", "workspace_name", "workspace_id"):
                    engine.storage.settings.pop(key, None)
            else:
                engine.storage.settings.clear()
            engine.storage.save()
            if entry["kind"] == "remote" and self.storage.settings.get("remote", {}).get("url") == entry.get("url"):
                legacy_profile = self.storage.settings.get("remote_agent")
                if (legacy_profile and legacy_profile.get("relay") == entry.get("url")
                        and not any(profile["id"] == legacy_profile["id"] for profile in self.storage.settings.get("agents", []))
                        and hasattr(self.storage.vault, "delete")):
                    self.storage.vault.delete("provider:" + legacy_profile["id"])
                self.storage.settings.pop("remote", None)
                self.storage.settings.pop("remote_agent", None)
                if hasattr(self.storage.vault, "delete"):
                    self.storage.vault.delete("remote-token")
                self.storage.save()
            self.catalog["workspaces"].remove(entry)
            self.engines.pop(entry["id"], None)
            self.cache.pop(entry["id"], None)
            self.emit("workspace_removed", entry["id"])
            if not self.catalog["workspaces"]:
                replacement = {"id": "workspace-" + uuid4().hex, "name": "My workspace", "kind": "local"}
                self.catalog["workspaces"].append(replacement)
            replacement = self.catalog["workspaces"][0]
            await self.ensure_engine(replacement)
            self.active_workspace_id = replacement["id"]
            self.catalog["active"] = replacement["id"]
            self.save_catalog()
            await self.snapshot()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.ready = False
        await asyncio.gather(*(engine.close() for engine in self.engines.values()))
