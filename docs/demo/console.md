# Console demo guide

## Initial walkthrough

1. Start Vite development mode on port 5176 and confirm the amber **INITIAL FIXTURE MODE** disclosure. A production proxy with unavailable services instead shows **FIXTURE FALLBACK · LIVE DISCONNECTED** and locks all controls.
2. Use **Oblique** and **Tactical** cameras to inspect the same NED harbor scene. The patrol vessel hull is 12 × 3 m. Cyan is the accepted path, dashed red is the rejected proposal, and dashed amber is the unprotected predicted branch.
3. Select **S19 · Camera mismatch**. Playback begins at the synthetic fault marker. Pause, scrub, reset, or change speed without changing event timestamps.
4. Select the intervention marker, then move between Navigation, Data flow, and Neural sensor. The event ID, observation IDs, and inference ID remain linked.
5. Select MV Kestrel in the scene to inspect its status, evidence age, supporting source IDs, and uncertainty bound.
6. In Neural sensor, verify that an unavailable artifact produces no substitute activations. When the A01 artifact route is configured, inspect the recorded raw frame, actual mask preview, and three measured layer summaries while keeping that reproduction separate from the selected control event.

## Live read-only seam

Run the A01 console proxy and open its same origin. The console consumes the public SSE snapshot stream, collector observations and diagnostics, A04 assurance telemetry/joined evidence, A05 fused evidence, gate telemetry, and the allowlisted perception artifact API. A production build switches to **LIVE PUBLIC MODE** after receiving a schema `0.1.0` `SimulationSnapshot`.

The authority panel requires a complete, identity-matched governor input, decision, and receipt. An accepted receipt is shown as current authority only while its command ID matches the public plant's active command, its run and branch match, its decision has not expired against the gate's sampled monotonic clock, and the snapshot, evidence, and gate services are fresh. Otherwise it is labeled historical. Each upstream has its own freshness indicator, so a healthy snapshot stream cannot hide stale assurance or collector evidence. A command without backend trajectory samples produces an explicit unavailable legend.

The Neural sensor workspace shows the completed 85-frame local CPU WaSR-T reproduction through A01's artifact routes. The raw source image and mask preview are recorded data, independent from the simulator camera. Timing is local CPU reproduction timing, the three layer rows are measured summaries, and the equality/reset result is limited to the measured three-frame validation prefix. No GPU or live 20 Hz claim is made.

The live control strip calls only the same-origin mediated actions returned by `GET /api/operator/capabilities`. Each POST includes `X-Horizon-Operator: 1`; bearer tokens remain in the proxy and never enter browser JavaScript. Pause, reset, acknowledge, and declared fault actions remain available when the capability endpoint is healthy. Resume is disabled until plant and gate epochs match and `startup_recovery_ready` is true.

Reset clears all browser observation cursors, events, joined lineage, diagnostics, gate state, and the current snapshot before the request is sent. Results from the prior polling generation are discarded. A `reset_in_progress` response is displayed as paused and pending; the console never automatically acknowledges or resumes. The expected sequence is operator reset, fresh new-epoch observations, gate handshake and accepted recovery prime, then a separate operator resume.

## Component handoff

| Area | Current input | Later integration |
|---|---|---|
| 3D scene | Public or consumed fused snapshot plus backend-supplied command trajectories | A09 licensed meshes; A10 ocean presentation package |
| Evidence panel | Identity-checked A04 decision and gate receipt plus A05 input summary | Add recorded history bundles for event replay |
| Data flow | Eight live collector groups, observations, consumed input, decision, and receipt | Add full observation pagination/history views |
| Neural inspector | Allowlisted recorded 85-frame WaSR-T reproduction; runtime trace remains separate | Link runtime artifacts only when an explicit trace mapping exists |
| Timeline | Latest coherent live control event; full fixture replay in development | Recorded common event bundles for historical replay |
| Controls | Local fixture playback or A01 mediated pause/resume/reset/acknowledge/declared-fault routes | Add recorded operator audit bundles; never expose privileged gate, plant bearer, or truth routes |

The NED-to-render conversion is centralized in `src/lib/coordinates.ts`: east maps to scene X, north maps to negative scene Z, and clockwise-from-north heading maps to negative scene yaw.
