# 자연어 블로그 작업

저장소의 `.agents/skills/blog-workflow/SKILL.md`는 자연어 요청을 현재 `main.py` CLI로 연결한다. 기본 경로는 Vault 관심 → Telegram 후보 → 선택 → 원문 조사 → 검토본 → 특정 수정본 승인 → 명시적 발행이다. Vault 관심은 경험의 증거가 아니다. CLI 출력은 JSON이며 `run.id`를 보관해 후속 명령에 사용한다. 아래 `RUN`, `DRAFT`, `SHA`, `REVIEWER`는 출력에서 얻은 실제 값이다. `SHA`는 전체 SHA-256이고 `REVIEWER`는 저장된 `run.reviewer`다. 모든 명령에 같은 `--workflow-root`를 사용한다.

CLI와 기본 bot의 workflow 저장 경로는 저장소의 `temp/workflow`다. 사용자 지정 경로로 후보를 만들었다면 실행 중인 bot에도 같은 경로를 전달한다. 상대 경로는 실행 작업 디렉터리 기준이므로 서로 다른 디렉터리에서 실행할 때는 동일한 절대 경로를 사용한다.

```powershell
python main.py bot --workflow-root temp/workflow
# 사용자 지정 예: briefing과 bot에 동일한 경로 전달
python main.py briefing --workflow-root C:/local/blog-runs
python main.py bot --workflow-root C:/local/blog-runs
```

Telegram callback은 run ID와 순위만 전달하며 파일 경로를 포함하지 않는다. `bot --output-root`는 기존 로컬 검토 bundle의 review 저장 경로를 지정하며, 사용자 지정 workflow를 공유할 때는 `--workflow-root`를 명시한다.

## 후보와 작성

“Vault 보고 주제 텔레그램으로 보내줘”:

```powershell
python main.py briefing --mode shadow --days 14 --topic-count 5 --intent "Vault 관심으로 주제 추천" --workflow-root temp/workflow
python main.py status RUN --workflow-root temp/workflow
python main.py select RUN --rank 2 --reviewer REVIEWER --workflow-root temp/workflow
python main.py review RUN --reviewer REVIEWER --workflow-root temp/workflow
```

`briefing`은 `send-briefing` 별칭이며 설정된 Telegram 수신자와 검토자에게 전달한다. 그룹에서는 `TELEGRAM_REVIEWER_USER_ID`가 필요하다. `request --reviewer cli:dongwoo`는 로컬 확인용 run만 만들며 `resume RUN`으로 후보 수집을 진행한다. `--intent`는 요청 기록과 라우팅 정보이고 임의의 초안 지시로 주입되지 않는다. Telegram 선택도 같은 저장 서비스와 승인 경계를 사용한다.

`review` JSON에는 전체 본문과 검토 보고서 텍스트, 미디어 경로, 현재 ID/hash가 포함된다. 이 내용을 사용자에게 실제로 보여 준다. 전송 기록만으로 사람이 읽었다고 판단하지 않는다.

## 수정과 한 편 발행

“판단 부분을 이 조건으로 수정해줘”:

```powershell
python main.py revise RUN DRAFT SHA --reviewer REVIEWER --instruction "요청한 수정 내용" --section-id decision --workflow-root temp/workflow
python main.py review RUN --reviewer REVIEWER --workflow-root temp/workflow
```

`--section-id`는 현재 `draft.json`의 실제 섹션 ID를 사용한다. 섹션 밖 본문은 유지하며 지시와 기준 수정본을 저장한다. 범위 없는 수정은 `--section-id`를 생략한다. 새 수정본의 전체 검토와 명시적 승인을 받아야 한다.

“이 검토본을 승인하고 이 한 편만 발행해줘”를 사용자가 **현재 전체 검토본을 본 뒤** 명시한 경우:

```powershell
python main.py approve RUN DRAFT SHA --reviewer REVIEWER --workflow-root temp/workflow
python main.py trial RUN DRAFT SHA --reviewer REVIEWER --workflow-root temp/workflow
python main.py publish RUN DRAFT SHA --reviewer REVIEWER --workflow-root temp/workflow
```

승인 명령은 blog/Vault를 쓰지 않는다. `trial`은 이 run의 승인된 한 수정본만 `reviewed_trial`로 허용한다. 전역 shadow나 cutover를 변경하지 않는다. `publish`는 설정된 blog/Vault를 변경하므로 해당 검토본의 발행 승인 범위 안에서만 실행한다. `production`은 별도의 실제 품질 보고서와 운영 전환 게이트를 유지한다. 과거 검토본은 `review-legacy DRAFT SHA --review-root <saved-review-root> --reviewer REVIEWER`로 전체 검토를 전달하고 반환된 run에 승인·trial을 적용한다. 직접 파일 수정이나 별도 발행 스크립트로 승인을 우회하지 않는다.

## 실패 후 계속

```powershell
python main.py status RUN --workflow-root temp/workflow
python main.py resume RUN --workflow-root temp/workflow
```

완료한 단계와 저장된 조사·수정 지시를 재사용한다. provider 실패는 `NEEDS_RESEARCH`/`NEEDS_REVISION`으로 남는다. Telegram 실패는 고정된 payload와 수신자로 재시도하며 설정 수신자 변경은 조사나 전송 전에 막힌다. Telegram이 전송을 받았지만 로컬 checkpoint 기록 전에 중단되면 재전달될 수 있으므로 exactly-once 전송은 보장하지 않는다.

