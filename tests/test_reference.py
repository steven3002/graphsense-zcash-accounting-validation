"""The reference calculation, on small hand-made transactions whose correct accounting is obvious."""

import sys
from decimal import Decimal

import pytest

from gs_zec_validate import reference
from gs_zec_validate.reference import EvidenceError, account, block_subsidy, shapes

PREV = {("aa" * 32, 0): 500, ("aa" * 32, 1): 250, ("bb" * 32, 3): 1_000}


def tx(vin=(), vout=(), sapling=0, orchard=None, ironwood=None, joinsplits=()):
    """A transaction as a node prints it, reduced to the members the accounting reads."""
    out = {
        "txid": "cc" * 32,
        "vin": [{"coinbase": "00"} if item == "coinbase" else {"txid": item[0], "vout": item[1]} for item in vin],
        "vout": [{"n": n, "valueZat": value} for n, value in enumerate(vout)],
        "vjoinsplit": [{"vpub_oldZat": old, "vpub_newZat": new} for old, new in joinsplits],
        "valueBalanceZat": sapling,
        "orchard": {"actions": [], "valueBalanceZat": orchard or 0},
    }
    if ironwood is not None:
        out["ironwood"] = {"actions": [{}], "valueBalanceZat": ironwood}
    return out


def test_module_does_not_import_graphsenselib():
    assert "graphsenselib" not in sys.modules
    with open(reference.__file__, encoding="utf-8") as handle:
        source = handle.read()
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert imports == ["from decimal import Decimal"]


def test_transparent_only():
    a = account(tx(vin=[("aa" * 32, 0), ("aa" * 32, 1)], vout=[700]), PREV)
    assert (a["transparent_input"], a["transparent_output"], a["fee"]) == (750, 700, 50)
    assert (a["total_input"], a["total_output"], a["input_count"], a["output_count"]) == (750, 700, 2, 1)
    assert a["shielded_inputs"] == [] and a["shielded_outputs"] == []
    assert shapes(a) == ["transparent-only"]


def test_shielding_puts_the_negative_balance_on_the_output_side():
    a = account(tx(vin=[("bb" * 32, 3)], vout=[100], orchard=-890), PREV)
    assert a["orchard_value_balance"] == -890
    assert a["shielded_outputs"] == [890] and a["shielded_inputs"] == []
    assert (a["total_input"], a["total_output"], a["fee"]) == (1_000, 990, 10)
    assert shapes(a) == ["shielding:orchard"]


def test_unshielding_puts_the_positive_balance_on_the_input_side():
    a = account(tx(vout=[900], ironwood=915), PREV)
    assert a["shielded_inputs"] == [915] and a["shielded_outputs"] == []
    assert (a["total_input"], a["total_output"], a["fee"]) == (915, 900, 15)
    assert shapes(a) == ["ironwood", "unshielding:ironwood"]


def test_positive_balance_without_transparent_output_is_a_fee_not_an_unshielding():
    a = account(tx(sapling=10), PREV)
    assert a["fee"] == 10
    assert shapes(a) == ["fully-shielded-fee", "fully-shielded-fee:sapling"]


def test_positive_balance_is_not_unshielding_when_transparent_inputs_cover_the_outputs():
    # 1000 in, 1000 out, and an Orchard balance of +5 that only pays the fee
    a = account(tx(vin=[("bb" * 32, 3)], vout=[1_000], orchard=5), PREV)
    assert a["fee"] == 5
    assert shapes(a) == []


def test_two_pools_are_both_counted_once():
    a = account(tx(sapling=89_000, orchard=-88_000), PREV)
    assert a["shielded_inputs"] == [89_000] and a["shielded_outputs"] == [88_000]
    assert (a["total_input"], a["total_output"], a["fee"], a["input_count"], a["output_count"]) == (
        89_000,
        88_000,
        1_000,
        1,
        1,
    )
    assert shapes(a) == ["cross-pool", "fully-shielded-fee", "sapling+orchard"]


def test_three_pools():
    a = account(tx(sapling=-500_000, orchard=24_898, ironwood=505_102), PREV)
    assert a["shielded_inputs"] == [24_898, 505_102] and a["shielded_outputs"] == [500_000]
    assert a["fee"] == 30_000
    assert shapes(a) == ["cross-pool", "fully-shielded-fee", "ironwood", "sapling+orchard", "three-pool"]


def test_sprout_legs_are_per_joinsplit_and_the_balance_is_new_minus_old():
    a = account(tx(sapling=-399_960_000, joinsplits=[(0, 150_000_000), (0, 250_000_000)]), PREV)
    assert a["sprout_value_balance"] == 400_000_000
    assert a["shielded_inputs"] == [150_000_000, 250_000_000] and a["shielded_outputs"] == [399_960_000]
    assert a["fee"] == 40_000
    b = account(tx(vin=[("bb" * 32, 3)], joinsplits=[(990, 0)]), PREV)
    assert b["sprout_value_balance"] == -990 and b["shielded_outputs"] == [990] and b["fee"] == 10
    assert shapes(b) == ["shielding:sprout", "sprout"]


