"""Async networking owned by the desktop process; contains no Qt dependencies."""
import asyncio
import contextlib
import json
import re
import secrets
import socket
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import uvicorn

from network_a2a.adapters import PROVIDERS, ProviderConfig, create_adapter
from network_a2a.client import AgentClient
from network_a2a.server import Relay, create_app
from network_a2a.persistence import HistoryStore


def relay_http_url(url, allow_insecure=False):
    # Reuse the device client's transport validation and enforce the endpoint.
    AgentClient(url, "unused", allow_insecure=allow_insecure)
    parsed = urlsplit(url)
    if parsed.path != "/connect" or parsed.query or parsed.fragment:
        raise ValueError("Relay URL must end in /connect without query parameters")
    return urlunsplit(("https" if parsed.scheme == "wss" else "http", parsed.netloc, "", "", ""))


class WorkspaceRuntime:
    def __init__(self, storage, emit=lambda event, data: None, restore_remote=True):
        self.storage = storage
        self.emit = emit
        self.restore_remote = restore_remote
        self.history_store = HistoryStore(storage.directory)
        self.credentials = {}
        self.app = None
        self.server = None
        self.server_task = None
        self.server_socket = None
        self.http = None
        self.runners = {}
        self.active_url = None
        self.active_token = None
        self.active_id = None
        self.remote = False
        self.sharing = False
        self.port = None
        self.poller = None
        self.closed = False
        self.generation = 0
        self.mutation = asyncio.Lock()

    async def start(self):
        self.http = httpx.AsyncClient(timeout=65, follow_redirects=False)
        self.credentials = self.storage.credentials() or {}
        # Recorded by Storage.credentials() before it dropped anything, so
        # it has to be read before a new token is minted below.
        recovered = list(getattr(self.storage, "dropped_identities", []))
        device_id = self.storage.settings.get("device_id")
        if not device_id:
            device_id = "device-" + uuid4().hex[:8]
            self.storage.settings["device_id"] = device_id
        if device_id not in self.credentials:
            self.credentials[device_id] = {"token": secrets.token_urlsafe(32), "group": "workspace"}
        Relay(self.credentials)
        self.storage.save_credentials(self.credentials)
        self.storage.save()
        if recovered:
            # The device carries on with a new token, but any workspace
            # joined under the old one no longer recognises it, so this is
            # worth saying rather than leaving the user to wonder why
            # their shared workspace stopped connecting.
            self.emit("notice", "This device's saved identity was missing from the "
                                "credential store, so a new one was created. Workspaces "
                                "joined with the old identity need a new invitation.")
        self.app = create_app(self.credentials, conversation_store=self.history_store, workspace={
            "id": self.storage.settings.get("workspace_id", "local"),
            "name": self.storage.settings.get("workspace_name", "My workspace"),
            "owner": device_id, "models": [profile["id"] for profile in self.storage.settings.get("agents", [])]})
        self.app.state.relay.member_remover = self.remove_member
        self.sharing = bool(self.storage.settings.get("share_lan", False))
        await self._start_server("0.0.0.0" if self.sharing else "127.0.0.1", self.storage.settings.get("relay_port", 0))
        self.storage.settings["relay_port"] = self.port
        self.storage.save()
        saved = self.storage.settings.get("remote") if self.restore_remote else None
        await self.use_local(forget_remote=False)
        if saved:
            try:
                token = self.storage.vault.get("remote-token")
                if token:
                    await self.join(saved["url"], token, saved.get("allow_insecure", False), save=False)
            except Exception:
                self.emit("notice", "Saved workspace is unavailable. Your local workspace is ready; you can reconnect from Join workspace.")
        self.poller = asyncio.create_task(self._poll())
        self.emit("ready", {"port": self.port, "device_id": device_id})

    async def _start_server(self, host, port):
        sock = socket.socket()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            self.port = sock.getsockname()[1]
            self.server_socket = sock
            config = uvicorn.Config(self.app, log_level="error", ws_max_size=262144, ws_ping_interval=20, ws_ping_timeout=20,
                                    proxy_headers=False, timeout_graceful_shutdown=5, log_config=None)
            self.server = uvicorn.Server(config)
            self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not self.server.started:
                    if self.server_task.done():
                        await self.server_task
                        raise RuntimeError("Local relay could not start")
                    await asyncio.sleep(.02)
        except BaseException:
            sock.close()
            raise

    async def _stop_agents(self):
        self.generation += 1
        entries = list(self.runners.values())
        self.runners.clear()
        owned = {client.agent_id for client, _ in entries}
        for client, _ in entries:
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
        for client, task in entries:
            task.cancel()
        await asyncio.gather(*(task for _, task in entries), return_exceptions=True)
        for client, _ in entries:
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
        # Cancellation must release registered sockets before identities are reused.
        if self.active_url and not self.remote:
            for _ in range(100):
                if not owned.intersection(self.app.state.relay.peers):
                    break
                await asyncio.sleep(.02)

    async def _attach(self, agent_id, token, handler, allow_insecure=False):
        generation = self.generation
        async def visible_handler(payload, source, shared=False):
            incoming = not shared and source != self.active_id and generation == self.generation
            if incoming:
                text = payload.get("text") if isinstance(payload, dict) else payload
                if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
                    messages = payload["messages"]
                    text = messages[-1].get("content") if messages and isinstance(messages[-1], dict) else text
                if not isinstance(text, str):
                    text = json.dumps(payload)
                self.emit("incoming", {"from": source, "to": agent_id, "text": text})
            try:
                result = await handler(payload, source)
            except Exception:
                if incoming and generation == self.generation:
                    self.emit("incoming_reply", {"from": source, "to": agent_id,
                                                "text": "The local agent could not complete this request.", "error": True})
                raise
            if incoming and generation == self.generation:
                text = result.get("text") if isinstance(result, dict) else result
                self.emit("incoming_reply", {"from": source, "to": agent_id,
                                            "text": text if isinstance(text, str) else json.dumps(result)})
            return result
        async def handle_frame(frame):
            return await visible_handler(frame["payload"], frame["from"], bool(frame.get("conversation_id")))
        client = AgentClient(self.active_url, token, visible_handler, allow_insecure=allow_insecure, frame_handler=handle_frame)
        task = asyncio.create_task(client.run())
        self.runners[agent_id] = (client, task)
        waiter = asyncio.create_task(client.ready.wait())
        try:
            done, _ = await asyncio.wait([task, waiter], timeout=12, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
                raise ConnectionError("Agent connection closed")
            if waiter not in done:
                raise TimeoutError("Agent did not connect. Check its token and relay address.")
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.runners.pop(agent_id, None)
            raise
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def _echo(self, payload, source):
        text = payload.get("text") if isinstance(payload, dict) else payload
        if not isinstance(text, str):
            text = json.dumps(payload)
        return {"text": f"This device received your message from {source}:\n\n{text}\n\nConfigure a model in Providers to turn this connectivity agent into an AI agent."}

    async def use_local(self, forget_remote=True):
        async with self.mutation:
            await self._use_local(forget_remote)

    async def _use_local(self, forget_remote=True):
        """Switch to the local workspace, assuming ``mutation`` is held.

        Split out because asyncio.Lock is not reentrant: reset_identity
        already holds it when it needs to return to the local workspace,
        and calling use_local from there would deadlock.
        """
        await self._stop_agents()
        self.remote = False
        self.active_url = f"ws://127.0.0.1:{self.port}/connect"
        self.active_id = self.storage.settings["device_id"]
        self.active_token = self.credentials[self.active_id]["token"]
        if forget_remote:
            self.storage.settings.pop("remote", None)
        self.storage.save()
        await self._attach(self.active_id, self.active_token, self._echo)
        await self._restore_profiles()
        self.emit("workspace", {"name": self.storage.settings.get("workspace_name", "My workspace"), "url": self.active_url, "self": self.active_id, "remote": False})
        await self.refresh()

    async def reset_identity(self):
        """Replace every identity in this workspace with a fresh token.

        Reachable from Settings. Useful when a credential is suspected to
        be exposed, or when the credential store has been partly lost and
        the leftovers are not wanted.

        The replacement is written before anything is deleted, so a store
        that refuses the write leaves the existing identity, the in-memory
        credentials and the relay exactly as they were. Only agents held
        back until the new token is durable, so a failure also leaves the
        running agents alone.

        Provider profiles get new tokens too, because _restore_profiles
        only relaunches an agent whose id is present in the credential
        set; dropping them would silently stop every model agent.
        """
        async with self.mutation:
            removed = list(self.storage.settings.get("identities", {}) or {})
            device_id = self.storage.settings.get("device_id")
            if not device_id:
                device_id = "device-" + uuid4().hex[:8]
                self.storage.settings["device_id"] = device_id
            fresh = {device_id: {"token": secrets.token_urlsafe(32), "group": "workspace"}}
            skipped = []
            for profile in self.storage.settings.get("agents", []):
                agent_id = profile.get("id")
                # A profile may name the device identity, which already has
                # a token above; a second entry would overwrite it.
                if agent_id == device_id or agent_id in fresh:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id or ""):
                    skipped.append(agent_id)
                    continue
                fresh[agent_id] = {"token": secrets.token_urlsafe(32), "group": "workspace"}
            Relay(fresh)
            self.storage.save_credentials(fresh)
            # Forget only what is genuinely superseded. Everything in fresh
            # was just written, so filtering on device_id alone deleted a
            # reissued provider profile's token moments after saving it,
            # leaving that identity present in memory but gone from the
            # vault and from settings, and so lost on the next launch.
            superseded = [i for i in removed if i not in fresh]
            self.storage.forget_identities(superseded)
            await self._stop_agents()
            self.credentials = fresh
            if self.app is not None:
                self.app.state.relay.credentials = fresh
            # Counted from the same set, so a reissued provider profile is
            # not reported to the user as a newly joined identity.
            joined = len(superseded)
            self.emit("notice", f"Replaced {len(removed)} saved "
                                f"{'identity' if len(removed) == 1 else 'identities'}. "
                                "Rejoin any shared workspace with a new invitation.")
            if skipped:
                # Skipping is better than refusing the reset outright, but the
                # user should know which agents did not get a new token.
                self.emit("notice", "Could not reissue an identity for "
                                    f"{', '.join(str(s) for s in skipped)}. Open Providers "
                                    "to rename and reconnect those agents.")
            await self._use_local(forget_remote=True)
            return {"removed": removed, "device_id": device_id, "skipped": skipped,
                    "reissued": sorted(i for i in fresh if i != device_id),
                    "joined": joined}

    async def _restore_profiles(self):
        for profile in self.storage.settings.get("agents", []):
            if profile.get("autostart", True) and profile["id"] in self.credentials:
                try:
                    await self._launch_profile(profile)
                except Exception:
                    self.emit("notice", f"Could not restore {profile['id']}. Open Providers to check its configuration.")

    async def inspect_invitation(self, url, token, allow_insecure=False, conversation_id=None):
        if conversation_id is not None and (not isinstance(conversation_id, str)
                or not re.fullmatch(r"conversation-[0-9a-f]{32}", conversation_id)):
            raise ValueError("Use a valid shared conversation invitation")
        base = relay_http_url(url, allow_insecure)
        try:
            response = await self.http.get(base + "/agents", headers={"Authorization": "Bearer " + token}, timeout=10)
        except httpx.TimeoutException:
            raise ConnectionError("The relay did not respond within 10 seconds. Keep the host app open and check the address, network, and host firewall. A LAN invitation works only on the same reachable network.") from None
        except httpx.HTTPError:
            raise ConnectionError("Could not connect to the relay. Check the address and port, keep the host app open, and allow its port through the host firewall. Internet connections need a reachable WSS relay.") from None
        if response.status_code != 200:
            raise ValueError("Relay rejected this invitation. Check the token and address.")
        try:
            result = response.json()
        except ValueError:
            raise ValueError("This address did not return a relay response. Use the host's invitation URL ending in /connect.") from None
        if not isinstance(result, dict) or not isinstance(result.get("self"), str):
            raise ValueError("This relay does not support desktop identity discovery")
        if conversation_id:
            conversation = await self.http.get(base + f"/conversations/{conversation_id}",
                headers={"Authorization": "Bearer " + token}, timeout=10)
            if conversation.status_code != 200:
                raise ValueError("This shared conversation is unavailable. Keep the host app open and ask for a new conversation invitation.")
        same_connection = self.remote and self.active_url == url and self.active_token == token
        if not same_connection and any(item.get("id") == result["self"] and item.get("online") for item in result.get("agents", [])):
            raise ValueError("This invitation's device identity is already connected. Ask the host to create a new invitation with a unique device name for this laptop.")
        return result

    async def join(self, url, token, allow_insecure=False, save=True, conversation_id=None):
        async with self.mutation:
            result = await self.inspect_invitation(url, token, allow_insecure, conversation_id)
            if self.remote and self.active_url == url and self.active_token == token:
                await self.refresh()
                if conversation_id:
                    self.emit("conversation_joined", conversation_id)
                return
            previous = (self.active_url, self.active_token, self.active_id, self.remote)
            await self._stop_agents()
            self.active_url, self.active_token, self.active_id, self.remote = url, token, result["self"], True
            try:
                await self._attach(self.active_id, token, self._echo, allow_insecure)
                remote_profile = self.storage.settings.get("remote_agent")
                if remote_profile and remote_profile.get("autostart") and remote_profile["id"] == self.active_id and remote_profile.get("relay") == url:
                    await self._launch_profile(remote_profile, allow_insecure)
                if save:
                    self.storage.vault.set("remote-token", token)
                    self.storage.settings["remote"] = {"url": url, "allow_insecure": allow_insecure}
                    self.storage.save()
            except BaseException:
                await self._stop_agents()
                self.active_url, self.active_token, self.active_id, self.remote = previous
                if previous[0]:
                    await self._attach(self.active_id, self.active_token, self._echo,
                                       allow_insecure=previous[3] and urlsplit(previous[0]).scheme == "ws")
                    if not self.remote:
                        await self._restore_profiles()
                raise
            self.emit("workspace", {"name": "Shared workspace", "url": url, "self": self.active_id, "remote": True})
            await self.refresh()
            if conversation_id:
                self.emit("conversation_joined", conversation_id)

    async def _remove_runner(self, agent_id):
        entry = self.runners.pop(agent_id, None)
        if entry:
            client, task = entry
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Explicitly close before a new connection reuses the same identity.
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
            await asyncio.sleep(.1)

    def _config(self, profile, key):
        spec = PROVIDERS[profile["provider"]]
        return ProviderConfig(provider=profile["provider"], model=profile["model"], base_url=profile.get("base_url") or spec.base_url,
                              api_key=key, system_prompt=profile.get("system_prompt"), allow_insecure=profile.get("allow_insecure", False))

    async def _launch_profile(self, profile, allow_insecure=False):
        key = self.storage.vault.get("provider:" + profile["id"])
        adapter = create_adapter(self._config(profile, key), self.http)
        token = self.active_token if self.remote else self.credentials[profile["id"]]["token"]
        await self._remove_runner(profile["id"])
        await self._attach(profile["id"], token, adapter, allow_insecure)

    async def save_agent(self, profile, key=None):
        async with self.mutation:
            agent_id = profile["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id):
                raise ValueError("Agent names need 1–64 letters, numbers, underscores, or hyphens")
            if self.remote and agent_id != self.active_id:
                raise ValueError("Use your invited identity for an agent in a shared workspace")
            if not self.remote and agent_id == self.active_id:
                raise ValueError("Choose a different name for your AI agent; the device identity stays available")
            saved_key = key if key else self.storage.vault.get("provider:" + agent_id)
            create_adapter(self._config(profile, saved_key), self.http)  # validate before mutating storage
            if key:
                self.storage.vault.set("provider:" + agent_id, key)
            if not self.remote:
                if agent_id not in self.credentials:
                    updated = {**self.credentials, agent_id: {"token": secrets.token_urlsafe(32), "group": "workspace"}}
                    Relay(updated)
                    self.storage.save_credentials(updated)
                    self.credentials = updated
                    self.app.state.relay.credentials = updated
                profiles = [p for p in self.storage.settings.get("agents", []) if p["id"] != agent_id]
                self.storage.settings["agents"] = [*profiles, dict(profile)]
            else:
                self.storage.settings["remote_agent"] = {**profile, "relay": self.active_url}
            self.storage.save()
            await self._launch_profile(profile, self.storage.settings.get("remote", {}).get("allow_insecure", False))
            self.emit("activity", {"title": f"{agent_id} connected", "detail": f"{profile['provider']} · {profile['model']}"})
            await self.refresh()

    async def stop_agent(self, agent_id):
        async with self.mutation:
            if agent_id == self.storage.settings["device_id"] and not self.remote:
                raise ValueError("The device connectivity agent runs while the app is open")
            if agent_id not in self.runners:
                raise ValueError("You can stop only an agent running on this device")
            await self._remove_runner(agent_id)
            if self.remote:
                if self.storage.settings.get("remote_agent"):
                    self.storage.settings["remote_agent"]["autostart"] = False
                # Saved before re-attaching the echo agent. That attach waits
                # on the network and raises on a closed socket or a timeout,
                # and saving afterwards meant a failure left the stop
                # unrecorded, so the next launch restored the agent the user
                # had just stopped.
                self.storage.save()
                await self._attach(self.active_id, self.active_token, self._echo,
                                   self.storage.settings.get("remote", {}).get("allow_insecure", False))
            else:
                for profile in self.storage.settings.get("agents", []):
                    if profile["id"] == agent_id:
                        profile["autostart"] = False
                self.storage.save()
            await self.refresh()

    async def refresh(self):
        if not self.active_token:
            return
        generation = self.generation
        base = relay_http_url(self.active_url, allow_insecure=True)
        response = await self.http.get(base + "/agents", headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
        response.raise_for_status()
        if generation != self.generation:
            return
        profiles = {p["id"]: p for p in self.storage.settings.get("agents", [])}
        remote_profile = self.storage.settings.get("remote_agent")
        if self.remote:
            profiles = {remote_profile["id"]: remote_profile} if remote_profile and remote_profile.get("relay") == self.active_url else {}
        agents = []
        for item in response.json()["agents"]:
            profile = profiles.get(item["id"], {})
            agents.append({**item, "provider": profile.get("provider"), "model": profile.get("model"),
                           "local": item["id"] in self.runners, "profile": profile})
        self.emit("agents", {"agents": agents, "self": self.active_id,
                            "connected": bool(self.runners.get(self.active_id, (None,))[0] and self.runners[self.active_id][0].ready.is_set())})
        response = await self.http.get(base + "/conversations", headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
        # Older relays still support direct agent chats.
        if response.status_code != 404:
            response.raise_for_status()
        if response.status_code == 200 and generation == self.generation:
            self.emit("conversations", response.json()["conversations"])
        if not self.remote:
            self.app.state.relay.workspace.update(name=self.storage.settings.get("workspace_name", "My workspace"),
                models=[profile["id"] for profile in self.storage.settings.get("agents", [])])
        response = await self.http.get(base + "/workspace", headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
        if response.status_code == 200 and generation == self.generation:
            self.emit("workspace_info", response.json())

    async def _poll(self):
        next_join = time.monotonic() + 15
        while not self.closed:
            saved = self.storage.settings.get("remote") if self.restore_remote else None
            if saved and not self.remote and time.monotonic() >= next_join:
                next_join = time.monotonic() + 15
                try:
                    token = self.storage.vault.get("remote-token")
                    if token:
                        await self.join(saved["url"], token, saved.get("allow_insecure", False), save=False)
                except Exception:
                    pass
            try:
                await self.refresh()
            except Exception:
                self.emit("offline", "Relay connection interrupted. The app will keep trying to reconnect.")
            await asyncio.sleep(3)

    async def send(self, target, payload):
        generation = self.generation
        base = relay_http_url(self.active_url, allow_insecure=True)
        try:
            response = await self.http.post(base + f"/agents/{target}/invoke", json=payload,
                                           headers={"Authorization": "Bearer " + self.active_token}, timeout=65)
            result = response.json()
            if response.status_code != 200:
                raise RuntimeError(result.get("error", "The agent could not complete this request"))
            if generation != self.generation:
                raise RuntimeError("Workspace changed while the request was running; it was not replayed")
            return result
        except httpx.TimeoutException:
            raise RuntimeError("The request timed out and was not replayed. The agent may already have processed it.") from None
        except httpx.HTTPError:
            raise RuntimeError("Could not reach the relay. Your request was not replayed.") from None

    async def send_conversation(self, conversation_id, text):
        generation = self.generation
        base = relay_http_url(self.active_url, allow_insecure=True)
        try:
            response = await self.http.post(base + f"/conversations/{conversation_id}/messages",
                json={"text": text}, headers={"Authorization": "Bearer " + self.active_token}, timeout=65)
            if generation != self.generation:
                raise RuntimeError("Workspace changed while the request was running; it was not replayed")
            await self.refresh()
            if response.status_code != 200:
                raise RuntimeError(response.json().get("error", "Could not send to the shared conversation"))
            return response.json()
        except httpx.TimeoutException:
            raise RuntimeError("The request timed out and was not replayed. Check the shared conversation for the reply.") from None
        except httpx.HTTPError:
            raise RuntimeError("Could not reach the relay. Your request was not replayed.") from None

    async def ollama_models(self, base_url="http://127.0.0.1:11434"):
        ProviderConfig("ollama", "validation", base_url)
        response = await self.http.get(base_url.rstrip("/") + "/api/tags", timeout=5)
        response.raise_for_status()
        return [item["name"] for item in response.json()["models"]]

    async def invite(self, agent_id, public_url, lan=False, conversation_id=None, target=None, messages=None):
        async with self.mutation:
            if self.remote:
                raise ValueError("Only this workspace's host can create invitations")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id) or agent_id in self.credentials:
                raise ValueError("Choose a new device identity with 1–64 letters, numbers, underscores, or hyphens")
            relay_http_url(public_url, allow_insecure=lan)
            conversations = self.app.state.relay.conversations
            if conversation_id and not conversations.get(conversation_id, self.active_id):
                raise ValueError("Choose an existing conversation in this workspace")
            if target and not self.app.state.relay.allowed(self.active_id, target):
                raise ValueError("Choose an agent in this workspace")
            if lan and not self.sharing:
                await self._stop_agents()
                self.server.should_exit = True
                await self.server_task
                self.server_socket.close()
                await self._start_server("0.0.0.0", self.port)
                self.sharing = True
                self.storage.settings["share_lan"] = True
                self.storage.save()
                await self._attach(self.active_id, self.active_token, self._echo)
                await self._restore_profiles()
            updated = {**self.credentials, agent_id: {"token": secrets.token_urlsafe(32), "group": "workspace"}}
            Relay(updated)
            self.storage.save_credentials(updated)
            self.credentials = updated
            self.app.state.relay.credentials = updated
            if target and not conversation_id:
                conversation_id = conversations.create(self.active_id, target, messages or ())["id"]
            if conversation_id:
                conversations.invite(conversation_id, self.active_id, agent_id)
            self.emit("activity", {"title": "Device invitation created", "detail": agent_id})
            await self.refresh()
            invitation = {"version": 1, "agent_id": agent_id, "url": public_url, "token": updated[agent_id]["token"], "allow_insecure": lan}
            invitation["workspace_name"] = self.storage.settings.get("workspace_name", "My workspace")
            if conversation_id:
                invitation["conversation_id"] = conversation_id
            return invitation

    async def remove_member(self, member):
        async with self.mutation:
            if self.remote:
                raise ValueError("Only the workspace owner can remove members")
            if member == self.active_id:
                raise ValueError("Delete the workspace to remove its owner")
            if member not in self.credentials:
                raise ValueError("This member is no longer in the workspace")
            updated = {name: config for name, config in self.credentials.items() if name != member}
            self.storage.save_credentials(updated)
            self.credentials = updated
            self.app.state.relay.credentials = updated
            self.app.state.relay.conversations.remove_member(member)
            peer = self.app.state.relay.peers.get(member)
            if peer:
                await peer.socket.close(code=1008, reason="Removed from workspace")
            await self._remove_runner(member)
            self.storage.settings["agents"] = [profile for profile in self.storage.settings.get("agents", []) if profile["id"] != member]
            self.storage.save()
            if hasattr(self.storage.vault, "delete"):
                self.storage.vault.delete("relay:" + member)
                self.storage.vault.delete("provider:" + member)
            self.emit("activity", {"title": "Member removed", "detail": member})
            await self.refresh()

    async def close(self):
        self.closed = True
        if self.poller:
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        await self._stop_agents()
        if self.server:
            self.server.should_exit = True
        if self.server_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server_task, 8)
        if self.server_socket:
            self.server_socket.close()
        if self.http:
            await self.http.aclose()
