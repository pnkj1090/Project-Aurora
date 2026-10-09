import pytest
from app.services.ai.base import BaseAIProvider, ChatMessage, MessageRole, AIResponse
from app.services.ai.mock_provider import MockProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.factory import get_ai_provider


def test_base_provider_is_abstract():
    with pytest.raises(TypeError):
        BaseAIProvider()


@pytest.mark.asyncio
async def test_mock_provider_generation():
    provider = MockProvider()
    assert provider.provider_name == "mock"

    messages = [
        ChatMessage(role=MessageRole.USER, content="Hello Aurora")
    ]
    response = await provider.generate_response(
        messages=messages,
        model="custom-mock",
        temperature=0.5,
        max_tokens=256
    )

    assert isinstance(response, AIResponse)
    assert response.provider == "mock"
    assert response.model == "custom-mock"
    assert "Hello Aurora" in response.content
    assert response.metadata["temperature"] == 0.5
    assert response.metadata["max_tokens"] == 256


def test_factory_resolution():
    mock_prov = get_ai_provider("mock")
    assert isinstance(mock_prov, MockProvider)

    openai_prov = get_ai_provider("openai")
    assert isinstance(openai_prov, OpenAIProvider)
    assert openai_prov.provider_name == "openai"

    gemini_prov = get_ai_provider("gemini")
    assert gemini_prov.provider_name == "gemini"
    assert isinstance(gemini_prov, BaseAIProvider)

    with pytest.raises(ValueError):
        get_ai_provider("non_existent_provider")


def test_provider_switching_env_override():
    from app.core.config import Settings
    
    # Test Gemini settings
    s_gemini = Settings(AI_PROVIDER="gemini", AI_MODEL="gemini-3.8-flash", GEMINI_API_KEY="test-gemini-key")
    p_gemini = get_ai_provider(s_gemini.AI_PROVIDER, settings_override=s_gemini)
    assert p_gemini.provider_name == "gemini"
    assert p_gemini.api_key == "test-gemini-key"

    # Test OpenAI settings
    s_openai = Settings(AI_PROVIDER="openai", AI_MODEL="gpt-4o-mini", OPENAI_API_KEY="test-openai-key")
    p_openai = get_ai_provider(s_openai.AI_PROVIDER, settings_override=s_openai)
    assert p_openai.provider_name == "openai"
    assert p_openai.api_key == "test-openai-key"

