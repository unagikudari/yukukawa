# Herdr response captures

Every file here is a REAL response from Herdr 0.8.0, captured on the kawa
dogfood node on 2026-08-18 while probing the runtime for #198/#200. Nothing
is hand-authored: the adapter is tested against what the runtime actually
said, including the responses that are awkward to produce on demand — a
trust-dialog `blocked` agent, a name collision, a pane that has not finished
starting its shell, and the post-teardown "agent not found".

`conformance.json` records which runtime the captures came from (version,
protocol, schema digest). The version pin in `kawa/runtime/herdr_backend.py`
is tested against it, so raising the pin without re-capturing fails.

Two properties are load-bearing and must survive any future re-capture:

* **Streams and exit codes are real.** Results arrive on stdout with rc 0;
  structured refusals arrive on stderr with rc 1. The first captures merged
  the streams with `2>&1` and hid that split, which made every real refusal
  look malformed to the adapter until the live conformance smoke caught it.
  The fake CLI therefore derives the stream from the payload rather than
  letting a test assert a split the runtime does not have.
* **Operational identifiers are scrubbed, shapes are not.** Host, user and
  working-directory names were replaced with neutral placeholders
  (`node`, `user`, `/home/node`, `workdir`) because these fixtures are <!-- pub-lint:allow synthetic placeholder -->
  published through the public mirror. Field names, value shapes, error
  codes, id formats and ordering are untouched.
