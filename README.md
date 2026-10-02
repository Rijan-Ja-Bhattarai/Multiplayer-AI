# Info
Multiplayer AI is a platform where authorized users by the session leader can invite people to work on the same session without sharing accounts or people 
from the same organization. 

### Under Development 

### Multi-device agent networking

An authenticated relay and reconnecting device client are available in
`network_a2a/`. Connect multiple laptops/desktops over LAN or the internet,
route requests between local agents, or forward requests to a local A2A server.

See [setup and deployment instructions](NETWORK_SETUP.md) for credentials,
client commands, HTTPS/WSS deployment, and current limits.

Provider adapters support Ollama, Bionic GPT, OpenAI, Claude, Gemini, Groq,
DeepSeek, Mistral, OpenRouter, and custom OpenAI-compatible endpoints. See
[provider setup and examples](PROVIDER_ADAPTERS.md).
