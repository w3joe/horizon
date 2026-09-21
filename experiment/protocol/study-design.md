# Frozen study design

## Units and pairing

An episode pair is identified by `scenario_id`, seed, raw observation-tape hash, exogenous fault-schedule hash, and external-AI policy version. Candidate branches share those values and the complete reset state. Pairing ends at inputs: closed-loop vessel states and observations evolve independently after accepted commands differ.

Scenario, session, and source-provenance group are the split unit. Adjacent video frames cannot cross partitions. Development data may be used to implement and debug methods. Calibration data may choose thresholds and risk mappings. Held-out data may be evaluated only after the candidate versions, health mappings, operating domain, selection rule, and artifact hashes are frozen.

MaSTr1325 is restricted to training/nominal-reference use because the provided WaSR/WaSR-T models were trained from that data family. The intended primary MODS benchmark remains unavailable through the current official download route; MODD2 is a separate additional stress dataset and must retain its identity.

## Comparisons

Stage 1 compares A1-A5 with health policy fixed. Controller isolation uses a frozen evidence bundle containing all required uncertainty representations. Full-pipeline comparison starts from identical raw observation/fault inputs and attributes the combined fusion/controller outcome to the pipeline, not solely to the controller.

Stage 2 compares H0-H4 on the selected one or two controllers. Each health method receives an independently calibrated threshold at the same false-alarm target. H2-H4 can change only the frozen perception-health contract and cannot issue steering commands.

At least 1,000 held-out episodes are required. Every stochastic headline scenario/architecture cell has at least 30 paired seeds. Preventable, initially unrecoverable, and out-of-domain episodes are reported separately.

## Statistical reporting

Safety gates use observed finite-suite counts. A Wilson 95% interval accompanies every binary event rate; zero observed violations is not a proof of zero failure probability. Binary paired differences may use the exact conditional McNemar calculation on discordant pairs. Continuous paired effects report the per-pair distribution and deterministic percentile summaries. Runtime reports median, p95, p99, maximum, and all deadline misses.

The Wilson implementation follows the score-interval definition summarized by the [NIST/SEMATECH handbook](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm). These intervals describe sampling uncertainty under their statistical model and do not convert a finite simulation suite into a formal safety guarantee.
