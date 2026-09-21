# Compute budget and data boundary

The user approved at most USD 100 for Horizon, inclusive of GPU, CPU, memory, storage, transfer, and image-build overhead. USD 20 remains protected, leaving a USD 80 working cap. Existing Modal workspace usage predates Horizon and is recorded only as context; the workspace-wide account limit remains unchanged.

The mutable Horizon ledger lives outside Git at `horizon-runs/compute/ledger.json`, initialized from `infra/compute-ledger.template.json`. It counts reconciled spend plus every active reservation against the USD 80 working cap. An exclusive file lock and atomic replacement prevent concurrent local updates. Actual costs must be reconciled from provider evidence; runtime is not treated as a billing measurement.

Reservation `a07-wasrt-sequence-001` was stopped during checkpoint image delivery before GPU allocation and reconciled from the provider report at USD 0.00515378. Attempt `a07-wasrt-sequence-002` was rejected before L4 inference and reconciled at USD 0; both attempts remain immutable. A later finite standard-L4 access probe succeeded and was verified stopped with zero tasks, while its provider charge remains pending complete hourly billing data.

The fresh `a07-wasrt-sequence-003` specification is authorized for at most USD 1.50 and does not reuse a prior Volume or output path. It has one L4, four physical CPU cores, 16 GiB RAM, zero retries, one total attempt, one container, no warm container, a 15-minute startup cap, 20-minute execution cap, 600-second controller wall cap over build and execution, 85-frame input cap, and 150 MiB output cap. GPU execution blocks network access. The checkpoint is an immutable startup mount after image build steps. The job includes a vanilla-versus-hooked agreement check and hook-overhead measurement. The external mutable ledger must contain its one-time reservation before execution; adding the reviewed specification alone does not reserve or launch compute.

Provider reconciliation uses `modal billing report --for today --resolution h --show-resources --json`. Because the report covers completed hourly intervals, the reservation remains outstanding until the attempt's interval is complete and attributable.

External source paths are documented, not committed:

- WaSR-T source: `horizon-data/sources/WaSR-T`, commit `1b5360af20408e09bbf0116a0029f7e0c0800e7c`.
- WaSR-T weights: `horizon-data/weights/wasrt_mastr1325.pth`, SHA-256 `6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef`.
- MaSTr1325 is nominal/reference material because pretrained-model overlap prevents treating it as held-out evidence.
- MODD2 is locally available but is not the full MODS benchmark. The current official MODS link requires institutional login.

Source-code licenses do not grant redistribution rights for datasets or checkpoints. The repository remains private and contains only acquisition manifests, hashes, and bounded job specifications.
