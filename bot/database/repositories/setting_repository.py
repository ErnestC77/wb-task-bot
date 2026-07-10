from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import SystemSetting


class SettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> SystemSetting | None:
        return await self.session.scalar(
            select(SystemSetting).where(SystemSetting.key == key))

    async def get_all(self) -> list[SystemSetting]:
        return list(await self.session.scalars(select(SystemSetting)))

    async def upsert(self, key: str, value_json: str, value_type: str, category: str,
                      updated_by: int | None, is_editable: bool = True) -> SystemSetting:
        setting = await self.get(key)
        if setting is None:
            setting = SystemSetting(key=key, value_json=value_json, value_type=value_type,
                                     category=category, updated_by_user_id=updated_by,
                                     is_editable=is_editable)
            self.session.add(setting)
        else:
            setting.value_json = value_json
            setting.value_type = value_type
            setting.category = category
            setting.updated_by_user_id = updated_by
            setting.is_editable = is_editable
        await self.session.flush()
        return setting
