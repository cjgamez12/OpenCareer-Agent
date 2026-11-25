"""Tests for the shared Claude helper."""
from types import SimpleNamespace

import pytest

from opencareer.ai import AIError, ask_claude


class Client:
    def __init__(self, stop_reason):
        self.stop_reason = stop_reason
        self.messages = self

    def create(self, **kwargs):
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text="hello")])


def test_returns_text():
    assert ask_claude("s", "p", client=Client("end_turn")) == "hello"


def test_cut_off_answers_are_rejected():
    with pytest.raises(AIError, match="cut off"):
        ask_claude("s", "p", client=Client("max_tokens"))
