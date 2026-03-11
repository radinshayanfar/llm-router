# Routing Proxy Server

A Flask-based HTTP proxy that routes incoming requests to different target domains based on configurable criteria (URL path, HTTP method, and JSON body field values). Each route can also rewrite the JSON request body before forwarding.

## Features

- **Content-based routing** – match on any JSON body field (e.g. `model`, `user.tier`, …)
- **Path & method matching** – route by URL pattern and HTTP method
- **Multiple body conditions** – combine conditions; all must pass for a route to match
- **Request header injection** – add or overwrite headers per route before forwarding
- **JSON body modifications** – add, overwrite, or remove keys at any nesting level
- **Default route** – evaluated in order like any other route; also acts as the ultimate fallback if no route matches
- **Hot-reload** – edit `routes.json` without restarting the proxy
- Forwards all HTTP methods, headers, query params, cookies, and the request body unchanged (unless modified by rules)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
cp routes.example.json routes.json   # create and edit your routes
cp .env.example .env                  # optional: configure host/port/file path
python proxy.py
```

The proxy starts on `http://0.0.0.0:5000` by default.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PROXY_HOST` | `0.0.0.0` | Host to bind the proxy server |
| `PROXY_PORT` | `5000` | Port to bind the proxy server |
| `ROUTES_FILE` | `routes.json` | Path to the routes configuration file |

## Routes configuration

All routing and modification logic lives in a single `routes.json` file (see `routes.example.json` for a full example).

```json
{
  "routes": [
    {
      "name":   "Model-A",
      "target": "https://api-model-a.example.com",
      "match": {
        "path_pattern": ".*",
        "methods": ["POST"],
        "body": [
          { "key_path": "model", "operator": "eq", "value": "Model-A" }
        ]
      },
      "modifications": [
        { "action": "set",    "key_path": "proxy_injected", "value": true },
        { "action": "remove", "key_path": "internal_token" }
      ]
    },
    {
      "name":    "Default",
      "default": true,
      "target":  "https://fallback.example.com",
      "modifications": []
    }
  ]
}
```

### Route fields

| Field | Required | Description |
|---|---|---|
| `name` | no | Human-readable label shown in logs |
| `target` | **yes** | Base URL of the upstream server |
| `default` | no | `true` → marks this route as the fallback; it is still evaluated in order with its own `match` criteria (omit `match` to match everything) |
| `match` | no | Criteria for selecting this route (see below) |
| `headers` | no | Key-value map of headers to add/overwrite on the forwarded request |
| `modifications` | no | Body rewrite rules applied before forwarding |

### Match criteria

All conditions in `match` must pass. Omitting a field means "match anything".

| Field | Description |
|---|---|
| `path_pattern` | Regex matched against the request path (default: `.*`) |
| `methods` | HTTP method whitelist, e.g. `["POST", "PUT"]` (default: all methods) |
| `body` | List of body conditions – **all** must be satisfied |

Routes are evaluated **in order**; the first fully-matching route is used. A route marked `"default": true` is evaluated just like any other route in its position, but is also remembered as the ultimate fallback — if it is placed last with no `match` criteria it naturally catches everything, while still allowing it to appear earlier with its own conditions.

### Body conditions

```json
{ "key_path": "user.tier", "operator": "eq", "value": "premium" }
```

| Field | Description |
|---|---|
| `key_path` | Dot-notation path into the JSON body, e.g. `"model"` or `"user.tier"` |
| `operator` | See table below (default: `"eq"`) |
| `value` | Expected value (not needed for `exists` / `not_exists`) |

| Operator | Passes when… |
|---|---|
| `eq` | `body[key_path] == value` |
| `ne` | `body[key_path] != value` |
| `in` | `body[key_path]` is a member of the `value` list, e.g. `"value": ["Model-A", "Model-B"]` |
| `contains` | `value` is a substring of `body[key_path]` (both strings) |
| `regex` | `value` regex matches `body[key_path]` |
| `exists` | the key is present in the body |
| `not_exists` | the key is absent from the body |

### Body modifications

| Field | Description |
|---|---|
| `action` | `"set"` to add/overwrite, `"remove"` to delete |
| `key_path` | Dot-notation path, e.g. `"metadata.source"` |
| `value` | Value to set (any JSON type). Required for `"set"`, ignored for `"remove"` |

Intermediate objects are created automatically when using `"set"` on a nested path.

## Production deployment

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 proxy:app
```

## License

MIT
