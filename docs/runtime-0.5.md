# Connection and execution behavior in 0.5.0

MCP stdio and HTTP tool callbacks offload native work from the transport event
loop. At most two worker operations execute concurrently per server; queued work
is cancellable. A long solve no longer blocks protocol ping/list-tools or receipt
of MCP cancellation notifications. This is connection responsiveness, not an
increase in VSPAERO numerical throughput. Each operation still uses an isolated
vspscript process and private model snapshot.

On POSIX, cancellation and timeout kill and reap the native process group,
including VSPAERO children. The worker checks for cancellation before committing
an edited source, and records cancelled operations. Cancellation after the atomic
file replacement cannot undo a completed edit. Clients must actually send the MCP
cancellation notification; abandoning a local client future alone is not a wire
cancellation. No operation is automatically replayed after a timeout/disconnect.
Windows child cleanup remains unverified. REST task cancellation uses the same
worker mechanism; detecting a browser disconnect is not guaranteed by this API.

Successful operations include preparation, native execution, validation and total
elapsed time. Native time includes startup, model loading, geometry work, solver
execution and output writing; it does not pretend to separate those stages.
Timing starts after queue admission. Failure records retain elapsed time too.

Immutable package fingerprints are cached for the lifetime of a process; restart
MCP after changing installed package files. Capability discovery is cached only
for an empty model, with a five-minute TTL and executable identity invalidation.
Health probes remain fresh. Parameter/analysis values and solver results are not
cached. A single parameter-edit call batches edits inside one operation; it is
not the deferred multi-case scheduling feature.

Saved-result reads are limited to 4 MiB manifests. Optional log tails read at most
64 KiB and return at most 200 lines, without rerunning the software.

## Reproducible benchmark

With both checkouts and native binary paths configured, run:

```sh
python scripts/benchmark_connection.py \
  --baseline-source /local/path/to/0.4.0-checkout \
  --output /local/path/benchmark.json --samples 5
```

It uses the same interpreter and SDK for both versions, records all samples and
median/p95, and exercises real MCP startup/discovery, XML inspection and native
health. A separate deterministic two-second stand-in measures protocol ping
latency while native work is running. The stand-in deliberately fails output
validation; it is not an aerodynamic benchmark.

The optional persistent Python worker is deferred: current native query startup
is already small, and adopting a worker adds Python ABI requirements, state
isolation and crash recovery obligations. A controlled comparison of that backend
is required before making it the default. This release does not claim solver
acceleration or memory-use improvements.
