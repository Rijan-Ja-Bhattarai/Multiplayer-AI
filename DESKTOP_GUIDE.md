# Multiplayer AI desktop

Multiplayer AI now has a native Qt desktop application. The previous web UI has
been removed. The networking APIs and command-line agent tools remain available.

## Launch the Windows app

Download `MultiplayerAI-Windows-x64.zip` from the release and extract **all**
files. Double-click **Install-MultiplayerAI.cmd**. It installs the bundled app
for your Windows account, adds Desktop and Start menu shortcuts, and opens it.
No Python installation, dependency commands, or administrator access is needed.
Keep the two installer scripts beside the `MultiplayerAI` folder until installation
finishes. Close the app and run the installer from a new release to update it;
your settings and saved credentials are preserved. Installation needs no download.

The installer is unsigned. Only run a release you trust. You can also use the app
directly without installing:

Open `dist/MultiplayerAI/MultiplayerAI.exe`. Keep the executable and its
`_internal` folder together when copying the application to another Windows
device. The app runs without a separately installed Python environment.

For development or other supported desktop operating systems:

```sh
python -m pip install -r requirements-desktop.txt
python -m desktop_app
```

On Linux you also need an unlocked Secret Service keyring. Windows uses Windows
Credential Manager, and macOS uses Keychain. The app requires a working secure
credential store; it does not fall back to writing secrets to a plaintext file.

## What happens automatically

On launch, the app:

1. Creates or restores this device's private identity.
2. Starts an authenticated local relay on an available port, reusing its saved
   port on subsequent launches.
3. Connects a device agent so you can exchange real connectivity messages
   immediately, without configuring a model.
4. Restores model agents marked **Start automatically**.
5. Restores the selected workspace, its saved conversations, and any draft.
   Rejoins a selected remote workspace; if its host is unavailable, saved history
   remains readable and the app retries. You can switch to an owned workspace
   while waiting.

Closing the app disconnects its agents and shuts down its local relay. There is
no background service left running after the window closes. The app supports a
single instance per preferences directory.

## Create and manage workspaces

Click **+** in the left rail and choose **Create a workspace**. Give it a name;
the app opens a separate workspace with its own chats, models, members, and
invitations. Click a workspace's initials in the rail to switch to it. Your owned
workspaces keep their relays and models running while you view another workspace,
so their members can continue working. All owned relays stop when the app closes.

Open **Workspace settings** to rename a workspace and see its members and online
status. Owners can invite members and click **Remove** to revoke a member's
invitation, close its connection, and remove access to shared chats. Removing a
local model also removes its configuration and saved credentials. Members can
view the list; the owner controls the workspace name and membership.

**Delete workspace** removes the owned workspace's saved chats, configuration,
and credentials on this device and disconnects its members. Other workspaces
remain available; deleting the last workspace creates a fresh one. In a joined
workspace, **Leave workspace** removes its local history and saved invitation.
When the host is reachable, leaving also revokes that invitation on the host.
If the host is offline or its membership is managed separately by an operator,
the invitation is forgotten locally; the owner can revoke it on the host.

## Connect a model

Open **Providers** or click **Connect a model**. Choose a provider, enter its
model ID, configure the API root if needed, and enter its API key. Click
**Connect agent**; the app launches the adapter in its own networking thread.
There is no generated terminal command or separate agent process to start.

For Ollama, start the Ollama application and install your chosen model first.
**Find models** lists installed Ollama models or models available at a compatible
provider's API root. You can always enter the model ID directly.
For Bionic GPT or a custom compatible endpoint, enter your deployment's API root.

The **Providers** page lists imported models. Click **Edit model** to change the
model, instructions, image support, or internet access. Models running on another
device are edited on that device. Model names stay fixed so existing chats and
invitations keep their identity.

