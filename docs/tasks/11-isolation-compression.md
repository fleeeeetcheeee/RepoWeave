Read AGENTS.md, DESIGN.md steps 8, 12, 13, and docs/tasks/10-api-live-coverage.md.
The user requests private-dataset tests, public multi-user hosting tests, and measured
compression improvements over plain zstd using existing Daytona/Factory capabilities.
Private input requires an exact user-selected root and destination authorization;
do not discover or inspect private datasets. Public deployment requires a selected
hosting target and authentication configuration. Until supplied, test isolation locally
using owned fixtures; do not describe those tests as a live public deployment.
Implement opt-in authenticated project isolation and bounded HTTP requests on top of
the existing API/worker, preserving the default local API. Keep project stores,
fixtures, policies, checkpoints, events, and downloads separate.
Investigate dictionary cost amortization, preserve archive decoding compatibility,
and require full archive bytes no worse than plain same-level RPW zstd. Measure
public/owned data with training/validation separation and whole-tree tar-zstd controls.
Use existing dependencies; never execute input source. Record measured results,
cloud version compatibility, cleanup, and all remaining deployment/data limitations.

The user subsequently authorized local reads and Daytona uploads of datasets under
~/Documents/VIETA and ~/Documents/Portfolio. Use complete CSV files only, at most
8 MiB per file, 16 MiB and 64 files per root; anonymize names in staged snapshots.
Do not send those datasets to Factory, LangSmith, or a hosting provider. The user
requested a hosting recommendation; recommend Fly.io + Auth0, prepare the optional
OIDC project gateway, and report actual public deployment as pending configuration.
