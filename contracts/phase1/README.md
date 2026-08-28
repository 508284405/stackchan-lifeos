# Phase 1 device gateway contract

The firmware gateway speaks one UTF-8 JSON object per line using schema
`lifeos.v1`. A receiver must reject a line larger than 16 KiB before parsing.
The C++ reference implementation in `firmware/include/lifeos/protocol` uses
fixed-size storage: payloads are limited to 8 KiB, command IDs are retained in
a 16-entry duplicate window, observations use a latest-value slot, and command
mailboxes are finite.

Every envelope has `schema`, `kind`, `type`, `event_id`, `seq`, `ts_ms`, and an
object `payload`. `command_id` is optional for compatibility; `event_id` is the
single idempotency key, including for commands. Sequence values must be strictly
consecutive per sender. Event IDs are remembered until the
guard is reset during a new hello/session; repeats are rejected as duplicates.
TTL is represented in the command payload contract by `issued_at_ms` and
`expires_at_ms`; the gateway must reject a command once its expiry is reached.
ACKs use `ack.command` and statuses `accepted`, `clamped`, `completed`,
`duplicate`, or `rejected`. Errors use `error.protocol` and the documented
error-code vocabulary.
