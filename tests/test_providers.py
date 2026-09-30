from nexus.providers import discover_providers, provider_for_model


def test_provider_discovery_is_offline_and_uses_expected_keys():
    discovered = discover_providers(
        {
            "DEEPSEEK_API_KEY": "configured",
            "GOOGLE_API_KEY": "configured",
        }
    )
    by_name = {str(item["provider"]): item for item in discovered}
    assert by_name["DeepSeek"]["configured"] is True
    assert by_name["Gemini"]["configured"] is True
    assert by_name["OpenAI"]["configured"] is False


def test_provider_model_names_are_classified():
    assert provider_for_model("deepseek/deepseek-chat") == "DeepSeek"
    assert provider_for_model("gemini/gemini-2.5-flash") == "Gemini"
    assert provider_for_model("openrouter/openai/gpt-4o-mini") == "OpenRouter"
    assert provider_for_model("responses/codex-mini-latest") == "OpenAI Responses"
