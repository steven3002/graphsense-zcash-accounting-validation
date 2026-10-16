"""The reproduction of the two Spark figures compare-range uses: relation estimates and clustering input sets."""

from gs_zec_validate import relations


def record(regular_inputs, regular_outputs, total_input=None, coinbase=False, coinjoin=False, total_output=None):
    inputs_sum = sum(value for _, value in regular_inputs)
    return {
        "coinbase": coinbase,
        "coinjoin": coinjoin,
        "regular_inputs": regular_inputs,
        "regular_outputs": regular_outputs,
        "total_input": inputs_sum if total_input is None else total_input,
        "total_output": sum(value for _, value in regular_outputs) if total_output is None else total_output,
    }


def test_half_up_rounds_ties_up_where_python_rounds_to_even():
    assert relations.half_up(2.5) == 3 and round(2.5) == 2
    assert relations.half_up(3.5) == 4 and round(3.5) == 4
    assert relations.half_up(2.4999999999999996) == 2
    assert relations.half_up(2.5000000000000004) == 3
    assert relations.half_up(12345678.5) == 12345679  # Java prints this one in scientific notation
    assert relations.half_up(0.0) == 0


def test_plain_transaction_splits_the_input_over_the_outputs():
    estimates = relations.estimates(record([["a", 100]], [["b", 60], ["c", 30]]))
    assert estimates == {("a", "b"): 60, ("a", "c"): 30}


def test_a_shielded_input_lowers_every_estimate_through_the_denominator():
    without = relations.estimates(record([["a", 100]], [["b", 120]]))
    with_shielded = relations.estimates(record([["a", 100]], [["b", 120]], total_input=150))
    assert without == {("a", "b"): 120}
    assert with_shielded == {("a", "b"): 80}  # 100 / 150 * 120


def test_a_shielded_output_changes_no_estimate():
    before = record([["a", 100]], [["b", 60]])
    after = dict(before, total_output=before["total_output"] + 39)  # a leg into a pool is no regular output
    assert relations.estimates(before) == relations.estimates(after)


def test_an_address_on_both_sides_is_netted_and_the_denominator_adjusted():
    # a spends 100 and receives 30 back: net sender of 70; regularSum 100, addressSum 70, totalInput 100
    estimates = relations.estimates(record([["a", 100]], [["a", 30], ["b", 69]]))
    assert estimates == {("a", "b"): 69}  # 70 / (100 - 100 + 70) * 69


def test_net_zero_addresses_are_on_neither_side():
    assert relations.estimates(record([["a", 50]], [["a", 50]])) == {}


def test_two_senders_and_rounding():
    estimates = relations.estimates(record([["a", 1], ["b", 2]], [["c", 2]]))
    assert estimates == {("a", "c"): 1, ("b", "c"): 1}  # 1/3*2 = 0.67 -> 1, 2/3*2 = 1.33 -> 1


def test_zero_values_make_no_sender():
    assert relations.estimates(record([["a", 0]], [["b", 0]])) == {}
    assert relations.estimates(record([["a", 0], ["b", 5]], [["b", 0], ["c", 5]])) == {("b", "c"): 5}


def test_coinbase_gets_the_coinbase_address_and_its_total_output():
    estimates = relations.estimates(record([], [["miner", 300], ["fund", 50]], coinbase=True, total_output=400))
    assert estimates == {("coinbase", "miner"): 300, ("coinbase", "fund"): 50}


def test_no_regular_input_or_output_means_no_relation():
    assert relations.estimates(record([], [["b", 10]], total_input=10)) == {}
    assert relations.estimates(record([["a", 10]], [], total_input=10)) == {}


def test_cluster_set():
    multi = record([["a", 1], ["b", 2], ["a", 3]], [["c", 1]])
    assert relations.cluster_set(multi, coinjoin_filtering=True) == ["a", "b"]
    assert relations.cluster_set(dict(multi, coinjoin=True), coinjoin_filtering=True) is None
    assert relations.cluster_set(dict(multi, coinjoin=True), coinjoin_filtering=False) == ["a", "b"]
    assert relations.cluster_set(dict(multi, coinjoin=None), coinjoin_filtering=True) is None  # Spark's === false
    assert relations.cluster_set(record([["a", 1], ["a", 2]], []), coinjoin_filtering=False) is None
    assert relations.cluster_set(dict(multi, coinbase=True), coinjoin_filtering=False) is None
