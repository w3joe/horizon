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

The current CPU launch starts the implemented simulator, separate decision-AI fixture, and console. Gate, assurance, fusion, and collector ports are stable assignments, but the launcher reports those components as unavailable until their owner entrypoints exist. It never substitutes success fixtures for an absent service.

`packages/contracts/schema/horizon.schema.json` is authoritative. Generated Python and TypeScript declarations are convenience types. Contract records carry explicit lineage, monotonic validity, simulation time, uncertainty semantics, and provenance. A covariance coverage level does not imply a hard bound; a bounded set names its assumption separately.

The console proxy exposes only the simulator's public routes under `/api`. It holds no plant, operator, or evaluation capability. Run-specific gate, evaluation, and operator capabilities are written outside Git with mode `0600` and are never printed.
