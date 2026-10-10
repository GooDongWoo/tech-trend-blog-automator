# 변경 기록

## 2026-10-11 — 자연어 workflow 최종 문서 검증

- `b62a859` 전체 회귀에서 발행 후 sync-only 재시도 결함을 확인해 `5f3a726`에서 수정했다. 수정 집중 게이트 `21 passed in 697.17s`, recovery 독립 리뷰 Spec PASS / Quality APPROVED (0 findings), 최종 오프라인 전체 회귀 `522 passed in 2832.82s`를 확인했다. 이전 `517 passed, 1 failed`는 수정 전 실행 기록으로 남긴다.
- 첫 UniSkill의 한 편 발행은 명시적으로 승인된 `reviewed_trial` 예외였으며, 전역 shadow와 production cutover 상태는 유지했다. 수동 조사·편집, 미확인 사람 점수·전체 비용, 확인되지 않은 사이트 배포 경계를 기록했다.
- `5f3a726` 구현 브랜치는 `main`에 병합하거나 원격에 push하지 않았다. 현재 문서 검증은 31개 로컬 링크가 모두 존재하고 57개 변경 추적 파일의 비밀 패턴 검사에 일치가 없음을 확인했다. recovery compatibility 경계는 [workflow guide](natural-language-workflow.md)에 있다.

## 2026-10-09 — 자연어 durable workflow

- repository `blog-workflow` skill과 실제 CLI 명령 안내를 추가했다. 기본 Vault→Telegram briefing, 저장 run 계속, 선택, 지시 기반 수정, 전체 검토 후 exact ID/hash 승인과 한 편 trial을 공유 서비스로 연결한다.
- 공개 UniSkill 최소 발췌 기반 합성 offline replay는 Telegram 실패/재시작, 승인 수정본, 임시 Git 한 번 발행과 push 없는 Vault sync 재시도를 확인한다. run receipt 평가에서 routine actions, editorial/recovery 개입과 system failures를 분리하며 없는 SDK usage·비용·사람 점수는 null로 둔다. 최종 전체 회귀는 별도 통합 게이트다.
- 첫 실제 시험의 승인 대기 기록을 확인된 `PUBLISHED`/`SYNCED`와 commit `6eeeac5f63872fca1a1a5ba21e23d0d18cc95df2`로 바로잡았다. 수동 조사·실험 발췌·ablation/문장 교정이 포함됐으며 배포와 자동 품질 향상은 확인되지 않았다.
- 공유 `OPENAI_MODEL` 기본값은 configurable `gpt-4o-mini`다(writer 이전 `gpt-4o`). Telegram acceptance 후 checkpoint 전 중단은 재전달될 수 있다. 전역 shadow/cutover 및 Vault 색인 금지는 유지한다.

## 2026-10-08 — 첫 실발행 시험 준비와 논문 브리프 인식 수정

- `Methodology`, 명시적인 `retain only` 데이터 조건, `does not guarantee` 평가 한계를 브리프의 작동 원리·채택 제약·판단을 뒤집을 조건으로 인식한다. 문서에 없는 설명은 여전히 차단한다.
- PDF에서 붙어 추출된 `ExperimentalSetup`과 실험 표의 단수 `Setting`을 실험 조건 단서로 인식한다. 수치·단위·대상·baseline·조건의 원문 일치와 실험 범위 검사는 유지한다.
- 누락된 설명이 계속 차단되는 경우를 포함해 회귀 검증을 추가했다. 브리프·주장 검증·통합 검토 테스트 127개가 통과했다. 전체 테스트 재실행 결과를 뜻하지 않는다.
- shadow가 Telegram 기본 발행 경로를 막는다는 점과, 특정 검토본 승인 뒤 별도로 허가된 한 편의 시험 발행에 운영자 CLI를 사용하는 경계를 README에 명시했다.
- 첫 UniSkill 시험은 Vault 관심 추출·실제 후보 수집·Telegram 선택을 거쳤다. 요약 페이지 근거만으로는 연구 단계가 막혔고, 원문 PDF·HTML·저장소를 확인한 뒤 편집자가 실험 구간과 근거 발췌를 보완했다. 첫 모델 초안도 검증에 실패해 원문 대조 편집을 거쳤다. 이 시험을 완전 자동 작성이나 재현 실험, 블라인드 품질 평가 성공으로 해석하지 않는다.

