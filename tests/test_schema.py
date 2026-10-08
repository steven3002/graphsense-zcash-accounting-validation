"""The built-in JSON Schema validator, on its own and against the reference `jsonschema` package."""

import copy
import json
import os

import pytest

from conftest import ROOT
from gs_zec_validate import schema


def load(name):
    with open(os.path.join(ROOT, "manifest", name), encoding="utf-8") as handle:
        return json.load(handle)


MANIFEST_SCHEMA = load("manifest.schema.json")
REPORT_SCHEMA = load("report.schema.json")


def mutations(document):
    """Damaged copies of the manifest, each breaking one schema rule."""

    def variant(change):
        damaged = copy.deepcopy(document)
        change(damaged)
        return damaged

    return {
        "missing top-level member": variant(lambda d: d.pop("cases")),
        "unexpected member": variant(lambda d: d.update(extra=1)),
        "wrong schema version": variant(lambda d: d.update(manifest_schema_version=2)),
        "txid too short": variant(lambda d: d["cases"][0].update(txid="abc")),
        "txid upper case": variant(lambda d: d["cases"][0].update(txid=d["cases"][0]["txid"].upper())),
        "amount as string": variant(lambda d: d["cases"][3]["expected"].update(fee="1000")),
        "boolean where an integer belongs": variant(lambda d: d["cases"][3]["expected"].update(fee=True)),
        "negative count": variant(lambda d: d["cases"][3]["expected"].update(input_count=-1)),
        "integer where a boolean belongs": variant(lambda d: d["cases"][3]["expected"].update(is_coinbase=0)),
        "missing expected field": variant(lambda d: d["cases"][3]["expected"].pop("total_input")),
        "unknown expected field": variant(lambda d: d["cases"][3]["expected"].update(tip=1)),
        "zero amount in a leg list": variant(lambda d: d["cases"][3]["expected"].update(shielded_inputs=[0])),
        "duplicate shape": variant(lambda d: d["cases"][0].update(shapes=["coinbase", "coinbase"])),
        "unknown rule": variant(lambda d: d["capture_differences_allowed"][0].update(rule="whatever")),
        "evidence path outside evidence/": variant(lambda d: d["blocks"][0]["evidence"][0].update(path="../x.json.gz")),
        "bad date": variant(lambda d: d["dataset"].update(date="6 October")),
        "empty changelog": variant(lambda d: d["dataset"].update(changelog=[])),
        "bad rpc method": variant(lambda d: d["blocks"][0]["evidence"][-1]["rpc"].update(method="sendrawtransaction")),
        "null where an object belongs": variant(lambda d: d.update(revisions=None)),
    }


def test_manifest_satisfies_its_schema(manifest):
    assert schema.validate(manifest, MANIFEST_SCHEMA) == []


def test_every_damaged_manifest_is_rejected_with_a_location(manifest):
    for name, damaged in mutations(manifest).items():
        problems = schema.validate(damaged, MANIFEST_SCHEMA)
        assert problems, name
        assert all(problem.startswith("$") for problem in problems), name


def test_agrees_with_the_reference_validator(manifest):
    jsonschema = pytest.importorskip("jsonschema")
    for rules in (MANIFEST_SCHEMA, REPORT_SCHEMA):
        jsonschema.Draft202012Validator.check_schema(rules)
    reference = jsonschema.Draft202012Validator(MANIFEST_SCHEMA)
    assert reference.is_valid(manifest)
    for name, damaged in mutations(manifest).items():
        assert not reference.is_valid(damaged), name
        assert len(schema.validate(damaged, MANIFEST_SCHEMA)) == len(list(reference.iter_errors(damaged))), name


def test_unsupported_keywords_are_refused_not_skipped():
    with pytest.raises(schema.SchemaError):
        schema.validate(1, {"maximum": 3})
    with pytest.raises(schema.SchemaError):
        schema.validate({}, {"$ref": "#/$defs/x", "type": "object", "$defs": {"x": {}}})
    with pytest.raises(schema.SchemaError):
        schema.validate(1.5, {"type": "number"})


def test_booleans_are_not_integers_and_true_is_not_one():
    assert schema.validate(True, {"type": "integer"}) != []
    assert schema.validate(1, {"const": True}) != []
    assert schema.validate([1, True], {"type": "array", "uniqueItems": True}) == []
    assert schema.validate(1, {"enum": [True, 2]}) != []
