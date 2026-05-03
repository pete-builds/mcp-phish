# Fixtures

Captured upstream API responses for replay-style tests. Currently empty —
Phase 1 uses respx-mocked responses inline in `test_clients.py` and the
in-process stubs (`StubPhishNetClient`, `StubPhishInClient`) in
`test_stubs.py` and `test_tools.py`. Drop captured `.json` files here when
we want to record real API responses against a live key (Phase 2 onward).