## 2026-10-08 — 근거 중심 기술 블로그 파이프라인

### 글을 만드는 방식

- 한 번의 프롬프트로 글을 완성하던 경로를 원문 스냅샷·근거 패킷·편집 브리프·초안·검증·검토 단계로 바꿨다. 누락된 원문이나 생성 실패는 `NEEDS_RESEARCH`/`NEEDS_REVISION`로 남긴다.
- 글의 질문, 작동 원리, 대안, 채택 조건과 판단을 뒤집을 조건을 먼저 정한다. 논문·도구·프로토콜·설계 비교에 서로 다른 브리프를 사용한다.
- 핵심 문장과 출처를 주장 연결표로 묶는다. 저자 보고 수치는 단위·대상·baseline·실험 조건을 검사하며 다른 실험의 조건을 빌리지 않는다. 논문에 없는 ablation이나 직접 해보지 않은 경험을 만들어 넣지 않는다.
- 사용자 목표·제약과 명시적 실행 로그를 선택한 주제와 함께 저장한다. 재시작·재시도에도 동일한 입력을 사용한다. 관심 기록은 경험 증거와 구분한다.
- 실행 프롬프트가 버전 관리된 [편집 정책](../src/editorial/policy.md)을 읽는다. 근거 문맥은 실제 생성·수정 예산 안에서 선택하고 필수 정보가 넘치면 호출을 차단한다.
- 밈은 적합성과 장면·alt·사용 조건·바이트를 확인한 GIF 0–1개만 선택한다. 억지 장면 설명과 중복 캡션을 막는다. 기존 8개 GIF는 사용 조건 미확인으로 제외했다.

### 검토와 발행

- Telegram에 전체 초안과 근거·검증·주장 연결표를 전달한다. 검토자와 특정 초안 ID·해시에 승인을 연결하며 재생성하면 이전 승인을 무효화한다.
- 검토 묶음과 원문·입력·미디어 해시를 로컬에 보존한다. 발행 직전에 다시 검사하고 해당 초안이 소유한 글·미디어만 커밋한다.
- 실제 push 목적지와 브랜치 결과를 확인한다. 불확실한 push는 원격 SHA 확인으로 복구하고, 확인된 발행 뒤의 Vault 동기화 실패는 별도로 재시도한다. Vault 색인은 자동 실행하지 않는다.
- Telegram은 기본 shadow이며 승인과 발행을 나눴다. 실제 품질 게이트와 별도의 운영 전환 결정이 있어야 발행 경로가 열린다.

### 평가와 남은 작업

오프라인 replay·단계별 비교 도구, 익명 검토 묶음과 사람 평가 게이트를 추가했다. 임시 Git·fake push·임시 Vault 검증은 발행·복구 동작을 확인하며 실제 블로그 발행을 뜻하지 않는다. 최신 검증과 병합 상태는 [refactor-status.md](refactor-status.md)에 기록한다.

과거 글 8편에는 당시 원문·프롬프트·모델 응답·예산이 없어 역사적 동일 조건 ablation을 재현하지 못했다. 합성 입력의 오류 제거는 검증 경로가 동작한다는 증거다. 실제 글의 품질 향상률로 해석하지 않는다.

다음 작업은 여러 글 유형의 실제 원문을 새로 고정하고 같은 모델·입력·예산으로 비교하는 것이다. 사람의 블라인드 평가, 모델 토큰·시간·비용, 자연스러움·밈 적합성을 기록한 뒤 출시 여부를 판단한다. 현재 이 평가와 운영 전환은 미완료다.

현재 흐름은 [architecture.md](architecture.md), 평가 조건은 [editorial-evaluation.md](editorial-evaluation.md)를 참고한다. 기존 JSON/HTML/PNG 다이어그램은 리팩토링 이전 기록으로 보존하고 표시했다.
