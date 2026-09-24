from __future__ import annotations

import ssl

from irbis_control.infrastructure import nkp_source
from irbis_control.infrastructure.tls import system_tls_context


def test_system_tls_context_verifies_servers_and_hostnames() -> None:
    context = system_tls_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert system_tls_context() is context


def test_nkp_request_uses_system_tls_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        headers = {"Content-Type": "application/octet-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return b"PK\x03\x04"

        @staticmethod
        def geturl() -> str:
            return nkp_source.NKP_DRUG_DOWNLOAD_URL

    def fake_urlopen(_request, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(nkp_source.urllib.request, "urlopen", fake_urlopen)

    payload, content_type, _final_url = nkp_source._request(nkp_source.NKP_DRUG_DOWNLOAD_URL)

    assert payload == b"PK\x03\x04"
    assert content_type == "application/octet-stream"
    assert captured["context"] is system_tls_context()
