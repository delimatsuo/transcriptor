# Engineering decision policy

This repository uses the following standing decision policy until the owner
replaces it with a newer instruction.

## Model routing

- The primary agent owns architecture, specifications and pinned briefs,
  ambiguous judgment, independent audit, and Git/GitHub operations.
- Pinned coding briefs default to headless `agy` with
  `gemini-3.7-flash-high` when every expected behavior can be stated in
  advance. Mechanical scaffolding, renames, boilerplate, and count updates may
  use `gemini-3.7-flash-low` or `gemini-3.7-flash-medium`.
- Each coding slice uses an isolated checkout. The PR records the selected
  model tier, reported token usage when the transport exposes it, retries, and
  defects found by the independent audit.
- Builder output and builder-run tests are untrusted until the primary agent
  inspects the exact diff and runs adversarial or causal verification.

## Decision authority

- Reversible technical decisions are decided by the primary agent, logged in
  the PR, and executed without pausing for approval.
- Irreversible operations, external spend, credential or identity access,
  legal or terms acceptance, and product-scope changes require an explicit
  owner envelope. Batch such gates into one paste-ready authorization request.
- Merges, deployments, provider access, signing, physical devices, publishing
  to real users, and real personal data remain separately authorized actions.

## Verification

- Use focused local causal tests before a coherent push and bind evidence to
  the exact commit under review.
- Hosted CI is the completion signal only for suites the exact workflow
  actually runs. The current Transcriptor workflow does not establish Swift or
  Playwright evidence; those gates remain explicit until CI is deliberately
  expanded under an approved spend envelope.
- Never report an infrastructure failure as an application pass.

## Hard denials

- Never read `.env` or `.env.*` files.
- Never use `rm -rf`, force-push, destructive reset/checkout, or broad staging.
- Never stage machine-local credentials, tokens, configuration databases,
  caches, logs, or protected instruction files.
