# DOMPurify sanitization hook advisory

Status: resolved, 2026-10-03.

Deployment's npm audit identified low-severity
[GHSA-p98j-92pf-mc4p](https://github.com/advisories/GHSA-p98j-92pf-mc4p)
in transitive DOMPurify 3.4.15. The affected path combines in-place sanitization
with a hook that removes nodes. Updated the compatible locked dependency to
3.4.16; no application or top-level framework upgrade was needed. npm audit
then reported zero vulnerabilities. Frontend checks/build run again before sync.
