---
id: schema-spindump-format
type: schema
domain: project
subject: spindump-format
status: active
created: 2026-02-21
updated: 2026-02-21
review_after: 2026-05-21
owner: rogue-hunter
aliases: [spindump_format]
tags: []
related: []
sources: []
---

# Spindump Output Format

Complete documentation of the spindump text format, as produced by `spindump -i <tailspin_file> -stdout`.

## Overview

Tailspin captures kernel trace data into a binary `.tailspin` file. Decoding with `spindump -i` produces a human-readable text format (~9MB for a 3.4s capture with ~1000 processes).

**Key insight**: `spindump -i` does NOT require sudo. Only `tailspin save` requires sudo.

## File Structure

1. **Header Section** - system-wide metadata
2. **Process Blocks** - one per process, containing threads and stacks
3. **I/O Histograms** - latency distribution data (end of file)
4. **I/O Aggregate Stats** - summary statistics (end of file)

---

## Header Section

All fields appear once at the top of the file, before the first `Process:` line.

| Field | Format | Example |
|-------|--------|---------|
| Date/Time | timestamp with timezone | `2026-02-02 23:22:34.999 -0800` |
| End time | timestamp with timezone | `2026-02-02 23:22:38.396 -0800` |
| OS Version | `macOS X.Y (Build XXXXX)` | `macOS 26.2 (Build 25C56)` |
| Architecture | `arm64` or `arm64e` | `arm64e` |
| Report Version | integer | `67` |
| Share With Devs | `Yes` or `No` | `Yes` |
| Data Source | string | `KPerf Lightweight PET` |
| Reason | string | `tailspin save mode default` |
| Duration | `N.NNs` | `3.41s` |
| Steps | `N (Nms sampling interval)` | `341 (10ms sampling interval)` |
| Hardware model | string | `Mac16,5` |
| Active cpus | integer | `16` |
| Memory size | `N GB` | `128 GB` |
| HW page size | integer | `16384` |
| VM page size | integer | `16384` |
| Shared cache residency | `N% (N MB / N MB)` | `49.17% (2592.98 MB / 5273.92 MB)` |
| Time Since Boot | `Ns` | `276319s` |
| Time Awake Since Boot | `Ns` | `276319s` |
| Total CPU Time | `N.NNNs (NG cycles, NG instructions, N.NNc/i)` | `17.620s (51.3G cycles, 87.4G instructions, 0.59c/i)` |
| Memory pressure | `average N%, highest N%` | `average 5%, highest 5%` |
| Available memory | `average N GB, lowest N GB` | `average 120.64 GB, lowest 120.20 GB` |
| Lost Perf | string | `No lost perf 3.398s (100%)` |
| Advisory levels | `Battery -> N, User -> N, ThermalPressure -> N, Combined -> N` | |
| Free disk space | `N GB/N GB, low space threshold N MB` | |
| Vnodes Available | `N% (N/N)` | `82.65% (217521/263168)` |
| Models | string | `UNKNOWN` |
| Preferred User Language | locale | `en-US` |
| Country Code | code | `US` |
| OS Cryptex File Extents | integer | `1` |

### Shared Cache (multiple entries)
```
Shared Cache:     UUID slid base address 0xADDR, slide 0xADDR (Name)
```
Example:
```
Shared Cache:     ACB998B6-263C-3634-B0A8-AE8270A116C2 slid base address 0x19de68000, slide 0x1de68000 (System Primary)
Shared Cache:     DF86E129-38B3-3A55-92F1-747C17DE0FC7 slid base address 0x19d7e0000, slide 0x1d7e0000 (DriverKit)
```

### I/O Statistics (nested under header)
```
I/O statistics:
  Overall:        N IOs (N IOs/s), N MB (N KB/s)
  Tier0:          N IOs (N IOs/s), N KB (N KB/s)
  Tier1:          N IOs (N IOs/s), N MB (N KB/s)
  Tier2:          N IOs (N IOs/s), N KB (N KB/s)
```

