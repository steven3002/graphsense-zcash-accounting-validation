"""The checks of a validation run.

Three groups, in the order they run:

  evidence.*   the evidence is what the manifest says, and its two captures agree
  expected.*   the frozen expected values follow from the evidence (reference calculation, node block totals)
  the rest     what the two GraphSense revisions returned, against the expected and the frozen values

Every function records into a `Checks` collection and never raises for a mismatch: a mismatch is a result.
"""

from . import evidence, reference

# The fields compared for every transaction, and the named check each one belongs to.
#   signs         every pool balance has the node's sign
#   accumulation  every shielded leg is counted, once, on the right side
#   totals        total input, total output and fee
#   resolution    transparent side: previous outputs resolved, outputs summed, coinbase recognised
FIELDS = {
    "is_coinbase": "resolution",
    "transparent_input": "resolution",
    "transparent_output": "resolution",
    "sprout_value_balance": "signs",
    "sapling_value_balance": "signs",
    "orchard_value_balance": "signs",
    "ironwood_value_balance": "signs",
    "shielded_inputs": "accumulation",
    "shielded_outputs": "accumulation",
    "input_count": "accumulation",
    "output_count": "accumulation",
    "total_input": "totals",
    "total_output": "totals",
    "fee": "totals",
}
BALANCE_FIELDS = ("sprout_value_balance", "sapling_value_balance", "orchard_value_balance", "ironwood_value_balance")
OBSERVED_FIELDS = tuple(name for name in FIELDS if name not in BALANCE_FIELDS)  # what the old revision reports too
TOTAL_FIELDS = ("total_input", "total_output", "fee")
OLD_CODE_READS = ("sprout", "sapling")  # pools the old parser reads

TITLES = {
    "evidence.identity": "Evidence files hold the blocks and transactions the manifest names",
    "evidence.captures-agree": "Two captures of the same block differ only where the manifest allows",
    "evidence.units": "Units: decimal and integer forms of every amount in the evidence agree",
    "expected.reference": "Expected values follow from the evidence (reference calculation)",
    "expected.shapes": "Every case has exactly the shapes it is listed under; every required shape is present",
    "expected.block-pools": "Per block, the transactions' balances add up to the node's value-pool changes",
    "expected.block-issuance": "Per block, the value created equals the block subsidy (at most, before NU6)",
    "revisions.identity": "Both GraphSense revisions are the pinned code",
    "graphsense.ran": "Both revisions processed every evidence block without an exception",
    "graphsense.requests": "GraphSense resolved previous outputs from exactly the packaged previous transactions",
    "graphsense.captures-agree": "Each revision returns the same records for both captures of a block",
    "graphsense.offline": "The GraphSense processes attempted no connection other than loopback",
    "units": "Units: every amount GraphSense returned is an integer number of zatoshi",
    "after.pool-balances-exposed": "The after revision exposes per-pool balances",
    "signs": "Signs: the after revision reports every pool balance with the node's sign",
    "accumulation": "Accumulation: the after revision counts every shielded leg once, on the right side",
    "totals": "Totals: the after revision reports the expected total input, total output and fee",
    "resolution": "Resolution: the after revision reports the expected transparent totals",
    "before.frozen": "The before revision reports the values frozen in the manifest",
    "before.explained": "Every difference between the before revision and the expected values is exactly the unread Orchard and Ironwood value",
}


class Check:
    def __init__(self, check_id):
        self.id = check_id
        self.checked = 0
        self.failures = []
        self.applicable = True

    def ok(self, count=1):
        self.checked += count

    def fail(self, message):
        self.checked += 1
        self.failures.append(message)

    def to_dict(self):
        if not self.applicable or self.checked == 0:  # nothing could be checked: that is not a pass
            status = "not-applicable"
        else:
            status = "fail" if self.failures else "pass"
        return {
            "id": self.id,
            "title": TITLES[self.id],
            "status": status,
            "checked": self.checked,
            "failed": len(self.failures),
            "failures": self.failures,
        }


class Checks:
    """The ordered list of checks of one run."""

    def __init__(self):
        self.items = {name: Check(name) for name in TITLES}

    def __getitem__(self, name):
        return self.items[name]

    def to_list(self):
        return [check.to_dict() for check in self.items.values()]

    def failed(self):
        return [check.id for check in self.items.values() if check.to_dict()["status"] == "fail"]


