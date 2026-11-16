# 구조와 검증 범위

| 파일 | 책임 |
| --- | --- |
| __main__.py | JSON-in/JSON-out CLI |
| journal.py | SQLite 원장, 출처 중복 방지, 정정 이력 |
| nutrients.py / assessment.py | 확장 가능한 영양 정의·단위·범위, 음식/섭취량과 기본 영양값 준비 상태 |\n| catalog.py | 영양 값·단위·불확실성 검증, 내용 hash 참조 |
| catalog_git.py / locking.py | 사전 구조 검증, 승인 snapshot 동기화, cache 잠금 |
| reporting.py | 확정·추정·결측을 보존한 기간 집계 |
| statistics.py | 날짜별 비중·변화, 선택적 소비 추정, 오프라인 HTML/SVG |

기술적 unit test는 SQLite·임시 Git·CLI 입출력·산술·공개 projection을 확인합니다.
실제 사용자 기록, 라이브 모델, 메시징 서비스, 유료 API는 검사에서 사용하지 않습니다.
여러 모델의 자연어 품질과 실제 OpenBot/Telegram 흐름은 상위 오케스트라에서 별도로 검증합니다.

기존 업무 코드에서 깨끗한 파일만 추출했고 과거 비공개 Git 이력은 복사하지 않았습니다.
SOURCE-PROVENANCE에 추출된 파일의 내용 hash와 의도된 변경을 기록합니다.
호환 경로와 catalog cache lock의 독립화 외에 데이터 schema나 집계 기준을 바꾸지 않았습니다.

현재 개인 원장과 사전은 협력적 역할로 분리합니다. 같은 OS 사용자의 임의 접근을 차단하는
인증 서버나 암호화 저장소를 제공하지 않습니다. 기존 데이터를 자동 이전하지도 않습니다.

개발 변경: 영양항목 정의는 NutrientRegistry로 분리했습니다. 명시적 JSON 정의와
보고 선택은 기존 불변 ref/SQLite schema를 바꾸지 않습니다. 공개 projection은
추가 개인 영양항목·절대값을 자동으로 포함하지 않습니다. 모델·도구 등록은 여전히
비공개 오케스트라의 책임입니다. [확장 계약](extensions.md)을 확인하세요.
