# MODD2 development extraction completion

The five remaining `kope81` development sequences in
`configs/perception/modd2-splits.json` were processed locally at integrated
commit `4b5d21c54cf8eb2599cbe2cc853001846f4c3258`. The run used WaSR-T commit
`1b5360af20408e09bbf0116a0029f7e0c0800e7c`, checkpoint SHA-256
`6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef`,
float32 MPS execution, and four CPU threads. It capped each sequence at the
actual number of left-camera frames and completed 1,525 of 1,525 declared
frames. Together with the earlier 1,338 frames, extraction now covers all
2,863 frames in the eight-sequence development split.

| Sequence | Frames | Forward median (ms) | Forward maximum (ms) | Manifest SHA-256 | Feature cache SHA-256 |
| --- | ---: | ---: | ---: | --- | --- |
| `kope81-00-00010940-00011100` | 161 | 812.909 | 837.701 | `e742a9e23ab4f41207a019547c52f63cd2b097bfe822689cdd06249a2aaa7152` | `c21f5161b483790fb5227fe4ce3fb64e474dda425b02e84783e4f543bc4943a3` |
| `kope81-00-00015980-00016270` | 291 | 810.062 | 861.822 | `c1e5be3139b7cc5de2839aa173bf3d93eb36fa4f04a816f305d2451787176461` | `b12d218f6af771bf0f61e25cb644e774f8067dfd2284e459cea959ca22f91587` |
| `kope81-00-00019370-00019710` | 341 | 813.745 | 843.141 | `7ee5d59597bf7d772bdebf89cae2897f609600ee75a91bd4e7b418d2ac750940` | `160b0c914f4cbc7a567bba4b22ed0b164d09819239e9ec4f00a167e0e64a764b` |
| `kope81-00-00021520-00022080` | 561 | 812.273 | 875.561 | `60cbf616968b9458293fb4abdc3a26b2e242597cd8f0d29c44a01f1c4dae23d1` | `7ff0b7c621a0eec62401b86e5511f0aa7e4b4107e0956d04aae9dd0643ac3f59` |
| `kope81-00-00022350-00022520` | 171 | 810.510 | 839.971 | `9fb43df5c49905699b0f61a88494a45079b79b2b2905be1541846e865b45dc03` | `39dc59ec417d8a4bdc2b955f8fd2ba0cd29aecf23e1a62081087adedee1a4faf` |

The completion record SHA-256 is
`9459cc254da467b29d9fab2044030f5580450698e24f405a7644d8ee75b9aa55`.
It records unchanged owned-source hashes after every sequence. Each inference
manifest also binds every input-frame hash, the model and preprocessing
identity, the environment, and the common kope81 camera calibration.

## Predeclared development scoring

Before the new scores were inspected, analysis plan SHA-256
`4f4654eab4e87525995fee5cfeaf459887f2eb3a7bdb24989f66a853c0510a3a`
froze `kope81-00-00006800-00007095` as the 296-frame fit sequence and the five
new sequences as separate score sequences. H2 used the fixed 64-dimensional
grouped encoder mean/standard-deviation projection, H3 used the same projection
with 16 PCA components, and H4 used all 2,048 encoder spatial means with 16
hidden features.

| Scored sequence | H2 median | H3 median | H4 median |
| --- | ---: | ---: | ---: |
| `kope81-00-00010940-00011100` | 9.2366 | 0.140816 | 1.38360 |
| `kope81-00-00015980-00016270` | 12.3660 | 0.348259 | 2.37940 |
| `kope81-00-00019370-00019710` | 9.7845 | 0.208671 | 1.90639 |
| `kope81-00-00021520-00022080` | 11.3146 | 0.572897 | 6.35682 |
| `kope81-00-00022350-00022520` | 11.5075 | 0.569901 | 4.01547 |

The H2, H3, and H4 reference hashes are respectively
`0bb0b761d020f4b9d701a52da7c05f14eae94b1b40c3bbea3fca2ff8e39da81a`,
`feacce931b4244d58cca2e8ad9729aa2881a34ac4200f28f70e6b4b7f0f1d097`,
and `8e2d2e2c79911e72670a13ef9386fa02fed430c088c7cb78e1eabcd64bcb0cd2`.
H4 used the corrected requested tolerance of `1e-6`; it still reached the
3,000-epoch limit without convergence, reducing loss from 0.992552 to 0.168764
with zero dead features. The external analysis report SHA-256 is
`1197d33e8cc24db91f19cb08d376161e8c9efab773a25963681f199f46302220`.

All inputs in this continuation are development data from the same `kope81`
collection group, and frames within each sequence are serially dependent.
Calibration and held-out partitions and their labels were not opened. These
scores describe cross-sequence novelty or reconstruction only; they do not
define an alarm threshold, calibrated risk, false-negative rate, obstacle-free
claim, metric geometry, safety claim, or production throughput. The roughly
810–814 ms medians are instrumented local MPS measurements and do not establish
20 Hz operation.

The 267 MB masks, previews, feature caches, references, per-frame scores, and
manifests remain external under
`horizon-runs/compute/a07-modd2-dev-completion-20260921/`.
