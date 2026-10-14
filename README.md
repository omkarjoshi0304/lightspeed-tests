# rhos-lightspeed-tests

Test suite for RHOS Lightspeed in RHOSO environments.

## Setup

```bash
uv venv
uv sync
```

## Configuration

Default settings are in `config/default.yaml`:

```yaml
rhos_lightspeed:
  base_url: "http://localhost:8080"
  timeout: 30

query:
  default_question: "What is OpenStack?"
```

Override the endpoint via environment variable:

```bash
export RHOS_LIGHTSPEED_URL=http://rhos-lightspeed:8080
```

If you run the tests outside of OpenShift, you need to have a running port forward
for the `lightspeed-app-server` service. For example:

```
oc port-forward -n openstack-lightspeed svc/lightspeed-app-server 8443:8443
```

## Running tests

```bash
# Run all tests
uv run pytest

# Run integration tests only
uv run pytest tests/integration/

# Generate JUnit XML (automatic via pyproject.toml config)
uv run pytest  # outputs test-results.xml
```

## Goose API integration tests

The Goose tests simulate the HTTP requests made by the OpenStack Goose image
against a deployed Lightspeed service. They exercise `/v1/responses` using
structured messages, streaming, `store: false`, Bearer authentication, and
`X-LCS-Merge-Server-Tools: true`. They do not launch the Goose container.

The contract follows the pinned
[container entrypoint](https://github.com/openstack-k8s-operators/s2i-openstack-containers/blob/5f1ddc43e54607f1b8e2a5b0101be1109bf11115/containers/goose/goose/openstack-goose-entrypoint.sh)
and the
[Goose Responses serializer](https://github.com/aaif-goose/goose/blob/61830521ad3134fe1ffa32f62d95ede205216bfc/crates/goose-provider-types/src/formats/openai_responses.rs).

Set `RHOS_LIGHTSPEED_URL` to the service origin (without `/v1`) and supply the
token through `RHOS_LIGHTSPEED_TOKEN`, as for the existing integration tests.
Set `RHOS_LIGHTSPEED_MODEL` to the same value as the Goose container's
`LIGHTSPEED_MODEL`; alternatively, set `goose.model` in `config/default.yaml`.
The Goose tests fail with a configuration message if no model is supplied.

```bash
export RHOS_LIGHTSPEED_MODEL="<same value as Goose LIGHTSPEED_MODEL>"
uv run pytest tests/integration/test_goose_responses.py
```

The three scenarios cover a simple OpenStack question, a follow-up with replayed
message history, and a question with a Goose client function tool attached.
Assertions check successful stream completion, structured assistant text, and
retention of the client tool, with broad OpenStack checks instead of exact
generated wording. The history scenario supplies a unique OpenStack project
name only in the first turn and requires the follow-up to recall it.

The client-tool scenario supports inline OKP retrieval without server-configured
tools. Inline retrieval does not require a response tool entry, and Lightspeed
can hide server MCP tools from client responses. This scenario checks client
tool compatibility; it does not establish server-tool merging or tool execution.

When using the test-operator playbook, supply `lightspeed_model` in your
`AnsibleTest` variables. It is forwarded as `RHOS_LIGHTSPEED_MODEL`.

## Adding tests

1. Create test file in appropriate directory:
   - `tests/unit/` - Fast, isolated tests
   - `tests/integration/` - Tests requiring deployed service
   - `tests/e2e/` - Full end-to-end scenarios

2. Use the shared fixtures from `conftest.py`:
   ```python
   class TestMyFeature:
       def test_something(self, client, config):
           response = client.query("my question")
           assert response.status_code == 200
   ```

3. Run: `uv run pytest tests/integration/test_my_feature.py`

## Project structure

```
rhos-lightspeed-tests/
├── config/
│   └── default.yaml                 # Default test configuration
├── src/rhos_lightspeed_tests/       # Shared utilities
│   ├── __init__.py
│   ├── client.py                    # API client (query, health)
│   └── config.py                    # Reads default.yaml, env var overrides
├── tests/
│   ├── conftest.py                  # Shared fixtures (config, client)
│   ├── unit/
│   ├── integration/
│   │   └── test_query_endpoint.py   # First test: /query endpoint
│   └── e2e/
├── pyproject.toml
└── README.md
```
