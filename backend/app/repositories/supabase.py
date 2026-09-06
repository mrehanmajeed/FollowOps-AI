from typing import Any
from uuid import UUID

from supabase import AsyncClient, create_async_client

from app.core.config import get_settings


class SupabaseRepository:
    def __init__(self, client: AsyncClient):
        self.client = client

    async def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.table(table).insert(payload).execute()
        if not response.data:
            raise RuntimeError(f"Insert failed for table '{table}'")
        return response.data[0]

    async def get_by_id(
        self,
        table: str,
        record_id: UUID,
    ) -> dict[str, Any] | None:
        response = (
            await self.client.table(table)
            .select("*")
            .eq("id", str(record_id))
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    async def get_one_by(
        self,
        table: str,
        column: str,
        value: str,
    ) -> dict[str, Any] | None:
        response = (
            await self.client.table(table)
            .select("*")
            .eq(column, value)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    async def update(
        self,
        table: str,
        record_id: UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = (
            await self.client.table(table)
            .update(payload)
            .eq("id", str(record_id))
            .execute()
        )
        if not response.data:
            raise RuntimeError(f"Update failed for table '{table}'")
        return response.data[0]

    async def list_all(self, table: str) -> list[dict[str, Any]]:
        response = await self.client.table(table).select("*").execute()
        return response.data or []

    async def find(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
        columns: str = "*",
    ) -> list[dict[str, Any]]:
        query = self.client.table(table).select(columns)
        for column, value in (filters or {}).items():
            query = query.eq(column, value)
        if order_by:
            query = query.order(order_by, desc=descending)
        if limit:
            query = query.limit(limit)
        response = await query.execute()
        return response.data or []

    async def find_one(
        self,
        table: str,
        filters: dict[str, Any],
        order_by: str | None = None,
        descending: bool = False,
    ) -> dict[str, Any] | None:
        records = await self.find(
            table, filters, order_by=order_by, descending=descending, limit=1
        )
        return records[0] if records else None

    async def exists(self, table: str, filters: dict[str, Any]) -> bool:
        return await self.find_one(table, filters) is not None


async def create_supabase_client() -> AsyncClient:
    settings = get_settings()
    return await create_async_client(
        settings.supabase_url,
        settings.supabase_secret_key.get_secret_value(),
    )
