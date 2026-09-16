# -*- coding: utf-8 -*-
"""大模型客户端：封装 OpenAI 兼容接口的 Chat Completions 调用。

只依赖标准库 + requests，模型配置（API Key / 模型名 / 端点）统一从
src.config 读取（由 .env 注入），便于切换任意 OpenAI 兼容服务。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import requests

from src import config


class LLMError(Exception):
    """LLM 调用失败（未配置 Key / 网络异常 / 非 200 / 返回结构异常）时抛出。"""


def _build_url() -> str:
    """拼接 Chat Completions 的完整请求地址。"""
    base = config.BASE_URL.rstrip("/")
    # 已直接给出完整端点时不再重复拼接
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def chat_completion(
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 2000,
    timeout: Optional[int] = None,
) -> str:
    """调用 OpenAI 兼容的 Chat Completions 接口并返回模型回复文本。

    参数:
        messages: 消息列表，元素形如 {"role": "system"|"user"|"assistant", "content": str}。
        temperature: 采样温度（生成 SQL 建议 0，追求确定性）。
        max_tokens: 最大生成 token 数。
        timeout: 请求超时（秒），默认取 config.REQUEST_TIMEOUT。

    返回:
        模型回复文本（choices[0].message.content，已 strip）。

    异常:
        LLMError: 未配置 API Key、网络错误、非 200 状态码、返回结构异常或空回复。
    """
    if not config.API_KEY:
        raise LLMError("未配置 API_KEY，无法调用大模型（请在 .env 配置或开启 ENABLE_MOCK）")

    url = _build_url()
    payload = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {config.API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False),
            timeout=timeout or config.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise LLMError(f"请求大模型失败：{exc}") from exc

    if resp.status_code != 200:
        raise LLMError(f"大模型返回异常状态码 {resp.status_code}：{resp.text[:500]}")

    try:
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason", "")
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise LLMError(f"大模型返回结构异常：{resp.text[:500]}") from exc

    if not isinstance(content, str) or not content.strip():
        # 推理模型（如 deepseek-v4-pro）会先输出思考过程再给最终答案；
        # 若因 max_tokens 不足被截断（finish_reason=length），content 可能为空。
        if finish_reason == "length":
            raise LLMError("大模型思考过长被截断（max_tokens 不足），未产出最终答案")
        raise LLMError("大模型返回了空内容")

    return content.strip()
