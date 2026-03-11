# Transparent Proxy Server

A simple transparent proxy server built with Flask that forwards all incoming requests to a configured target domain while preserving paths, headers, and request methods.

## Features

- Forwards all HTTP methods (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
- Preserves request paths and query parameters
- Forwards request headers and body
- Returns responses from the target domain
- Configurable via environment variables
- **JSON request modification** – add, overwrite, or remove keys at any nesting level before forwarding

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
# Set the target domain
export TARGET_DOMAIN=https://api.example.com

# Run the proxy
python proxy.py
```

The proxy will start on `http://0.0.0.0:5000` by default.

### Configuration

Configure the proxy using environment variables:

- `TARGET_DOMAIN`: The target domain to forward requests to (default: `https://example.com`)
- `PROXY_HOST`: Host to bind the proxy server (default: `0.0.0.0`)
- `PROXY_PORT`: Port to bind the proxy server (default: `5000`)
- `JSON_RULES_FILE`: Path to the JSON modification rules file (default: `json_rules.json`)

### Examples

```bash
# Forward to a specific API
export TARGET_DOMAIN=https://jsonplaceholder.typicode.com
python proxy.py

# Now you can access:
# http://localhost:5000/posts → https://jsonplaceholder.typicode.com/posts
# http://localhost:5000/users/1 → https://jsonplaceholder.typicode.com/users/1
```

```bash
# Custom host and port
export TARGET_DOMAIN=https://api.github.com
export PROXY_HOST=127.0.0.1
export PROXY_PORT=8080
python proxy.py
```

## JSON Request Modification

The proxy can rewrite JSON request bodies **before** forwarding them. This lets you add, overwrite, or remove any key at any nesting level without touching the client or the upstream server.

### Setup

```bash
cp json_rules.example.json json_rules.json
# edit json_rules.json to define your rules
```

The rules file is **hot-reloaded** – changes take effect immediately without restarting the proxy.

### Rules file format

```json
{
  "rules": [
    {
      "match": {
        "path_pattern": "^/api/users",
        "methods": ["POST", "PUT"]
      },
      "modifications": [
        { "action": "set",    "key_path": "role",              "value": "guest"   },
        { "action": "set",    "key_path": "meta.source",       "value": "proxy"   },
        { "action": "remove", "key_path": "sensitive_token"                        },
        { "action": "remove", "key_path": "user.password_hint"                     }
      ]
    }
  ]
}
```

| Field | Description |
|---|---|
| `match.path_pattern` | Regular expression matched against the request path (default: `.*`) |
| `match.methods` | HTTP methods this rule applies to (default: all methods) |
| `modifications[].action` | `"set"` to add/overwrite, `"remove"` to delete |
| `modifications[].key_path` | Dot-notation path to the target key, e.g. `"user.address.city"` |
| `modifications[].value` | Value to set (any JSON type). Required for `"set"`, ignored for `"remove"` |

**Dot-notation** allows targeting keys at any depth. When using `"set"`, intermediate objects are created automatically if they don't exist.

```json
// Input body
{ "user": { "name": "Alice" }, "token": "secret" }

// Rule: set "user.role" = "admin", remove "token"
// Output body forwarded to upstream
{ "user": { "name": "Alice", "role": "admin" } }
```

Rules are evaluated in order; multiple rules can match the same request and all matching modifications are applied.

## How It Works

1. The proxy receives an incoming request
2. It constructs the target URL by combining the `TARGET_DOMAIN` with the request path
3. It forwards the request with the same method, headers, body, and query parameters
4. It receives the response from the target domain
5. It returns the response to the client with the same status code and headers

## Production Deployment

For production use, consider using a WSGI server like Gunicorn:

```bash
pip install gunicorn
export TARGET_DOMAIN=https://your-target-domain.com
gunicorn -w 4 -b 0.0.0.0:5000 proxy:app
```

## License

MIT
