"""Entry point for the out-of-process trader selection.

`python -m smartmoney.run_select OUT_JSON [CACHE_DIR]` -- a module of its
own because the package imports `smartmoney.select` itself, and running
that module with `-m` would load it twice. See `smartmoney.select.main`.
"""
from smartmoney.select import main

if __name__ == "__main__":
    raise SystemExit(main())
