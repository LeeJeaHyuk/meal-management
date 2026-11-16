# 영양항목·도구 확장과 실제 조사

이 변경은 개발 코드입니다. 원장 DB 형식과 기존 영양 ref를 유지합니다.
운영 binding·submodule pin·원격 저장소 반영은 별도 단계입니다.

## 최소 정보와 완료 상태

식사 저장(record)은 원문 접수입니다. 음식과 섭취량·단위가 아직 구조화되지 않아도
보존할 수 있습니다. assess는 저장된 원문을 바꾸지 않고 연결된 음식·양·기준량과
기본 네 영양항목의 준비 상태를 반환합니다.

- 음식 참조 미해결, 양 미정, 기준량 단위 불일치, 검토 전 근거, 기본 영양값 결측을 구분합니다.
- 기본 개인 보고는 열량(kcal), 탄수화물(g), 단백질(g), 지방(g) 순서입니다.
- 값이 있는 부분합과 확정/추정·누락 원인을 항목별로 제공합니다. 미확인은 0이 아닙니다.
- ready_for_recorded_meals는 구조화된 기록의 준비 상태입니다. 하루 식사를 전부 보냈거나 원문 속 모든 음식이 빠짐없이 해석됐다는 보장은 아닙니다.
- 공개 HTML/SVG는 기존 비율 projection을 사용하며 새 영양 절대값·항목 정의·개인 기록을 자동 공개하지 않습니다.

명령(명시적인 로컬 경로를 사용):

~~~bash
python3 -m meal_management nutrients
python3 -m meal_management --journal <JOURNAL> --catalog <CATALOG> assess --event-id <EVENT_ID>
python3 -m meal_management --journal <JOURNAL> --catalog <CATALOG> report --start <DATE> --end <DATE> --format markdown
~~~

## 영양항목 추가

코드를 여러 곳 수정하는 대신, 다음 형식의 명시적인 JSON 설정 파일을 추가합니다.
이 예제는 항목 정의만 포함하며 실제 음식 영양값이나 개인 자료가 아닙니다.

~~~json
{
  "schema_version": 1,
  "definitions": [
    {"id": "cholesterol_mg", "label": "콜레스테롤", "unit": "mg"}
  ]
}
~~~

~~~bash
python3 -m meal_management --nutrient-definitions <DEFINITIONS> nutrients
python3 -m meal_management --nutrient-definitions <DEFINITIONS> --catalog <CATALOG> catalog-check --directory <CATALOG_REPOSITORY>
python3 -m meal_management --nutrient-definitions <DEFINITIONS> --journal <JOURNAL> --catalog <CATALOG> report --start <DATE> --end <DATE> --nutrients fiber_g,cholesterol_mg
python3 -m meal_management --nutrient-definitions <DEFINITIONS> --journal <JOURNAL> --catalog <CATALOG> stats --start <DATE> --end <DATE> --format html --nutrients cholesterol_mg
~~~

같은 catalog를 읽고 쓰고 검사·동기화하는 프로세스에는 같은 정의를 제공합니다.
ID는 단위 접미사를 가지며(kcal/g/mg/ug), 기존 ID를 덮어쓰지 않습니다.
minimum/maximum을 추가하면 영양값 검증에도 적용합니다. 이는 기술적 범위 정의이며
개인의 권장 섭취량이나 건강 판단이 아닙니다.
정의는 메타데이터입니다. 임의 코드·Python module·서비스를 불러오지 않습니다.
기본 네 항목은 항상 보고서에 남고 --nutrients는 추가 표시 항목을 고릅니다.
이 설정 없이 미등록 항목을 넣으면 거부합니다.

Python 호출은 NutrientRegistry, load_registry와 Catalog(root, registry)를 사용합니다.
build(..., nutrients=["추가 ID"])는 해당 정의를 따라 JSON·Markdown·개인 HTML을 생성합니다.
nutrition_summary는 항목별 total_partial/confirmed_partial/estimated_partial, 포함·누락·미해결 수와 이유를 제공합니다.

## 오케스트라의 도구 추가·교체

도구 등록부는 비공개 agent-platform의 harnesses/meal_real_use/tools.py에 있습니다.
공개 meal-management는 모델·검색·OCR·발송을 실행하지 않습니다.

도구를 추가할 때 trusted 코드의 create_registry에서 ToolRegistry.register를 호출합니다.
정의(JSON Schema 입력), handler, capability, adapter ID, version, 선택 result_schema를 지정합니다.
도구 목록은 이 등록부에서 모델에 전달되고 호출도 같은 등록부로 실행됩니다.
새 handler를 연결하기 위해 Workspace의 기존 분기문을 추가할 필요가 없습니다.