이전에 명시적 발행 시도가 예약된 run은 같은 승인 ID/hash의 불확실한 push를 원격 SHA와 대조하거나 확인된 발행의 Vault sync만 재시도한다. 아직 발행을 시도하지 않은 승인 run의 `resume`은 발행을 시작하지 않는다. 명시적 수동 대조는 `publish ... --reconcile`도 지원한다. Vault 증분 색인은 실행하지 않는다.

## 검증과 측정 계약

`tests/test_workflow_replay.py`는 공개 최소 UniSkill 발췌와 합성 응답, 주입된 `ModelClient`, fake Telegram, 임시 Git/fake push, 임시 Vault로 중단·재시작·수정·전체 검토·해시 승인·한 번의 발행·sync만 재시도를 확인한다. 실제 모델 생성 품질이나 실제 Telegram/GitHub 발행의 재현이 아니다. 원문 자동 추적, scoped table context, provider 실패와 문장 span 수정은 기존 `test_primary_research.py`, `test_korean_grounding.py`, `test_llm_client.py`, `test_drafting.py`, `test_workflow_runs.py`의 집중 회귀로 따로 검증한다.

`scripts.evaluate_drafts.workflow_receipt(service, run_id, synthetic=...)`는 저장 run을 읽어 현재 artifact ID/hash, 검토 전달·승인·발행 영수증, 검증된 unsupported claim 수, 이벤트와 모델 capture 메타데이터를 요약한다. `synthetic`은 호출자가 사실대로 지정하며 자동 판별이나 사람 점수 생성은 하지 않는다. `operator_interventions`는 operator의 editorial/recovery 이벤트이며 select/approve/publish 같은 routine actions, system failures와 별도 집계한다. 개입 횟수는 활동 기록이며 자동 품질 성공률이 아니다.

기존 run의 영수증 JSON을 새 로컬 디렉터리에 저장하려면:

```powershell
python -m scripts.evaluate_drafts --workflow-run RUN --workflow-root temp/workflow --output temp/run-receipts-NEW
```

주입된 합성 run은 `--synthetic-replay`를 추가한다. 이 명령은 저장 상태만 읽으며 생성·전송·발행·resume을 실행하지 않는다. blog/Vault 출력이나 기존 보고서 덮어쓰기는 차단한다. 보고서는 `measurement_scope=available_captures_only`, capture/reference 수와 `captured_model_stages`를 기록한다. 부분 capture의 합을 전체 run 사용량으로 해석하지 않는다. 누락 파일은 `capture_errors`로 드러난다.

`measurements.stage_timings`는 저장 이벤트의 해당 단계 실행 시간이다. 재시도 포함 전체 pipeline wall time과 같지 않다. 실제 capture가 모두 SDK usage를 갖는 경우만 tokens를 합산한다. 누락·합성 transport의 tokens와 live model 시간, 미확인 비용은 `null`이다. `model_call_seconds`는 실제 capture의 호출 기간 합으로 대기·편집·전체 실행 시간을 나타내지 않는다. `human_scores`는 항상 `null`; 기존 익명 평가와 release gate를 통해 사람이 별도로 평가한다. 영수증은 paired ablation 또는 품질 상승 증거가 아니다.

## 첫 실제 시험의 확인 범위

2026-10-08 UniSkill 시험은 실제 Vault 관심 추출, 후보 수집과 Telegram 선택을 거쳤다. 요약만으로 조사 단계가 막혔고 편집자가 PDF·HTML·저장소를 직접 확인해 실험 발췌와 조건을 보완했다. 첫 모델 초안 실패 뒤 ablation 해석과 문장을 원문 대조 수정했다. 이는 자동 작성 품질로 집계하지 않는다.

최종 수정본 `Fv4PmoV6OSTxehQp`, 본문 SHA-256 `8b1de5e9916f4146ac72f50f3aa2da3c28572d53500ce131f339fcf0113fe8ff`의 승인 후 영수증 `temp/first-publication-20261008-232932/publication.json`은 `success=true`, `PUBLISHED`, commit `6eeeac5f63872fca1a1a5ba21e23d0d18cc95df2`, `SYNCED`, `sync_error=null`을 기록한다. 이는 main의 무시된 로컬 영수증을 확인한 기록이며 전체 논문·private prompt·Vault 발췌는 커밋하지 않는다. 사이트 배포는 확인되지 않았다. 전역 shadow와 정식 운영 전환은 유지된다.

확인된 **실패한 초안 호출 한 번**만 7.16초와 SDK input 5892/output 1569/total 7461 tokens를 기록했다. 전체 pipeline 비용·시간이나 사람 품질 점수가 아니다. 과거 글 8편은 당시 source/prompt/response/budget capture가 없어 paired ablation은 보류한다. 공유 `OPENAI_MODEL` 기본값은 현재 설정 가능한 `gpt-4o-mini`이며 writer의 이전 값은 `gpt-4o`였다. 모델 이용 가능성이나 품질 향상을 뜻하지 않는다.
