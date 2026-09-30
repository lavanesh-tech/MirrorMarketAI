from __future__ import annotations

import pytest

from app.domain.roles import WorkspaceRole, has_at_least

pytestmark = pytest.mark.unit

ORDER = [WorkspaceRole.VIEWER, WorkspaceRole.MEMBER, WorkspaceRole.EDITOR, WorkspaceRole.OWNER]


@pytest.mark.parametrize("role", ORDER)
@pytest.mark.parametrize("minimum", ORDER)
def test_role_hierarchy_is_total_order(role: WorkspaceRole, minimum: WorkspaceRole) -> None:
    assert has_at_least(role, minimum) is (ORDER.index(role) >= ORDER.index(minimum))


def test_every_role_is_ranked() -> None:
    assert set(ORDER) == set(WorkspaceRole)
