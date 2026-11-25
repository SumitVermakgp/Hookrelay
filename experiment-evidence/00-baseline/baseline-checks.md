# Baseline checks (Run 0, after the Phase 8 review)

Captured 2026-11-25T12:21:56Z in a clean virtual environment created with the CI install command:

    python -m pip install -c constraints.txt -e ".[dev]"

Commit SHA: not recorded (the baseline was not under version control when captured). Fill in after the first commit.

## Environment
```
Python 3.12.3
Linux 6.18.44-fc-v880

annotated-doc==0.0.7
annotated-types==0.8.0
anyio==4.16.0
ast_serialize==0.13.1
certifi==2026.11.20
click==8.5.1
fastapi==0.165.0
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
iniconfig==2.3.1
librt==0.17.0
mypy==2.4.1
mypy_extensions==1.2.0
opentelemetry-api==1.45.2
packaging==26.4
pathspec==1.1.1
pluggy==1.7.0
pydantic==2.14.4
pydantic_core==2.50.5
Pygments==2.21.0
pytest==9.2.0
ruff==0.17.6
starlette==1.10.0
typing-inspection==0.4.5
typing_extensions==4.16.0
uvicorn==0.54.0
```

## ruff check .
```
All checks passed!
exit code: 0
```

## ruff format --check .
```
59 files already formatted
exit code: 0
```

## mypy
```
Success: no issues found in 23 source files
exit code: 0
```

## pytest -q -ra
```
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 54%]
........................................................................ [ 72%]
........................................................................ [ 90%]
....................................                                     [100%]
=============================== warnings summary ===============================
../../..<venv>/lib/python3.12/site-packages/fastapi/testclient.py:1
  <venv>/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
396 passed, 1 warning in 21.06s
exit code: 0
```

## Coverage (branch) of app/

Tool: Coverage.py, version 7.17.5 with C extension. Coverage shows which lines ran, not that they were checked well.
```
Name                         Stmts   Miss Branch BrPart  Cover
--------------------------------------------------------------
app/api/deliveries.py           33      0      6      0   100%
app/api/deps.py                  9      0      0      0   100%
app/api/endpoints.py            21      0      0      0   100%
app/api/events.py               43      0      6      0   100%
app/api/health.py               16      2      0      0    88%
app/api/schemas.py              45      0      0      0   100%
app/core/body_limit.py          31      0     12      1    98%
app/core/config.py              36      1     14      1    96%
app/core/retry.py               16      0      8      0   100%
app/core/security.py            51      0     26      0   100%
app/core/timeutil.py             3      0      0      0   100%
app/core/url_policy.py          65      2     28      0    98%
app/db/database.py              36      1      4      1    95%
app/main.py                     48      2      4      0    96%
app/services/deliveries.py      26      1     10      1    94%
app/services/delivery.py       128      2     28      0    99%
app/services/endpoints.py       12      0      0      0   100%
app/services/events.py          32      1      6      1    95%
--------------------------------------------------------------
TOTAL                          651     12    152      5    98%

5 empty files skipped.
```
