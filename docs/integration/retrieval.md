# Retrieval

Relevance is not permission. `POST /v1/retrieve` returns only documents the
caller's identity may read, based on access metadata declared in
`config/knowledge.yaml` (tenant, department, classification, group ACLs).

```python
def retrieve(token: str, source: str, query: str, top_k: int = 5) -> list[dict]:
    r = httpx.post(f"{PLANE}/v1/retrieve", headers={"Authorization": f"Bearer {token}"},
                   json={"source": source, "query": query, "top_k": top_k})
    r.raise_for_status()
    return r.json()["results"]      # already filtered, redaction applied
```

Swap this in where the RAG pipeline reads the vector store directly. The
identity's `tenant`, `department`, `clearance`, and `groups` claims drive the
filter; the `redact` obligation from policy applies to returned text.

Task leases are not evaluated on this edge. Retrieval is a read; if a read of
a particular source should itself be task-scoped, call `/v1/authorize` with
an action such as `knowledge.read` and the source as the resource before
retrieving.
