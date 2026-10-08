"""Reference calculation: the accounting of a Zcash transaction from the node's integer fields alone.

This module does not import graphsenselib and shares no code with it. It is the yardstick the validation
measures GraphSense against, so it is kept short enough to read in a few minutes.

Rules implemented. Section names are those of the Zcash Protocol Specification
(https://zips.z.cash/protocol/protocol.pdf); the anchor after "#" opens the section in the PDF.

[#transactions] Transactions and Treestates
    Transparent inputs insert value into the transaction's transparent value pool and transparent outputs
    remove value from it. "The remaining value in the transparent transaction value pool of a non-coinbase
    transaction is available to miners as a transaction fee."
[#joinsplitbalance] Balance (Sprout)
    "vpub_old takes value from the transparent transaction value pool and vpub_new adds value to the
    transparent transaction value pool. As a result, vpub_old is treated like an output value, whereas
    vpub_new is treated like an input value."
[#saplingbalance] Balance and Binding Signature (Sapling)
    "A positive Sapling balancing value takes net value from the Sapling chain value pool and adds it to
    the transparent transaction value pool. A negative Sapling balancing value does the reverse. As a
    result, positive vBalance is treated like an input to the transparent transaction value pool, whereas
    negative vBalance is treated like an output from that pool."
[#orchardbalance] Balance and Binding Signature (Orchard)
    The same sentence for the Orchard balancing value and, from NU6.3, for the Ironwood balancing value.
[#chainvaluepoolbalances] Chain Value Pool Balances
    The Sprout pool balance is the sum of all vpub_old minus the sum of all vpub_new; the Sapling, Orchard
    and Ironwood pool balances are the negation of the sum of all valueBalance fields of that pool; the
    transparent pool balance is the sum of the values of all unspent transparent outputs.
[#subsidies] Calculating Block Subsidy, Funding Streams, Lockbox Disbursement, and Founders' Reward
    The block subsidy as a function of the block height.
[#txnconsensus] Transaction Consensus Rules
    "[NU6 onward] The total output value of a coinbase transaction MUST be equal to its total input value."
    "[Pre-NU6] The total output value of a coinbase transaction MUST NOT be greater than its total input value."

All amounts are integers in zatoshi (1 ZEC = 100,000,000 zatoshi). A pool balance keeps the node's sign:
positive means value left the pool.
"""

from decimal import Decimal

ZATOSHI_PER_ZEC = 100_000_000
POOLS = ("sprout", "sapling", "orchard", "ironwood")

# Mainnet constants of the protocol specification, section "Constants" (#constants) and "Networks".
SLOW_START_INTERVAL = 20_000
PRE_BLOSSOM_HALVING_INTERVAL = 840_000
POST_BLOSSOM_HALVING_INTERVAL = 1_680_000
BLOSSOM_ACTIVATION_HEIGHT = 653_600
BLOSSOM_POW_TARGET_SPACING_RATIO = 2
MAX_BLOCK_SUBSIDY = 1_250_000_000
NU6_ACTIVATION_HEIGHT = 2_726_400


class EvidenceError(ValueError):
    """The evidence does not have the form the calculation needs."""


def _zatoshi(value, where):
    """An amount must be a plain integer; anything else is an error, never silently converted."""
    if type(value) is not int:
        raise EvidenceError(f"{where}: expected an integer amount in zatoshi, found {value!r}")
    return value


def previous_outputs(previous_transactions):
    """Map (txid, output index) to the output's value, from `getrawtransaction <txid> 1` results."""
    return {
        (tx["txid"], out["n"]): _zatoshi(out["valueZat"], f"transaction {tx['txid']} output {out['n']}")
        for tx in previous_transactions
        for out in tx["vout"]
    }


