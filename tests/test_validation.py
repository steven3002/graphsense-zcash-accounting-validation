"""The whole validation, with the records a GraphSense revision returns built from the manifest (no GraphSense needed).

Each test changes one thing and states what must happen: which check fails, and that the message names the case and field.
"""

import pytest

from conftest import edit_manifest
from gs_zec_validate import envs
from gs_zec_validate.compare import TITLES

WORKED_EXAMPLE = "bd84ece1a1f9930085768a880d99445ee1ee13686c2e5979b30d920e90866517"
AFTER = {"after": {"python": "/x/after/bin/python"}}
BEFORE = {"before": {"python": "/x/before/bin/python"}}


def failed(report):
    return {check["id"]: check["failures"] for check in report["results"]["checks"] if check["status"] == "fail"}


def record(output, txid=WORKED_EXAMPLE):
    return [r for run in output["runs"] for r in run["transactions"] if r["txid"] == txid]


def test_pass(fake_run, manifest):
    status, report, text = fake_run()
    assert status == 0 and report["verdict"] == "pass" and report["results"]["canonical"] is True
    assert failed(report) == {}
    assert [check["id"] for check in report["results"]["checks"]] == list(TITLES)
    assert all(check["status"] == "pass" and check["checked"] > 0 for check in report["results"]["checks"])
    summary = report["results"]["summary"]
    assert (
        summary["cases"] == 47
        and summary["after"]["cases_matching"] == 47
        and summary["after"]["fields_mismatching"] == 0
    )
    affected = sorted(case["id"] for case in manifest["cases"] if case["old_code"]["differs"])
    assert len(affected) == 13
    assert (
        summary["before"]["cases_differing_from_expected"] == affected == summary["before"]["cases_expected_to_differ"]
    )
    assert (
        summary["before"]["cases_differing_explained"] == 13 and summary["before"]["cases_differing_unexplained"] == []
    )
    assert "PASS (exit status 0)" in text


def test_the_fake_records_give_the_published_checksum(fake_run):
    """The records built from the manifest are what the real revisions return: same results checksum as published."""
    _, report, _ = fake_run()
    assert report["matches_published"] is True and report["results_sha256"] == report["published_results_sha256"]


# the old code where the corrected one is expected, and the other way round


def test_old_code_as_after_fails_on_exactly_the_affected_cases(fake_run, manifest):
    status, report, text = fake_run(swap={"after": "before"}, overrides=AFTER)
    assert status == 1 and report["results"]["canonical"] is False
    affected = sorted(case["id"] for case in manifest["cases"] if case["old_code"]["differs"])
    assert report["results"]["summary"]["after"]["cases_mismatching"] == affected
    for case in report["results"]["cases"]:
        assert case["after"]["matches_expected"] is (not case["old_code_expected_to_differ"])
    assert set(failed(report)) == {"after.pool-balances-exposed", "accumulation", "totals"}
    assert "NON-CANONICAL RUN" in text and report["matches_published"] is None
    assert "verdict     FAIL for a NON-CANONICAL pair of revisions (exit status 1)" in text
    assert any(
        "case 1687194/4 (bd84ece1…6517) fee: expected 1000, after reports 89000" in line
        for line in failed(report)["totals"]
    )


def test_corrected_code_as_before_fails(fake_run):
    status, report, _ = fake_run(swap={"before": "after"}, overrides=BEFORE)
    assert status == 1
    failures = failed(report)
    assert set(failures) == {"before.frozen", "before.explained"}
    assert len({line.split(" ")[1] for line in failures["before.frozen"]}) == 13, "exactly the 13 affected cases"
    assert any(
        "case 1687194/4 (bd84ece1…6517): before was required to differ from the expected totals and fee but does not"
        in line
        for line in failures["before.explained"]
    )


def test_another_revision_without_an_override_is_refused(fake_run):
    with pytest.raises(
        envs.SetupError,
        match=r"after: found graphsense-lib 2\.16\.3, the manifest pins 2\.16\.4\. To run another revision on purpose",
    ):
        fake_run(swap={"after": "before"})


def test_same_version_but_changed_file_is_refused(fake_run):
    def tamper(side, output):
        if side == "after":
            output["identity"]["package_tree_sha256"] = "0" * 64

    with pytest.raises(
        envs.SetupError, match="the installed graphsenselib files differ from the pinned release 2.16.4"
    ):
        fake_run(tamper=tamper)


