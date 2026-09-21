# Modal execution boundary

Horizon uses Modal only for centrally authorized finite jobs. The workspace limit remains unchanged because it covers unrelated prior usage. `infra/compute-ledger.template.json` accounts only for Horizon's USD 100 cap, protects USD 20, and permits at most USD 80 of reconciled spend plus active reservations.

The initial A07 reservation is authorized for at most USD 1.50 but remains unspent. Its reviewed entrypoint must include `block_network=True` on the GPU function, exact resource limit tuples, `timeout=1200`, `startup_timeout=900`, `retries=0`, `max_containers=1`, `min_containers=0`, and a short scale-down window. The callable must enforce the 85-frame input cap, one sequential pass, one total attempt including platform preemption, and the 150 MiB output cap.

Initialize or inspect the external mutable ledger:

```sh
./scripts/python.sh scripts/compute_ledger.py init
./scripts/python.sh scripts/compute_ledger.py status
```

After the A07 entrypoint is reviewed, the coordinator runs exactly one authorized attempt:

```sh
./scripts/python.sh scripts/run_modal_job.py \
  --reservation a07-wasrt-sequence-001 --execute
```

The wrapper refuses missing/uncommitted entrypoints, duplicate attempts, cap violations, and unauthorised reservations. At execution it creates the single named Volume and applies a 2,250-second controller wall-clock cap over image build plus remote execution. It captures the exact ephemeral app ID from CLI output or the app-list delta, stops that ID, and verifies the app is absent or stopped with zero tasks. A successful download must contain 85 class masks, 85 previews, features, and a manifest before the Volume is deleted. If execution, termination, download, or validation is uncertain, the ledger remains awaiting reconciliation and the bounded Volume is retained for coordinator recovery. It does not infer billing from runtime. After Modal reports the actual inclusive charge, reconcile it explicitly:

```sh
./scripts/python.sh scripts/compute_ledger.py reconcile \
  --reservation a07-wasrt-sequence-001 --actual-usd 0.00 \
  --note "replace with provider job ID and measured inclusive charge"
```

Do not place tokens in the repository or output logs. The configured CLI lives outside Git at `/Users/w3joe/Desktop/2026_sdth/horizon-tools/bin/modal`.
