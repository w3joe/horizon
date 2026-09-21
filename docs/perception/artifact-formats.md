# Artifact formats

`ReferenceArtifact` records method ID, version, layer, projected feature dimension, fit split, source-provenance groups, model parameters, and a canonical SHA-256. H2 stores a diagonal precision matrix; H3 stores PCA components; H4 stores encoder/decoder weights plus the offline-control evidence record.

`CalibrationArtifact` records method ID, reference hash where applicable, alert threshold, explicit operating scope, labeled sample count, empirical calibration bins, and a canonical SHA-256. The builder sets `split=calibration`, `frozen=true`, and `risk_validated_on_heldout=false`. This supports frozen alert thresholds without presenting calibration-set event frequencies as validated deployed risk.

`features.jsonl` contains one row per real model inference. Every row includes the sequence/frame IDs, source and model geometry, timestamp source, temporal buffer age, cold-start state, inference and hook timing, and summaries from the fixed hooks. Each tensor summary includes its shape, dtype, finite flag, scalar range/moments, and channel-wise spatial mean and standard deviation. Full activation tensors remain external and are not required by runtime monitoring.

Large caches, masks, checkpoints, and run output belong under sibling `horizon-data/` or `horizon-runs/`, never Git.
