# Model calls

The zero-code edge. Any OpenAI-compatible client works unchanged by pointing
`base_url` at agent-plane and using the agent's bearer token as the API key.
The upstream provider keys live on the server (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, Azure settings) and the model catalog in
`config/models.yaml`.

```python
from openai import OpenAI
client = OpenAI(base_url="http://agent-plane:8000/v1", api_key=agent_jwt)
resp = client.chat.completions.create(model="gpt-4.1", messages=[...])
```

```ts
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://agent-plane:8000/v1", apiKey: agentJwt });
```

```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4.1", openai_api_base="http://agent-plane:8000/v1", openai_api_key=agent_jwt)
```

What every call gets: policy allow / deny / redact, per-user token quota,
provider routing with fallback, content-derived classification that can only
escalate, and a signed audit record. The response carries an `x_control_plane`
block with the decision id; `GET /v1/audit` (admin) lists the records.

What this edge does **not** do: evaluate task leases. A model call is not a
side effect. Put `POST /v1/authorize` in front of the tool the model asks
for; see [tool calls](tool-calls.md) and [frameworks](frameworks.md).

Endpoints: `GET /v1/models`, `POST /v1/chat/completions`, `GET /v1/usage`.
