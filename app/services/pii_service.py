from app.ai.model_manager import get_pii_detector
from app.schemas.pii import PIIDetectionResponse, DetectedEntity
from app.services.pii_settings_service import PIISettingsService
from app.db.session import get_db  # get_session -> get_db로 변경
import logging

logger = logging.getLogger(__name__)


class PIIDetectionService:
    """PII 탐지 비즈니스 로직을 처리하는 서비스"""

    def __init__(self):
        # 싱글톤 패턴으로 모델 인스턴스 재사용
        self.detector = None

    async def analyze_text(self, text: str) -> PIIDetectionResponse:
        """텍스트를 분석하여 PII 탐지 결과 반환 (설정 기반 필터링 포함)"""

        # 싱글톤 모델 인스턴스 가져오기
        detector = get_pii_detector()

        # AI 모델로 PII 탐지
        detection_result = await detector.detect_pii(text)

        # PII 설정 조회 (캐시 사용)
        settings_dict = await self._get_pii_settings()

        # 설정 기반 필터링
        filtered_entities = []
        for entity in detection_result["entities"]:
            entity_type = entity["type"]

            # 해당 타입의 설정 확인
            setting = settings_dict.get(entity_type)

            # 설정이 없거나 비활성화된 경우 제외
            if not setting or not setting.get("enabled", True):
                logger.debug(f"Filtered out {entity_type} (disabled)")
                continue

            # confidence를 퍼센트로 변환 (0.0~1.0 → 0~100)
            confidence_percent = entity["confidence"] * 100
            threshold = setting.get("threshold", 0)

            # threshold 미만인 경우 제외
            if confidence_percent < threshold:
                logger.debug(
                    f"Filtered out {entity_type} '{entity['value']}' "
                    f"(confidence {confidence_percent:.1f}% < threshold {threshold}%)"
                )
                continue

            # 필터 통과
            filtered_entities.append(entity)

        # 필터링된 엔티티로 응답 구성
        entities = [
            DetectedEntity(
                type=entity["type"],
                value=entity["value"],
                confidence=entity["confidence"],
                token_count=entity["token_count"]
            )
            for entity in filtered_entities
        ]

        # has_pii는 필터링 후 결과 기준
        has_pii = len(entities) > 0

        # reason과 details 생성
        reason = self._generate_reason(has_pii, entities)
        details = self._generate_details(has_pii, entities)

        return PIIDetectionResponse(
            has_pii=has_pii,
            reason=reason,
            details=details,
            entities=entities
        )

    def _generate_reason(self, has_pii: bool, entities: list[DetectedEntity]) -> str:
        """탐지 결과에 대한 이유 생성"""
        if not has_pii:
            return "개인정보가 탐지되지 않았습니다"

        if len(entities) == 1:
            entity_type = entities[0].type
            return f"개인정보 1개 탐지됨 ({entity_type})"

        entity_types = list(set(entity.type for entity in entities))
        type_str = ", ".join(entity_types)
        return f"개인정보 {len(entities)}개 탐지됨 ({type_str})"

    def _generate_details(self, has_pii: bool, entities: list[DetectedEntity]) -> str:
        """탐지된 개인정보에 대한 상세 설명 생성"""
        if not has_pii:
            return "입력된 텍스트에서 개인정보가 발견되지 않았습니다."

        details_parts = []
        for entity in entities:
            confidence_pct = f"{entity.confidence:.1%}"
            details_parts.append(f"{entity.type} '{entity.value}' (신뢰도: {confidence_pct})")

        details_str = ", ".join(details_parts)
        return f"다음 개인정보가 탐지되었습니다: {details_str}"

    async def _get_pii_settings(self) -> dict[str, dict]:
        """
        PII 설정 조회 (캐시 활용)

        Returns:
            dict[entity_type, {"enabled": bool, "threshold": int}]
        """
        try:
            # DB 세션 가져오기 (get_session -> get_db로 변경)
            async for session in get_db():
                service = PIISettingsService(session)
                settings_dict = await service.get_settings_for_filtering()
                return settings_dict
        except Exception as e:
            logger.error(f"Failed to load PII settings, using defaults: {str(e)}")
            # 실패 시 모든 타입 활성화 (기본값)
            return {}