"""One place that talks to Claude, so every feature handles keys and errors the same way."""
import os

MODEL = os.environ.get("OPENCAREER_MODEL", "claude-sonnet-5-5")


class AIError(Exception):
    """A problem the dashboard should show as a friendly message."""


def default_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AIError("No API key: add ANTHROPIC_API_KEY=... to the .env file in your project folder.")
    import anthropic

    return anthropic.Anthropic()


def ask_claude(system: str, prompt: str, *, client=None, max_tokens: int = 4096) -> str:
    """Send one request and return Claude's text reply."""
    client = client or default_client()
    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except AIError:
        raise
    except Exception as error:  # network problems, bad key, rate limits...
        raise AIError(f"Claude API error: {error}") from error
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise AIError("Claude's answer was too long and got cut off, so it wasn't used. Try again.")
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
