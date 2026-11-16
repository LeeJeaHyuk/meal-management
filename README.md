# Meal Management

**모델·채팅 앱에서 독립된 식사 기록, 영양 사전, 통계 도구입니다.**

짧은 글을 해석하는 에이전트와 실제 기록·집계를 분리합니다. 확인된 데이터만
로컬 원장에 보존하고, 일반 영양정보는 출처가 있는 별도 사전으로 재사용합니다.
모델 연결, OCR, Telegram/OpenBot, 권한과 예약은 외부 오케스트라의 책임입니다.

| 이 저장소 | 외부 오케스트라 |
| --- | --- |
| 식사·체중 저장, 조회, append-only 정정 | 입력 해석, 모델·도구 선택 |
| 출처·기준량·단위가 연결된 영양 사전 | 공개 원문 조사, OCR |
| 결측·추정·확정값을 구분한 집계 | 사용자 확인 질문, 검토 정책 |
| 로컬 HTML·비율 SVG 생성 | 본인 채널 발송·예약·실패 복구 |
| 발송 claim/결과 기록 | 실제 메시징 호출과 영수증 정산 |

개인 원장·이미지·식단·신체값·호스트 설정·키·실행 결과는 포함하지 않습니다.
업무 코드만 MIT로 공개합니다. 벤치·모델 점수·원본 대화도 이 저장소의 범위가 아닙니다.

## 시작

Native Linux, Python 3.10+, Git을 기준으로 합니다. 실행 의존성은 Python 표준 라이브러리입니다.

~~~bash
git clone https://github.com/LeeJeaHyuk/meal-management.git
cd meal-management
python3 -m meal_management --help
python3 -m unittest discover -s tests -v
python3 tools/check_public_tree.py --root .
~~~

설치해서 사용할 수도 있습니다.

~~~bash
python3 -m pip install .
meal-management --help
~~~

## 원장과 사전은 코드 Git 밖에 둡니다

로컬에서 허용된 담당이 명시적으로 초기화합니다. 다음 예시는 빈 원장만 만들며
식사나 체중을 임의로 기록하지 않습니다.

~~~bash
MEAL_DATA="$HOME/.local/state/meal-management"
mkdir -p "$MEAL_DATA/catalog"
python3 -m meal_management --journal "$MEAL_DATA/journal.sqlite3" init
python3 -m meal_management --journal "$MEAL_DATA/journal.sqlite3" --catalog "$MEAL_DATA/catalog" report --start <YYYY-MM-DD> --end <YYYY-MM-DD> --format json
~~~

record / revise / catalog-put는 stdin으로 JSON 객체 하나를 받습니다.
실제 원문·개인값을 argv, Git, 공유 로그에 넣지 않습니다.
[입력·출력 계약](docs/contracts.md)을 먼저 읽으세요.

## 주요 명령

| 명령 | 용도 |
| --- | --- |
| nutrients / assess | 영양항목 정의 조회·저장된 음식/섭취량/기본 영양값 준비 상태 |\n| record / show / list | 원문 보존, 식사·체중 구분, 조회 |
| revise | 원래 식별자를 보존한 정정, 판 번호 충돌 방지 |
| catalog-compose | 구성품 ref·수량으로 열량·영양값과 추정 범위 합산, 사전 쓰기 없음 |
| catalog-validate | stdin 값 검사·예상 ref 계산, 사전 쓰기 없음 |
| catalog-put / find / get | 공개 영양 후보·검토된 참조의 내용 hash 연결 |
| catalog-check / sync | 사전 구조 검사와 승인 snapshot의 불변 참조 동기화 |
| report | 확정·추정 부분합, 연결되지 않은 항목과 결측 사유 |
| stats | 날짜별 열량·체중·선택적 소비 추정, HTML·공개 비율 SVG |
| delivery-claim / finish | 발송 중복 방지·실패/결과 불명 기록. 자체 발송은 하지 않음 |

~~~bash
python3 -m meal_management --journal <JOURNAL> --catalog <CATALOG> stats --start <DATE> --end <DATE> --format html > <PRIVATE_REPORT>
python3 -m meal_management --journal <JOURNAL> --catalog <CATALOG> stats --start <DATE> --end <DATE> --mode public --format svg --svg-view daily > <SHAREABLE_RATIO_SVG>
~~~

공개 모드는 실제 날짜·음식명·섭취량·체중·kcal 절대값을 제외합니다.
날짜별 비율도 개인 패턴을 드러낼 수 있으므로 익명성을 보장하지 않습니다.
파일 생성은 공개 게시나 사용자에게 발송한 상태와 다릅니다.

## 오케스트라에서 submodule로 사용

~~~bash
git submodule add https://github.com/LeeJeaHyuk/meal-management.git components/meal-management
git submodule update --init --recursive
~~~

상위 저장소가 승인한 commit을 고정합니다. 자동화마다 main으로 자동 갱신하지 않습니다.
설치 방식 또는 module path adapter로 호출하며 연결 예시는
[오케스트라 연결](docs/orchestrator-integration.md)에 있습니다.

[영양항목·도구 확장](docs/extensions.md) · [새 에이전트 시작](docs/agent-start.md) · [보안 경계](SECURITY.md) ·
[설계·검증 범위](docs/design.md) · [MIT](LICENSE)
