#!/usr/bin/env python3
"""
Routing Proxy Server with Flask
Routes requests to different target domains based on configurable criteria:
path pattern, HTTP method, and JSON body field conditions.
Each route can also apply JSON body modifications (add, overwrite, remove keys)
before forwarding. A single default route acts as the fallback.
"""

import json
import os
import re
from flask import Flask, request, Response
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Path to the routes configuration file
ROUTES_FILE = os.environ.get('ROUTES_FILE', 'routes.json')

# Cache for routes: avoids re-reading the file on every request unless it changes
_routes_cache: dict = {'mtime': None, 'routes': []}


def load_routes() -> list:
    """
    Load routes from ROUTES_FILE.
    Automatically reloads when the file changes on disk so routes can be
    updated without restarting the proxy.
    """
    global _routes_cache
    if not os.path.exists(ROUTES_FILE):
        return []
    try:
        mtime = os.path.getmtime(ROUTES_FILE)
        if mtime != _routes_cache['mtime']:
            with open(ROUTES_FILE, 'r') as f:
                data = json.load(f)
            _routes_cache = {'mtime': mtime, 'routes': data.get('routes', [])}
            print(f"[proxy] Loaded {len(_routes_cache['routes'])} route(s) from '{ROUTES_FILE}'")
        return _routes_cache['routes']
    except (json.JSONDecodeError, IOError) as e:
        print(f"[proxy] Warning: Could not load routes from '{ROUTES_FILE}': {e}")
        return []


# ---------------------------------------------------------------------------
# Nested key helpers
# ---------------------------------------------------------------------------

def get_nested(obj: dict, key_path: str):
    """
    Retrieve a value from *obj* using dot-notation *key_path*.
    Returns ``(value, found)`` – *found* is False when any part of the path
    is missing.
    """
    keys = key_path.split('.')
    current = obj
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None, False
        current = current[key]
    return current, True


def set_nested(obj: dict, key_path: str, value) -> None:
    """
    Set *value* at the dot-notation *key_path* inside *obj*.
    Intermediate dicts are created automatically when they are missing.

    Example:
        set_nested(obj, "user.address.city", "Berlin")
        # obj["user"]["address"]["city"] == "Berlin"
    """
    keys = key_path.split('.')
    current = obj
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def remove_nested(obj: dict, key_path: str) -> None:
    """
    Remove the key at the dot-notation *key_path* from *obj*.
    Does nothing if any part of the path does not exist.

    Example:
        remove_nested(obj, "user.sensitive_token")
        # obj["user"]["sensitive_token"] is deleted (if present)
    """
    keys = key_path.split('.')
    current = obj
    for key in keys[:-1]:
        if not isinstance(current, dict) or key not in current:
            return  # Path doesn't exist – nothing to remove
        current = current[key]
    if isinstance(current, dict):
        current.pop(keys[-1], None)


# ---------------------------------------------------------------------------
# Route matching
# ---------------------------------------------------------------------------

def matches_body_condition(body_data, condition: dict) -> bool:
    """
    Evaluate a single body condition against *body_data* (a parsed JSON dict).

    Supported operators (``condition.operator``):
      - ``"eq"``         – exact equality (default)
      - ``"ne"``         – not equal
      - ``"in"``         – actual value is a member of the expected list
      - ``"contains"``   – substring check (both actual and expected must be strings)
      - ``"regex"``      – regex search on the actual string value
      - ``"exists"``     – key is present (no ``value`` required)
      - ``"not_exists"`` – key is absent  (no ``value`` required)
    """
    key_path = condition.get('key_path', '').strip()
    if not key_path:
        return False

    operator = condition.get('operator', 'eq').lower()
    expected = condition.get('value')

    if not isinstance(body_data, dict):
        return False

    actual, found = get_nested(body_data, key_path)

    if operator == 'exists':
        return found
    if operator == 'not_exists':
        return not found
    if not found:
        return False
    if operator == 'eq':
        return actual == expected
    if operator == 'ne':
        return actual != expected
    if operator == 'in':
        return isinstance(expected, list) and actual in expected
    if operator == 'contains':
        return isinstance(actual, str) and isinstance(expected, str) and expected in actual
    if operator == 'regex':
        return isinstance(actual, str) and bool(re.search(str(expected), actual))
    return False


