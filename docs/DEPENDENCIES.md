# Dependency review

| Dependency | Purpose | Required | Scope | License | Network/download behavior | Core startup import |
|---|---|---:|---|---|---|---:|
| FastMCP | MCP server and HTTP transport | yes | runtime | Apache-2.0 | no required external call | yes |
| Pydantic | payload/schema validation | yes | runtime | MIT | none | yes |
| sentence-transformers | embeddings | no (`semantic`) | runtime extra | Apache-2.0 | may download models | no |
| sqlite-vec | SQLite vector extension | no (`semantic`) | runtime extra | MIT/Apache-2.0 dual licensing | wheel download; no model | no |
| google-genai | optional Gemini transport | no (`gemini`) | runtime extra | Apache-2.0 | external API calls | no |
| json-repair | optional provider JSON recovery | no (`gemini`) | runtime extra | MIT | none | no |
| pytest | tests | no (`dev`) | development | MIT | package installation only | no |
| Ruff | lint/static checks | no (`dev`) | development | MIT | package installation only | no |

Transitive dependencies are resolved by pip and must be reviewed again before publication. Optional-provider packages are not imported during model-free startup. The public export removed unused direct pins from the private environment rather than upgrading them automatically.

No copied third-party source was identified in the reviewed export. Dependency licenses do not select a license for MAPI itself.