def short(txid):
    return f"{txid[:8]}…{txid[-4:]}"


def label(case):
    return f"case {case['id']} ({short(case['txid'])})"


# evidence.* and expected.*


def check_evidence(manifest, blocks, previous, checks):
    """`blocks` maps height -> {capture name: parsed block}; `previous` maps txid -> parsed transaction.

    Returns {height: number of allowed differences between the block's two captures} for blocks that have two.
    """
    cases = {(case["block_height"], case["index"]): case for case in manifest["cases"]}
    rules = manifest["capture_differences_allowed"]
    allowed_differences = {}
    for entry in manifest["blocks"]:
        height = entry["height"]
        captures = blocks[height]
        names = list(captures)
        for name, block in captures.items():
            where = f"block {height} ({name})"
            if block.get("hash") != entry["hash"] or block.get("height") != height:
                checks["evidence.identity"].fail(
                    f"{where}: the file holds block {block.get('height')} {block.get('hash')}"
                )
                continue
            txids = [tx.get("txid") for tx in block.get("tx", [])]
            if len(txids) != entry["transaction_count"]:
                checks["evidence.identity"].fail(
                    f"{where}: {len(txids)} transactions, the manifest says {entry['transaction_count']}"
                )
                continue
            checks["evidence.identity"].ok()
            for index, txid in enumerate(txids):
                case = cases.get((height, index))
                if case is None or case["txid"] != txid:
                    checks["evidence.identity"].fail(
                        f"{where}: transaction {txid} at position {index} has no case in the manifest"
                    )
                else:
                    checks["evidence.identity"].ok()
            problems = reference.dual_form_mismatches(block)
            checks["evidence.units"].ok(reference.count_dual_forms(block) - len(problems))
            for problem in problems:
                checks["evidence.units"].fail(f"{where}: {problem}")
        for other in names[1:]:
            unexpected, allowed = evidence.differences(captures[names[0]], captures[other], rules)
            allowed_differences[height] = len(allowed)
            if unexpected:
                for problem in unexpected:
                    checks["evidence.captures-agree"].fail(f"block {height}: {names[0]} versus {other}: {problem}")
            else:
                checks["evidence.captures-agree"].ok()
    for item in manifest["previous_transactions"]:
        tx = previous[item["txid"]]
        where = f"previous transaction {item['txid']}"
        if tx.get("txid") != item["txid"]:
            checks["evidence.identity"].fail(f"{where}: the file holds transaction {tx.get('txid')}")
        elif tx.get("height") != item["block_height"] or tx.get("blockhash") != item["block_hash"]:
            checks["evidence.identity"].fail(
                f"{where}: the file places it in block {tx.get('height')} {tx.get('blockhash')}"
            )
        else:
            checks["evidence.identity"].ok()
        problems = reference.dual_form_mismatches(tx)
        checks["evidence.units"].ok(reference.count_dual_forms(tx) - len(problems))
        for problem in problems:
            checks["evidence.units"].fail(f"{where}: {problem}")
    return allowed_differences


