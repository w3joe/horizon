# Modal execution boundary

Horizon uses Modal only for centrally authorized finite jobs. The workspace limit remains unchanged because it covers unrelated prior usage. `infra/compute-ledger.template.json` accounts only for Horizon's USD 100 cap, protects USD 20, and permits at most USD 80 of reconciled spend plus active reservations.

Reservation `a07-wasrt-sequence-001` was stopped before GPU allocation and reconciled at USD 0.00515378. Its immutable ledger record names app `ap-Q3PIof7DQF5xAtfMdtUYOw`; the empty Volume was deleted. The corrected independent reservation, `a07-wasrt-sequence-002`, is authorized for at most USD 1.50. It retains one L4, four physical CPU cores, 16 GiB RAM, zero retries, one total attempt, one container, no warm container, a 15-minute startup cap, 20-minute execution cap, 85-frame input cap, and 150 MiB output cap. Its controller wall cap is 600 seconds and the checkpoint is delivered as an immutable startup mount after cached image-build layers. GPU execution blocks network access.

Initialize or inspect the external mutable ledger:

```sh
./scripts/python.sh scripts/compute_ledger.py init
./scripts/python.sh scripts/compute_ledger.py status
```

After the A07 entrypoint is reviewed, the coordinator runs exactly one authorized attempt:

```sh
./scripts/python.sh scripts/run_modal_job.py \
  --reservation a07-wasrt-sequence-002 --execute
```

The wrapper refuses missing or dirty declared source, duplicate attempts, cap violations, and unauthorised reservations. Before provider launch it records the repository commit plus entrypoint, job-spec, and declared tracked source-tree hashes in an external launch-state file. Completion metadata reuses this immutable capture rather than reading a potentially newer Git HEAD. At execution it creates the single named Volume and applies the job specification's finite controller wall-clock cap over image build plus remote execution. It captures the exact ephemeral app ID from CLI output or the app-list delta, stops that ID, and verifies the app is absent or stopped with zero tasks. A successful download must contain 85 class masks, 85 previews, features, and a manifest before the Volume is deleted. If execution, termination, download, or validation is uncertain, the ledger remains awaiting reconciliation and the bounded Volume is retained for coordinator recovery. It does not infer billing from runtime. After Modal reports the actual inclusive charge, reconcile it explicitly:

```sh
./scripts/python.sh scripts/compute_ledger.py reconcile \
  --reservation a07-wasrt-sequence-002 --actual-usd 0.00 \
  --note "replace with provider job ID and measured inclusive charge"
```

Do not place tokens in the repository or output logs. The configured CLI lives outside Git at `/Users/w3joe/Desktop/2026_sdth/horizon-tools/bin/modal`.
