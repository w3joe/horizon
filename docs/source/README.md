# Reviewed source material

These are the only parent-workspace sources copied into Horizon. They are design and research inputs, not implementation evidence. No datasets, model weights, generated runs, or unrelated Desktop files are included.

[`aisstream_integration_plan.md`](aisstream_integration_plan.md) is a 2026-09-22 planning-only handoff for a backend-only Singapore-area civilian AIS overlay, deterministic AIS-derived traffic mirror, and offline geography bundle. It records protocol, safety, provenance, data-rights, resilience, ownership, and verification gates, plus a sanitized summary of a separate bounded connectivity smoke. No key or raw vessel record is stored in the repository.

[`../../experiment/protocol/next-robustness-plan.md`](../../experiment/protocol/next-robustness-plan.md) orders the next research gates: repair experimental validity, freeze splits and assumptions, calibrate uncertainty, run the held-out A1-A5 tournament, test neural health methods, and stress the selected stack with AIS-derived Singapore traffic.

`horizon_agent_implementation_plan.reviewed-original.md` is the byte-for-byte plan reviewed before execution. `horizon_agent_implementation_plan.md` records the accepted 2026-09-21 execution status and fixes links for this repository. The other Markdown copies preserve their substantive content while fixing links to deliberately external sample files.

Upstream SHA-256 values at copy time:

```text
ffb839e586d69d486d67f95aa3df473e905dc1241171d94190da78ed8aa58724  horizon_agent_implementation_plan.reviewed-original.md
3da5c171caa523ad03500aa226ab530eb38905bd628fb79c4c6b9b5a7e8da34f  maritime_rta_research.md
bcdda4350fd5d868e5b007963307518e0830ac041a377443c7256cd55f902a3c  runtime_assurance_uav_concept.md
2ad879ae051dc33e2236dca314c45ad54f4a5c26f63b29dac867d2aa17b541d9  maritime_data_survey.md
ae17ca78ce12c5687ca12d7f39d1e6d104ab6df450ab396738908133538f21ec  starter_sample_provenance.json
```
