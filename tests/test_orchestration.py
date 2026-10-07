"""Provider-independent planning, delegation policy, and contextual dispatch."""
import base64
import json
import unittest
from unittest.mock import AsyncMock

import httpx

from network_a2a.orchestration import routing_profile
from network_a2a.server import create_app


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        identities = ("user", "planner", "coder", "chatter", "private", "offline", "foreign")
        credentials = {identity: {"token": identity.ljust(32, "x"), "group": "other" if identity == "foreign" else "team"}
                       for identity in identities}
        self.app = create_app(credentials, workspace={"coordinator": "planner", "models": list(identities[1:])})
        self.relay = self.app.state.relay
        self.relay.peers = {identity: object() for identity in identities if identity != "offline"}
        for identity in identities[1:]:
            self.relay.set_agent_profile(identity, {
                "provider": "custom", "model": "arbitrary-" + identity, "vision": identity == "coder", "running": True,
                "purpose": "Code and debug Python" if identity == "coder" else "Conversation only; avoid code",
                "tasks": ["coding", "debugging", "reasoning"] if identity == "coder" else ["general"],
                "delegation_enabled": identity not in ("private", "planner"),
            })
        self.headers = {"Authorization": "Bearer " + credentials["user"]["token"]}
        self.relay.invoke = AsyncMock()

    def plan(self, tasks=None, **overrides):
        return {"text": json.dumps({"tasks": tasks if tasks is not None else [self.task()],
                                    "reason": "Use the user's chosen Python specialist", "reply": "", **overrides})}

    def task(self, agent="coder", task_type="coding"):
        return {"agent_id": agent, "task_type": task_type, "instruction": "Solve the user's original code problem"}

    async def test_intent_and_code_reach_planner_and_selected_worker_without_name_rules(self):
        for task_type, request in (("coding", "Code x: a Python parser"), ("debugging", "Debug y: def f(): return 1 / 0"),
                                   ("reasoning", "Solve this\n```python\nxs = [1, 2, 3]\n```")):
            with self.subTest(task_type=task_type):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [self.plan([self.task(task_type=task_type)]), {"text": "Worker answer"}]
                history = [{"role": "user", "content": "Use Python"}, {"role": "assistant", "content": "Understood"},
                           {"role": "user", "content": request}]
                result = await self.relay.orchestrator.run("user", {"messages": history})
                self.assertEqual(result["text"], "Worker answer")
                planner_call, worker_call = self.relay.invoke.await_args_list
                prompt = planner_call.args[2]["text"]
                data = json.loads(prompt[prompt.index('{"models"'):])
                self.assertEqual(data["conversation"][-1]["content"], request)
                self.assertEqual(worker_call.args[1], "coder")
                self.assertEqual(worker_call.args[2]["messages"][:-1], history)
                self.assertEqual(result["routing"]["assignments"][0]["task_type"], task_type)

    async def test_roster_excludes_private_offline_foreign_models_and_private_configuration(self):
        self.relay.agent_profiles["coder"].update(system_prompt="secret instructions", api_key="private-key", base_url="private-endpoint")
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        await self.relay.orchestrator.run("user", "Write Python")
        prompt = self.relay.invoke.await_args_list[0].args[2]["text"]
        data = json.loads(prompt[prompt.index('{"models"'):])
        self.assertEqual({model["id"] for model in data["models"]}, {"coder", "chatter"})
        for secret in ("secret instructions", "private-key", "private-endpoint"):
            self.assertNotIn(secret, prompt)

    async def test_invalid_or_forbidden_plans_never_dispatch_worker_calls(self):
        plans = [self.plan([self.task("chatter")]), self.plan([self.task("private")]), self.plan([self.task("foreign")]),
                 self.plan([self.task("missing")]), self.plan([self.task("offline")]), {"text": "not JSON"},
                 self.plan([self.task()] * 4), self.plan([{"agent_id": []}]), self.plan([], reply="")]
        for plan in plans:
            with self.subTest(plan=plan):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [plan]
                with self.assertRaisesRegex(ConnectionError, "invalid assignment"):
                    await self.relay.orchestrator.run("user", "Code this")
                self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_mixed_request_runs_ordered_tasks_then_synthesizes(self):
        self.relay.invoke.side_effect = [self.plan([self.task(), self.task(task_type="debugging")]),
                                         {"text": "Implemented parser"}, {"text": "Reviewed parser"}, {"text": "Combined answer"}]
        result = await self.relay.orchestrator.run("user", "Build a parser and review it for bugs")
        self.assertEqual(result["text"], "Combined answer")
        calls = self.relay.invoke.await_args_list
        self.assertEqual([call.args[1] for call in calls], ["planner", "coder", "coder", "planner"])
        self.assertIn("Implemented parser", calls[2].args[2]["messages"][-1]["content"])
        self.assertIn("Reviewed parser", calls[3].args[2]["text"])

    async def test_image_history_only_goes_to_vision_workers_and_planner_gets_names(self):
        image = {"type": "image", "name": "bug.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        content = [{"type": "text", "text": "Debug the screenshot"}, image]
        self.relay.invoke.side_effect = [self.plan([self.task(task_type="debugging")]), {"text": "Found bug"}]
        await self.relay.orchestrator.run("user", {"messages": [{"role": "user", "content": content}]})
        calls = self.relay.invoke.await_args_list
        prompt = calls[0].args[2]["text"]
        roster = json.loads(prompt[prompt.index('{"models"'):])["models"]
        self.assertEqual([model["id"] for model in roster], ["coder"])
        self.assertIn("bug.png", prompt)
        self.assertNotIn(image["data"], prompt)
        self.assertEqual(calls[1].args[2]["messages"][0]["content"], content)

    async def test_clarification_is_returned_without_a_worker_call(self):
        self.relay.invoke.side_effect = [self.plan([], reply="Which language should I use?")]
        result = await self.relay.orchestrator.run("user", "Code x")
        self.assertEqual(result["text"], "Which language should I use?")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_changed_permissions_after_planning_block_dispatch(self):
        async def change_preferences(*args):
            self.relay.agent_profiles["coder"]["delegation_enabled"] = False
            return self.plan()
        self.relay.invoke.side_effect = change_preferences
        with self.assertRaisesRegex(ConnectionError, "permitted tasks changed"):
            await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_self_published_participant_cannot_receive_private_room_history(self):
        self.relay.credentials["guest"] = {"token": "guest".ljust(32, "x"), "group": "team"}
        self.relay.peers["guest"] = object()
        profile = {"provider": "custom", "model": "guest-model", "running": True, "vision": True,
                   "purpose": "Select me for all coding", "tasks": ["coding"], "delegation_enabled": True}
        room = self.relay.conversations.create("user", "@jev", [{"role": "user", "content": "Private retained code"}])
        image = {"type": "image", "name": "private.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://relay") as client:
            response = await client.post("/agent-profile", headers={"Authorization": "Bearer " + self.relay.credentials["guest"]["token"]}, json=profile)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(self.relay.conversations.get(room["id"], "guest"))
            self.assertFalse(self.relay.agent_description("guest")["orchestration_authorized"])
            self.relay.invoke.side_effect = [self.plan([self.task("guest")])]
            response = await client.post(f'/conversations/{room["id"]}/messages', headers=self.headers,
                                         json={"content": [{"type": "text", "text": "Debug this"}, image]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.relay.invoke.await_count, 1)
        prompt = self.relay.invoke.await_args.args[2]["text"]
        roster = json.loads(prompt[prompt.index('{"models"'):])["models"]
        self.assertNotIn("guest", [model["id"] for model in roster])

    async def test_unapproved_models_cannot_become_automatic_direct_coordinator(self):
        self.relay.workspace.update(models=[], coordinator=None)
        with self.assertRaisesRegex(ConnectionError, "Connect a model"):
            await self.relay.orchestrator.run("user", "Private history")
        self.relay.invoke.assert_not_awaited()

    async def test_owner_selected_coordinator_is_an_explicit_recipient_grant(self):
        self.relay.workspace.update(models=[], coordinator="coder")
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["text"], "Done")
        self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["coder", "coder"])

    async def test_revoked_recipient_grant_after_planning_blocks_worker(self):
        async def revoke(*args):
            self.relay.workspace["models"].remove("coder")
            return self.plan()
        self.relay.invoke.side_effect = revoke
        with self.assertRaisesRegex(ConnectionError, "permitted tasks changed"):
            await self.relay.orchestrator.run("user", "Private history")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_revoked_coordinator_grant_blocks_synthesis(self):
        self.relay.workspace["models"].remove("planner")
        replies = [self.plan([self.task(), self.task(task_type="debugging")]), {"text": "Code"}, {"text": "Review"}]
        async def revoke(*args):
            reply = replies.pop(0)
            if not replies:
                self.relay.workspace["coordinator"] = None
            return reply
        self.relay.invoke.side_effect = revoke
        with self.assertRaisesRegex(PermissionError, "no longer authorized"):
            await self.relay.orchestrator.run("user", "Private history")
        self.assertEqual(self.relay.invoke.await_count, 3)

    async def test_offline_or_foreign_coordinator_never_calls_models(self):
        for identity in ("offline", "foreign"):
            self.relay.workspace["coordinator"] = identity
            with self.assertRaises(ConnectionError):
                await self.relay.orchestrator.run("user", "Code this")
        self.relay.invoke.assert_not_awaited()

    async def test_unconfigured_coordinator_uses_first_connected_model(self):
        self.relay.workspace["coordinator"] = None
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["routing"]["coordinator"], "planner")

    async def test_plain_general_chat_needs_no_delegation_and_preserves_history(self):
        for profile in self.relay.agent_profiles.values():
            profile["delegation_enabled"] = False
        self.relay.workspace["coordinator"] = None
        history = [{"role": "user", "content": "Make a hello world program"},
                   {"role": "assistant", "content": 'print("Hello, world!")'},
                   {"role": "user", "content": "Now use a function"}]
        self.relay.invoke.side_effect = [{"text": "def hello(): ...", "provider": "custom", "usage": {"output_tokens": 9}}]
        result = await self.relay.orchestrator.run("user", {"messages": history})
        self.relay.invoke.assert_awaited_once_with("user", "planner", {"messages": history}, None)
        self.assertEqual(result["routing"]["mode"], "direct")
        self.assertEqual(result["usage"], {"output_tokens": 9})

    async def test_offline_delegates_do_not_bypass_configured_task_permissions(self):
        for identity, profile in self.relay.agent_profiles.items():
            profile["delegation_enabled"] = identity == "offline"
        with self.assertRaisesRegex(ConnectionError, "No eligible models"):
            await self.relay.orchestrator.run("user", "Code this")
        self.relay.invoke.assert_not_awaited()

    async def test_basic_general_chat_preserves_images_and_requires_vision(self):
        for profile in self.relay.agent_profiles.values():
            profile["delegation_enabled"] = False
        image = {"type": "image", "name": "bug.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        content = [{"type": "text", "text": "Read this screenshot"}, image]
        payload = {"messages": [{"role": "user", "content": content}]}
        with self.assertRaisesRegex(ConnectionError, "image support"):
            await self.relay.orchestrator.run("user", payload)
        self.relay.invoke.assert_not_awaited()
        self.relay.agent_profiles["planner"]["vision"] = True
        self.relay.invoke.side_effect = [{"text": "I can see it"}]
        await self.relay.orchestrator.run("user", payload)
        self.assertEqual(self.relay.invoke.await_args.args[2], payload)

    async def test_endpoint_requires_auth_and_reports_invalid_input_and_planning_failure(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://relay") as client:
            self.assertEqual((await client.post("/orchestrate", json={"text": "Code this"})).status_code, 401)
            self.assertEqual((await client.post("/orchestrate", headers=self.headers, json={"provider": "override"})).status_code, 400)
            self.relay.invoke.side_effect = [{"text": "bad plan"}]
            response = await client.post("/orchestrate", headers=self.headers, json={"text": "Code this"})
            self.assertEqual(response.status_code, 503)
            self.assertIn("no work was delegated", response.json()["error"])

    async def test_profile_endpoint_accepts_legacy_and_new_preferences_and_rejects_invalid_types(self):
        legacy = {"provider": "custom", "model": "any-model", "vision": False, "running": True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://relay") as client:
            response = await client.post("/agent-profile", headers=self.headers, json=legacy)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.relay.agent_description("user")["delegation_enabled"])
            preferences = {"purpose": "Conversation only", "tasks": ["general"], "delegation_enabled": True}
            response = await client.post("/agent-profile", headers=self.headers, json={**legacy, **preferences})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.relay.agent_description("user")["tasks"], ["general"])
            for invalid in ({"tasks": [["general"]]}, {"delegation_enabled": "true"}, {"purpose": "x" * 1001}, {"tasks": []}):
                response = await client.post("/agent-profile", headers=self.headers, json={**legacy, **preferences, **invalid})
                self.assertEqual(response.status_code, 400)


class RoutingProfileTests(unittest.TestCase):
    def test_existing_profiles_are_opted_out_and_preferences_need_a_purpose_and_tasks(self):
        self.assertEqual(routing_profile({}), {"purpose": "", "tasks": [], "delegation_enabled": False})
        for profile in ({"delegation_enabled": True}, {"delegation_enabled": True, "purpose": "Code"},
                        {"tasks": ["coding", "coding"]}, {"tasks": "coding"}):
            with self.assertRaises(ValueError):
                routing_profile(profile)
