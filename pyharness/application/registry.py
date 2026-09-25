"""Tenant-keyed ApplicationService registry for desktop shells."""
from __future__ import annotations

from typing import Any

from pyharness.application.service import ApplicationService
from pyharness.core.tenant_settings import normalize_tenant_id


class ApplicationServiceRegistry:
    """Create one isolated service instance per tenant on demand."""

    def __init__(self, ctx: Any, *, channel: str = "desktop") -> None:
        self.ctx = ctx
        self.channel = str(channel or "desktop")
        self._services: dict[str, ApplicationService] = {}

    def get(self, tenant_id: str = "default") -> ApplicationService:
        tenant = normalize_tenant_id(tenant_id)
        service = self._services.get(tenant)
        if service is None:
            service = ApplicationService(
                self.ctx, channel=self.channel, tenant_id=tenant)
            self._services[tenant] = service
        return service

    def tenants(self) -> list[str]:
        return sorted(self._services)

    def all(self) -> list[ApplicationService]:
        return list(self._services.values())

    async def close_all(self) -> None:
        errors = []
        for tenant, service in list(self._services.items()):
            try:
                await service.shutdown()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._services.pop(tenant, None)
        if errors:
            for error in errors[1:]:
                errors[0].add_note(f'additional tenant shutdown failure: {type(error).__name__}')
            raise errors[0]


__all__ = ["ApplicationServiceRegistry"]
