# Enforced classification response contract

## Failure
Run 42 returned a bare JSON array. The classifier supplied its Pydantic schema
only as prompt text; the Gemini request enabled JSON mode without a response
schema. The runtime correctly rejected the array before metadata publication.

## Change
Add an optional response schema to the shared structured runtime and pass it to
Gemini as response_json_schema alongside application/json. Classification supplies a projection of its Pydantic schema containing core
object/array types, required fields, and enums, with references inlined. The live
endpoint rejects the full constraint schema with 400 INVALID_ARGUMENT; synthetic
requests verified the projection. Full bounds, patterns, extra-field checks and
semantic constraints remain in local Pydantic validation. Instructions describe the top-level
tracks object in its instructions. Preserve schema configuration through model
fallbacks. Reject schema plus tools for this text-only path before a model call.
Keep strict Pydantic, exact video membership, cached metadata, and retry validation;
do not silently wrap or coerce invalid responses. Do not invalidate cached tracks.

## Verification
Exercise schema forwarding through the real Strands/Google request adapter,
including fallback; verify the classifier requests its exact schema, rejects
invalid batches, and publishes valid results. Run the backend suite, deploy from
primary main, and run the failed job once to check the two pending tracks.