def find_matching_route(request_path: str, method: str, body_data) -> dict | None:
    """
    Iterate through all configured routes in order and return the first one
    whose ``match`` criteria are all satisfied. Routes marked ``"default": true``
    are skipped during normal evaluation and only used as a fallback when no
    other route matches.

    Matching criteria (all must pass):
      1. ``match.path_pattern``  – regex matched against the request path
      2. ``match.methods``       – HTTP method whitelist
      3. ``match.body``          – list of body conditions (all must be true)
    """
    routes = load_routes()
    default_route = None

    for route in routes:
        # Record the first default route as fallback, but still evaluate it normally
        if route.get('default', False) and default_route is None:
            default_route = route

        match_cfg = route.get('match', {})

        # 1. Path pattern
        path_pattern = match_cfg.get('path_pattern', '.*')
        if not re.search(path_pattern, request_path):
            continue

        # 2. HTTP method
        allowed_methods = [m.upper() for m in match_cfg.get(
            'methods', ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']
        )]
        if method.upper() not in allowed_methods:
            continue

        # 3. Body conditions (all must match)
        body_conditions = match_cfg.get('body', [])
        if body_conditions:
            if not all(matches_body_condition(body_data, cond) for cond in body_conditions):
                continue

        return route  # First fully-matching route wins

    return default_route  # Fallback


def apply_modifications(data: dict, modifications: list) -> bool:
    """
    Apply a list of ``set``/``remove`` modifications to *data* in place.
    Returns ``True`` if at least one modification was applied.
    """
    modified = False
    for mod in modifications:
        action = mod.get('action', '').lower()
        key_path = mod.get('key_path', '').strip()
        if not key_path:
            continue
        if action == 'set':
            set_nested(data, key_path, mod.get('value'))
            modified = True
        elif action == 'remove':
            remove_nested(data, key_path)
            modified = True
    return modified


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def proxy(path):
    """
    Receive an incoming request, select the matching route, optionally rewrite
    the JSON body, and forward the request to the route's target domain.
    """
    request_path = f'/{path}'

    # Parse the JSON body once – needed for both route matching and modifications
    body = request.get_data()
    body_data = None
    content_type = request.headers.get('Content-Type', '')
    is_json = 'application/json' in content_type and bool(body)
    if is_json:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                body_data = parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Select route
    route = find_matching_route(request_path, request.method, body_data)
    if not route:
        return Response("Proxy Error: No matching route and no default route configured.", status=502)

    target_domain = route.get('target', '').rstrip('/')
    if not target_domain:
        return Response("Proxy Error: Matched route has no 'target' domain.", status=502)

    # Prepare headers
    headers = {key: value for key, value in request.headers if key.lower() not in ['host', 'connection']}

    # Inject/overwrite route-level headers
    route_headers = route.get('headers', {})
    if route_headers:
        headers.update(route_headers)

    # Apply body modifications (only when we have a parsed JSON dict)
    if is_json and body_data is not None:
        if apply_modifications(body_data, route.get('modifications', [])):
            body = json.dumps(body_data, separators=(',', ':')).encode('utf-8')
            headers['Content-Length'] = str(len(body))

    # Build target URL
    target_url = f"{target_domain}/{path}"
    if request.query_string:
        target_url += f"?{request.query_string.decode('utf-8')}"

    # Forward the request
    try:
        response = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=body,
            cookies=request.cookies,
            allow_redirects=False,
            stream=True
        )

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [
            (name, value) for name, value in response.raw.headers.items()
            if name.lower() not in excluded_headers
        ]

        route_name = route.get('name', target_domain)
        print(f"[proxy] {request.method} {request_path} → {target_domain} (route: {route_name}) [{response.status_code}]")

        return Response(response.content, status=response.status_code, headers=response_headers)

    except requests.exceptions.RequestException as e:
        return Response(f"Proxy Error: {str(e)}", status=502)


if __name__ == '__main__':
    host = os.environ.get('PROXY_HOST', '0.0.0.0')
    port = int(os.environ.get('PROXY_PORT', 5000))

    print("Starting routing proxy server...")
    print(f"Listening on: {host}:{port}")

    routes = load_routes()
    if routes:
        for i, r in enumerate(routes):
            name = r.get('name', f'route-{i + 1}')
            target = r.get('target', '(no target)')
            tag = ' [DEFAULT]' if r.get('default') else ''
            print(f"  {name} → {target}{tag}")
    else:
        print(f"  No routes loaded from '{ROUTES_FILE}'. Create it to start routing.")

    app.run(host=host, port=port, debug=True)