---

## Process Block

Each process starts with `Process:` and continues until the next `Process:` or end of file.

### Process Header Fields

| Field | Required | Format | Notes |
|-------|----------|--------|-------|
| Process | Yes | `name [pid]` | Process name and PID |
| UUID | No | UUID string | Binary UUID |
| Path | No | absolute path | Executable path |
| Identifier | No | bundle ID | e.g., `com.apple.Safari` |
| Version | No | `X.Y (build)` | App version |
| Shared Cache | Yes | UUID + addresses | Same format as header |
| Architecture | Yes | `arm64` or `arm64e` | |
| Responsible | No | `name [pid]` | Responsible process |
| RunningBoard Mgd | No | `Yes` or `No` | Managed by RunningBoard |
| Parent | No | `name [pid]` | Parent process |
| Execed from | No | `name [pid]` | Process this was execed from |
| Execed to | No | `name [pid]` | Process this execed into |
| Sudden Term | No | string | e.g., `Tracked (allows idle exit)` |
| Note | No | string | Can appear multiple times |
| Footprint | No | `N MB` or `N KB` | Memory footprint |
| I/O | No | `N I/Os (N KB)` | I/O during capture |
| Time Since Fork | No | `Ns` | Time since process started |
| Start time | No | timestamp | For short-lived processes |
| End time | No | timestamp | For short-lived processes |
| Num samples | No | `N (range)` | e.g., `341 (1-341)` |
| CPU Time | No | `N.NNNs (cycles, instructions, c/i)` | |
| Num threads | No | integer | |

### Note Field Variations
- `Suspended for N samples`
- `Has hardened heap`
- Other diagnostic notes

### Footprint with Change
For short-lived processes that changed size:
```
Footprint:        6416 KB -> 11.45 MB (+5312 KB)
```

---

## Thread Block

Threads are indented under their process with 2 spaces.

### Thread Header Format
```
  Thread 0xHEXID    [DispatchQueue "name"(N)]    [Thread name "name"]    N samples (range)    priority N (base N)    [cpu time Xs (cycles, instructions, c/i)]    [N I/Os (size)]
```

All parts after `Thread 0xHEXID` are optional:
- DispatchQueue with name and serial number
- Thread name (for named threads)
- Sample count and range
- Priority (current and base)
- CPU time with performance counters
- I/O count and size

### Examples
```
  Thread 0x3fd504    DispatchQueue "com.apple.main-thread"(1)    341 samples (1-341)    priority 31 (base 31)    cpu time 0.922s (3.2G cycles, 15.4G instructions, 0.21c/i)    3 I/Os (48 KB)
  Thread 0x45928b    Thread name "JIT Worklist Helper Thread"    341 samples (1-341)    priority 31 (base 31)    cpu time 0.018s
  Thread 0x3fd641    341 samples (1-341)    priority 31 (base 31)
```

---

## Stack Frames

Stack frames are indented under their thread. Indentation level indicates call depth (tree structure).

### Frame Format
```
[*]SAMPLES  symbol + offset (library + offset) [0xADDRESS] [(state)]
```

| Component | Required | Description |
|-----------|----------|-------------|
| `*` prefix | No | Indicates kernel frame |
| SAMPLES | Yes | Number of samples at this frame |
| symbol | Yes | Function name or `???` if unknown |
| offset | Yes | Offset within function |
| library | Yes | Library/binary name |
| library offset | Yes | Offset within library |
| address | Yes | Instruction pointer address |
| state | No | Execution state |

### Symbol Formats
1. **Named symbol**: `kevent64 + 8`
2. **Unknown symbol**: `???`
3. **Method syntax**: `-[NSApplication run] + 368`
4. **C++ mangled**: `icu::RuleBasedBreakIterator::next() + 28`

