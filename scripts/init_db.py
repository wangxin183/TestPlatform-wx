"""Initialize database: create all tables."""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.database import engine
from src.core.models import Base  # noqa: F401 — need to import all models so Base.metadata is populated
import src.core.models.models  # noqa: F401 — ensure all table definitions are loaded


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("All tables created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
