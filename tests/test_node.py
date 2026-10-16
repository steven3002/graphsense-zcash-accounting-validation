"""The JSON-RPC client: credentials, Zebra's envelope, other envelopes, and every way a node can fail."""

import base64
import json
import types

import pytest

from conftest import FakeNodeServer
from gs_zec_validate import node


@pytest.fixture()
def serve():
    servers = []

    def start(reply):
        servers.append(FakeNodeServer(reply))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def arguments(url, cookie=None, user=None, variable="GS_TEST_PASSWORD"):
    return types.SimpleNamespace(rpc_url=url, rpc_cookie_file=cookie, rpc_user=user, rpc_password_env=variable)


def test_zebra_envelope_gives_the_result_bytes_unchanged(serve):
    result = b'{"height":5,"value":0.10000000,"valueZat":10000000}'
    server = serve(lambda body: (200, b'{"jsonrpc":"2.0","id":1,"result":' + result + b"}"))
    parsed, content = node.Node(server.url).result("getblock", ["5", 2])
    assert content == result and str(parsed["value"]) == "0.10000000"


def test_other_envelopes_are_parsed_and_written_again(serve):
    server = serve(lambda body: (200, b'{"result":{"a":1.5,"b":[1,2]},"error":null,"id":1}'))
    parsed, content = node.Node(server.url).result("x", [])
    assert json.loads(content) == {"a": 1.5, "b": [1, 2]} and parsed["b"] == [1, 2]


def test_cookie_credentials_are_sent_as_basic_authentication(serve, tmp_path):
    cookie = tmp_path / ".cookie"
    cookie.write_text("__cookie__:secret\n")
    server = serve(lambda body: (200, b'{"jsonrpc":"2.0","id":1,"result":3}'))
    client = node.connect(arguments(server.url, cookie=str(cookie)))
    assert client.call("getblockcount", []) == 3 and client.calls == 1
    assert server.received[0][1] == "Basic " + base64.b64encode(b"__cookie__:secret").decode()
    assert "secret" not in repr(client.__dict__.get("url"))


def test_password_from_the_environment(serve, monkeypatch):
    monkeypatch.setenv("GS_TEST_PASSWORD", "pw")
    server = serve(lambda body: (200, b'{"jsonrpc":"2.0","id":1,"result":3}'))
    node.connect(arguments(server.url, user="u")).call("getblockcount", [])
    assert server.received[0][1] == "Basic " + base64.b64encode(b"u:pw").decode()


def test_missing_credentials_are_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("GS_TEST_PASSWORD", raising=False)
    with pytest.raises(node.NodeError, match="GS_TEST_PASSWORD is not set"):
        node.credentials(arguments("http://x", user="u"))
    with pytest.raises(node.NodeError, match="cannot read the cookie file"):
        node.credentials(arguments("http://x", cookie=str(tmp_path / "missing")))
    assert node.credentials(arguments("http://x")) is None


def test_refused_credentials(serve):
    server = serve(lambda body: (401, b""))
    with pytest.raises(node.NodeError, match="refused the credentials"):
        node.Node(server.url, "a:b").call("getinfo", [])


def test_error_member(serve):
    server = serve(lambda body: (200, b'{"jsonrpc":"2.0","id":1,"error":{"code":-8,"message":"no such block"}}'))
    with pytest.raises(node.NodeError, match="no such block"):
        node.Node(server.url).call("getblock", ["9", 2])


def test_http_error_with_error_member(serve):
    server = serve(lambda body: (500, b'{"result":null,"error":{"code":-5,"message":"not found"},"id":1}'))
    with pytest.raises(node.NodeError, match="HTTP 500: .*not found"):
        node.Node(server.url).call("getrawtransaction", ["ab", 1])


def test_not_json(serve):
    server = serve(lambda body: (200, b"<html>"))
    with pytest.raises(node.NodeError, match="not JSON"):
        node.Node(server.url).call("getinfo", [])
    server = serve(lambda body: (200, b"[1]"))
    with pytest.raises(node.NodeError, match="not a JSON-RPC response"):
        node.Node(server.url).call("getinfo", [])
    server = serve(lambda body: (200, b'{"id":1}'))
    with pytest.raises(node.NodeError, match="no result"):
        node.Node(server.url).call("getinfo", [])


def test_unreachable_node():
    with pytest.raises(node.NodeError, match="no usable answer"):
        node.Node("http://127.0.0.1:9", timeout=5).call("getinfo", [])