To enable internet access, select **Allow this model to search the web with
SearXNG**, enter your SearXNG server URL, and choose **Automatic** or **Always**.
Automatic mode lets the model request a search for current or uncertain facts;
it cannot guarantee detection of everything absent from its training data.
Always mode searches the question before each answer. The app supplies up to
five result snippets and source links to the model. It does not fetch full pages.
Use **Test web search** to check the endpoint before saving. Your server must
enable JSON search in `settings.yml`, as described in the
[SearXNG Search API](https://docs.searxng.org/dev/search_api.html):

```yaml
search:
  formats:
    - html
    - json
```

Internet access is off until you enable it for that model. Search queries go to
your configured SearXNG server; provider API keys are sent only to the provider.

## Attach images and PDFs

In a model's conversation, click **Attach images / PDFs** and select your files.
The app reads them locally and shows the prepared files above the composer.
Add a question and send, or send the files alone to request an analysis. **Remove**
discards an attachment before sending. Files work in direct and shared chats,
and their content remains available in saved conversation history for follow-ups.
The selected model's provider receives the prepared content when you send it;
members invited to a shared chat can see its attachments.

Text PDFs are extracted for any model. Enable **image support** with a
vision-capable model to send images or scanned PDFs. Vision mode includes PDF
page images for diagrams as well as extracted text. For PDFs above 16 pages,
only extracted text is included; split the PDF to include page images. Scanned
PDFs need at most 16 pages per file. Password-protected PDFs must be unlocked.

Attach up to eight files, each up to 20 MiB, within an 8 MiB prepared request
and about 190 KB of extracted text. PDFs can have up to 200 pages. Large documents
must be split. Images are resized to fit 1600 pixels and converted to JPEG;
animated GIFs use their first frame. No OCR or image capability is added to a
text-only model by enabling a checkbox.

Tokens and API keys are stored in the OS credential store. Nonsecret settings
(model IDs, endpoints, instructions, and autostart choices) live in:

- Windows: `%LOCALAPPDATA%\MultiplayerAI\settings.json`
- Linux: `$XDG_CONFIG_HOME/multiplayer-ai/settings.json` or
  `~/.config/multiplayer-ai/settings.json`
- macOS: `~/.config/multiplayer-ai/settings.json`

Use `--data-dir PATH` to select a different preferences directory.
The same directory contains `workspaces.json` and the default workspace's
`history.sqlite3`. Additional workspaces keep separate settings and history
under `workspaces/<workspace-id>/`. Chat history is saved on disk; tokens and
provider keys remain in the OS credential store with workspace-specific keys.
If the app closes unexpectedly, `desktop-crash.log` in the preferences directory
records Python exceptions, Qt messages, and native crash details for diagnosis.

## Invite another device

In your local workspace, click **Invite a device**. Give the other device a new
identity, then specify a relay address it can reach. Each invitation contains a
different private token. Copy it and send it privately to the intended user.

For devices on a trusted LAN, leave **Share this relay on my local network**
enabled and check the suggested IP address. Creating the invitation makes the
relay listen on the LAN; the host firewall must allow the displayed port.
Sharing remains enabled across app launches after you opt in. A `ws://` LAN
connection is plaintext, so use WSS for untrusted networks.

For internet connections, use a reachable TLS-protected relay:

- Join the publicly hosted relay described in [NETWORK_SETUP.md](NETWORK_SETUP.md)
  using its WSS URL and a device token supplied by its operator; or
- Put an HTTPS/WebSocket reverse proxy in front of your desktop's relay and use
  its public WSS address in the invitation.

The app automatically sets up its local services, credentials, and connections.
It does not provision a cloud server, configure router port forwarding, change
firewall rules, or bypass carrier NAT. Use a public relay or a suitable private
network/VPN when devices cannot reach one another directly. The host app must
stay open if its relay is serving the workspace.

## Join a workspace

Click **+** in the left workspace rail and choose **Join a workspace**. Paste the invitation, or enter the WSS
relay address and the device's token. The app validates the invitation, connects
the device, and shows the agents in its group. This workspace is remembered for
the next launch. Use the workspace's initials to return to it, or click **M** to
open your first owned workspace. Joining another workspace keeps the existing
ones in the rail.
While joining, the button shows **Connecting…**. Success opens an online peer's
conversation; if nobody else is online, the app shows the agent list instead.
A timeout or unreachable host produces a visible error. LAN invitations require
a reachable host address on your network and an allowed host firewall port.
Use a different invitation for each laptop: an identity already connected on
another device cannot be reused.

In a remote workspace, configuring a model uses your invited identity. Hosts
can create multiple local model identities; a remote device needs additional
invited tokens to expose additional identities. Provider agents in your local
workspace continue running while you use a remote workspace.

## Work with your team

**Overview** shows real agent counts and activity from the current visit.
Counts include model agents, excluding users and connectivity devices. Click
the workspace agent count or the **agents** button in a chat to see each model's
name, provider, model ID, and online status.
**Agents** supports search, editing local model configuration, and stopping local
model agents. **Conversations** sends requests to connected agents and includes
history in follow-ups to provider agents. Unconfigured device agents reply with
connectivity echoes rather than AI-generated text.
Model replies display Markdown headings, bold and italic text, lists, links,
tables, and code blocks. You can select and copy their rendered text; the
original message stays intact for conversation history and follow-up requests.
Requests from another device appear in its conversation on the receiving desktop,
with an unread count in the agent list and the local agent's response below.
Select the sending device to read incoming messages and send a request back.
To share the **same AI conversation**, open the model's chat and click **Invite
to conversation**. Create a new invitation for the other device, then paste it
into **+ → Join a workspace** on that device. The shared chat appears on both devices with the
existing messages. Either participant can send the next message; everyone sees
the AI reply and the model receives their combined conversation history.
An ordinary **Invite a device** invitation connects the workspace without sharing
a chat. Shared chats are visible only to their invited devices.

The app uses black backgrounds and white text, with a workspace rail and agent
sidebar. **Reduce animations** in Providers disables page fades.

Direct chats, shared chats, and their model context are saved as messages arrive
and restored after switching workspaces or reopening the app. Shared history and
membership persist on the host; guests also keep a local copy for offline
reading. Existing invitations remain valid across a host restart unless revoked.
Keep the host app open to send new requests and synchronize a shared workspace.

The complete chat archive remains saved. Follow-up requests send up to 100 recent
user and assistant messages within a 190 KB transport budget; older messages
remain readable but are outside that model context window. Large individual
shared replies can be shortened to fit the relay. Requests interrupted by an
app shutdown are marked unsuccessful and never automatically replayed.

## Build a Windows distribution

From an environment with the desktop dependencies installed:

```sh
python -m pip install pyinstaller
python -m PyInstaller --noconfirm MultiplayerAI.spec
python package_windows_release.py
```

The build creates `dist/MultiplayerAI/`. This is a local, unsigned distribution;
build on each operating system you want to distribute to, and sign releases
before public distribution. The checked build in this workspace targets Windows.
The packaging script creates `dist/MultiplayerAI-Windows-x64.zip` with the
complete app, installer scripts, and this guide. Upload that ZIP as a release asset.
The Windows build omits unused Qt QML, PDF, virtual keyboard, translation,
and image-format components. It retains the native interface, animations,
networking, credentials, and all supported provider adapters. Source code,
tests, build tools, and local settings are not included in the release ZIP.

## Verify

```sh
python -m pytest
```

Desktop runtime tests cover automatic relay startup, secure-token separation,
provider routing, two-desktop messaging, saved chat context after reopening,
workspace creation/switching/deletion, member revocation, invalid
invitation recovery, TLS validation, and orderly shutdown. Native UI flows are
also checked with Qt's offscreen platform. Provider calls in verification use
mock endpoints; no paid cloud calls are made.
