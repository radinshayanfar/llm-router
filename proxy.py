#!/usr/bin/env python3
"""
Transparent Proxy Server with Flask
Forwards all requests to a configured target domain while preserving paths, headers, and methods.
Supports modifying JSON request bodies (add, modify, remove keys at any nesting level) before forwarding.
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

# Configure the target domain (can be set via environment variable)
TARGET_DOMAIN = os.environ.get('TARGET_DOMAIN', 'https://example.com')

# Remove the trailing slash if present
TARGET_DOMAIN = TARGET_DOMAIN.rstrip('/')

# Path to the JSON modification rules file
JSON_RULES_FILE = os.environ.get('JSON_RULES_FILE', 'json_rules.json')

# Cache for JSON rules: avoids re-reading the file on every request unless it changes
_rules_cache: dict = {'mtime': None, 'rules': []}


def load_json_rules() -> list:
    """
    Load JSON modification rules from JSON_RULES_FILE.
    Automatically reloads the file when it has been modified on disk,
    so rules can be updated without restarting the proxy.
    """
    global _rules_cache
    if not os.path.exists(JSON_RULES_FILE):
        return []
    try:
        mtime = os.path.getmtime(JSON_RULES_FILE)
        if mtime != _rules_cache['mtime']:
            with open(JSON_RULES_FILE, 'r') as f:
                data = json.load(f)
            _rules_cache = {'mtime': mtime, 'rules': data.get('rules', [])}
            print(f"[proxy] Loaded {len(_rules_cache['rules'])} JSON rule(s) from '{JSON_RULES_FILE}'")
        return _rules_cache['rules']
    except (json.JSONDecodeError, IOError) as e:
        print(f"[proxy] Warning: Could not load JSON rules from '{JSON_RULES_FILE}': {e}")
        return []


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


def apply_json_modifications(body: bytes, request_path: str, method: str):
    """
    Parse *body* as a JSON object, apply all matching rules from the rules file,
    and return a tuple ``(new_body_bytes, was_modified)``.

    Rules are matched by ``path_pattern`` (regex on the URL path) and ``methods``.
    Each rule carries a list of modifications with actions:
      - ``"set"``    – add or overwrite a key (supports dot-notation for nesting)
      - ``"remove"`` – delete a key (supports dot-notation for nesting)
    """
    rules = load_json_rules()
    if not rules:
        return body, False

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body, False  # Not valid JSON – pass through unchanged

    if not isinstance(data, dict):
        return body, False  # Only JSON objects are supported

    modified = False
    for rule in rules:
        match_cfg = rule.get('match', {})
        path_pattern = match_cfg.get('path_pattern', '.*')
        allowed_methods = [m.upper() for m in match_cfg.get(
            'methods', ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']
        )]

        if not re.search(path_pattern, request_path):
            continue
        if method.upper() not in allowed_methods:
            continue

        for mod in rule.get('modifications', []):
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

    if modified:
        return json.dumps(data, separators=(',', ':')).encode('utf-8'), True
    return body, False


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def proxy(path):
    """
    Proxy all requests to the target domain.
    Preserves the path, query parameters, headers, and request body.
    """
    # Construct the target URL
    target_url = f"{TARGET_DOMAIN}/{path}"
    
    # Preserve query parameters
    if request.query_string:
        target_url += f"?{request.query_string.decode('utf-8')}"
    
    # Prepare headers (exclude some headers that should not be forwarded)
    headers = {key: value for key, value in request.headers if key.lower() not in ['host', 'connection']}

    # Get request body
    body = request.get_data()

    # Apply JSON modifications when the request carries a JSON body
    content_type = request.headers.get('Content-Type', '')
    if 'application/json' in content_type and body:
        body, was_modified = apply_json_modifications(body, f'/{path}', request.method)
        if was_modified:
            # Keep Content-Length in sync with the rewritten body
            headers['Content-Length'] = str(len(body))

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
        
        # Prepare response headers (exclude some headers)
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [
            (name, value) for name, value in response.raw.headers.items()
            if name.lower() not in excluded_headers
        ]
        
        # Return the response
        return Response(
            response.content,
            status=response.status_code,
            headers=response_headers
        )
    
    except requests.exceptions.RequestException as e:
        return Response(
            f"Proxy Error: {str(e)}",
            status=502
        )


if __name__ == '__main__':
    host = os.environ.get('PROXY_HOST', '0.0.0.0')
    port = int(os.environ.get('PROXY_PORT', 5000))
    
    print(f"Starting transparent proxy server...")
    print(f"Listening on: {host}:{port}")
    print(f"Forwarding to: {TARGET_DOMAIN}")
    if os.path.exists(JSON_RULES_FILE):
        rules = load_json_rules()
        print(f"JSON rules file: {JSON_RULES_FILE} ({len(rules)} rule(s) loaded)")
    else:
        print(f"JSON rules file: not found at '{JSON_RULES_FILE}' (create it to enable request modifications)")
    
    app.run(host=host, port=port, debug=True)
