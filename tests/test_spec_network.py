"""Download Apple's real OpenAPI spec and check our endpoints (run with: pytest -m network).

No credentials and no call to the App Store Connect API itself: only the published spec file.
"""

from __future__ import annotations

import httpx
import pytest

from app_store_connect import report


@pytest.mark.network
def test_every_endpoint_we_use_exists_and_none_is_deprecated() -> None:
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        spec = report.load_spec(client)
    missing, deprecated = report.endpoint_problems(spec)
    assert len(spec.operations) > 500  # sanity: we really parsed Apple's spec
    assert missing == [], f"endpoints missing from spec {spec.version}: {missing}"
    assert deprecated == [], f"endpoints deprecated in spec {spec.version}: {deprecated}"
    for schema, used in report.USED_ATTRIBUTES.items():
        gone = [name for name in used if name not in spec.attributes[schema]]
        assert gone == [], f"{schema} attributes missing from spec {spec.version}: {gone}"
