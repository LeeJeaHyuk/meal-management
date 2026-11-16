# 입력·출력 계약

## 기록 입력

record/revise는 JSON 객체 하나를 stdin으로 받습니다.
필수: channel, actor(로컬 불투명 식별자), message_id, received_at(시간대 포함 ISO),
kind(meal/weight), text(원문).
occurred_on/occurred_at은 실제로 알려진 경우만 지정합니다.
식사 날짜가 불명이면 접수일을 식사일로 만들지 않습니다.

meal components:
nutrition_ref, amount, unit(g/ml/serving), certainty(confirmed/estimated).
분석이 덜 끝나면 components 없이 원문을 먼저 보존할 수 있습니다.
새 정보는 revise에 기존 event_id, expected-revision, revision-key를 제공해 보완합니다.
체중은 weight_kg로 구분하며 음식 성분과 합치지 않습니다.

같은 출처 메시지 재수신은 중복 방지하고, 다른 메시지를 같은 텍스트라는 이유로 합치지 않습니다.
정정은 원래 식별자·접수 시각을 보존하며 append-only 판을 추가합니다.

## 영양 사전

catalog-put 필수:
food_id, name, basis(quantity/unit), nutrients,
source(url/observed_on/publisher), review_state(unreviewed/verified).
brand, preparation 및 근거 evidence_sha256/evidence_quote는 선택입니다.
개인 식사·체중·채팅 metadata는 사전 항목에 넣지 않습니다.

nutrients의 기본 정의는 energy_kcal, carbohydrate_g, protein_g, fat_g,
fiber_g, sodium_mg, sugars_g, saturated_fat_g입니다. 원문에서 확인한 값 또는
허용된 추정 근거가 있는 값만 제공합니다. --nutrient-definitions의 명시적 정의로
새 ID/단위/검증 범위를 추가할 수 있습니다. catalog-put/get/find/check/sync는
같은 정의를 사용하며 기존 ID는 덮어쓸 수 없습니다.
사전 입력에서 미확인 영양소는 해당 필드를 생략하고 0으로 채우지 않습니다.
원장 집계 결과의 결측값은 null로 반환될 수 있습니다.
추정 참조는 uncertainty.kind=estimated, reason, ranges(low/high)를 지정합니다.
범위는 같은 basis이며 중앙값을 포함합니다. 통계적 신뢰구간이라는 뜻은 아닙니다.

영양 ref는 내용 hash입니다. catalog-get --ref는 food_id가 아닌 전체 nutrition_ref를 받습니다.
새 정보는 새 ref를 만들며 과거 계산을 조용히 덮어쓰지 않습니다.
verified는 승인된 호출 담당의 원문 검토 기록이며 자동 사실 검증 기능이 아닙니다.

## 집계·그래프

report는 연결 가능한 확인값과 추정값을 구분합니다. 미기록 날짜는 섭취 0이 아닙니다.
stats --format json|html, 공개 SVG는 --mode public --format svg로 생성합니다.
--svg-view summary는 전체 구성비, daily는 상대 날짜별 비중·변화·영양 구성비입니다.

날짜별 증감은 바로 전날과만 비교합니다. 첫날, 전날/당일 미기록, 전날 0의 증감률은
미확인입니다. 미기록 구간을 연결하거나 결식을 추정하지 않습니다.
삼대영양소가 함께 확인된 항목만 4·4·9 kcal/g로 구성비를 계산합니다.

선택 --energy-profile의 로컬 JSON에 equation(male/female), age_years, height_cm,
weight_kg, activity_factor를 명시한 경우에만 BMR·소비 추정치를 표시합니다.
활동량이나 신체값의 기본값을 만들지 않습니다. 추정값은 의료적 판단·체중 예측이 아닙니다.

## 발송 상태

delivery-claim은 등록된 본인 수신처의 report_hash를 고정합니다.
claimed=true일 때만 외부 adapter가 실제 발송합니다.
delivery-finish는 sent/failed/unknown과 실제 영수증을 남깁니다.
확실한 failed만 정해진 횟수 안에서 재시도하며 unknown을 자동 재발송하지 않습니다.
이 라이브러리는 Telegram/OpenBot API를 호출하지 않습니다.

## 기록 준비 상태와 기본 영양 보고

record 성공은 원문 저장입니다. assess --event-id는 음식 참조·섭취량·단위와 기본
네 영양값의 준비 상태를 확인하며 원장 수정이나 원문 재해석을 수행하지 않습니다.
report/stats는 기본 네 항목과 --nutrients로 선택한 추가 항목을 nutrient_summary에
반환합니다. 각 항목에 확정/추정 부분합, 포함·누락·미해결 성분 수, 날짜 미확인 제외 수와
이유를 남깁니다. 계산 불가능한 값은 null이며 실제 관측된 0과 구분합니다.
기본 네 값이나 요청한 추가 값이 부족하면 calculation_state는 partial_or_pending입니다.
ready_for_recorded_meals도 원문에서 누락된 성분이나 미기록된 하루 전체를 보증하지 않습니다.
설정/호출 예시는 [확장 방법](extensions.md)을 따릅니다.

## Intake-provided nutrient values

A journal component may include provided_nutrients and provided_basis together.
These are user-declared values for that basis, stored only in the intake event.
They override those nutrient contributions after quantity scaling. Unspecified
nutrients still require a public reference; they are not zero. Basis units must
match the component. Shared catalog records and factual review are unchanged.
