"""Optional: compare the packaged evidence with an operator's own node (`verify-node`).

The validation itself never needs this; it works offline from the packaged files. This command is for an
operator who does not want to take the evidence on trust. It asks the node for every evidence block
(`getblock <height> 2`) and every previous transaction (`getrawtransaction <txid> 1`), 53 calls in all after
one `getinfo`, one at a time, and checks two things:

  accounting   the reference calculation, run on the node's answer, gives the manifest's expected values, and
               the node reports the same block hash, transaction ids and value-pool changes;
  everything   the node's answer equals the packaged capture leaf by leaf, apart from the differences the
               manifest allows. Differences here do not fail the check when the accounting agrees: another
               node version may print additional members.

The password is read from a file or an environment variable and is never printed or stored.
"""

import base64
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

from . import envs, evidence, reference
from . import manifest as manifest_module
from .compare import FIELDS
from .validate import EXIT_FAIL, EXIT_PASS


class NodeError(envs.SetupError):
    """The node cannot be reached, refuses the credentials, or answers in a form that cannot be compared."""


def _credentials(arguments):
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


def _call(url, credentials, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json"}
    if credentials:
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=120) as response:
            answer = json.loads(response.read(), parse_float=Decimal)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise NodeError(f"the node refused the credentials (HTTP {error.code})") from None
        raise NodeError(f"{method}: the node answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise NodeError(f"{method}: no usable answer from the node: {getattr(error, 'reason', error)}") from None
    if answer.get("error"):
        raise NodeError(f"{method} {params}: the node reports an error: {answer['error']}")
    return answer["result"]


def verify(root, arguments, echo=print):
    try:
        manifest, _ = manifest_module.load(root)
        credentials = _credentials(arguments)
        info = _call(arguments.rpc_url, credentials, "getinfo", [])
        echo(f"node        {info.get('subversion', 'unknown version')}, height {info.get('blocks')}")
        rules = manifest["capture_differences_allowed"]
        accounting_problems, other_differences = [], 0

        previous = {}
        for item in manifest["previous_transactions"]:
            packaged = evidence.parse(evidence.read_verified(root, item["evidence"]))
            fresh = _call(arguments.rpc_url, credentials, "getrawtransaction", [item["txid"], 1])
            previous[item["txid"]] = fresh
            ours = [(out["n"], out["valueZat"]) for out in packaged["vout"]]
            theirs = [(out.get("n"), out.get("valueZat")) for out in fresh.get("vout", [])]
            if fresh.get("txid") != item["txid"] or ours != theirs:
                accounting_problems.append(
                    f"previous transaction {item['txid']}: the node's outputs differ from the packaged evidence"
                )
            unexpected, _ = evidence.differences(packaged, fresh, rules)
            other_differences += len(unexpected)
        echo(f"previous    {len(previous)} transactions fetched")
        prevouts = reference.previous_outputs(previous.values())

        cases = {(case["block_height"], case["index"]): case for case in manifest["cases"]}
        for block in manifest["blocks"]:
            height = block["height"]
            fresh = _call(arguments.rpc_url, credentials, "getblock", [str(height), 2])
            before = len(accounting_problems)
            if fresh.get("hash") != block["hash"]:
                accounting_problems.append(
                    f"block {height}: the node has block {fresh.get('hash')}, the manifest {block['hash']}"
                )
                continue
            if [tx.get("txid") for tx in fresh["tx"]] != [
                cases[(height, i)]["txid"] for i in range(block["transaction_count"])
            ]:
                accounting_problems.append(f"block {height}: the node lists other transactions")
                continue
            accounts = [reference.account(tx, prevouts) for tx in fresh["tx"]]
            for index, account in enumerate(accounts):
                case = cases[(height, index)]
                for field in FIELDS:
                    if account[field] != case["expected"][field]:
                        accounting_problems.append(
                            f"case {case['id']} {field}: your node's data gives {account[field]}, the manifest says {case['expected'][field]}"
                        )
            if "valuePools" in fresh:
                totals = reference.block_totals(fresh, accounts)
                for pool, value in block["expected"]["value_pool_deltas"].items():
                    if totals["node_deltas"].get(pool) != value:
                        accounting_problems.append(
                            f"block {height} {pool} pool: your node reports a change of {totals['node_deltas'].get(pool)}, the manifest says {value}"
                        )
            differences = 0
            for entry in block["evidence"]:
                packaged = evidence.parse(evidence.read_verified(root, entry))
                unexpected, _ = evidence.differences(packaged, fresh, rules)
                differences = max(differences, len(unexpected))
            other_differences += differences
            status = "accounting identical" if len(accounting_problems) == before else "ACCOUNTING DIFFERS"
            echo(f"block {height:<8} {status}; {differences} other differences from the packaged capture")
    except (reference.EvidenceError, KeyError, TypeError) as error:
        raise NodeError(f"the node's answer does not have the expected form: {error}") from None
    echo("")
    for problem in accounting_problems:
        echo(f"  - {problem}")
    if accounting_problems:
        echo(f"RESULT: your node disagrees with the packaged evidence in {len(accounting_problems)} places.")
        return EXIT_FAIL
    echo(
        f"RESULT: your node confirms the packaged evidence: every hash, transaction id, amount and value-pool change agrees "
        f"({other_differences} differences in members the accounting does not use)."
    )
    return EXIT_PASS
