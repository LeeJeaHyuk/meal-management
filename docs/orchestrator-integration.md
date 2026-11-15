# 비공개 오케스트라 연결

공개 저장소의 책임은 업무 로직입니다.
비공개 상위 저장소는 모델·입력 앱·OCR·권한·예약·실제 경로·키·실행 이력을 관리합니다.
공개 module은 상위 오케스트라를 import하지 않으며 비공개 Git 접근이 없어도 동작합니다.

## Submodule

~~~bash
git submodule add https://github.com/LeeJeaHyuk/meal-management.git components/meal-management
git submodule update --init --recursive
~~~

gitlink의 commit으로 버전을 고정합니다.
처음 clone한 PC는 --recurse-submodules 또는 update --init --recursive를 실행합니다.
상위 코드와 하위 module을 검증한 뒤 gitlink 변경을 검토합니다.
git submodule update --remote를 매번 실행하는 방식으로 자동 배포하지 않습니다.

## 호출

방법 1: submodule의 package를 별도 환경에 설치하고 meal-management CLI를 호출합니다.
방법 2: Python module 경로에 submodule을 명시하고 python3 -m meal_management로 호출합니다.
방법 3: 기존 오케스트라의 package path adapter로 같은 소스를 참조합니다.

기존 호출 이름이 harnesses.meal인 경우, 상위의 __init__에서 meal_management 디렉터리를
package __path__ 앞에 두고 업무 로직 파일의 중복 사본을 제거할 수 있습니다.
상위 전용 live/bridge adapter는 상위 package 경로에 유지합니다.
submodule이 없으면 명시적으로 실패해야 하며 오래된 중복 코드로 fallback하지 않습니다.

업무 데이터·DB·설정은 submodule 경로에도 저장하지 않습니다.
submodule 추가와 현재 실행 중인 배포/코드 pin 변경은 별개 작업입니다.
