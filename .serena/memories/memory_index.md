# Memory Index

| Memory | What It Answers |
|--------|-----------------|
| `design_spec` | What should exist? (canonical spec from design docs) |
| `implementation_guide` | What does exist and how does it work? |
| `unimplemented_features` | What's missing? (gaps between design and code) |
| `project_philosophy` | What are the guiding principles? |
| `insights` | What patterns and gotchas have we learned? |
| `process_tracker_design` | How does per-process tracking work? |
| `reboot_data_correlation_design` | How to correlate data across reboots? |
| `per_process_data_sources` | What data sources are available for processes? |
| `powermetrics_per_process_flags` | What powermetrics flags give per-process data? |
| `architecture_postmortem` | What's wrong with current data architecture? |

**Focus:** Architecture review in progress. Data flow misaligned — forensics goes to disk instead of database, ring buffer serialized instead of queried. See `architecture_postmortem`.
