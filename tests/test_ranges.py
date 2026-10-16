"""compare-range's comparison of the two revisions' records, on records built here."""

import copy
import csv
import json

import pytest

from gs_zec_validate import ranges


def base(txid, height=100, index=1, **changes):
    record = {
        "height": height,
        "index": index,
        "txid": txid,
        "coinbase": False,
        "input_count": 1,
        "output_count": 2,
        "total_input": 1000,
        "total_output": 900,
        "fee": 100,
        "coinjoin": False,
        "shielded_inputs": [],
        "shielded_outputs": [],
        "pool_balances": None,
        "regular_inputs": [["a", 1000]],
        "regular_outputs": [["b", 600], ["c", 300]],
        "unresolved_inputs": 0,
        "cluster_set": None,
        "cluster_set_filtered": None,
    }
    record.update(changes)
    return record


def corrected(before, sapling=0, orchard=0, ironwood=0, **changes):
    """What the corrected parser makes of `before` when the transaction also has these Orchard/Ironwood balances."""
    after = copy.deepcopy(before)
    after["pool_balances"] = [sapling, orchard, ironwood]
    for value in (orchard, ironwood):
        if value > 0:
            after["shielded_inputs"] = sorted(after["shielded_inputs"] + [value])
            after["input_count"] += 1
            after["total_input"] += value
        elif value < 0:
            after["shielded_outputs"] = sorted(after["shielded_outputs"] + [-value])
            after["output_count"] += 1
            after["total_output"] -= value
    after["fee"] = 0 if after["coinbase"] else after["total_input"] - after["total_output"]
    after.update(changes)
    return after


def run(tmp_path, pairs):
    before_path, after_path = tmp_path / "before.jsonl", tmp_path / "after.jsonl"
    with open(before_path, "w") as b, open(after_path, "w") as a:
        for before, after in pairs:
            b.write(json.dumps(before) + "\n")
            a.write(json.dumps(after) + "\n")
    changes = tmp_path / "changes.csv"
    return ranges.compare_records(str(before_path), str(after_path), str(changes)), changes


def test_unchanged_transactions(tmp_path):
    before = base("t1")
    result, changes = run(tmp_path, [(before, corrected(before))])
    assert result["changed"]["transactions"] == 0 and result["differences_explained"]["unexplained"] == 0
    assert result["relation_estimates"]["pairs_before"] == 2 and result["relation_estimates"]["pairs_changed"] == 0
    with open(changes, encoding="utf-8") as handle:
        assert len(list(csv.reader(handle))) == 1  # header only


