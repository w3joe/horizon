# Horizon agent instructions

All implementation agents use `gpt-5.6-sol` with high reasoning. Nested agents require an explicit coordinator allocation. Work only in the assigned absolute worktree and branch. Never change another worktree's branch.

## Ownership

- A01: root manifests and lockfiles, this file, `.github/`, `packages/contracts/`, `scripts/`, `infra/`, `docs/architecture/`, and `docs/source/`.
- A02: `experiment/`.
- A03: `services/simulator/`, `adapters/decision-ai/`, `fixtures/decision-ai/`, and `scenarios/`.
- A04: `services/assurance/`, `services/gate/`, and `tests/assurance/`.
- A05: `services/collector/`, `services/fusion/`, `adapters/maritime/`, `data/manifests/`, and `tests/ingestion/`.
- A06: `apps/console/`, `assets/core/`, and `docs/demo/`.
- A07: `services/perception/`, `services/neural-health/`, `configs/perception/`, `tests/perception/`, and `docs/perception/`.
- A08: `tests/e2e/`, `tests/system/`, and `docs/verification/`.
- A09: `assets/maritime/`, `tools/assets/`, and `docs/realism/assets/`.
- A10: `packages/marine-environment/`, `configs/sea-state/`, `tests/marine-environment/`, and `docs/realism/physics/`.

Only A01 edits shared contracts, root dependency manifests, lockfiles, CI, launcher scripts, or central compute controls. Request changes through the coordinator with a concrete schema or dependency patch. Component owners keep tests and local documentation in their owned paths. The coordinator alone integrates branches into `main` after bootstrap.

## Worktrees

Use `/Users/w3joe/Desktop/2026_sdth/horizon` for integrated `main` and `/Users/w3joe/Desktop/2026_sdth/horizon-worktrees/aXX-name` for assignment branches named `agent/aXX-name`. Report base and head commits, commands, observed results, limitations, and dependency requests at handoff. Do not force-push, rewrite published history, delete unfinished worktrees, or edit another agent's directory.

## Contract and process boundaries

JSON Schema in `packages/contracts/schema/` is authoritative. Generated Python and TypeScript declarations are committed and must remain reproducible. Preserve recorded, synthetic, and unavailable provenance. Keep evaluation truth and fault labels outside online AI, fusion, governor, and UI inputs. The external decision AI is a replaceable process, while the actuator gate is the only protected plant writer. Use monotonic deadlines in control paths and map simulation time explicitly.

Large data, weights, packet archives, raw runs, and activation caches stay outside Git under sibling `horizon-data/` and `horizon-runs/` locations. Never commit secrets. Do not add a project license, publish the repository, deploy a public site, add weapons or targeting functions, or provision cloud compute outside the central budget process.

## Required checks

Use `/opt/homebrew/bin/python3.12` locally when the system `python3` is older than 3.11. The platform commands are:

```sh
./scripts/bootstrap.sh
./scripts/check.sh
```

Until those wrapper scripts land, A01's contract checks are:

```sh
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python scripts/generate_contract_types.py --check
.venv/bin/pytest -q packages/contracts/python/tests
npm run contracts:validate
npm run contracts:typecheck
```