def test_pinned_code_reached_through_an_override_is_still_canonical(fake_run):
    status, report, text = fake_run(overrides=AFTER)
    assert status == 0 and report["results"]["canonical"] is True and "verdict     PASS (exit status 0)" in text


# one wrong value from the "after" revision: each named check catches its own kind


def change_after(field, value):
    def tamper(side, output):
        if side == "after":
            for r in record(output):
                r[field] = value(r[field]) if callable(value) else value

    return tamper


@pytest.mark.parametrize(
    "field, value, check, message",
    [
        ("orchard_value_balance", 88_000, "signs", "orchard_value_balance: expected -88000, after reports 88000"),
        (
            "orchard_value_balance",
            -8_800_000_000_000,
            "signs",
            "orchard_value_balance: expected -88000, after reports -8800000000000",
        ),
        ("shielded_outputs", [], "accumulation", "shielded_outputs: expected [88000], after reports []"),
        (
            "shielded_outputs",
            [88_000, 88_000],
            "accumulation",
            "shielded_outputs: expected [88000], after reports [88000, 88000]",
        ),
        ("output_count", 2, "accumulation", "output_count: expected 1, after reports 2"),
        ("fee", 1_001, "totals", "fee: expected 1000, after reports 1001"),
        ("total_output", 0, "totals", "total_output: expected 88000, after reports 0"),
        ("total_input", 88_999, "totals", "total_input: expected 89000, after reports 88999"),
        ("is_coinbase", True, "resolution", "is_coinbase: expected False, after reports True"),
    ],
)
def test_one_wrong_value_from_after(fake_run, field, value, check, message):
    status, report, text = fake_run(tamper=change_after(field, value))
    assert status == 1
    failures = failed(report)
    assert check in failures
    assert any(f"case 1687194/4 (bd84ece1…6517) {message}" in line for line in failures[check]), failures[check]
    assert "case 1687194/4" in text


def test_a_sign_error_is_caught_by_signs_and_by_the_side_it_lands_on(fake_run):
    def tamper(side, output):
        if side == "after":
            for r in record(output):  # the Orchard leg on the input side instead of the output side
                r["shielded_inputs"], r["shielded_outputs"], r["orchard_value_balance"] = [88_000, 89_000], [], 88_000

    _, report, _ = fake_run(tamper=tamper)
    assert {"signs", "accumulation"} <= set(failed(report))


def test_a_float_amount_is_a_units_failure(fake_run):
    _, report, _ = fake_run(tamper=change_after("fee", "NOT-AN-INTEGER: 1000.0 (float)"))
    failures = failed(report)
    assert (
        "units" in failures
        and f"after: transaction {WORKED_EXAMPLE}: not an integer amount: fee" in failures["units"][0]
    )
    assert "totals" in failures


def test_unresolved_input_fails_the_request_check(fake_run):
    def tamper(side, output):
        output["runs"][0]["transactions"][1]["unresolved_inputs"] = 1

    _, report, _ = fake_run(tamper=tamper)
    assert "1 inputs unresolved" in failed(report)["graphsense.requests"][0]


def test_a_request_outside_the_packaged_evidence_fails(fake_run):
    def tamper(side, output):
        output["runs"][0]["requests"].append(
            {"method": "getrawtransaction", "params": ["ff" * 32, 1], "answered": False}
        )

    _, report, _ = fake_run(tamper=tamper)
    assert "1 unanswered" in failed(report)["graphsense.requests"][0]


def test_refused_network_access_fails(fake_run):
    def tamper(side, output):
        if side == "after":
            output["network"]["refused"].append("socket.connect 203.0.113.7")

    status, report, _ = fake_run(tamper=tamper)
    assert status == 1 and failed(report)["graphsense.offline"] == [
        "after: refused 1 attempts, first: socket.connect 203.0.113.7"
    ]


def test_different_records_for_the_two_captures_of_a_block_fail(fake_run):
    def tamper(side, output):
        if side == "before":
            output["runs"][1]["transactions"][0]["total_output"] += 1  # block 600000, second capture

    _, report, _ = fake_run(tamper=tamper)
    assert failed(report)["graphsense.captures-agree"] == [
        "before: block 600000: records for graphsense-lib-fixtures and zebra-6.4.2 differ"
    ]


