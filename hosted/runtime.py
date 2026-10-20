"""Deployment-specific adapters; the verifier remains in switrs_agent."""
import os
import secrets


def make_agent():
    import anthropic
    from switrs_agent.agent import ApiAgent

    token = os.environ.get('AI_GATEWAY_API_KEY') or os.environ.get('VERCEL_OIDC_TOKEN')
    if not token:
        raise RuntimeError('AI Gateway authentication is not configured')
    client = anthropic.AsyncAnthropic(api_key=token, base_url='https://ai-gateway.vercel.sh',
                                      timeout=90, max_retries=0)
    return ApiAgent('web-' + secrets.token_hex(16),
                    model=os.getenv('SWITRS_MODEL', 'anthropic/claude-opus-5-5'),
                    max_turns=8, max_tokens=4096, client=client)
