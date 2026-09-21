import pytest

from ninerouter_orchestrator.models import FindingSeverity, Plan, Ticket, repair_policy


def test_dependency_order_is_stable() -> None:
    plan = Plan(
        objective="test",
        tickets=[
            Ticket(
                id="B", title="B", description="B", acceptance_criteria=["done"], dependencies=["A"]
            ),
            Ticket(id="A", title="A", description="A", acceptance_criteria=["done"]),
        ],
    )
    assert [ticket.id for ticket in plan.dependency_order()] == ["A", "B"]


def test_dependency_cycle_is_rejected_when_ordering() -> None:
    with pytest.raises(ValueError, match="cycle"):
        Plan(
            objective="test",
            tickets=[
                Ticket(
                    id="A",
                    title="A",
                    description="A",
                    acceptance_criteria=["done"],
                    dependencies=["B"],
                ),
                Ticket(
                    id="B",
                    title="B",
                    description="B",
                    acceptance_criteria=["done"],
                    dependencies=["A"],
                ),
            ],
        )


@pytest.mark.parametrize(
    ("severity", "forks", "reviewer_can_fix", "replan"),
    [
        (FindingSeverity.TRIVIAL, 0, True, False),
        (FindingSeverity.SMALL, 1, False, False),
        (FindingSeverity.MEDIUM, 2, False, False),
        (FindingSeverity.LARGE, 3, False, True),
    ],
)
def test_repair_policy(severity, forks, reviewer_can_fix, replan) -> None:
    policy = repair_policy(severity)
    assert (policy.forks, policy.reviewer_can_fix, policy.replan) == (
        forks,
        reviewer_can_fix,
        replan,
    )