def test_an_exception_is_recorded_as_the_result(fake_run):
    def tamper(side, output):
        if side == "before":
            for run in output["runs"]:
                if run["height"] == 1687194:
                    run.update(
                        exception={"type": "ValueError", "message": "Unknown fields in transaction: {'ironwood'}"},
                        transactions=None,
                    )

    status, report, _ = fake_run(tamper=tamper)
    assert status == 1
    assert (
        "before: block 1687194 (graphsense-lib-fixtures): ValueError: Unknown fields"
        in failed(report)["graphsense.ran"][0]
    )
    case = next(case for case in report["results"]["cases"] if case["id"] == "1687194/4")
    assert case["before"]["exception"] == {
        "type": "ValueError",
        "message": "Unknown fields in transaction: {'ironwood'}",
    }
    assert case["fields"]["fee"]["before"] is None and case["after"]["matches_expected"] is True


def test_import_error_of_an_overridden_side_is_a_failed_run_not_a_crash(fake_run):
    def tamper(side, output):
        if side == "after":
            output.update(
                identity=None, import_error={"type": "ModuleNotFoundError", "message": "No module named 'x'"}, runs=[]
            )

    status, report, _ = fake_run(tamper=tamper, overrides=AFTER)
    assert status == 1 and report["results"]["revisions"]["after"]["found"] is None
    assert "after: graphsenselib could not be imported: ModuleNotFoundError" in failed(report)["graphsense.ran"][0]


# a changed manifest


def edit_case(document, change, case_id="1687194/4"):
    change(next(case for case in document["cases"] if case["id"] == case_id))


def test_expected_value_changed_by_one_zatoshi(fake_run, copy_of_repo):
    edit_manifest(copy_of_repo, lambda d: edit_case(d, lambda case: case["expected"].update(fee=1_001)))
    status, report, _ = fake_run(root=copy_of_repo)
    assert status == 1
    failures = failed(report)
    assert (
        "case 1687194/4 (bd84ece1…6517) fee: the manifest says 1001, the evidence (graphsense-lib-fixtures) gives 1000"
        in failures["expected.reference"]
    )
    assert "case 1687194/4 (bd84ece1…6517) fee: expected 1001, after reports 1000" in failures["totals"]
    assert report["matches_published"] is False


def test_frozen_before_value_changed_by_one_zatoshi(fake_run, copy_of_repo):
    edit_manifest(copy_of_repo, lambda d: edit_case(d, lambda case: case["before_observed"].update(fee=89_001)))
    status, report, _ = fake_run(root=copy_of_repo)
    assert status == 1
    assert failed(report)["before.frozen"] == [
        "case 1687194/4 (bd84ece1…6517) fee: before reports 89000, the manifest froze 89001"
    ]


def test_wrong_shape_label_is_rejected(fake_run, copy_of_repo):
    # the label an earlier description used for an Orchard-to-Ironwood move with no transparent output
    edit_manifest(
        copy_of_repo, lambda d: edit_case(d, lambda case: case["shapes"].append("unshielding:orchard"), "3479000/3")
    )
    status, report, _ = fake_run(root=copy_of_repo)
    assert status == 1
    assert "case 3479000/3 (3115796d…9e53) shapes: the manifest says" in failed(report)["expected.shapes"][0]


def test_required_shape_without_a_case(fake_run, copy_of_repo):
    edit_manifest(copy_of_repo, lambda d: d["required_shapes"].append("unshielding:sprout"))
    _, report, _ = fake_run(root=copy_of_repo)
    assert "required shape 'unshielding:sprout' has no case in the dataset" in failed(report)["expected.shapes"]


def test_wrong_previous_output_value(fake_run, copy_of_repo):
    def change(case):
        case["previous_outputs"][0]["value"] += 1

    edit_manifest(copy_of_repo, lambda d: edit_case(d, change, "2501409/3"))
    _, report, _ = fake_run(root=copy_of_repo)
    assert any(
        "case 2501409/3 (7bd7717f…fe69) previous_outputs" in line for line in failed(report)["expected.reference"]
    )