def check_expected(manifest, blocks, previous, checks):
    """Recompute every expected value from the evidence and compare with the manifest.

    Returns (accounts, models, block_results): the reference accounting per txid, the same with the Orchard and
    Ironwood balances left out (what the old parser is expected to report), and one summary per block.
    """
    accounts, models, block_results = {}, {}, []
    try:
        prevouts = reference.previous_outputs(previous.values())
    except (reference.EvidenceError, KeyError, TypeError) as error:
        checks["expected.reference"].fail(f"previous transactions: {error}")
        prevouts = {}
    cases = {(case["block_height"], case["index"]): case for case in manifest["cases"]}
    seen_shapes = set()
    for entry in manifest["blocks"]:
        height = entry["height"]
        for name, block in blocks[height].items():
            where = f"block {height} ({name})"
            try:
                block_accounts = [reference.account(tx, prevouts) for tx in block["tx"]]
                block_models = [reference.account(tx, prevouts, pools_read=OLD_CODE_READS) for tx in block["tx"]]
                totals = reference.block_totals(block, block_accounts)
            except (reference.EvidenceError, KeyError, TypeError) as error:
                checks["expected.reference"].fail(f"{where}: {error}")
                continue
            for index, (tx, account, model) in enumerate(zip(block["tx"], block_accounts, block_models, strict=True)):
                case = cases.get((height, index))
                if case is None or case["txid"] != tx["txid"]:
                    continue  # reported by evidence.identity
                accounts.setdefault(case["txid"], account)
                models.setdefault(case["txid"], model)
                for field in FIELDS:
                    if account[field] == case["expected"][field]:
                        checks["expected.reference"].ok()
                    else:
                        checks["expected.reference"].fail(
                            f"{label(case)} {field}: the manifest says {case['expected'][field]}, the evidence ({name}) gives {account[field]}"
                        )
                spent = [
                    {"txid": vin["txid"], "index": vin["vout"], "value": prevouts[(vin["txid"], vin["vout"])]}
                    for vin in tx["vin"]
                    if "coinbase" not in vin
                ]
                if spent == case["previous_outputs"]:
                    checks["expected.reference"].ok()
                else:
                    checks["expected.reference"].fail(
                        f"{label(case)} previous_outputs: the manifest's list differs from the evidence ({name})"
                    )
                not_read = {"orchard": account["orchard_value_balance"], "ironwood": account["ironwood_value_balance"]}
                if not_read == case["old_code"]["not_read"]:
                    checks["expected.reference"].ok()
                else:
                    checks["expected.reference"].fail(
                        f"{label(case)} old_code.not_read: the manifest says {case['old_code']['not_read']}, the evidence ({name}) gives {not_read}"
                    )
                shapes = reference.shapes(account)
                seen_shapes.update(shapes)
                if shapes == sorted(case["shapes"]):
                    checks["expected.shapes"].ok()
                else:
                    checks["expected.shapes"].fail(
                        f"{label(case)} shapes: the manifest says {sorted(case['shapes'])}, the evidence ({name}) gives {shapes}"
                    )

            pools = {}
            for pool, derived in totals["derived_deltas"].items():
                node = totals["node_deltas"].get(pool)
                frozen = entry["expected"]["value_pool_deltas"][pool]
                pools[pool] = {"node": node, "derived": derived, "match": node == derived == frozen}
                if node == derived == frozen:
                    checks["expected.block-pools"].ok()
                else:
                    checks["expected.block-pools"].fail(
                        f"{where} {pool} pool: node reports {node}, transactions give {derived}, the manifest says {frozen}"
                    )
            lockbox = totals["node_deltas"].get("lockbox")
            if (
                lockbox == entry["expected"]["value_pool_deltas"]["lockbox"]
                and totals["fees"] == entry["expected"]["fees"]
                and totals["subsidy"] == entry["expected"]["subsidy"]
            ):
                checks["expected.block-pools"].ok()
            else:
                checks["expected.block-pools"].fail(
                    f"{where}: lockbox change {lockbox}, fees {totals['fees']}, subsidy {totals['subsidy']}; the manifest says "
                    f"{entry['expected']['value_pool_deltas']['lockbox']}, {entry['expected']['fees']}, {entry['expected']['subsidy']}"
                )
            created = totals["node_delta_sum"]
            must_equal = totals["coinbase_must_claim_everything"]
            holds = created == totals["subsidy"] if must_equal else created <= totals["subsidy"]
            if holds:
                checks["expected.block-issuance"].ok()
            else:
                relation = "must equal" if must_equal else "must not exceed"
                checks["expected.block-issuance"].fail(
                    f"{where}: the pool changes add up to {created}, which {relation} the block subsidy {totals['subsidy']}"
                )
            if name == next(iter(blocks[height])):
                block_results.append(
                    {
                        "height": height,
                        "hash": entry["hash"],
                        "transactions": entry["transaction_count"],
                        "pool_changes": pools,
                        "lockbox_change": lockbox,
                        "fees": totals["fees"],
                        "value_created": created,
                        "subsidy": totals["subsidy"],
                        "rule": "value created equals subsidy"
                        if must_equal
                        else "value created does not exceed subsidy",
                        "rule_holds": holds,
                    }
                )
    for shape in manifest["required_shapes"]:
        if shape in seen_shapes:
            checks["expected.shapes"].ok()
        else:
            checks["expected.shapes"].fail(f"required shape '{shape}' has no case in the dataset")
    return accounts, models, block_results


