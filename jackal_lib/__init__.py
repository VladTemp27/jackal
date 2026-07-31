"""jackal internals.

Split by concern, not by size: each module owns one boundary the others do not
reach across. The dependency graph is a DAG — terminal has no jackal imports,
gateways and models depend only on terminal, updates depends only on terminal
and gateways, and setup and launch sit on top.
"""
