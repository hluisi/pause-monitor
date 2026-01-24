# Powermetrics Per-Process Flags

**Discovered:** 2026-01-23

The daemon currently uses basic powermetrics flags, but there are many more per-process flags available:

## Available Per-Process Flags

```
--show-process-gpu           show per-process gpu time
--show-process-io            show per-process io information
--show-process-energy        show per-process energy impact number
--show-process-netstats      show per-process network information
--show-process-wait-times    show per-process sfi wait time info
--show-process-qos-tiers     show per-process QOS latency and throughput tiers
--show-process-qos           show QOS times aggregated by process
--show-process-amp           show per-process AMP information
--show-process-ipc           show per-process ipc
--show-process-coalition     group processes by coalitions
--show-process-samp-norm     show CPU time normalized by sample window
```

## Current Daemon Usage

The daemon runs:
```
/usr/bin/powermetrics --samplers cpu_power,gpu_power,thermal,tasks,disk -f plist -i 100
```

## Per-Task Fields Currently Captured

From the `tasks` array in plist output:
- `pid`, `name`
- `cputime_ms_per_s` - CPU usage
- `pageins_per_s` - Swap activity (critical for pause detection)
- `idle_wakeups_per_s`, `intr_wakeups_per_s` - Wakeups
- `diskio_bytesread_per_s`, `diskio_byteswritten_per_s` - Disk I/O
- `qos_*` breakdown - Quality of service time distribution

## Findings

### `--show-process-gpu` Output

In human-readable format, adds a `GPU ms/s` column showing per-process GPU time.

Example output columns:
```
Name  ID  CPU ms/s  User%  Deadlines (<2 ms, 2-5 ms)  Wakeups (Intr, Pkg idle)  GPU ms/s
```

Values are in milliseconds per second (like CPU). Need to verify field name in plist format.

## TODO: Investigate

- What is the plist field name for GPU ms/s? (likely `gputime_ms_per_s` or similar)
- What fields does `--show-process-energy` add?
- Per-process memory still needs psutil (not in powermetrics)