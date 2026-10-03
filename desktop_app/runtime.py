"""Own saved local and joined workspaces and dispatch commands to the selected one."""
import asyncio
import json
import re
import shutil
from uuid import uuid4

import httpx

from network_a2a.persistence import HistoryStore

from .storage import UNAVAILABLE, Storage, WorkspaceVault
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
        directory = self._workspace_directory(entry["id"])
        if directory is None:
            return self.storage
        return Storage(directory, WorkspaceVault(self.storage.vault, entry["id"]),
                       root=self.storage.root)

    def _workspace_directory(self, workspace_id):
        """The directory a workspace owns, or None for the local root.

        The local workspace shares the root settings, so it has no
        directory of its own and must never be treated as one.

        The id is validated because this path is handed to shutil.rmtree
        during a workspace deletion. It matches the check the catalog is
        held to, and the containment test is a second layer against a
        symlink or a future loosening of the pattern.
        """
        if workspace_id == "local":
            return None
        if not re.fullmatch(r"workspace-[0-9a-f]{32}", workspace_id):
            raise ValueError("Invalid saved workspace identity")
        root = (self.storage.directory / "workspaces").resolve()
        directory = (root / workspace_id).resolve()
        if root not in directory.parents:
            raise ValueError("Invalid saved workspace identity")
        return directory

    def save_catalog(self):
        temporary = self.catalog_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.catalog, indent=2), encoding="utf-8")
        temporary.replace(self.catalog_path)

    # --- deletions that have not finished ---------------------------------
    #
    # A workspace deletion destroys a relay, credentials and saved chats.
    # If it is interrupted part way through, the workspace is already out of
    # the catalog, so nothing on disk points at what is left and the tokens
    # would sit in the OS credential store indefinitely. Each record holds
    # the exact credential names still owed, so a later attempt does not have
    # to re-derive them from a directory that may already be gone.
    #
    # The ledger itself lives on Storage, beside the vault it describes, so
    # that a workspace runtime can record what it could not delete without
    # reaching into this class.

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
        # Before anything connects: a deletion interrupted on a previous run
        # may still owe credential deletions, and those are worth finishing
        # before the app starts a relay or reads a token.
        self.retry_pending_cleanup()
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
                engine = await self.ensure_engine(entry)
                results.append(await engine.reset_identity())
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

    def _credential_names_for(self, storage):
        """Every credential name a workspace owns, before anything is deleted.

        Collected first because the settings that name them live in the
        directory the deletion is about to remove.
        """
        names = ["remote-token"]
        for identity in storage.settings.get("identities", {}):
            names.append("relay:" + identity)
            names.append("provider:" + identity)
        profile_ids = {profile["id"] for profile in storage.settings.get("agents", [])}
        if storage.settings.get("remote_agent"):
            profile_ids.add(storage.settings["remote_agent"]["id"])
        for identity in profile_ids:
            names.append("provider:" + identity)
        return list(dict.fromkeys(names))

    def _purge_workspace_data(self, entry, engine=None, owed=()):
        """Delete one workspace's credentials and saved data.

        Returns the names still owed in the workspace's own vault and, kept
        separate, the ones owed in the root vault. They have to be separate
        because the same name means a different entry under each, so a retry
        that folded them together would look for ``remote-token`` behind the
        workspace prefix and never touch the root entry.

        ``owed`` is an already-read record for this workspace, if there is
        one, so that a retry deletes exactly what is left rather than
        re-deriving names from a directory that may be gone.

        Credentials live in the OS store and the directory holds both the
        settings and the chat archive, so each is removed explicitly rather
        than relying on the directory.

        Usable without a live engine, which is what lets an interrupted
        deletion be finished on a later launch.
        """
        storage = engine.storage if engine is not None else self.scoped_storage(entry)
        names = self.storage._owed(owed[0], "credentials") if owed else self._credential_names_for(storage)
        # Only the explicit UNAVAILABLE marker counts as a failure, so a vault
        # that does not speak in these terms is treated as having done the
        # work rather than as stranding a secret.
        remaining = [name for name in names
                     if storage.vault.delete(name) == UNAVAILABLE]
        directory = self._workspace_directory(entry["id"])
        if directory is None:
            # The local workspace shares the root, so only its own keys and
            # its chat archive are cleared; the directory must survive.
            for key in ("identities", "device_id", "agents", "remote", "remote_agent",
                        "share_lan", "relay_port", "workspace_name", "workspace_id"):
                storage.settings.pop(key, None)
            storage.save()
            HistoryStore(storage.directory).clear()
        elif directory.exists():
            # settings.json and history.sqlite3 both live here.
            shutil.rmtree(directory, ignore_errors=True)
        return remaining, self._purge_legacy_root_credentials(entry, owed)

    def _purge_legacy_root_credentials(self, entry, owed=()):
        """Delete the credentials the pre-catalog layout kept in the root.

        Returns the names still owed there. A record naming them is retried
        whether or not the root settings still describe them: they are popped
        on the same pass, so the description is gone by the time a later
        attempt runs and the names cannot be derived again.
        """
        names = self.storage._owed(owed[0], "root_credentials") if owed else []
        if not names:
            legacy = self.storage.settings.get("remote_agent")
            matched = (entry["kind"] == "remote"
                       and self.storage.settings.get("remote", {}).get("url") == entry.get("url"))
            if matched:
                names = ["remote-token"]
                if (legacy and legacy.get("relay") == entry.get("url")
                        and not any(p["id"] == legacy["id"]
                                    for p in self.storage.settings.get("agents", []))):
                    names.append("provider:" + legacy["id"])
        if not names:
            return []
        remaining = [name for name in names
                     if self.storage.vault.delete(name) == UNAVAILABLE]
        self.storage.settings.pop("remote", None)
        self.storage.settings.pop("remote_agent", None)
        self.storage.save()
        return remaining

    def pending_cleanup_count(self):
        """How many credentials still owe removal, across all records."""
        records = self.storage.read_pending_cleanup()
        return sum(len(self.storage._owed(record, key))
                   for record in records
                   for key in ("credentials", "root_credentials"))

    def publish_pending_cleanup(self):
        """Tell the window whether anything is still owed.

        Settings keeps this visible rather than relying on a toast, because
        the condition can outlive the message that announced it.
        """
        owed = self.pending_cleanup_count()
        self.emit("pending_cleanup", {"credentials": owed})
        return owed

    def _vault_for(self, scope):
        """The vault a credential record belongs to.

        ``scope`` is None for the root vault and the workspace id otherwise,
        which is what ``Storage.vault_scope`` recorded when the name was owed.
        """
        return self.storage.vault if scope is None else WorkspaceVault(self.storage.vault, scope)

    def retry_pending_cleanup(self):
        """Finish cleanups that were interrupted part way through.

        Called before any engine starts, so credentials owed from a previous
        run are dealt with before the app connects to anything. Each pass is
        idempotent: a credential already gone reports as absent, and a
        directory already removed is skipped.
        """
        for record in self.storage.read_pending_cleanup():
            if record["type"] == "credentials":
                self._retry_owed_credentials(record)
            else:
                self._retry_workspace_deletion(record)
        owed = self.publish_pending_cleanup()
        if owed:
            # Counted separately from the notice so a store that stays locked
            # does not produce a growing counter and a notice on every launch.
            self.emit("notice", "Some saved credentials could not be removed. "
                                "Unlock your credential store and restart; the "
                                "app will try again.")

    def _retry_owed_credentials(self, record):
        """Retry names owed by an identity reset or a removed member.

        The workspace itself is untouched: only the credentials named in the
        record are gone, so nothing here may reach the catalog.
        """
        scope = record.get("workspace")
        if scope is not None and not re.fullmatch(r"workspace-[0-9a-f]{32}", scope):
            self.storage.discard_pending_cleanup(
                {"type": "credentials", "workspace": scope, "source": record.get("source")})
            return
        vault = self._vault_for(scope)
        remaining = [name for name in self.storage._owed(record, "credentials")
                     if vault.delete(name) == UNAVAILABLE]
        self.storage.replace_owed_credentials(scope, record.get("source"), remaining)

    def _retry_workspace_deletion(self, record):
        entry = {"id": record["id"], "kind": record.get("kind", "local"), "url": record.get("url")}
        try:
            self._workspace_directory(entry["id"])
        except ValueError:
            self.storage.discard_pending_cleanup({"type": "workspace", "id": record["id"]})
            return
        if any(existing["id"] == entry["id"] for existing in self.catalog["workspaces"]):
            # Interrupted before the catalog was written, so the removal
            # itself never completed.
            self.catalog["workspaces"] = [e for e in self.catalog["workspaces"]
                                         if e["id"] != entry["id"]]
            if not self.catalog["workspaces"]:
                self.catalog["workspaces"].append({"id": "local", "name": "My workspace",
                                                   "kind": "local"})
            if self.active_workspace_id == entry["id"]:
                self.active_workspace_id = self.catalog["active"] = self.catalog["workspaces"][0]["id"]
            self.save_catalog()
        remaining, owed_on_root = self._purge_workspace_data(entry, owed=[record])
        key = {"type": "workspace", "id": entry["id"]}
        if not remaining and not owed_on_root:
            self.storage.discard_pending_cleanup(key)
            return
        # Pruned to what actually survived, so the record stops naming
        # credentials that are already gone and a later pass cannot confuse a
        # root name with a workspace-scoped one.
        self.storage.record_workspace_deletion(entry, remaining, owed_on_root)

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
            # The intent is durable before anything is removed, and the
            # credential names are copied into it because the directory
            # holding them is about to be deleted. If this deletion is
            # interrupted, a later launch can finish it exactly.
            self.storage.record_workspace_deletion(entry, self._credential_names_for(engine.storage))
            previous_entries = list(self.catalog["workspaces"])
            previous_active = self.active_workspace_id
            self.catalog["workspaces"].remove(entry)
            if not self.catalog["workspaces"]:
                self.catalog["workspaces"].append({"id": "workspace-" + uuid4().hex, "name": "My workspace", "kind": "local"})
            replacement = self.catalog["workspaces"][0]
            self.active_workspace_id = replacement["id"]
            self.catalog["active"] = replacement["id"]
            try:
                # Recorded before the relay for the replacement is started.
                # ensure_engine() binds a socket and touches the credential
                # store, so it can fail; previously the credentials and the
                # history were already gone by then, while the catalog still
                # listed the workspace, so the next launch brought it back
                # empty rather than removed.
                self.save_catalog()
            except Exception:
                # Not recorded, so put the workspace back and keep the engine
                # registered: close() only closes engines it still knows
                # about, and dropping it here would strand its relay.
                self.catalog["workspaces"] = previous_entries
                self.active_workspace_id = self.catalog["active"] = previous_active
                self.storage.discard_pending_cleanup({"type": "workspace", "id": entry["id"]})
                raise
            self.cache.pop(entry["id"], None)
            self.engines.pop(entry["id"], None)
            self.emit("workspace_removed", entry["id"])
            await engine.close()
            remaining, owed_on_root = self._purge_workspace_data(entry, engine)
            key = {"type": "workspace", "id": entry["id"]}
            if remaining or owed_on_root:
                # Pruned to what actually survived, so the record stops
                # naming credentials that are already gone and keeps the root
                # names apart from the workspace's own.
                self.storage.record_workspace_deletion(entry, remaining, owed_on_root)
                self.emit("notice", "This workspace was deleted, but some of its saved "
                                    "credentials could not be removed. Unlock your "
                                    "credential store and restart; the app will try again.")
            else:
                self.storage.discard_pending_cleanup(key)
            self.publish_pending_cleanup()
            await self.ensure_engine(replacement)
            await self.snapshot()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.ready = False
        await asyncio.gather(*(engine.close() for engine in self.engines.values()))
