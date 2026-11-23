"""wa-gw-7: the live bot answered "ada gangguan" because google-genai 3.x
defaults to API version v1, where function calling is refused (400)."""
from app.ai import gemini


def test_client_is_pinned_to_v1beta():
    gemini._client.cache_clear()
    client = gemini._client()
    assert client._api_client._http_options.api_version == "v1beta"
