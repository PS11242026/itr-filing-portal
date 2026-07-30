"""Engine entrypoint: compute both regimes, recommend, stamp provenance."""

import hashlib
import json

from itrcore.tax import serde
from itrcore.tax.ayconfig import load as load_config
from itrcore.tax.regimes import compute_regime
from itrcore.tax.types import AssessmentInputs, ComputationResult
from itrcore.version import ENGINE_VERSION


def compute_assessment(inputs: AssessmentInputs) -> ComputationResult:
    cfg = load_config(inputs.ay)
    old = compute_regime(inputs, cfg, "old")
    new = compute_regime(inputs, cfg, "new")

    if inputs.regime_election in ("old", "new"):
        recommended = inputs.regime_election
    else:
        # Compare total outgo (liability + interest + fee); new wins ties
        # because it is the statutory default u/s 115BAC.
        outgo_old = (old.tax_liability + old.interest_234a
                     + old.interest_234b + old.interest_234c + old.fee_234f)
        outgo_new = (new.tax_liability + new.interest_234a
                     + new.interest_234b + new.interest_234c + new.fee_234f)
        recommended = "new" if outgo_new <= outgo_old else "old"

    saving = abs(old.tax_liability - new.tax_liability)
    digest = hashlib.sha256(
        json.dumps(serde.inputs_to_dict(inputs), sort_keys=True)
        .encode("utf-8")).hexdigest()[:16]

    return ComputationResult(
        ay=inputs.ay, engine_version=ENGINE_VERSION,
        config_hash=cfg.config_hash, inputs_digest=digest,
        old=old, new=new, recommended=recommended, saving=saving)
