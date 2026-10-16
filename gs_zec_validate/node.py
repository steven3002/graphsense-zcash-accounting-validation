"""JSON-RPC access to an operator's Zcash node.

The credentials are read from a cookie file or from an environment variable, kept in memory and sent as HTTP
Basic authentication. They are never printed, logged or written to a file.
"""

import base64
import json
import os
import threading
import urllib.error
import urllib.request
from decimal import Decimal

from . import envs

# The envelope Zebra writes when the request id is 1. The bytes after it are the `result` member as the node sent it.
ENVELOPE = b'{"jsonrpc":"2.0","id":1,"result":'


class NodeError(envs.SetupError):
    """The node cannot be reached, refuses the credentials, or answers in a form that cannot be used."""


def add_arguments(parser, required=True):
    """The command-line options every command that talks to a node shares."""
    parser.add_argument(
        "--rpc-url",
        required=required,
        metavar="URL",
        help="JSON-RPC endpoint of a Zcash node with a transaction index, e.g. http://127.0.0.1:8232",
    )
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--rpc-cookie-file", metavar="FILE", help="file holding 'user:password' (a node's .cookie file)")
    auth.add_argument(
        "--rpc-user",
        metavar="USER",
        help="user name; the password is read from the environment variable named by --rpc-password-env",
    )
    parser.add_argument(
        "--rpc-password-env",
        metavar="NAME",
        default="GS_ZEC_VALIDATE_RPC_PASSWORD",
        help="name of the environment variable that holds the password (default: GS_ZEC_VALIDATE_RPC_PASSWORD)",
    )


def credentials(arguments):
    """'user:password' from the cookie file or the environment, or None for a node without authentication."""
    if arguments.rpc_cookie_file:
        try:
            with open(arguments.rpc_cookie_file, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError as error:
            raise NodeError(f"cannot read the cookie file: {error.strerror}") from None
    if arguments.rpc_user:
        password = os.environ.get(arguments.rpc_password_env)
        if password is None:
            raise NodeError(f"the environment variable {arguments.rpc_password_env} is not set")
        return f"{arguments.rpc_user}:{password}"
    return None


class Node:
    """One JSON-RPC endpoint. Safe to use from several threads."""

    def __init__(self, url, secret=None, timeout=300):
        self.url = url
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if secret:
            self._headers["Authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def calls(self):
        return self._calls

    def raw(self, method, params):
        """The response body for one request with id 1, unchanged."""
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        request = urllib.request.Request(self.url, data=body, headers=self._headers)
        with self._lock:
            self._calls += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise NodeError(f"the node refused the credentials (HTTP {error.code})") from None
            detail = _error_member(error)
            raise NodeError(f"{method} {params}: the node answered HTTP {error.code}{detail}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise NodeError(f"{method}: no usable answer from the node: {getattr(error, 'reason', error)}") from None

    def call(self, method, params, parse_float=Decimal):
        """The parsed `result` member. Decimal numbers stay exact unless `parse_float` says otherwise."""
        result, _ = self.result(method, params, parse_float)
        return result

    def result(self, method, params, parse_float=Decimal):
        """(parsed result, bytes of the result member).

        With Zebra's envelope the bytes are cut out of the response unchanged. Any other envelope (zcashd orders the
        members differently) is parsed and the result written again; the values are the same.
        """
        body = self.raw(method, params)
        if body.startswith(ENVELOPE) and body.endswith(b"}"):
            content = body[len(ENVELOPE) : -1]
            try:
                return json.loads(content, parse_float=parse_float), content
            except ValueError:
                pass
        try:
            answer = json.loads(body, parse_float=parse_float)
        except ValueError:
            raise NodeError(f"{method}: the node's answer is not JSON") from None
        if not isinstance(answer, dict):
            raise NodeError(f"{method}: the node's answer is not a JSON-RPC response")
        if answer.get("error"):
            raise NodeError(f"{method} {params}: the node reports an error: {answer['error']}")
        if "result" not in answer:
            raise NodeError(f"{method}: the node's answer has no result")
        content = json.dumps(json.loads(body)["result"], separators=(",", ":")).encode()
        return answer["result"], content


def _error_member(error):
    try:
        answer = json.loads(error.read())
        return f": {answer['error']}" if isinstance(answer, dict) and answer.get("error") else ""
    except (ValueError, OSError, TypeError):
        return ""


def connect(arguments, timeout=300):
    """A Node from the command-line options."""
    return Node(arguments.rpc_url, credentials(arguments), timeout=timeout)
