"""Add a column

Revision ID: 5c89a85b3248
Revises: 051077b31c5a
Create Date: 2024-09-10 21:35:59.741079

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c89a85b3248'
down_revision: Union[str, None] = '051077b31c5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
