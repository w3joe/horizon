# Decision AI adapter

`client.py` is the transport boundary to the separate decision-making AI. It
accepts only the public/noisy `SimulationSnapshot` and returns a shared
`ProposedCommand` plus `AIInferenceTrace`. The runtime-assurance service can
replace the fixture URL with another implementation without changing plant or
governor code.

The adapter has no actuator capability and never receives the simulator's gate
or evaluation bearer tokens.
