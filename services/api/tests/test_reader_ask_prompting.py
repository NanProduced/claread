from app.services.reader_ask.prompting import load_prompt_layers


def test_load_prompt_layers_reads_reader_ask_prompt_files() -> None:
    layers = load_prompt_layers()

    assert set(layers) == {"system", "planner", "answer", "schema", "policy_examples"}
    assert "Ask Claread" in layers["system"]