def account(tx, prevouts, pools_read=POOLS):
    """Return the accounting of one transaction as the node's fields determine it.

    `tx` is one element of `tx` in a `getblock <height> 2` result. `prevouts` maps (txid, index) to
    the value of every transparent output this transaction spends.

    `pools_read` names the pools whose balance is taken into account. With all four (the default)
    the result is the correct accounting. With ("sprout", "sapling") it is what a program reports
    that never reads the Orchard and Ironwood balances; the validation uses that to explain every
    difference between the old GraphSense code and the correct values.
    """
    txid = tx["txid"]
    is_coinbase = any("coinbase" in vin for vin in tx["vin"])
    spent = [(vin["txid"], vin["vout"]) for vin in tx["vin"] if "coinbase" not in vin]
    missing = [outpoint for outpoint in spent if outpoint not in prevouts]
    if missing:
        raise EvidenceError(
            f"transaction {txid}: previous output {missing[0][0]}:{missing[0][1]} is not in the evidence"
        )

    transparent_input = sum(prevouts[outpoint] for outpoint in spent)
    transparent_output = sum(_zatoshi(out["valueZat"], f"transaction {txid} output {out['n']}") for out in tx["vout"])

    # Sprout has no single balance field. Each JoinSplit carries vpub_old (into the pool) and vpub_new (out of it).
    joinsplits = tx.get("vjoinsplit") or []
    vpub_new = [_zatoshi(js["vpub_newZat"], f"transaction {txid} vpub_newZat") for js in joinsplits]
    vpub_old = [_zatoshi(js["vpub_oldZat"], f"transaction {txid} vpub_oldZat") for js in joinsplits]
    if "sprout" not in pools_read:
        vpub_new, vpub_old = [], []

    # The other pools each carry one signed balance. An absent field or bundle means zero.
    balance = {
        "sprout": sum(vpub_new) - sum(vpub_old),
        "sapling": _zatoshi(tx.get("valueBalanceZat", 0), f"transaction {txid} valueBalanceZat"),
        "orchard": _zatoshi(
            (tx.get("orchard") or {}).get("valueBalanceZat", 0), f"transaction {txid} orchard.valueBalanceZat"
        ),
        "ironwood": _zatoshi(
            (tx.get("ironwood") or {}).get("valueBalanceZat", 0), f"transaction {txid} ironwood.valueBalanceZat"
        ),
    }
    for pool in POOLS:
        if pool not in pools_read:
            balance[pool] = 0

    # The specification treats value leaving a pool like an input and value entering a pool like an output.
    # GraphSense represents exactly that: one shielded input per positive leg, one shielded output per negative leg.
    single = [balance[pool] for pool in ("sapling", "orchard", "ironwood")]
    shielded_inputs = [v for v in vpub_new if v > 0] + [b for b in single if b > 0]
    shielded_outputs = [v for v in vpub_old if v > 0] + [-b for b in single if b < 0]

    total_input = transparent_input + sum(shielded_inputs)
    total_output = transparent_output + sum(shielded_outputs)
    remaining = transparent_input - transparent_output + sum(balance.values())
    assert total_input - total_output == remaining

    return {
        "is_coinbase": is_coinbase,
        "transparent_input": transparent_input,
        "transparent_output": transparent_output,
        "sprout_value_balance": balance["sprout"],
        "sapling_value_balance": balance["sapling"],
        "orchard_value_balance": balance["orchard"],
        "ironwood_value_balance": balance["ironwood"],
        "transparent_input_count": len(spent),
        "transparent_output_count": len(tx["vout"]),
        "shielded_inputs": sorted(shielded_inputs),
        "shielded_outputs": sorted(shielded_outputs),
        "input_count": len(spent) + len(shielded_inputs),
        "output_count": len(tx["vout"]) + len(shielded_outputs),
        "total_input": total_input,
        "total_output": total_output,
        # A coinbase transaction pays no fee: its remaining value is not available to anyone.
        "fee": 0 if is_coinbase else remaining,
    }


def shapes(acc):
    """Name the shapes a transaction has, by rules on the values of `account()` alone.

    A positive pool balance is not by itself an unshielding. It is one only when transparent outputs
    receive more than the transparent inputs provide, so that pool value must have reached them.
    """
    balance = {pool: acc[f"{pool}_value_balance"] for pool in POOLS}
    nonzero = [pool for pool in POOLS if balance[pool] != 0]
    net_transparent = acc["transparent_input"] - acc["transparent_output"]
    found = []
    if acc["is_coinbase"]:
        found.append("coinbase")
        if any(b < 0 for b in balance.values()):
            found.append("shielded-coinbase")
    else:
        if not nonzero:
            found.append("transparent-only")
        for pool in POOLS:
            if balance[pool] < 0 and net_transparent > 0:
                found.append(f"shielding:{pool}")
            if balance[pool] > 0 and net_transparent < 0:
                found.append(f"unshielding:{pool}")
        if acc["transparent_input_count"] == 0 and acc["transparent_output_count"] == 0 and acc["fee"] > 0:
            found.append("fully-shielded-fee")
            if len(nonzero) == 1:  # one pool only: its positive balance is the fee and nothing else
                found.append(f"fully-shielded-fee:{nonzero[0]}")
    if any(b > 0 for b in balance.values()) and any(b < 0 for b in balance.values()):
        found.append("cross-pool")
    if balance["sprout"] != 0:
        found.append("sprout")
    if balance["sapling"] != 0 and balance["orchard"] != 0:
        found.append("sapling+orchard")
    if balance["ironwood"] != 0:
        found.append("ironwood")
    if len(nonzero) >= 3:
        found.append("three-pool")
    return sorted(found)


