"""Claude API 호출 (선택 기능). ANTHROPIC_API_KEY 가 없으면 AI 기능만 꺼진다."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import anthropic

DEFAULT_MODEL = "claude-opus-5"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "llm_cache"
# 한 번에 보내는 본문 상한 (글자 수). 넘으면 잘랐다고 화면에 알린다.
MAX_INPUT_CHARS = 150_000

SYSTEM = (
    "당신은 한국 주식시장의 산업·기업 분석을 돕는 애널리스트입니다. "
    "주어진 자료(공시, 리포트, 뉴스)에 근거해서만 한국어로 답하고, 자료에 없는 내용은 추측하지 말고 '자료에 없음'이라고 쓰세요. "
    "숫자는 단위를 명확히 적으세요. 투자 권유가 아니라 분석 보조 자료입니다."
)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _cache(self, kind: str, payload: dict) -> Path:
        key = json.dumps({"m": self.model, **payload}, sort_keys=True, ensure_ascii=False)
        return CACHE_DIR / f"{kind}_{hashlib.sha1(key.encode()).hexdigest()[:20]}.json"

    def _call(self, prompt: str, schema: dict | None, max_tokens: int) -> str:
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium"},
        )
        if schema:
            kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        if self.model.startswith(("claude-opus-5", "claude-fable")):
            # 안전 분류기가 거절하면 서버에서 다른 모델로 자동 재시도
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
            api = self.client.beta.messages
        else:
            api = self.client.messages
        try:
            resp = api.create(**kwargs)
        except anthropic.AuthenticationError:
            raise LLMError("Anthropic API 키가 올바르지 않습니다.")
        except anthropic.RateLimitError:
            raise LLMError("요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API 오류 ({e.status_code}): {e.message}")
        except anthropic.APIConnectionError:
            raise LLMError("Claude API 에 연결하지 못했습니다.")
        if resp.stop_reason == "refusal":
            raise LLMError("모델이 이 요청에 답하지 않았습니다.")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "max_tokens":
            text += "\n\n(응답이 길어 일부가 잘렸습니다.)"
        return text

    def ask(self, instruction: str, document: str, max_tokens: int = 8000) -> tuple[str, bool]:
        """문서 + 지시 -> 답변. (답변, 본문을 잘랐는지)"""
        truncated = len(document) > MAX_INPUT_CHARS
        doc = document[:MAX_INPUT_CHARS]
        path = self._cache("ask", {"i": instruction, "d": doc})
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["text"], truncated
        prompt = f"<자료>\n{doc}\n</자료>\n\n{instruction}"
        text = self._call(prompt, None, max_tokens)
        path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        return text, truncated

    def extract(self, instruction: str, document: str, schema: dict,
                max_tokens: int = 8000) -> tuple[dict, bool]:
        truncated = len(document) > MAX_INPUT_CHARS
        doc = document[:MAX_INPUT_CHARS]
        path = self._cache("extract", {"i": instruction, "d": doc, "s": schema})
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), truncated
        prompt = f"<자료>\n{doc}\n</자료>\n\n{instruction}"
        text = self._call(prompt, schema, max_tokens)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise LLMError("구조화된 결과를 해석하지 못했습니다. 다시 시도해 주세요.")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data, truncated