기존 도구 구현을 교체할 때 register_adapter(도구명, 새 adapter ID, handler)로
같은 입력·출력 계약을 구현한 callable을 등록합니다. 그 뒤 operator에서 선택합니다.

~~~json
{
  "meal_module": "meal_management",
  "nutrient_definitions": "nutrient-definitions.json",
  "tool_adapters": {"ocr_extract": "registered-adapter-id"},
  "disabled_tools": []
}
~~~

registered-adapter-id는 설명용 자리표시자입니다. 실제 코드에 등록된 ID만 선택할 수 있습니다.
현재 기본 adapter ID는 builtin-v1이며, 새로운 ID를 설정하는 것만으로 OCR이 설치되지는 않습니다.
설정 파일에서 import 경로·shell·URL 실행기를 직접 지정할 수 없습니다.
입력은 type/required/enum/추가 필드 및 명시한 길이·항목 수 제한을 확인하고, 출력도
등록된 result_schema와 JSON 유효성을 확인합니다.
진행 중 시험은 도구·adapter 계약 fingerprint를 고정해 중간 교체를 거부합니다.

공개 도메인 CLI를 연결할 때 meal_module=meal_management를 명시합니다.
기본값 harnesses.meal은 기존 실행기의 호환 경로입니다. nutrient_definitions는
준비된 개발 workspace 안의 상대 경로이며, 모델이 CLI 인자로 임의 경로를 덮어쓸 수 없습니다.

## 실제 조사와 테스트 입력

- 실제 조사는 data_mode=public_research와 research_target.name/official_url을 사용합니다.
  모델에 실제 대상과 URL을 전달하며, URL은 근거를 찾는 출발점이지 검증 완료 표시가 아닙니다.
- 조사 예: 백채김치찌개의 돼지김치찌개 메뉴 구성·기준량과 공개 영양정보 유무를 조사하거나,
  스타벅스 아이스 카페 아메리카노의 국내 사이즈별 공식 영양정보를 확인합니다.
- 실제 페이지를 읽지 못했거나 수치가 없으면 그 한계를 남깁니다. 가짜 메뉴·가짜 검색 결과·출처 없는 영양값으로 대체하지 않습니다.
- 개인 식사 기록·수정·집계 시험에 실제 입력이 필요할 때만 격리된 기술 입력 또는 승인된 합성 자료를 사용합니다.
  조사를 위해 식사·체중·방문 이력을 임의로 만들지 않습니다.
- 사진 양 추정과 영양표 OCR은 별도 기능입니다. 사진 추정 미지원 사례는 실행·통과율 분모에서 제외합니다.
- 이번 unit test의 숫자는 임시 디렉터리의 산술·저장 프로토콜 입력이며 음식 사전이나 실제 조사 결과로 저장하지 않습니다.

## 구성품 참조의 조합

catalog-compose는 identity, components, references를 stdin으로 받습니다.
components는 nutrition_ref·amount·unit과 선택 amount_range(low/high/reason)입니다.
references는 실제 record의 ref→record mapping이며 canonical hash를 검증합니다.
각 성분은 amount / 원래 basis.quantity로 환산하고 Decimal로 영양항목을 합산합니다.
결과는 1개의 조합 serving이며 components가 원래 참조와 수량을 보존합니다.
이미 만든 반량 조합을 1회 쓸 때 amount=1 serving으로 사용해 이중 반량을 방지합니다.
서로 다른 단위의 임의 환산과 기본 영양항목 결측의 0 채우기를 거부합니다.
추정 수량 범위와 참조의 불확실성은 구간 합산하며, 실측 신뢰구간으로 표시하지 않습니다.
파생 record의 source는 첫 구성품 근거의 인덱스입니다. 모든 실제 근거는 components
참조를 따라가며, 조합 합계를 공식 단일 메뉴 측정값으로 주장하지 않습니다.
명령은 JSON 값만 반환하며 사전·원장에 쓰지 않습니다.

미계량인 일부 분량은 단일 구성품의 fraction과 estimate_reason만 지정할 수 있습니다.
정량 범위를 모를 때에는 원래 1회분을 넘지 않는다는 가정의 [0, 원래 전체량]을 보수적인
범위로 표시합니다. 이는 실측 오차/신뢰구간이 아니며 사용자가 검토할 초안입니다.
전체량 이상이나 여러 구성품의 추정은 명시적인 수량 범위를 요구합니다.
