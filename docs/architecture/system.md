# System boundaries

Horizon has one authoritative plant and one protected actuation path. The simulator publishes noisy observations and public display snapshots. Evaluation truth and fault labels use a separate capability and never enter the decision AI, fusion, assurance, or console path.

```text
recorded/synthetic/unavailable sources
  -> collectors -> fusion/health -> GovernorInput
  -> external decision AI proposal -> A1-A5 assurance
  -> exclusive gate -> authoritative simulator plant
  -> public display snapshot -> console

authoritative simulator truth -> evaluation-only scorer -> EvaluationRecord
```

The current CPU launch starts the simulator, separate Decision AI fixture, collector, fusion, exclusive gate, assurance control loop, and console as separate processes. The gate retains its own watchdog if the supervisor or renderer stops. The launcher never restarts a failed complete run or substitutes a success fixture for an unavailable service.

`packages/contracts/schema/horizon.schema.json` is authoritative. Generated Python and TypeScript declarations are convenience types. Contract records carry explicit lineage, monotonic validity, simulation time, uncertainty semantics, and provenance. A covariance coverage level does not imply a hard bound; a bounded set names its assumption separately.

The console proxy exposes an explicit read-only route allowlist. Its narrow operator facade holds only simulator and gate operator capabilities so it can mediate declared local actions; it never exposes these tokens or holds the gate decision or evaluation capabilities. Run-specific capabilities are written outside Git with mode `0600` and are never printed.
