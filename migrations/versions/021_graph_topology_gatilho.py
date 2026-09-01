"""graph_topology.gatilho — frase de teste opcional por fluxo

Hub v2. O Graph Studio virou construtor de pipeline com teste manual; cada
fluxo pode guardar uma frase de gatilho ("teste de GUI") que só serve para
prefixar o campo "Mensagem de teste" do painel. NÃO liga classificação —
isso é decisão da tela /hub/routes.

Revision ID: 021_graph_topology_gatilho
Revises: 020_config_graph_executor
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa

revision: str = "021_graph_topology_gatilho"
down_revision = "020_config_graph_executor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_topology",
        sa.Column("gatilho", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graph_topology", "gatilho")
