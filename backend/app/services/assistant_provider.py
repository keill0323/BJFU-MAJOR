"""助手专用文本 tool_calls 适配；不复用或放宽图片审核传输门控。"""
import json
import math
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from app.config import settings

# 5.3 Flash 强制思考；4.7 仅供维护者显式选择兼容，不做自动降级。
# 文档能力不代表当前账户已验收；独立启用/外传开关仍然有效。
SUPPORTED_MODELS = {'glm-5.3-flash', 'glm-4.7-flash'}
MAX_CONTEXT_BYTES = 100_000
MAX_RESPONSE_BYTES = 131_072
MAX_REASONING_BYTES = 24_000
MAX_TURN_REASONING_BYTES = 48_000


class ProviderError(Exception):
    def __init__(self, code='provider_unavailable'):
        self.code = code


def validated_reasoning(message, *, required=False):
    """仅供同次请求的工具协议使用，绝不落库或返回客户端。"""
    reasoning = message.get('reasoning_content')
    if 'reasoning_content' not in message:
        if required:
            raise ProviderError('provider_response_invalid')
        return None
    if not isinstance(reasoning, str) or (required and not reasoning.strip()):
        raise ProviderError('provider_response_invalid')
    if len(reasoning.encode('utf-8')) > MAX_REASONING_BYTES:
        raise ProviderError('provider_reasoning_limit')
    return reasoning


def configured():
    return bool(settings.ASSISTANT_EXTERNAL_ENABLED and settings.ASSISTANT_TOOL_MODEL_VERIFIED
                and settings.ASSISTANT_MODEL.strip().lower() in SUPPORTED_MODELS and settings.ASSISTANT_API_KEY.strip()
                and _endpoint_allowed())


def _endpoint_allowed():
    try:
        parsed = urlsplit(settings.ASSISTANT_BASE_URL.rstrip('/'))
        return (parsed.scheme == 'https' and parsed.hostname == 'open.bigmodel.cn' and parsed.port in (None, 443)
                and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                and parsed.path == '/api/paas/v4')
    except ValueError:
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise ProviderError('provider_redirect_blocked')


def _model_payload(messages, *, latest_max_tokens):
    if not configured():
        raise ProviderError('provider_not_configured')
    model = settings.ASSISTANT_MODEL.strip().lower()
    payload = {'model': model, 'messages': messages}
    if model == 'glm-5.3-flash':
        payload.update(max_tokens=latest_max_tokens, temperature=1.0, top_p=0.95,
                       thinking={'type': 'enabled', 'clear_thinking': False}, reasoning_effort='low')
    else:
        payload.update(max_tokens=1200, temperature=0.2, thinking={'type': 'disabled'})
    return payload


def _request_message(payload, timeout, *, finish_reasons):
    """两种通道共用出站边界；调用方分别校验工具协议或摘要正文。"""
    if not configured():
        raise ProviderError('provider_not_configured')
    base = settings.ASSISTANT_BASE_URL.rstrip('/')
    if not _endpoint_allowed():
        raise ProviderError('provider_endpoint_not_allowed')
    try:
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(encoded) > MAX_CONTEXT_BYTES:
            raise ProviderError('context_limit')
        request = urllib.request.Request(base + '/chat/completions', data=encoded,
            headers={'Authorization': 'Bearer ' + settings.ASSISTANT_API_KEY, 'Content-Type': 'application/json'})
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ProviderError('provider_response_limit')
        choice = json.loads(body)['choices'][0]
        message = choice['message']
        if not isinstance(message, dict) or choice.get('finish_reason') not in finish_reasons:
            raise ProviderError('provider_response_invalid')
        return message
    except ProviderError:
        raise
    except (TimeoutError, urllib.error.URLError, OSError):
        raise ProviderError('provider_request_failed') from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError('provider_response_invalid') from None


def complete(messages, tools, timeout):
    payload = _model_payload(messages, latest_max_tokens=4096)
    payload.update(tools=tools, tool_choice='auto')
    message = _request_message(payload, max(0.1, min(20, timeout)), finish_reasons=('stop', 'tool_calls'))
    result = {'role': 'assistant', 'content': message.get('content') or '',
              'tool_calls': message.get('tool_calls') or []}
    try:
        # 标准接口已实测允许工具响应省略思考字段；仅验证实际返回的值，不补造。
        reasoning = validated_reasoning(message)
    except (ValueError, TypeError):
        raise ProviderError('provider_response_invalid') from None
    if reasoning is not None:
        result['reasoning_content'] = reasoning
    return result


def summarize(messages, timeout) -> str:
    """独立、无工具的短期摘要；思考内容只校验并丢弃。"""
    if not settings.ASSISTANT_SUMMARY_ENABLED:
        raise ProviderError('provider_summary_disabled')
    payload = _model_payload(messages, latest_max_tokens=2048)
    try:
        request_timeout = min(8, float(settings.ASSISTANT_SUMMARY_TIMEOUT_SECONDS), float(timeout))
        if not math.isfinite(float(timeout)) or not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ProviderError('time_budget')
    except (ValueError, TypeError, OverflowError):
        raise ProviderError('time_budget') from None
    message = _request_message(payload, request_timeout, finish_reasons=('stop',))
    try:
        tool_calls = message.get('tool_calls')
        if tool_calls is not None and tool_calls != []:
            raise ProviderError('provider_response_invalid')
        content = message.get('content')
        if not isinstance(content, str) or not content.strip():
            raise ProviderError('provider_response_invalid')
        if len(content.encode('utf-8')) > settings.ASSISTANT_SUMMARY_MAX_BYTES:
            raise ProviderError('provider_summary_limit')
        validated_reasoning(message)
        return content
    except (ValueError, TypeError):
        raise ProviderError('provider_response_invalid') from None
