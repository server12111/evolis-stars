from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from bot.database.models import Task, TaskCompletion
from bot.database.repositories.base import BaseRepository


class TaskRepository(BaseRepository):
    async def all_active(self) -> list[Task]:
        result = await self.session.execute(
            select(Task).where(Task.is_active == True).order_by(Task.id)
        )
        return list(result.scalars().all())

    async def all_tasks(self) -> list[Task]:
        result = await self.session.execute(select(Task).order_by(Task.id.desc()))
        return list(result.scalars().all())

    async def get(self, task_id: int) -> Task | None:
        return await self.session.get(Task, task_id)

    async def completed_ids(self, user_id: int) -> set[int]:
        result = await self.session.execute(
            select(TaskCompletion.task_id).where(TaskCompletion.user_id == user_id)
        )
        return set(result.scalars().all())

    async def is_completed(self, task_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            select(TaskCompletion.id).where(
                TaskCompletion.task_id == task_id,
                TaskCompletion.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_completed(self, task_id: int, user_id: int) -> bool:
        if await self.is_completed(task_id, user_id):
            return False
        result = await self.session.execute(
            update(Task)
            .where(
                Task.id == task_id,
                or_(Task.max_completions == 0, Task.completions_count < Task.max_completions),
            )
            .values(completions_count=Task.completions_count + 1)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False

        self.session.add(TaskCompletion(task_id=task_id, user_id=user_id))
        try:
            await self.session.flush()
            return True
        except IntegrityError:
            await self.session.rollback()
            return False

    async def create(
        self,
        title: str,
        description: str,
        url: str,
        task_type: str,
        reward: float,
        max_completions: int = 0,
        photo_file_id: str | None = None,
    ) -> Task:
        t = Task(
            title=title,
            description=description,
            url=url,
            task_type=task_type,
            reward=reward,
            max_completions=max_completions,
            photo_file_id=photo_file_id,
        )
        self.session.add(t)
        await self.session.commit()
        return t

    async def delete(self, task_id: int) -> bool:
        t = await self.session.get(Task, task_id)
        if t:
            await self.session.delete(t)
            await self.session.commit()
            return True
        return False
