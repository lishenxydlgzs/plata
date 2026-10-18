# Classification returns an array instead of the batch object

Status: Resolved; deployed and verified in production run 44.

Classification could fail with `expected object, got list` because JSON mode was
enabled but the batch schema was supplied only in the prompt. Last-good metadata
was retained and affected tracks stayed retryable.

The classifier now passes a core projection of its Pydantic JSON schema to Gemini's structured-output
configuration and explicitly requests the top-level tracks object. Strict local
validation and diagnostic logs remain in place. Tests cover provider forwarding,
fallback preservation, successful publication, and invalid batch rejection.

Run 43 exposed a 400 rejection of the full constraint schema. Synthetic probes
confirmed core types, required fields, and enums are accepted; full constraints
remain enforced locally.

Production run 44 succeeded: 2 songs classified, 46 cached, 0 failed batches,
and 48 tracks with 0 unclassified. All 214 backend tests passed before deployment.
