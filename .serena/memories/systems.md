# Architectural Systems

Overview of reusable infrastructure components in the codebase.

## System Index

| System | Location | Purpose |
|--------|----------|---------|
| Configuration | `config.py` | Hierarchical TOML config with XDG paths |
| Data Persistence | `storage.py` | SQLite with WAL, schema versioning |
| Logging | `daemon.py` | Dual-output structlog (console + JSON file) |
| Ring Buffer | `ringbuffer.py` | Circular buffer for samples, sparklines |
| IPC (Sockets) | `socket_server.py`, `socket_client.py` | Unix socket streaming (daemon ↔ TUI) |
| Process Scoring | `collector.py` | Canonical `ProcessScore` data schema |
| State Tracking | `tracker.py` | Event lifecycle with snapshots |
| Forensics | `forensics.py` | Multi-source capture (spindump/logs/tailspin) |
| System Collection | `libproc.py`, `iokit.py`, `sysctl.py` | Native macOS API bindings |
| CLI | `cli.py` | Click-based command interface |
| TUI | `tui/app.py` | Textual dashboard |
| Formatting | `formatting.py` | Display formatting utilities |

## Key Integration Patterns

### Configuration Flow
```
Config.load() → passed to Daemon, ProcessTracker, LibprocCollector
```
No hot-reload; changes require daemon restart.

### Data Flow
```
libproc.dylib → LibprocCollector → ProcessSamples
                      ↓
              ProcessTracker → Storage (SQLite)
                      ↓
              SocketServer → TUI (Unix socket)
```

### Error Handling
- `DatabaseNotAvailable`: Raised when DB missing
- Errors logged via structlog, not swallowed
- No silent degradation

### Logging Pattern
```python
import structlog
log = structlog.get_logger()
log.info("event_name", key=value)
```
All logs flow to `~/.local/state/rogue-hunter/daemon.log` (JSON Lines).

### Database Access
```python
from rogue_hunter.storage import require_database
async with require_database(config) as db:
    await create_process_event(db, ...)
```

### Socket Communication
```python
# Server (daemon)
server = SocketServer(config)
await server.start()
server.broadcast(samples)

# Client (TUI)
client = SocketClient(config)
await client.connect()
msg = await client.read_message()
```

## For Detailed Information

| Topic | Memory |
|-------|--------|
| Full schema | `data_schema` |
| Implementation details | `implementation_guide` |
| Design rationale | `design_spec` |
| libproc/IOKit research | `libproc_and_iokit_research` |
