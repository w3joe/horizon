# Horizon white paper

Six-page ACM-format paper covering A-series runtime assurance, A6 policy authorization, H-series perception monitoring, ROC/precision–recall results, controlled faults, and recorded NVIDIA L4 latency.

- **[Read the PDF](horizon-runtime-assurance-white-paper.pdf)**
- **[Download the complete LaTeX source ZIP](horizon-whitepaper-latex-source.zip)** — includes the `.tex` file and all plot data.
- [Edit the LaTeX source](horizon-runtime-assurance-white-paper.tex)
- [Experiment provenance, policy sources, and reproduction details](../docs/whitepaper/README.md)

The source explains A6 and H5, maps the implemented rules to COLREGs and the MASS Code, and cites ten external sources. All figures are generated directly in LaTeX from the included CSVs or TikZ code.

## Build

From the repository root with Tectonic installed:

```sh
mkdir -p tmp/pdfs/whitepaper-build
tectonic -X compile whitepaper/horizon-runtime-assurance-white-paper.tex \
  --outdir tmp/pdfs/whitepaper-build
```

Or extract the source ZIP and run the build command in `BUILD.txt`. Building the PDF does not require GPU access, model weights, or the external experiment caches.