# What GraphSense returned


def _is_amount(value):
    return type(value) is int


def _amount_problems(record):
    """Names of the fields of one worker record that are not plain integers."""
    bad = [
        name
        for name in ("total_input", "total_output", "fee", "transparent_input", "transparent_output")
        if not _is_amount(record[name])
    ]
    bad += [name for name in ("shielded_inputs", "shielded_outputs") if not all(_is_amount(v) for v in record[name])]
    if record["exposes_pool_balances"]:
        bad += [name for name in BALANCE_FIELDS[1:] if not _is_amount(record[name])]
    return bad


def _request_key(request):
    return (request["method"], str(request["params"]))


def observed_values(record):
    """Turn one worker record into the values of FIELDS (None where the revision reports nothing)."""
    values = {name: record[name] for name in OBSERVED_FIELDS}
    exposed = record["exposes_pool_balances"]
    for name in BALANCE_FIELDS[1:]:
        values[name] = record[name] if exposed else None
    legs = record["shielded_inputs"] + record["shielded_outputs"]
    if exposed and all(_is_amount(v) for v in legs) and all(_is_amount(values[name]) for name in BALANCE_FIELDS[1:]):
        # GraphSense has no Sprout field. What it attributes to Sprout is the net shielded value
        # minus the three balances it does expose.
        net = sum(record["shielded_inputs"]) - sum(record["shielded_outputs"])
        values["sprout_value_balance"] = net - sum(values[name] for name in BALANCE_FIELDS[1:])
    else:
        values["sprout_value_balance"] = None
    return values


def collect(manifest, worker, side, checks):
    """Bring one worker's output into the form {txid: record} and run the per-revision checks.

    Returns (records, exceptions): `exceptions` maps txid -> description for every transaction whose block raised.
    """
    records, exceptions = {}, {}
    runs = {run["key"]: run for run in worker["runs"]}
    for entry in manifest["blocks"]:
        height = entry["height"]
        per_capture = []
        for file in entry["evidence"]:
            key = f"{height}/{file['capture']}"
            where = f"{side}: block {height} ({file['capture']})"
            run = runs.get(key)
            if run is None:
                checks["graphsense.ran"].fail(f"{where}: no result")
                continue
            if run["exception"] is not None:
                checks["graphsense.ran"].fail(f"{where}: {run['exception']['type']}: {run['exception']['message']}")
                for case in manifest["cases"]:
                    if case["block_height"] == height:
                        exceptions[case["txid"]] = run["exception"]
                continue
            checks["graphsense.ran"].ok()
            per_capture.append((file["capture"], run["transactions"]))

            wanted = [
                {"method": "getblockhash", "params": [height]},
                {"method": "getblock", "params": [entry["hash"], 2]},
            ]
            wanted += [{"method": "getrawtransaction", "params": [txid, 1]} for txid in entry["previous_transactions"]]
            made = [{"method": r["method"], "params": r["params"]} for r in run["requests"]]
            unanswered = [r for r in run["requests"] if not r["answered"]]
            unresolved = sum(record["unresolved_inputs"] for record in run["transactions"])
            as_expected = sorted(made, key=_request_key) == sorted(wanted, key=_request_key)
            if as_expected and not unanswered and not unresolved and run.get("block_hash") == entry["hash"]:
                checks["graphsense.requests"].ok()
            else:
                detail = (
                    f"{len(made)} requests made, {len(wanted)} expected, {len(unanswered)} unanswered, "
                    f"{unresolved} inputs unresolved, block hash returned {run.get('block_hash')}"
                )
                checks["graphsense.requests"].fail(f"{where}: {detail}")
            for record in run["transactions"]:
                bad = _amount_problems(record)
                if bad:
                    checks["units"].fail(
                        f"{side}: transaction {record['txid']}: not an integer amount: {', '.join(bad)}"
                    )
                else:
                    checks["units"].ok()
        if not per_capture:
            continue
        first_name, first = per_capture[0]
        for name, other in per_capture[1:]:
            if other == first:
                checks["graphsense.captures-agree"].ok()
            else:
                checks["graphsense.captures-agree"].fail(
                    f"{side}: block {height}: records for {first_name} and {name} differ"
                )
        for record in first:
            records[record["txid"]] = record
    refused = worker["network"]["refused"]
    if refused:
        checks["graphsense.offline"].fail(f"{side}: refused {len(refused)} attempts, first: {refused[0]}")
    else:
        checks["graphsense.offline"].ok()
    return records, exceptions


