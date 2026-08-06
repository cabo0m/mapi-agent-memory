# Comparison with adjacent approaches

## MAPI and AGENTS.md

`AGENTS.md` stores static repository instructions: how an agent should work in a checkout, which commands to run and which local rules to respect. MAPI stores dynamic project memories, work history, corrections and current state across sessions and MCP clients.

They work well together. Keep durable repository rules in `AGENTS.md`; use MAPI for evolving decisions, progress, evidence and lineage. MAPI does not replace repository instructions.

## MAPI and other memory systems

Memory products make different trade-offs: files can be transparent and easy to version, vector stores can retrieve similar passages, knowledge graphs can make relations explicit, and managed services can reduce operations work. MAPI does not claim exclusive ownership of graphs, semantic search, supersession or MCP connectivity.

MAPI's emphasis is the combination of auditable lifecycle operations, separate explicit writes and proposals, review, current-state resolution with preserved history, project boundaries, preview hashes, guarded apply, rollback and memory-quality governance. Choose it when those controls matter and self-hosting is acceptable. Choose a simpler file or store when lifecycle governance would add more complexity than value.
