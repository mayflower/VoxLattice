# Local changes

- Imports use the installed `fastenhancer_upstream` package instead of the
  upstream repository-root `functional` package.
- Training-only `test()` code is omitted from the model module.
- The unreachable `center=False` transform branches raise `ValueError` rather
  than carrying upstream placeholder exceptions; production always uses
  `center=True` for the static transform and explicit caches for streaming.
- Trailing whitespace and redundant blank lines are normalized.
- No numerical or architectural inference behavior is changed.