def test_an_orchard_input_changes_totals_fee_and_relations(tmp_path):
    before = base("t1")
    after = corrected(before, orchard=500)  # 500 left Orchard: shielded input, larger denominator
    result, changes = run(tmp_path, [(before, after)])
    assert result["changed"]["transactions"] == 1 and result["changed"]["fee"] == 1
    assert result["changed"]["total_input"] == 1 and result["changed"]["total_output"] == 0
    assert result["newly_accounted"]["orchard"] == {"transactions": 1, "out_of_pool": 500, "into_pool": 0}
    relations = result["relation_estimates"]
    assert relations["pairs_changed"] == 2 and relations["transactions_with_changed_pairs"] == 1
    assert (
        relations["estimated_value_before_of_changed"] == 900 and relations["estimated_value_after_of_changed"] == 600
    )
    assert result["differences_explained"]["unexplained"] == 0
    with open(changes, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["fee_before"] == "100" and rows[0]["fee_after"] == "600" and rows[0]["orchard_balance"] == "500"


def test_an_ironwood_output_changes_the_fee_but_no_relation(tmp_path):
    before = base("t1")
    after = corrected(before, ironwood=-50)  # 50 entered Ironwood: the old fee of 100 included it
    result, _ = run(tmp_path, [(before, after)])
    assert after["fee"] == 50 and result["changed"]["total_output"] == 1 and result["changed"]["total_input"] == 0
    assert result["newly_accounted"]["ironwood"] == {"transactions": 1, "out_of_pool": 0, "into_pool": 50}
    assert result["relation_estimates"]["pairs_changed"] == 0
    assert result["differences_explained"]["unexplained"] == 0


def test_fees(tmp_path):
    shielded_fee = base(
        "t1", regular_inputs=[], regular_outputs=[], total_input=0, total_output=0, fee=0, input_count=0, output_count=0
    )
    unshield = base("t2", index=2, regular_inputs=[], total_input=0, fee=-900, input_count=0)
    pairs = [(shielded_fee, corrected(shielded_fee, orchard=1000)), (unshield, corrected(unshield, orchard=901))]
    result, _ = run(tmp_path, pairs)
    fees = result["fees_of_non_coinbase_transactions"]
    assert fees == {"before": {"negative": 1, "zero": 1}, "after": {"negative": 0, "zero": 0}}


def test_coinjoin_flip_and_clustering_sets(tmp_path):
    before = base(
        "t1", regular_inputs=[["a", 500], ["d", 500]], cluster_set=["a", "d"], cluster_set_filtered=["a", "d"]
    )
    after = corrected(before, orchard=-200, coinjoin=True, cluster_set_filtered=None)
    result, _ = run(tmp_path, [(before, after)])
    assert result["coinjoin_flags"] == {
        "before": 0,
        "after": 1,
        "false_to_true": 1,
        "true_to_false": 0,
        "flagged_transactions": {"before": [], "after": ["t1"]},
    }
    sets = result["clustering_input_sets_changed"]
    assert sets["multi_input_address_set"] == {"without_coinjoin_filter": 0, "with_coinjoin_filter": 1}
    assert sets["spark_regular_inputs"] == {"without_coinjoin_filter": 0, "with_coinjoin_filter": 1}
    assert result["clustering_edges"]["before"]["with_coinjoin_filter"] == 1
    assert result["clustering_edges"]["after"]["with_coinjoin_filter"] == 0
    assert result["differences_explained"]["unexplained"] == 0


def test_a_coinjoin_flip_without_a_new_leg_is_unexplained(tmp_path):
    before = base("t1")
    result, _ = run(tmp_path, [(before, corrected(before, coinjoin=True))])
    assert result["differences_explained"]["unexplained"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"regular_inputs": [["a", 999]]},
        {"total_input": 1001},
        {"fee": 99},
        {"shielded_inputs": [5]},
        {"input_count": 2},
        {"cluster_set": ["x", "y"]},
        {"pool_balances": None},
    ],
)
def test_any_other_difference_is_unexplained(tmp_path, change):
    before = base("t1")
    result, _ = run(tmp_path, [(before, corrected(before, **change))])
    assert result["differences_explained"]["unexplained"] == 1
    assert result["differences_explained"]["examples"] == ["100 t1"]


def test_a_sapling_value_read_differently_is_unexplained(tmp_path):
    before = base("t1", shielded_inputs=[100], total_input=1100, fee=200, input_count=2)
    after = corrected(before, sapling=100)
    after["shielded_inputs"] = [101]
    after["total_input"] = 1101
    after["fee"] = 201
    result, _ = run(tmp_path, [(before, after)])
    assert result["differences_explained"]["unexplained"] == 1


def test_coinbase_with_a_shielded_output(tmp_path):
    before = base(
        "cb",
        coinbase=True,
        regular_inputs=[],
        total_input=0,
        fee=0,
        input_count=0,
        regular_outputs=[["m", 300]],
        total_output=300,
    )
    after = corrected(before, orchard=-50)
    result, _ = run(tmp_path, [(before, after)])
    assert result["changed"]["coinbase_total_output"] == 1 and result["changed"]["fee"] == 0
    assert result["relation_estimates"]["pairs_changed"] == 0
    assert result["differences_explained"]["unexplained"] == 0


def test_different_transaction_lists_stop_the_comparison(tmp_path):
    with pytest.raises(ranges.RangeError, match="different transactions"):
        run(tmp_path, [(base("t1"), corrected(base("t2")))])
    before_path, after_path = tmp_path / "b.jsonl", tmp_path / "a.jsonl"
    before_path.write_text(json.dumps(base("t1")) + "\n")
    after_path.write_text(json.dumps(corrected(base("t1"))) + "\n" + json.dumps(corrected(base("t2", index=2))) + "\n")
    with pytest.raises(ranges.RangeError, match="more transactions"):
        ranges.compare_records(str(before_path), str(after_path), str(tmp_path / "c.csv"))


def test_records_digest_depends_on_the_records(tmp_path):
    before = base("t1")
    first, _ = run(tmp_path, [(before, corrected(before, orchard=5))])
    second, _ = run(tmp_path, [(before, corrected(before, orchard=6))])
    assert first["records_sha256"]["before"] == second["records_sha256"]["before"]
    assert first["records_sha256"]["after"] != second["records_sha256"]["after"]


def test_check_range():
    ranges.check_range(5, 5)
    with pytest.raises(ranges.RangeError):
        ranges.check_range(6, 5)
    with pytest.raises(ranges.RangeError):
        ranges.check_range(-1, 5)
