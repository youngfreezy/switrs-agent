"""Deployment-specific adapters; the verifier remains in switrs_agent."""
import os
import secrets


def gateway_client(*, oidc_token=None, timeout=90):
    import anthropic
    token = os.environ.get('AI_GATEWAY_API_KEY') or oidc_token or os.environ.get('VERCEL_OIDC_TOKEN')
    if not token:
        raise RuntimeError('AI Gateway authentication is not configured')
    return anthropic.AsyncAnthropic(api_key=token, base_url='https://ai-gateway.vercel.sh',
                                    timeout=timeout, max_retries=0)


def make_agent(*, oidc_token=None):
    from switrs_agent.agent import ApiAgent
    client = gateway_client(oidc_token=oidc_token)
    return ApiAgent('web-' + secrets.token_hex(16),
                    model=os.getenv('SWITRS_MODEL', 'anthropic/claude-opus-5-5'),
                    max_turns=8, max_tokens=4096, client=client)
