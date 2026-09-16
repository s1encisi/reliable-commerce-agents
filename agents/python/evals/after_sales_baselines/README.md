# Frozen return-path baselines

These modules are immutable evaluation inputs, not production imports.

- B0: upstream-derived private snapshot `2ca851fd74d45531ce794c0f6cdb56b3f2f4ec91`, return tool implementation; source attribution and MIT license remain in the repository root.
- B1: M2 return policy/service and tool adapter saved before M3 changes on 2026-09-16. SHA-256 values are in manifest.json.
- The harness loads each baseline in a separate process. Shared database/auth utilities and package versions are held constant. No LLM is involved.
- B2 is the current implementation. Reported comparisons concern the selected return path, not all six agents or all framework features.
