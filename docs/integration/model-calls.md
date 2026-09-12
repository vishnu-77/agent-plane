# Model calls

An advanced edge. Any OpenAI-compatible client works unchanged by pointing
`base_url` at agent-plane. The upstream provider keys live on the server
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, Azure settings) and the model catalog
in `config/models.yaml`.

This edge authenticates with an **agent identity token**, not a Project API
Key: it resolves the caller through `IDENTITY_MODE` (`jwt_claims` or
`delegation`). See [authorization.md](authorization.md#2-credentials).

```python
from openai import OpenAI
client = OpenAI(base_url="http://agent-plane:8000/v1", api_key=agent_token)
resp = client.chat.completions.create(model="gpt-4.1", messages=[...])
```

```ts
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://agent-plane:8000/v1", apiKey: agentToken });
```

```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4.1", openai_api_base="http://agent-plane:8000/v1",
                 openai_api_key=agent_token)
```

What every call gets: policy allow / deny / redact, per-user token quota,
provider routing with fallback, content-derived classification that can only
escalate, and a signed audit record. The response carries an `x_control_plane`
block with the decision id.

What this edge does **not** do: evaluate rules or task leases. A model call is
not a side effect. Put the decision in front of the tool the model asks for -
see [tool calls](tool-calls.md), [connectors](../connectors.md), and
[frameworks](frameworks.md).

Endpoints: `GET /v1/models`, `POST /v1/chat/completions`, `GET /v1/usage`.
