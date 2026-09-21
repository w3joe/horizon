# Sequence-disjoint perception protocol

Split recorded perception data by source video, collection session, and provenance group before
extracting frames. Consecutive frames, clips from the same voyage, and transformed copies stay in
one partition. Freeze file hashes and group assignments before held-out evaluation. Run exact-hash
and perceptual-near-duplicate audits across partitions; publish every detected overlap and move the
whole connected group to the least privileged partition before freezing.

MaSTr1325 is nominal/reference material because it overlaps the training family of the supplied
WaSR and WaSR-T weights. The current 85-frame reproduction sequence is an integration example,
not calibration or held-out evidence. MODD2 retains its own dataset identity and can provide an
additional recorded stress set after the sequence audit. It must not be reported as MODS. The
official MODS download is currently blocked by an institutional sign-in and no access control may
be bypassed.

Development may select features and debug H0-H4. Calibration selects thresholds and empirical risk
maps at a common false-alarm target. Held-out sequences may be opened only after model, layer,
preprocessor, geometry, source-group assignments, reference artifacts, calibration artifacts, and
overlap-audit hashes are frozen. A calibrated risk band authorizes camera free-space only when all
those identities match the runtime payload. Missing or mismatched lineage yields `unknown`.

Gaussian simulator noise is unbounded. The controller's three-sigma radii are declared engineering
assumptions, not guarantees. Truth-only evaluation reports bound violations and out-of-domain
episodes separately without deleting them from the held-out denominator.
