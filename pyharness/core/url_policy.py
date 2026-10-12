"""HTTP endpoint validation shared by registries and knowledge clients."""
from urllib.parse import urlsplit
from pyharness.errors import raise_code

METADATA_HOSTS = frozenset({'169.254.169.254', 'metadata.google.internal', '100.100.100.200'})


def validate_service_url(url: str) -> str:
    value = str(url or '').strip()
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise_code('CFG-601', reason='invalid_endpoint')
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise_code('CFG-601', reason='invalid_endpoint')
    if parts.hostname.lower() in METADATA_HOSTS:
        raise_code('CFG-601', reason='metadata_endpoint_denied')
    if parts.username or parts.password or parts.fragment:
        raise_code('CFG-601', reason='endpoint_credentials_forbidden')
    return value
