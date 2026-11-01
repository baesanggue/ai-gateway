"""가명화 부품 (#12에서 자리를 만들고, M2에서 진짜 가명화로 바꾼다).

파이프라인은 Masker만 알고 구현은 모른다. 그래서 M2에서 부품을 바꿔 끼우면 흐름은 그대로다.
- mask(messages): 밖으로 나갈 메시지 전체를 가명화한다 (#37: 도구 결과·대화 이력 포함).
  복원 대응표(mapping)는 요청 처리 중 메모리에만 있다 (DB·로그·외부 저장 금지)
- restore(text, mapping): LLM 답변의 가명 토큰을 원래 값으로 되돌린다
- available: False면 가명화를 할 수 없는 부품이다. 파이프라인은 개인정보가 보이거나 확인할 수 없는 요청을
  밖으로 보내지 않고 막는다 (routing.policy.decide_without_masking)
"""
from dataclasses import dataclass, field
from typing import Protocol

from gateway.masking.patterns import find_pii


@dataclass
class MaskResult:
    messages: list[dict[str, str]]                         # 가명화된 메시지 ({"role", "content"})
    mapping: dict[str, str] = field(default_factory=dict)  # 가명 토큰 → 원래 값. 메모리에만 둔다
    counts: dict[str, int] = field(default_factory=dict)   # 개인정보 종류별 개수 (로그용, 값 없음)

    @property
    def has_pii(self) -> bool:
        return bool(self.counts)


class Masker(Protocol):
    available: bool

    def mask(self, messages: list[dict[str, str]]) -> MaskResult: ...

    def restore(self, text: str, mapping: dict[str, str]) -> str: ...


class UnavailableMasker:
    """M1용: 가명화가 아직 없다. 메시지는 그대로 두고, 정규식으로 보이는 개인정보 종류만 센다.
    available=False라서 파이프라인이 개인정보 요청을 막는다. 원본이 밖으로 나가지 않게 하는 임시 장치다."""

    available = False

    def mask(self, messages: list[dict[str, str]]) -> MaskResult:
        counts: dict[str, int] = {}
        for m in messages:
            for kind in find_pii(m["content"]):
                counts[kind] = counts.get(kind, 0) + 1
        return MaskResult([dict(m) for m in messages], {}, counts)

    def restore(self, text: str, mapping: dict[str, str]) -> str:
        return text
