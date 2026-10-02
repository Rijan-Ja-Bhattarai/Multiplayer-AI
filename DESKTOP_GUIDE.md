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
5. Rejoins the saved remote workspace if there is one. If it is temporarily
   unavailable, your local workspace remains usable and the app retries.

Closing the app disconnects its agents and shuts down its local relay. There is
no background service left running after the window closes. The app supports a
single instance per preferences directory.

## Connect a model

Open **Providers** or click **Connect a model**. Choose a provider, enter its
model ID, configure the API root if needed, and enter its API key. Click
**Connect agent**; the app launches the adapter in its own networking thread.
There is no generated terminal command or separate agent process to start.

For Ollama, start the Ollama application and install your chosen model first.
**Find local models** lists installed models from the running Ollama service.
For Bionic GPT or a custom compatible endpoint, enter your deployment's API root.

Tokens and API keys are stored in the OS credential store. Nonsecret settings
(model IDs, endpoints, instructions, and autostart choices) live in:

- Windows: `%LOCALAPPDATA%\MultiplayerAI\settings.json`
- Linux: `$XDG_CONFIG_HOME/multiplayer-ai/settings.json` or
  `~/.config/multiplayer-ai/settings.json`
- macOS: `~/.config/multiplayer-ai/settings.json`

Use `--data-dir PATH` to select a different preferences directory.

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

Click **+** in the left workspace rail. Paste the invitation, or enter the WSS
relay address and the device's token. The app validates the invitation, connects
the device, and shows the agents in its group. This workspace is remembered for
the next launch. Click **M** in the rail to return to your local workspace and
stop automatic remote reconnection.

In a remote workspace, configuring a model uses your invited identity. Hosts
can create multiple local model identities; a remote device needs additional
invited tokens to expose additional identities. Provider agents in your local
workspace stop while you use a remote workspace, and resume when you return.

## Work with your team

**Overview** shows real agent counts and activity from the current visit.
**Agents** supports search, editing local model configuration, and stopping local
model agents. **Conversations** sends requests to connected agents and includes
history in follow-ups to provider agents. Unconfigured device agents reply with
connectivity echoes rather than AI-generated text.
Requests from another device appear in its conversation on the receiving desktop,
with an unread count in the agent list and the local agent's response below.
Select the sending device to read incoming messages and send a request back.
This is agent request/response messaging; joining does not share another laptop's
screen, files, or conversations with other agents.

The design uses charcoal surfaces, blurple actions, a workspace rail, an agent
sidebar, fading page transitions, animated hover shapes, and quiet orbital
motion. **Reduce animations** in Providers disables the welcome animation and
page fades. Message history stays in memory and clears when switching workspaces
or closing the app. Failed requests are never automatically replayed.

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
provider routing, two-desktop messaging, saved workspace restoration, invalid
invitation recovery, TLS validation, and orderly shutdown. Native UI flows are
also checked with Qt's offscreen platform. Provider calls in verification use
mock endpoints; no paid cloud calls are made.
