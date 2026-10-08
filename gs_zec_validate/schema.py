"""A small JSON Schema validator, so that the tool needs nothing outside the standard library.

It implements only the keywords the two schemas in `manifest/` use:

    type, enum, const, properties, required, additionalProperties, items, minItems, uniqueItems,
    minimum, pattern, minLength, $ref (to "#/$defs/<name>" only)

A schema that uses any other keyword is rejected, so an unsupported rule can never be skipped silently.
The test suite checks this validator against the reference `jsonschema` package on valid and invalid documents.
"""

import re

KEYWORDS = {
    "type",
    "enum",
    "const",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "uniqueItems",
    "minimum",
    "pattern",
    "minLength",
    "$ref",
}
ANNOTATIONS = {"$schema", "$id", "$defs", "title", "description", "$comment"}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    # JSON Schema: a boolean is not a number (in Python it is an int); floats are never integers here.
    "integer": lambda v: type(v) is int,
}


class SchemaError(ValueError):
    """The schema itself uses something this validator does not implement."""


def validate(instance, schema):
    """Return a list of problems ("<where>: <what>"); empty when `instance` satisfies `schema`."""
    problems = []
    _check(instance, schema, schema, "$", problems)
    return problems


def _check(value, schema, root, where, problems):
    unknown = set(schema) - KEYWORDS - ANNOTATIONS
    if unknown:
        raise SchemaError(f"schema keyword not supported at {where}: {sorted(unknown)}")
    if "$ref" in schema:
        if set(schema) & (KEYWORDS - {"$ref"}):
            raise SchemaError(f"$ref next to other keywords is not supported at {where}")
        prefix = "#/$defs/"
        if not schema["$ref"].startswith(prefix):
            raise SchemaError(f"only local references are supported: {schema['$ref']}")
        _check(value, root["$defs"][schema["$ref"][len(prefix) :]], root, where, problems)
        return
    if "type" in schema:
        wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        for name in wanted:
            if name not in _TYPES:
                raise SchemaError(f"type not supported at {where}: {name}")
        if not any(_TYPES[name](value) for name in wanted):
            problems.append(f"{where}: expected {' or '.join(wanted)}, found {_describe(value)}")
            return
    if "const" in schema and not _same(value, schema["const"]):
        problems.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and not any(_same(value, option) for option in schema["enum"]):
        problems.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            problems.append(f"{where}: {value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{where}: shorter than {schema['minLength']} characters")
    if type(value) is int and "minimum" in schema and value < schema["minimum"]:
        problems.append(f"{where}: {value} is less than {schema['minimum']}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{where}: missing '{name}'")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                _check(item, properties[name], root, f"{where}.{name}", problems)
            elif extra is False:
                problems.append(f"{where}: unexpected '{name}'")
            elif isinstance(extra, dict):
                _check(item, extra, root, f"{where}.{name}", problems)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{where}: fewer than {schema['minItems']} items")
        if schema.get("uniqueItems") and any(_same(a, b) for i, a in enumerate(value) for b in value[:i]):
            problems.append(f"{where}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], root, f"{where}[{index}]", problems)


def _same(a, b):
    """Equality as JSON Schema defines it: true and 1 are different values."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return type(a) is type(b) and a == b


def _describe(value):
    for name, test in _TYPES.items():
        if test(value):
            return name
    return type(value).__name__
