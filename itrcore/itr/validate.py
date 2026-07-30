"""Layer 1: jsonschema validation with readable error paths."""

import jsonschema

from itrcore.itr.schema_registry import SchemaBundle


def validate_schema(json_dict: dict, bundle: SchemaBundle) -> list[str]:
    validator = jsonschema.Draft7Validator(bundle.schema)
    errors = []
    for err in sorted(validator.iter_errors(json_dict),
                      key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{path}: {err.message}")
    return errors
