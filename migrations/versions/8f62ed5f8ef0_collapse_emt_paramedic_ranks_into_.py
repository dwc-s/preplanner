"""collapse EMT/Paramedic ranks into Firefighter

Revision ID: 8f62ed5f8ef0
Revises: 82a7f5c8f620
Create Date: 2026-07-24 09:56:25.594076

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f62ed5f8ef0'
down_revision = '82a7f5c8f620'
branch_labels = None
depends_on = None


def upgrade():
    # The Firefighter/EMT and Firefighter/Paramedic ranks were collapsed into a
    # single "Firefighter" (see models.FIRE_RANKS). Move any existing members over
    # so their rank stays valid.
    op.get_bind().execute(sa.text(
        "UPDATE app_user SET rank='Firefighter' "
        "WHERE rank IN ('Firefighter/EMT', 'Firefighter/Paramedic')"
    ))


def downgrade():
    # Irreversible: the EMT/Paramedic distinction is not recoverable once merged.
    pass
