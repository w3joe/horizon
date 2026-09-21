# Horizon contracts v0

`schema/horizon.schema.json` is the language-neutral source of truth. The contract version is `0.1.0`; incompatible changes require a version increase and migration notes.

Generate the committed Python `TypedDict` and TypeScript declarations with `scripts/generate_contract_types.py`. Validate every sample with `scripts/validate_contracts.py` and the TypeScript workspace validator. The JSON Schema governs runtime validation; generated declarations make ordinary producer and consumer code type-checkable.

The fixture stream is synthetic and illustrative. `SimulationSnapshot` contains public display state. `EvaluationRecord` is emitted only by an evaluation-only truth scorer and must never enter decision-AI or governor inputs. Covariance coverage and bounded-error assumptions are separate fields; neither may be inferred from the other.

Python import:

```python
from horizon_contracts import GovernorInput, ProposedCommand
```

TypeScript import:

```ts
import type { GovernorInput, SimulationSnapshot } from "@horizon/contracts";
```
