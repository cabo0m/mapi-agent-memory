# Contributing

MAPI is licensed under Apache License 2.0. By intentionally submitting a
contribution for inclusion, you agree that it is provided under Apache-2.0
unless an explicit written agreement says otherwise.

For local development:

1. create a focused branch;
2. install `.[dev]`;
3. add tests for behavior changes;
4. preserve fail-closed profiles and preview contracts;
5. run focused tests, full pytest, Ruff, capability generation and the public audit;
6. never include real memories, databases, logs, backups, tokens or private paths.

Do not edit applied migrations. Do not add provider/network requirements to the core quickstart.
