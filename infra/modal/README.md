# Modal execution boundary

Horizon uses Modal only for centrally authorized finite jobs. The workspace limit remains unchanged because it covers unrelated prior usage. `infra/compute-ledger.template.json` accounts only for Horizon's USD 100 cap, protects USD 20, and permits at most USD 80 of reconciled spend plus active reservations.

Reservation `a07-wasrt-sequence-001` was stopped before GPU allocation and reconciled at USD 0.00515378. Its immutable ledger record names app `ap-Q3PIof7DQF5xAtfMdtUYOw`; the empty Volume was deleted. Attempt `a07-wasrt-sequence-002` remains immutable and was reconciled at USD 0 after Modal rejected L4 activation before inference. A later bounded access probe verified that a standard L4 can start; its charge remains separate and must be reconciled from provider evidence.

`a07-wasrt-sequence-003` is the fresh reviewed reproduction attempt. Its job specification has a unique Volume, remote output path, and local output path. It permits at most USD 1.50 and retains one L4, four physical CPU cores, 16 GiB RAM, zero retries, one total attempt, one container, no warm container, a 15-minute startup cap, 20-minute execution cap, 85-frame input cap, and 150 MiB output cap. Its controller wall cap is 600 seconds and the checkpoint is delivered as an immutable startup mount after cached image-build layers. GPU execution blocks network access.

Initialize or inspect the external mutable ledger:

```sh
./scripts/python.sh scripts/compute_ledger.py init
./scripts/python.sh scripts/compute_ledger.py status
```

The coordinator first creates the one-time external reservation, then runs exactly one authorized attempt:

```sh
./scripts/python.sh scripts/compute_ledger.py reserve \
  --reservation a07-wasrt-sequence-003 \
  --job-spec infra/modal/jobs/a07-wasrt-sequence-003.json \
  --upper-bound-usd 1.50 \
  --authorization "Coordinator authorization recorded in the 2026-09-21 session"

./scripts/python.sh scripts/run_modal_job.py \
  --reservation a07-wasrt-sequence-003 --execute
```

The wrapper refuses missing or dirty declared source, duplicate attempts, cap violations, and unauthorised reservations. Before provider launch it records the repository commit plus entrypoint, job-spec, and declared tracked source-tree hashes in an external launch-state file. Completion metadata reuses this immutable capture rather than reading a potentially newer Git HEAD. At execution it creates the single named Volume and applies the job specification's finite controller wall-clock cap over image build plus remote execution. It captures the exact ephemeral app ID from CLI output or the app-list delta, stops that ID, and verifies the app is absent or stopped with zero tasks. A successful download must contain 85 class masks, 85 previews, features, and a manifest before the Volume is deleted. If execution, termination, download, or validation is uncertain, the ledger remains awaiting reconciliation and the bounded Volume is retained for coordinator recovery. It does not infer billing from runtime. After Modal reports the actual inclusive charge, reconcile it explicitly:

```sh
./scripts/python.sh scripts/compute_ledger.py reconcile \
  --reservation a07-wasrt-sequence-003 --actual-usd 0.00 \
  --note "replace with provider job ID and measured inclusive charge"
```

The supported provider report is:

```sh
/Users/w3joe/Desktop/2026_sdth/horizon-tools/bin/modal billing report \
  --for today --resolution h --show-resources --json
```

Hourly rows cover completed intervals. Keep the reservation awaiting reconciliation while the relevant interval is incomplete rather than treating runtime as billing truth.

Do not place tokens in the repository or output logs. The configured CLI lives outside Git at `/Users/w3joe/Desktop/2026_sdth/horizon-tools/bin/modal`.