def compare_cases(manifest, accounts, models, before, after, checks):
    """Compare both revisions' records with the expected values, case by case. Returns the list of case results.

    `before` and `after` are the (records, exceptions) pairs returned by `collect`.
    """
    results = []
    exposed = [record["exposes_pool_balances"] for record in after[0].values()]
    if exposed and all(exposed):
        checks["after.pool-balances-exposed"].ok()
    else:
        checks["after.pool-balances-exposed"].fail(
            "after: the transaction records carry no sapling_value_balance, orchard_value_balance and ironwood_value_balance"
        )

    for case in manifest["cases"]:
        txid = case["txid"]
        expected = case["expected"]
        result = {
            "id": case["id"],
            "txid": txid,
            "block_height": case["block_height"],
            "version": case["version"],
            "shapes": case["shapes"],
            "old_code_expected_to_differ": case["old_code"]["differs"],
            "fields": {},
            "after": {"exception": after[1].get(txid), "matches_expected": False, "mismatches": []},
            "before": {
                "exception": before[1].get(txid),
                "matches_frozen": False,
                "differs_from_expected": None,
                "explained": False,
                "explanation": None,
            },
        }
        got_after = observed_values(after[0][txid]) if txid in after[0] else None
        got_before = observed_values(before[0][txid]) if txid in before[0] else None

        for field, group in FIELDS.items():
            row = {
                "expected": expected[field],
                "before": None,
                "after": None,
                "before_matches": None,
                "after_matches": None,
            }
            if got_after is not None:
                row["after"] = got_after[field]
                if got_after[field] is None and field in BALANCE_FIELDS:
                    pass  # not exposed by this revision: reported once by after.pool-balances-exposed
                elif got_after[field] == expected[field]:
                    row["after_matches"] = True
                    checks[group].ok()
                else:
                    row["after_matches"] = False
                    result["after"]["mismatches"].append(field)
                    checks[group].fail(
                        f"{label(case)} {field}: expected {expected[field]}, after reports {got_after[field]}"
                    )
            else:
                checks[group].fail(f"{label(case)} {field}: expected {expected[field]}, after returned no record")
                result["after"]["mismatches"].append(field)
            if got_before is not None:
                row["before"] = got_before[field]
                if got_before[field] is not None:
                    row["before_matches"] = got_before[field] == expected[field]
            result["fields"][field] = row
        result["after"]["matches_expected"] = got_after is not None and not result["after"]["mismatches"]

        # The old revision: (1) it still reports what was frozen, (2) every difference from the expected values
        # is exactly what leaving out the Orchard and Ironwood balances produces.
        if got_before is None:
            checks["before.frozen"].fail(f"{label(case)}: before returned no record")
            checks["before.explained"].fail(f"{label(case)}: before returned no record")
        else:
            drift = [f for f in OBSERVED_FIELDS if got_before[f] != case["before_observed"][f]]
            for field in drift:
                checks["before.frozen"].fail(
                    f"{label(case)} {field}: before reports {got_before[field]}, the manifest froze {case['before_observed'][field]}"
                )
            if not drift:
                checks["before.frozen"].ok()
            result["before"]["matches_frozen"] = not drift

            model = models.get(txid)
            differs = any(got_before[f] != expected[f] for f in OBSERVED_FIELDS)
            result["before"]["differs_from_expected"] = differs
            if model is None:
                checks["before.explained"].fail(f"{label(case)}: no reference calculation available")
            else:
                unexplained = [f for f in OBSERVED_FIELDS if got_before[f] != model[f]]
                for field in unexplained:
                    checks["before.explained"].fail(
                        f"{label(case)} {field}: before reports {got_before[field]}; leaving out the Orchard and Ironwood balances gives {model[field]}, the expected value is {expected[field]}"
                    )
                required = case["old_code"]["differs"] and not any(got_before[f] != expected[f] for f in TOTAL_FIELDS)
                if required:
                    checks["before.explained"].fail(
                        f"{label(case)}: before was required to differ from the expected totals and fee but does not"
                    )
                if not unexplained and not required:
                    checks["before.explained"].ok()
                    result["before"]["explained"] = True
                    if differs:
                        parts = [
                            f"{pool.capitalize()} balance {amount}"
                            for pool, amount in case["old_code"]["not_read"].items()
                            if amount
                        ]
                        result["before"]["explanation"] = "not read by the old revision: " + ", ".join(parts)
        results.append(result)
    return results


