"""What we checked our code against: Apple's OpenAPI specification.

APPLE_API_SPEC_VERSION is the version of Apple's published OpenAPI spec that the endpoints
below were verified against (the `info.version` field of `openapi.oas.json`). The monthly
report compares it with the latest published spec and checks every endpoint in
ENDPOINTS_USED still exists and is not marked deprecated.

Every request the client sends must be listed here. The client refuses anything else, so
there is no way to call an arbitrary endpoint.
"""

from __future__ import annotations

from typing import Final

APPLE_API_SPEC_VERSION: Final = "4.5.1"
SPEC_URL: Final = (
    "https://developer.apple.com/sample-code/app-store-connect/"
    "app-store-connect-openapi-specification.zip"
)
RELEASE_NOTES_URL: Final = (
    "https://developer.apple.com/documentation/appstoreconnectapi/"
    "app-store-connect-api-release-notes"
)
API_HOST: Final = "api.appstoreconnect.apple.com"
API_BASE_URL: Final = f"https://{API_HOST}"

Endpoint = tuple[str, str]  # (HTTP method, path template exactly as in Apple's spec)

ENDPOINTS_USED: Final[frozenset[Endpoint]] = frozenset(
    {
        # --- read tools ---
        ("GET", "/v1/apps"),
        ("GET", "/v1/apps/{id}"),
        ("GET", "/v1/apps/{id}/appStoreVersions"),
        ("GET", "/v1/appStoreVersions/{id}"),
        ("GET", "/v1/builds"),
        ("GET", "/v1/builds/{id}"),
        ("GET", "/v1/apps/{id}/betaGroups"),
        ("GET", "/v1/betaTesters"),
        ("GET", "/v1/appStoreVersions/{id}/appStoreVersionLocalizations"),
        ("GET", "/v1/apps/{id}/appInfos"),
        ("GET", "/v1/appInfos/{id}/appInfoLocalizations"),
        ("GET", "/v1/appStoreVersionLocalizations/{id}/appScreenshotSets"),
        ("GET", "/v1/appScreenshotSets/{id}/appScreenshots"),
        ("GET", "/v1/apps/{id}/customerReviews"),
        ("GET", "/v1/apps/{id}/reviewSubmissions"),
        # --- reads used by write tools to show "old value -> new value" ---
        ("GET", "/v1/appStoreVersionLocalizations/{id}"),
        ("GET", "/v1/appInfoLocalizations/{id}"),
        ("GET", "/v1/appStoreVersions/{id}/relationships/build"),
        ("GET", "/v1/customerReviews/{id}"),
        ("GET", "/v1/betaGroups/{id}"),
        ("GET", "/v1/betaTesters/{id}"),
        ("GET", "/v1/appScreenshotSets/{id}"),
        # --- write tools (only registered when ASC_ALLOW_WRITES is on) ---
        ("PATCH", "/v1/appStoreVersionLocalizations/{id}"),
        ("PATCH", "/v1/appInfoLocalizations/{id}"),
        ("POST", "/v1/appStoreVersions"),
        ("PATCH", "/v1/appStoreVersions/{id}/relationships/build"),
        ("POST", "/v1/betaTesters"),
        ("DELETE", "/v1/betaGroups/{id}/relationships/betaTesters"),
        ("POST", "/v1/betaGroups/{id}/relationships/builds"),
        ("POST", "/v1/betaAppReviewSubmissions"),
        ("POST", "/v1/reviewSubmissions"),
        ("POST", "/v1/reviewSubmissionItems"),
        ("PATCH", "/v1/reviewSubmissions/{id}"),
        ("POST", "/v1/customerReviewResponses"),
        ("POST", "/v1/appStoreVersionLocalizations"),
        ("POST", "/v1/appInfoLocalizations"),
        ("PATCH", "/v1/apps/{id}"),
        ("POST", "/v1/appScreenshots"),
        ("PATCH", "/v1/appScreenshots/{id}"),
    }
)

# Endpoints that change data. Used by tests to make sure only write tools call them.
WRITE_METHODS: Final = frozenset({"POST", "PATCH", "DELETE", "PUT"})
