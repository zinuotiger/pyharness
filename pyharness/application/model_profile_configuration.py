"""One mapping from a model profile to its declared execution parameters."""
from __future__ import annotations


def profile_values(profile, tenant_id, defaults):
    """Legacy missing fields inherit caller defaults, never another profile."""
    return {
        'model': profile['model'],
        'base_url': profile['base_url'],
        'api_key': f"tenant:{tenant_id}:{profile['id']}",
        'temperature': profile.get('temperature', defaults.get('temperature', 0.7)),
        'max_tokens': profile.get('max_tokens', defaults.get('max_tokens', 4096)),
        'fallback_models': list(profile.get('fallback_models') or []),
    }