def test_coinbase_pays_no_fee_and_can_shield():
    a = account(tx(vin=["coinbase"], vout=[62_500_000], sapling=-250_000_000), PREV)
    assert a["is_coinbase"] and a["fee"] == 0
    assert (a["total_input"], a["total_output"]) == (0, 312_500_000)
    assert shapes(a) == ["coinbase", "shielded-coinbase"]


def test_leaving_out_orchard_and_ironwood_gives_the_old_figures():
    full = account(tx(sapling=89_000, orchard=-88_000), PREV)
    old = account(tx(sapling=89_000, orchard=-88_000), PREV, pools_read=("sprout", "sapling"))
    assert (old["total_input"], old["total_output"], old["fee"]) == (89_000, 0, 89_000)
    assert old["fee"] - full["fee"] == 88_000  # exactly the balance that was not read
    assert old["orchard_value_balance"] == 0 and old["shielded_outputs"] == []


def test_missing_previous_output_is_an_error_not_a_zero():
    with pytest.raises(EvidenceError, match="previous output"):
        account(tx(vin=[("dd" * 32, 0)], vout=[1]), PREV)


@pytest.mark.parametrize("bad", [1.5, "100", None, True, Decimal(5)])
def test_amounts_must_be_plain_integers(bad):
    damaged = tx(vout=[100])
    damaged["vout"][0]["valueZat"] = bad
    with pytest.raises(EvidenceError, match="integer amount"):
        account(damaged, PREV)


@pytest.mark.parametrize(
    "height, subsidy",
    [
        (1, 62_500),
        (10_000, 625_062_500),
        (20_000, 1_250_000_000),
        (600_000, 1_250_000_000),
        (653_599, 1_250_000_000),
        (653_600, 625_000_000),
        (1_046_399, 625_000_000),
        (1_046_400, 312_500_000),
        (1_687_194, 312_500_000),
        (2_726_399, 312_500_000),
        (2_726_400, 156_250_000),
        (3_479_000, 156_250_000),
        (4_406_400, 78_125_000),
    ],
)
def test_block_subsidy(height, subsidy):
    assert block_subsidy(height) == subsidy


def test_block_totals_follow_from_the_transactions():
    coinbase = tx(vin=["coinbase"], vout=[1_010])
    spend = tx(vin=[("bb" * 32, 3)], vout=[100], orchard=-890)
    accounts = [account(coinbase, PREV), account(spend, PREV)]
    block = {
        "height": 3_000_000,
        "valuePools": [{"id": "transparent", "valueDeltaZat": 110}, {"id": "orchard", "valueDeltaZat": 890}],
    }
    totals = reference.block_totals(block, accounts)
    assert totals["derived_deltas"] == {
        "transparent": 1_010 + 100 - 1_000,
        "sprout": 0,
        "sapling": 0,
        "orchard": 890,
        "ironwood": 0,
    }
    assert totals["fees"] == 10 and totals["coinbase_must_claim_everything"] is True
    assert (
        reference.block_totals({"height": 2_000_000, "valuePools": []}, accounts)["coinbase_must_claim_everything"]
        is False
    )


def test_decimal_and_integer_forms_are_compared_exactly():
    good = {
        "value": Decimal("0.0000328"),
        "valueZat": 3280,
        "nested": [{"valueBalance": Decimal("-0.00088"), "valueBalanceZat": -88000}],
    }
    assert reference.dual_form_mismatches(good) == [] and reference.count_dual_forms(good) == 2
    assert reference.dual_form_mismatches({"value": Decimal("3.28E-5"), "valueZat": 3280}) == []
    bad = {"vout": [{"value": Decimal("0.0000328"), "valueZat": 3281}]}
    assert reference.dual_form_mismatches(bad) == ["$.vout[0].value=0.0000328 but valueZat=3281"]
    assert (
        len(reference.dual_form_mismatches({"value": Decimal(1), "valueZat": Decimal(100000000)})) == 1
    )  # the integer form must be an integer


def test_a_missing_or_non_numeric_decimal_form_is_a_mismatch_not_a_crash():
    assert len(reference.dual_form_mismatches({"value": None, "valueZat": 0})) == 1
    assert len(reference.dual_form_mismatches({"value": "0.5", "valueZat": 50_000_000})) == 1
    assert reference.dual_form_mismatches({"value": 0, "valueZat": 0}) == []


def test_block_totals_need_the_integer_delta():
    with pytest.raises(EvidenceError, match="valuePools sapling valueDeltaZat"):
        reference.block_totals(
            {"height": 3_000_000, "valuePools": [{"id": "sapling", "valueDelta": Decimal("0.5")}]}, []
        )
