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
        for service in list(self._services.values()):
            await service.shutdown()
        self._services.clear()


__all__ = ["ApplicationServiceRegistry"]
