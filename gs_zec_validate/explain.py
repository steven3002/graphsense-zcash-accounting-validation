"""`explain`: show, for one case, how its expected values follow from the evidence. Uses no GraphSense code."""

from . import PROG, evidence, reference
from . import manifest as manifest_module
from .compare import OLD_CODE_READS
from .validate import EXIT_PASS, EXIT_USAGE


def _n(value):
    return f"{value:,}"


def _signed(value):
    return f"{value:+,}" if value else "0"


def explain(root, key, echo=print):
    manifest, _ = manifest_module.load(root)
    matches = [case for case in manifest["cases"] if case["id"] == key or case["txid"].startswith(key.lower())]
    if len(matches) != 1:
        echo(
            f"{PROG}: error: '{key}' names {len(matches)} cases; give a case id such as 1687194/4 or the start of a transaction id"
        )
        return EXIT_USAGE
    case = matches[0]
    block_entry = next(block for block in manifest["blocks"] if block["height"] == case["block_height"])
    files = {item["txid"]: item["evidence"] for item in manifest["previous_transactions"]}
    previous = [
        evidence.parse(evidence.read_verified(root, files[txid])) for txid in block_entry["previous_transactions"]
    ]
    prevouts = reference.previous_outputs(previous)

    echo(f"case {case['id']}   transaction {case['txid']}")
    echo(f"block {case['block_height']} {case['block_hash']}, transaction version {case['version']}")
    echo(case["description"])
    for entry in block_entry["evidence"]:
        block = evidence.parse(evidence.read_verified(root, entry))
        tx = block["tx"][case["index"]]
        a = reference.account(tx, prevouts)
        echo("")
        echo(f"From {entry['path']} (capture: {entry['capture']})")
        for vin in tx["vin"]:
            if "coinbase" in vin:
                echo("  input   coinbase (creates value, spends nothing)")
            else:
                echo(
                    f"  input   spends {vin['txid']}:{vin['vout']}   worth {_n(prevouts[(vin['txid'], vin['vout'])]):>16}   (from {files[vin['txid']]['path']})"
                )
        echo(
            f"  transparent inputs   {_n(a['transparent_input']):>16}   (number of inputs: {a['transparent_input_count']})"
        )
        echo(
            f"  transparent outputs  {_n(a['transparent_output']):>16}   (number of outputs: {a['transparent_output_count']}; sum of valueZat)"
        )
        joinsplits = tx.get("vjoinsplit") or []
        echo(
            f"  Sprout balance       {_signed(a['sprout_value_balance']):>16}   (JoinSplits: {len(joinsplits)}; sum of vpub_newZat minus sum of vpub_oldZat)"
        )
        echo(f"  Sapling balance      {_signed(a['sapling_value_balance']):>16}   (valueBalanceZat)")
        echo(f"  Orchard balance      {_signed(a['orchard_value_balance']):>16}   (orchard.valueBalanceZat)")
        echo(
            f"  Ironwood balance     {_signed(a['ironwood_value_balance']):>16}   (ironwood.valueBalanceZat{'' if 'ironwood' in tx else '; the transaction has no ironwood member'})"
        )
        remaining = (
            a["transparent_input"]
            - a["transparent_output"]
            + sum(a[f"{pool}_value_balance"] for pool in reference.POOLS)
        )
        echo(
            f"  remaining value      {_signed(remaining):>16}   = transparent inputs - transparent outputs + the four balances"
        )
        if a["is_coinbase"]:
            echo(f"  fee                  {0:>16}   (a coinbase transaction pays no fee)")
        else:
            echo(f"  fee                  {_n(a['fee']):>16}   = the remaining value")
        echo("  As GraphSense represents it: a positive balance is a shielded input, a negative one a shielded output.")
        echo(f"    shielded inputs  {a['shielded_inputs']}   shielded outputs {a['shielded_outputs']}")
        echo(
            f"    total input {_n(a['total_input'])}   total output {_n(a['total_output'])}   inputs {a['input_count']}   outputs {a['output_count']}"
        )
        echo(f"  shapes: {', '.join(reference.shapes(a))}")
        same = all(a[field] == case["expected"][field] for field in case["expected"])
        echo(f"  equals the expected values in the manifest: {'yes' if same else 'NO'}")
        model = reference.account(tx, prevouts, pools_read=OLD_CODE_READS)
        echo("  A parser that never reads the Orchard and Ironwood balances would report:")
        echo(
            f"    total input {_n(model['total_input'])}   total output {_n(model['total_output'])}   fee {_n(model['fee'])}"
        )
    echo("")
    echo(f"Old parser: {case['old_code']['why']}")
    for line in case["references"]:
        echo(f"See also: {line}")
    return EXIT_PASS
