"""One pure derivation of the engine's private connection registry key."""
import hashlib


def connection_registry_key(api_ref,base_url,model_name,limits,binding,credential_root):
    identity=(api_ref,str(base_url).rstrip('/'),model_name,repr(limits),
        binding['revision'] if binding else '',credential_root)
    digest=hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()[:20]
    return f'__connection_{digest}__{model_name}'
