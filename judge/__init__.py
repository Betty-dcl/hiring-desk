"""The judge: a regression net over exchanges, never a measure of their quality.

The contracts in `harness/contracts.py` decide everything a trace can settle
by being read. The judge is asked about what is left -- eight short
principles in `principles.toml` -- and is held to the rule the rest of the
repository is held to: a finding quotes the trace, verbatim, at the turn it
names, or it is dropped.

A judge that finds nothing has not certified anything. What it is worth is
what `seeds.py` measures: defects planted on purpose in a recorded exchange,
and how many of them it names.
"""