def compare_transformer(manifest, before, after):
    """The further layer: GraphSense's TransformerUTXO rows, against the exporter's records and the manifest.

    `before` and `after` are worker outputs. Returns (case results, check list as dicts).
    """
    rows = {}
    failures = {"ran": [], "totals": [], "coinjoin": []}
    counts = {"ran": 0, "totals": 0, "coinjoin": 0}
    for side, worker in (("before", before), ("after", after)):
        error = worker["import_error"] or worker["transformer_import_error"]
        if error:  # nothing can be checked for this side: that is a failure, not a pass
            counts["ran"] += 1
            failures["ran"].append(
                f"{side}: the transformer could not be imported: {error['type']}: {error['message']}"
            )
        for run in worker["runs"]:
            where = f"{side}: block {run['height']} ({run['key'].split('/', 1)[1]})"
            counts["ran"] += 1
            if run["transformer"] is None or len(run["transformer"]) != len(run["transactions"]):
                error = (
                    run["transformer_exception"]
                    or run["exception"]
                    or {"type": "none", "message": "no row for every transaction"}
                )
                failures["ran"].append(f"{where}: {error['type']}: {error['message']}")
                continue
            for record, row in zip(run["transactions"], run["transformer"], strict=True):
                counts["totals"] += 1
                same = (
                    row["txid"] == record["txid"]
                    and row["coinbase"] == record["is_coinbase"]
                    and all(row[f] == record[f] for f in TOTAL_FIELDS)
                )
                if not same:
                    failures["totals"].append(
                        f"{where}: transaction {record['txid']}: the stored row differs from the exporter's record"
                    )
                previous = rows.setdefault(side, {}).setdefault(row["txid"], row)
                if previous != row:
                    failures["totals"].append(
                        f"{side}: transaction {row['txid']}: rows differ between the two captures"
                    )
    results = []
    for case in manifest["cases"]:
        txid = case["txid"]
        item = {"id": case["id"], "txid": txid, "rows": {}}
        for side in ("before", "after"):
            row = rows.get(side, {}).get(txid)
            item["rows"][side] = (
                None
                if row is None
                else {f: row[f] for f in ("total_input", "total_output", "fee", "coinbase", "coinjoin")}
            )
            if row is None:
                continue
            target = case["expected"] if side == "after" else case["before_observed"]
            counts["totals"] += 1
            if any(row[f] != target[f] for f in TOTAL_FIELDS) or row["coinbase"] != target["is_coinbase"]:
                what = "the expected values" if side == "after" else "the frozen values of the old revision"
                failures["totals"].append(
                    f"{label(case)}: {side} row total_input/total_output/fee {row['total_input']}/{row['total_output']}/{row['fee']} differs from {what}"
                )
            counts["coinjoin"] += 1
            if row["coinjoin"] is not case["transformer_observed"][f"{side}_coinjoin"]:
                failures["coinjoin"].append(
                    f"{label(case)}: {side} coinjoin flag is {row['coinjoin']}, the manifest froze {case['transformer_observed'][f'{side}_coinjoin']}"
                )
        results.append(item)
    titles = {
        "ran": "Both revisions' transformers processed every evidence block",
        "totals": "Stored rows carry the exporter's total_input, total_output and fee; rows of the after revision equal the expected values, rows of the before revision the frozen ones",
        "coinjoin": "The coinjoin flag of every row equals the value frozen in the manifest",
    }
    check_list = [
        {
            "id": f"transformer.{name}",
            "title": titles[name],
            "status": "fail" if failures[name] or counts[name] == 0 else "pass",
            "checked": counts[name],
            "failed": len(failures[name]),
            "failures": failures[name],
        }
        for name in ("ran", "totals", "coinjoin")
    ]
    return results, check_list
