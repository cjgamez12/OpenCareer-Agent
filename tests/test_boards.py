"""Tests for the company board registry."""
import pytest

from opencareer import boards, storage
from opencareer.models import JobPosting


@pytest.fixture
def conn():
    c = storage.connect(":memory:")
    yield c
    c.close()


def test_added_boards_join_the_defaults(conn):
    boards.add_board(conn, "Stripe", "Stripe")
    assert boards.all_boards(conn, {"okta": "Okta"}) == {"okta": "Okta", "stripe": "Stripe"}


@pytest.mark.parametrize("bad", ["", "has space", "../etc", "a/b", "x?y=1"])
def test_bad_board_names_are_rejected(conn, bad):
    with pytest.raises(ValueError):
        boards.add_board(conn, bad, "X")


def test_board_for_uses_remembered_board_then_company(conn):
    job = JobPosting(source="greenhouse", source_id="1", title="T", company="Okta", url="u1")
    assert boards.board_for(conn, job, {"okta": "Okta"}) == "okta"
    boards.remember(conn, [job], "okta-careers")
    assert boards.board_for(conn, job, {"okta": "Okta"}) == "okta-careers"