def block_subsidy(height):
    """Block subsidy in zatoshi at a Mainnet height (specification section #subsidies)."""
    slow_start_shift = SLOW_START_INTERVAL // 2
    if height < slow_start_shift:
        return (MAX_BLOCK_SUBSIDY // SLOW_START_INTERVAL) * height
    if height < SLOW_START_INTERVAL:
        return (MAX_BLOCK_SUBSIDY // SLOW_START_INTERVAL) * (height + 1)
    if height < BLOSSOM_ACTIVATION_HEIGHT:
        halving = (height - slow_start_shift) // PRE_BLOSSOM_HALVING_INTERVAL
        return MAX_BLOCK_SUBSIDY // (2**halving)
    # floor(a/b + c/d) computed exactly on integers: floor((a*d + c*b) / (b*d))
    numerator = (BLOSSOM_ACTIVATION_HEIGHT - slow_start_shift) * POST_BLOSSOM_HALVING_INTERVAL + (
        height - BLOSSOM_ACTIVATION_HEIGHT
    ) * PRE_BLOSSOM_HALVING_INTERVAL
    halving = numerator // (PRE_BLOSSOM_HALVING_INTERVAL * POST_BLOSSOM_HALVING_INTERVAL)
    return MAX_BLOCK_SUBSIDY // (BLOSSOM_POW_TARGET_SPACING_RATIO * 2**halving)


def block_totals(block, accounts):
    """Derive a block's pool changes from its transactions and set them beside the node's own figures.

    `accounts` is the list of `account()` results for the block's transactions, in block order.

    The node keeps a running balance per value pool. `valuePools[*].valueDeltaZat` in a block is the
    change that block made. For the four shielded pools that change is minus the sum of the
    transactions' balances; for the transparent pool it is outputs created minus outputs spent.
    The "lockbox" entry is set by consensus parameters, not by transactions, so it is only read.

    The sum of all six changes is the value the block created. From NU6 onward consensus requires
    the coinbase to claim exactly subsidy plus fees, so the sum equals the block subsidy. Before NU6
    a miner could claim less, so the sum may be smaller.
    """
    derived = {"transparent": sum(a["transparent_output"] - a["transparent_input"] for a in accounts)}
    for pool in POOLS:
        derived[pool] = -sum(a[f"{pool}_value_balance"] for a in accounts)
    height = block["height"]
    node = {
        entry["id"]: _zatoshi(entry.get("valueDeltaZat"), f"block {height} valuePools {entry['id']} valueDeltaZat")
        for entry in block["valuePools"]
    }
    return {
        "derived_deltas": derived,
        "node_deltas": node,
        "fees": sum(a["fee"] for a in accounts),
        "node_delta_sum": sum(node.values()),
        "subsidy": block_subsidy(height),
        "coinbase_must_claim_everything": height >= NU6_ACTIVATION_HEIGHT,
    }


def dual_form_mismatches(node, path="$"):
    """List every place where a decimal ZEC field and its integer zatoshi sibling denote different amounts.

    The node prints many amounts twice, for example "value" (decimal ZEC) and "valueZat" (integer).
    The evidence must be parsed with `parse_float=decimal.Decimal` so the decimal is compared exactly.
    """
    problems = []
    if isinstance(node, dict):
        for key, value in node.items():
            sibling = key + "Zat"
            if sibling in node and not isinstance(value, (dict, list)):
                is_number = isinstance(value, (int, Decimal)) and not isinstance(value, bool)
                if not is_number or type(node[sibling]) is not int or value * ZATOSHI_PER_ZEC != node[sibling]:
                    problems.append(f"{path}.{key}={value} but {sibling}={node[sibling]}")
            problems.extend(dual_form_mismatches(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            problems.extend(dual_form_mismatches(item, f"{path}[{index}]"))
    return problems


def count_dual_forms(node):
    """Count the decimal/integer pairs `dual_form_mismatches` looks at (reported, so "0 mismatches" has a denominator)."""
    if isinstance(node, dict):
        own = sum(1 for key, value in node.items() if key + "Zat" in node and not isinstance(value, (dict, list)))
        return own + sum(count_dual_forms(value) for value in node.values())
    if isinstance(node, list):
        return sum(count_dual_forms(item) for item in node)
    return 0
