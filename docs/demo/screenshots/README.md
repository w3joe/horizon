# README screenshot provenance

Captured on **25 September 2026** from the running local Horizon console at
`http://127.0.0.1:5173`, using Safari. These are native window screenshots,
1016 × 768 JPEGs; no UI content, values, or scene outcomes were fabricated or
composited. Browser chrome and source-mode labels are retained.

| File | Screen and state | Evidence source |
|---|---|---|
| `hackathon-recorded-comparison.jpg` | Guided replay, comparison marker, T+37.5 s, oblique camera | Installed `unsafe-route-a5-v1` recorded local service run; protected and evaluation-only counterfactual branches. |
| `hackathon-recorded-outcome.jpg` | Same playhead, command evidence and post-run outcome cards | Matched issued command and recorded outcome summary, not online evaluation truth. |
| `hackathon-navigation.jpg` | Dashboard → Simulation → Navigation → 2D, paused at T+34.7 s | Browser-local randomized synthetic traffic, session `43772bd6`, fixture candidate `STUB`. |
| `hackathon-neural-perception.jpg` | Dashboard → Neural sensor, frame 43/85 (`00042.jpg`) | Installed recorded WaSR-T CPU reproduction. Separate from simulator observations and H5 experiments. |

The replay was recorded on **22 September 2026** from clean source commit
`d2dcd5ba8a9ed8ef96316c780ce88c791710d41c`, with candidate
`A5 / a5-evidence-conditioned-predictive-filter-hybrid-v1` and scenario
`S22-static-obstacle-approach` version `1.1.0`.
Its catalog pins replay SHA-256
`73f99ee62207c9f6f7d34ff880900dff38607dc766fbcb4891fdc6ea76a47521`.
The capture date describes the updated UI screenshots, not a newly recorded
control experiment. In particular, these images do not claim that H5 or the
current A6 enforcement produced the historical intervention.

To capture comparable views, launch the console, select Guided replay and its
comparison marker, then scroll to the comparison and outcome sections. For
navigation, open `/?view=dashboard&mode=simulation`, pause with Space, and choose
2D. The randomized traffic will differ between sessions. For perception, select
Neural sensor and move its frame slider; the recorded artifact must be installed.

The older `singapore-traffic-overview.png` and `singapore-rta-conflict.png` files
are retained as historical fixtures for existing documentation. The main README
uses the four new captures above.