def test_wrong_block_total(fake_run, copy_of_repo):
    edit_manifest(copy_of_repo, lambda d: d["blocks"][0]["expected"]["value_pool_deltas"].update(sapling=1))
    _, report, _ = fake_run(root=copy_of_repo)
    assert any(
        "block 600000 (graphsense-lib-fixtures) sapling pool: node reports 18442940000" in line
        for line in failed(report)["expected.block-pools"]
    )


def test_case_pointing_at_another_transaction(fake_run, copy_of_repo):
    def swap(document):
        a, b = document["cases"][1], document["cases"][2]
        a["txid"], b["txid"] = b["txid"], a["txid"]

    edit_manifest(copy_of_repo, swap)
    _, report, _ = fake_run(root=copy_of_repo)
    assert any("has no case in the manifest" in line for line in failed(report)["evidence.identity"])


# the transformer layer


def test_transformer_layer_passes_and_leaves_the_results_unchanged(fake_run):
    _, plain, _ = fake_run()
    status, report, text = fake_run(transformer=True)
    assert status == 0 and report["results_sha256"] == plain["results_sha256"]
    assert report["transformer_results"]["verdict"] == "pass" and report["transformer_matches_published"] is True
    assert [check["id"] for check in report["transformer_results"]["checks"]] == [
        "transformer.ran",
        "transformer.totals",
        "transformer.coinjoin",
    ]
    assert "transformer PASS, sha256" in text


def test_transformer_row_that_differs_from_the_exporter_fails(fake_run):
    def tamper(side, output):
        if side == "after":
            output["runs"][0]["transformer"][1]["total_input"] += 1

    status, report, _ = fake_run(transformer=True, tamper=tamper)
    assert status == 1 and report["results"]["verdict"] == "pass" and report["transformer_results"]["verdict"] == "fail"
    assert any(
        "the stored row differs from the exporter's record" in line
        for line in report["transformer_results"]["checks"][1]["failures"]
    )


def test_changed_coinjoin_flag_fails(fake_run):
    def tamper(side, output):
        for run in output["runs"]:
            run["transformer"][0]["coinjoin"] = True

    status, report, _ = fake_run(transformer=True, tamper=tamper)
    assert status == 1
    assert any(
        "coinjoin flag is True, the manifest froze False" in line
        for line in report["transformer_results"]["checks"][2]["failures"]
    )


def test_environment_without_the_transformer_dependencies_is_a_clear_error(fake_run):
    def tamper(side, output):
        output["transformer_import_error"] = {"type": "ModuleNotFoundError", "message": "No module named 'pandas'"}

    with pytest.raises(
        envs.SetupError, match="cannot run the transformer layer .*No module named 'pandas'.* --with-transformer first"
    ):
        fake_run(transformer=True, tamper=tamper)


def test_transformer_layer_that_checked_nothing_is_a_failure(fake_run):
    def tamper(side, output):
        if side == "after":
            output["transformer_import_error"] = {"type": "ModuleNotFoundError", "message": "No module named 'pandas'"}
            for run in output["runs"]:
                run["transformer"] = None

    status, report, _ = fake_run(transformer=True, tamper=tamper, overrides=AFTER)
    assert status == 1 and report["transformer_results"]["verdict"] == "fail"
    assert "after: the transformer could not be imported" in report["transformer_results"]["checks"][0]["failures"][0]


def test_block_hash_returned_by_graphsense_is_checked(fake_run):
    def tamper(side, output):
        output["runs"][0]["block_hash"] = "00" * 32

    _, report, _ = fake_run(tamper=tamper)
    assert "block hash returned 0000" in failed(report)["graphsense.requests"][0]


def test_allowed_differences_between_captures_are_counted(fake_run, manifest):
    _, report, _ = fake_run()
    counts = {block["height"]: block["allowed_capture_differences"] for block in report["results"]["blocks"]}
    twice = [block["height"] for block in manifest["blocks"] if len(block["evidence"]) == 2]
    assert len(twice) == 7 and all(counts[height] > 0 for height in twice)
    assert sum(counts[height] for height in twice) == 47 + 13  # confirmations, and anchors in the other byte order
    assert all(counts[block["height"]] is None for block in manifest["blocks"] if len(block["evidence"]) == 1)
