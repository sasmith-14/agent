from pilot.providers.base import Message, Usage


def test_message_defaults_are_independent() -> None:
    first = Message(role="assistant")
    second = Message(role="assistant")
    first.provider_data["opaque"] = "private"
    assert second.provider_data == {}
    assert "provider_data" not in first.model_dump()
    assert Usage(input_tokens=3, output_tokens=2).total_tokens == 5
