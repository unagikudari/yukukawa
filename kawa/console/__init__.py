"""Repository-native Operator Console (Phase 3): a read-only view rendered from LIVE projections.

No static snapshot is embedded — every request re-reads the disposable `current_*` projections, so
the page always shows Kawa's actual current state (including Kawa's own roadmap route). Reads only;
never writes. This is the product path — deploy Kawa, run the server, open a browser.
"""
