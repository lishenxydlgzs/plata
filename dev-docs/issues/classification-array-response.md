# Classification returns an array instead of the batch object

Status: Fixed in code; production verification pending.

Classification could fail with `expected object, got list` because JSON mode was
enabled but the batch schema was supplied only in the prompt. Last-good metadata
was retained and affected tracks stayed retryable.

The classifier now passes its Pydantic JSON schema to Gemini's structured-output
configuration and explicitly requests the top-level tracks object. Strict local
validation and diagnostic logs remain in place. Tests cover provider forwarding,
fallback preservation, successful publication, and invalid batch rejection.
