# WOOWANGTHON_TEAM_30_AI — 여운 AI 설계·검증

2026 AI 우왕톤 30팀 **여운**의 AI 저장소입니다.
여운에서 AI가 무엇을 하는지, 무엇을 직접 만들었고 무엇을 외부 API에 맡겼는지, AI 출력이 맞는지 어떻게 확인하는지를 모았습니다.

> **실제로 실행되는 AI 코드는 [BE 저장소](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE)의 `server/app` 안에 있습니다.**
> 녹음 처리 파이프라인과 대화 API가 같은 코드를 바로 불러 쓰고, 서버 설정·데이터 형식·오디오 처리(ffmpeg)를 함께 쓰기 때문에 코드를 두 곳에 나눠 두지 않았습니다.
> 배포도 BE 서버 한 대 안에서 함께 돌아갑니다.

| 저장소 | 내용 |
|---|---|
| [FE](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_FE) | Next.js 화면 |
| [BE](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE) | FastAPI 서버, DB, 녹음 처리, **실행되는 AI 코드** |
| AI (이 저장소) | AI 설계 설명, 평가 질문 세트, 검증 결과 |

## AI 코드 위치 (BE 저장소)

| 위치 | 하는 일 | 직접 구현 / 외부 |
|---|---|---|
| [`server/app/ai/stt.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/ai/stt.py) | 음성 → 글(받아쓰기) | 외부 API 호출 (OpenAI·Gemini) |
| [`server/app/ai/diarization.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/ai/diarization.py) | 말한 사람 나누기 | 외부 API 호출 |
| [`server/app/ai/llm.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/ai/llm.py) | 질문 의도 파악, 녹음 클립 고르기, 주제 붙이기 | 외부 API 호출 (Claude·OpenAI·Gemini) + 직접 만든 프롬프트·규칙 대체 |
| [`server/app/ai/embedding.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/ai/embedding.py) | 의미 검색용 벡터 | 외부 API 호출 |
| [`server/app/ai/guard.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/ai/guard.py) | **AI 출력 검증**: 후보에 없는 클립, 지어낸 인용, 형식이 틀린 응답을 버림 | 직접 구현 |
| [`server/app/ai/registry.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/ai/registry.py) | 키·동의 여부에 따라 공급자 선택, 실패하면 규칙 기반(mock)으로 대체 | 직접 구현 |
| [`server/app/search/`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/tree/main/server/app/search) | 한국어 토크나이저, BM25 검색, 사투리 정규화 사전 | 직접 구현 |
| [`server/app/care/crisis.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/care/crisis.py) | 자살·자해 위험 신호 감지 → 상담 번호 안내 | 직접 구현 |
| [`server/app/services/chat.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/services/chat.py) | 대화 응답 흐름 전체 | 직접 구현 |
| [`server/app/eval.py`](https://github.com/woowangthon/WOOWANGTHON_TEAM_30_BE/blob/main/server/app/eval.py) | 골든 세트 평가 실행 | 직접 구현 |

## 입력 → 처리 → 출력

```text
[녹음 파일] → ffmpeg 정리(16kHz mono) → 받아쓰기(STT) → 말한 사람 나누기 → 문장 단위 클립 + 주제 태그
                                                                                  ↓
[가족의 질문] → ① 위기 신호 검사 → ② 의도·키워드 → ③ BM25(+임베딩)로 후보 20개 → ④ LLM이 최대 3개 고르기
                                                                                  ↓
                                                    ⑤ guard 검증 → 원본 녹음 클립 카드 + 짧은 안내 문장
```

원칙: **그 사람의 말은 언제나 DB에 저장된 원본 녹음(자막)뿐입니다.** LLM이 쓴 문장은 안내 문장으로만 나가고, 후보 밖 클립이나 지어낸 인용은 guard가 버립니다.

## 평가 자료 (이 저장소)

| 파일 | 내용 |
|---|---|
| [`fixtures/eval/queries.json`](fixtures/eval/queries.json) | 골든 세트: 질문 30개와 정답 클립, 맞는 녹음이 없어야 하는 질문 4개 |
| [`fixtures/stt/`](fixtures/stt) | 받아쓰기 결과 샘플 |

평가는 BE 저장소에서 실행합니다. 질문 세트를 바꾸면 BE의 `fixtures/eval/queries.json`도 함께 바꿉니다.

```sh
# BE 저장소에서
make eval                                   # 규칙 기반(mock)
cd server && .venv/bin/python -m app.eval --llm   # 설정된 LLM으로
```

## 검증 결과

| 항목 | 방법 | 결과 | 날짜 |
|---|---|---|---|
| 검색 정확도 (top-3) | 골든 세트 30문항, mock 고르기 | lite 토크나이저 96.7%, kiwi 100% | 2026-11-09 |
| '맞는 녹음 없음' 판정 | 골든 세트 no_match 4문항 | 4/4 | 2026-11-09 |
| 실제 LLM 고르기 정확도 | `--llm` | 측정 예정 | |
| 문단-원본 근거 연결률 (v4 기억책) | 생성 문단마다 원본 클립이 붙는지 | 측정 예정 | |
| 위기 신호 감지 | 위험 표현 문장 세트 | 측정 예정 | |
| 받아쓰기 오류율(CER) | 고령·사투리 녹음 | 측정 예정 | |

## 한계

- 고령자·사투리 음성은 받아쓰기 오류가 더 많을 수 있습니다. 그래서 화면에는 원본 음성을 항상 함께 둡니다.
- 위기 신호 감지는 규칙 기반이라 돌려 말한 표현을 놓칠 수 있습니다. 놓치는 것보다 한 번 더 안내하는 쪽으로 넓게 잡았습니다.
- 외부 API 키가 없거나 호출이 실패하면 규칙 기반(mock)으로 대체됩니다. 응답마다 어느 방식으로 골랐는지(`picker`)가 함께 남습니다.