### Library Formats
1. **Named library**: `(libsystem_kernel.dylib + 52100)`
2. **Binary name**: `(2.1.27 + 16272)` - when library is the main executable
3. **Kernel**: `(kernel.release.t6041 + 6029376)`
4. **Kernel extension**: `(<UUID> + 18508)` - with UUID instead of name

### JIT/Anonymous Frames
No library info, just address:
```
1    ??? [0x11e6fd800]
```

### State Values
- `(running)` - CPU was executing
- `(running, p-core)` - Running on performance core
- `(running, e-core)` - Running on efficiency core
- `(blocked by wait4 on PROCESS [PID])` - Waiting for child process

### Tree Structure

Indentation indicates parent-child relationship. Each 2-space increase = one level deeper in call stack.

```
341  start + 7184 (dyld + 36180) [0x19df79d54]           <- root (depth 0)
  341  ??? (2.1.27 + 16272) [0x1004dff90]                 <- depth 1
    341  ??? (2.1.27 + 21356) [0x1004e136c]               <- depth 2
      258  ??? (2.1.27 + 2799616) [0x100787800]           <- depth 3, 258 samples
        243  kevent64 + 8 (...) [...]                    <- depth 4, 243 samples took this path
        13   ??? (2.1.27 + 17769240) [...]                <- depth 4, 13 samples took different path
```

Sample counts at each level show how execution branched.

---

## Binary Images Section

Each process has a `Binary Images:` section listing loaded libraries.

### Format
```
  Binary Images:
           0xSTART -        0xEND  name version  <UUID>  /path
```

### Examples
```
  Binary Images:
           0x100070000 -        0x10007ffff  com.apple.AccessibilityUIServer 1.0 (1)     <E0D79DBE-2CB7-367E-A1C6-6788D7CECB3C>  /System/Library/CoreServices/AccessibilityUIServer.app/Contents/MacOS/AccessibilityUIServer
           0x19df71000 -        0x19e010fff  dyld (1335)                                 <0975AFBA-C46B-364C-BD84-A75DAA9E455A>  /usr/lib/dyld
   *0xfffffe0008860000 - 0xfffffe00091cffff  kernel.release.t6041 (12377.61.12)          <B3B9C89A-5728-31D0-8065-3C50623191AE>__TEXT_EXEC  /System/Library/Kernels/kernel.release.t6041
```

Note: Kernel images have `*` prefix and `__TEXT_EXEC` suffix.

---

## I/O Histograms (End of File)

### IO Size Histogram
```
IO Size Histogram:
     Begin      End            Frequency                     CDF
       0KB       4KB		     218		     218
       4KB       8KB		      10		     228
       ...
         >    1024KB		       0		     293
```

### Tier Latency Histograms
```
Tier 0 Latency Histogram:
      Begin        End                Freq                    CDF
        0us      100us		       46		       46
      100us      200us		        1		       47
      ...
         >  1000000us		        0		       55
```

Same format for Tier 1 and Tier 2.

---

## I/O Aggregate Stats (End of File)

```
Tier 0 Aggregate Stats:
	Num IOs 55 Latency Mean 68us Max Latency 329us Latency SD 92us
	Reads 0 (0 KB) Writes 55 (674 KB)
```

Same format for Tier 1 and Tier 2.

---

## Parsing Notes

1. **Process boundary**: New `Process:` line starts new process block
2. **Thread boundary**: `Thread 0x` pattern at 2-space indent starts new thread
3. **Frame depth**: Count leading spaces, divide by 2 for depth
4. **Kernel frames**: `*` prefix on sample count
5. **Tree reconstruction**: Track indent level stack; pop until finding smaller indent = parent

## Typical Sizes

- Raw `.tailspin` file: ~25 MB
- Decoded text: ~9 MB
- Processes: ~1000
- Threads: ~6000
- Duration: ~3.4 seconds at 10ms sampling (341 samples)
