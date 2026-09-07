These fixtures contain redacted ACE captures, not generated protocol examples.
`captured_provenance.json` records their Git source, complete frame/line ranges,
capture-reported versions, redactions, and exact output hashes. The graphics
fixture and telemetry capture are separate observations; do not assume the
standalone static fixture supplies the standalone graphics capture's version.

`sample_telemetry.jsonl` retains all 1,931 frames, including initialization,
standing start, complete laps, invalidation, and return to the pit box. Physics
and static bytes are unchanged. Graphics redaction changes only populated
driver-name/surname fields at half-open byte ranges `[3020,3053)` and
`[3053,3086)`, preserving fixed widths and all other bytes. Timestamps retain
the original sampling intervals after a constant shift to a fictional epoch.
The full sequence intentionally exceeds small synthetic-fixture size limits.

`sample_log.txt` retains all 14,672 lines and their original ordering, including
opponent traffic and two in-place restarts. Names, account/car identifiers,
personal-directory prefixes, and handshake payloads are redacted consistently.
Game/build dates, public car/track names, event grammar, lap times, and existing
mixed/invalid UTF-8 bytes remain; read it with the parser's `errors="ignore"`
policy. It contains no `Relevant onSplit` verdicts. Its public-callback test
therefore covers missing-verdict behavior, while synthetic tests cover delayed
and reordered verdicts. This log is not synchronized with the SHM capture.

The literal decoder assertions at captured frames 999 and 1930 are independent
of production offset constants. Full-lap analyzer tests use contiguous frames
251 through 1361. Frame 1857 says `Waiting_For_PitBox`, not `Ended`; the fixture
alone does not establish whether its last-time change was a physical finish.

To verify the complete redaction against Git source without writing anything:

```powershell
python tools/restore_captured_fixtures.py --check
```

Omit `--check` to reproduce the fixtures. The source revision must be available
locally; tests themselves need neither Git history nor the original identities.
Scoped `.gitattributes` entries preserve capture bytes across Windows checkouts;
the original log's CRLF and trailing whitespace are intentional captured data.
