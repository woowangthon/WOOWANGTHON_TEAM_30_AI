# eval — 음성 합성 모델 비교 도구

여운에서 **'AI가 만든 말'을 그분 목소리로 들려주는 부품**(음성 복제)에 쓸 fal 모델 두 개를 같은 조건으로 비교하는 도구입니다.
혼자 도는 스크립트 하나(`tts_compare.py`)이고, BE 코드를 불러 쓰지 않으며 파이썬 표준 라이브러리만 씁니다.

> **음성 파일은 실제 사람 목소리·유명인 목소리를 쓰지 않습니다. 팀원 본인 녹음만 씁니다.** (스크립트 `--help` 에도 같은 문구가 있습니다.)

## 비교 대상

| 모델 | fal 모델 ID | 방식 |
|---|---|---|
| Qwen3-TTS 1.7B 음성 복제 | `fal-ai/qwen-3-tts/clone-voice/1.7b` → `fal-ai/qwen-3-tts/text-to-speech/1.7b` | 2단계: 참조 음성에서 목소리 정보를 만들고(복제), 그 정보로 문장을 합성 |
| Chatterbox Multilingual | `fal-ai/chatterbox/text-to-speech/multilingual` | 1단계: 참조 음성과 문장을 한 번에 보냄 (참조 자막은 받지 않음) |

### NOVA 음성 합성은 비교에서 뺐습니다

충북대 NOVA 게이트웨이의 음성 합성은 **음성 복제가 안 돼서** 그분 목소리 합성(가)에는 쓸 수 없고, 비교 대상에서 뺐습니다.
근거는 문서입니다. 문서(<https://docs.mindlogic.ai/docs/general/api-gateway/reference/audio-tts.md>)에는 정해진 목소리 목록만 있고 복제·사용자 목소리 등록에 대한 언급이 없습니다. (문서가 "복제 불가"라고 직접 말한 것은 아니고, 그 기능이 문서에 없습니다.)
NOVA 음성 합성은 (나) 여운 안내 목소리(정해진 목소리로 안내 문장 읽기)에 씁니다. 이 도구는 NOVA 를 호출하지 않고 NOVA 키도 쓰지 않습니다.

## 실행 방법

```sh
# 키 (없으면 호출하지 않고 "키 필요: YEOUN_FAL_API_KEY" 라고 알리고 종료 코드 2 로 끝납니다)
set YEOUN_FAL_API_KEY=...          # 윈도우 cmd
$env:YEOUN_FAL_API_KEY = "..."     # 파워셸

# 호출 없이 요청 본문의 필드 이름만 확인 (키 불필요)
python eval/tts_compare.py --ref-audio ref.wav --ref-text ref.txt --sentences sentences.txt --dry-run

# 실제 실행
python eval/tts_compare.py --ref-audio ref.wav --ref-text ref.txt --sentences sentences.txt --out out
```

| 인자 | 내용 |
|---|---|
| `--ref-audio` | 참조 음성 파일 (wav, mp3, m4a, ogg, flac). 팀원 본인 녹음만. wav 는 300초를 넘으면 실행 전에 막습니다(Qwen 복제 한도). |
| `--ref-text` | 참조 자막. 문자열 또는 자막 파일 경로 |
| `--sentences` | 시험 문장 파일. 한 줄에 한 문장, 정확히 5개, 문장마다 300자 이하(Chatterbox 한도) |
| `--out` | 출력 폴더 (기본 `out`). 없으면 만듭니다. |
| `--dry-run` | 호출하지 않고 단계·헤더 이름·본문 필드 이름만 출력 |

종료 코드: 0 정상, 1 입력 오류, 2 키 없음, 3 실행 중 오류가 하나라도 있음.

### 시험 문장 예시 (`sentences.txt`)

사람 이름·실제 대화 내용이 아닌 일반 문장으로 만듭니다.

```text
오늘은 날씨가 맑고 바람이 시원합니다.
저녁 식사는 여섯 시에 시작합니다.
창문을 열어 두었으니 환기가 잘 될 거예요.
내일 아침에는 조금 일찍 출발해야 합니다.
책상 위에 있는 상자를 서랍 안에 넣어 주세요.
```

## 입력 → 처리 → 출력

```text
[참조 음성 1개 + 참조 자막 + 시험 문장 5개]
        ↓ 참조 음성을 data URI 로 만들어 요청 본문에 직접 실음 (fal CDN 에 올리지 않음)
        ↓ 개인정보 설정 헤더를 붙여 큐 방식으로 제출 → request_id 확보
   Qwen:       ① 복제 1회 → ② 문장 5개 합성 (복제 결과를 5문장이 같이 씀)
   Chatterbox: 문장 5개 합성
        ↓ 응답이 준 status_url 로 완료를 기다리고 response_url 로 결과 JSON 을 받음 → 음성 주소로 파일 내려받기
        ↓ 요청이 끝날 때마다 삭제 API 호출 (실패는 경고만 출력하고 계속)
[출력 폴더: 모델별 음성 파일 + results.json]
```

### 지연 정의

스트리밍이 아니므로 아래처럼 잽니다. (`time.perf_counter` 로 제출 직전부터 잰 초 단위 값)

| 항목 | 의미 |
|---|---|
| 첫 응답까지 (`first_response_s`) | 제출부터 결과 JSON(음성 주소)을 받을 때까지 |
| 전체 (`total_s`) | 제출부터 음성 파일을 다 내려받을 때까지 |
| Qwen 복제 단계 (`qwen_clone_s`) | 복제 1단계의 제출부터 결과 JSON 까지, 1회. Qwen 의 문장별 값은 **합성 2단계만의 시간**이라 복제 시간은 따로 이 값을 더해서 봅니다. |

큐 대기 시간이 값에 포함됩니다(큐 방식 호출이라서).

### 출력 파일

- 음성 파일: `<모델>_<문장번호 2자리>.<확장자>` (예: `chatterbox-multilingual_01.wav`). 확장자는 내려받을 때 응답의 content type 으로 정합니다. Qwen 은 mp3 로 올 수 있다고만 문서에 예시가 있고 고정값이 아닙니다.
- `results.json`: 모델, 문장 번호, 지연 값, 파일 이름, 오류 코드. **문장 내용·자막·키는 넣지 않고 번호만 넣습니다.** 오류는 `HTTP_422`, `TIMEOUT`, `NETWORK` 같은 짧은 코드뿐입니다. 화면 출력에도 문장·자막·키를 찍지 않습니다.

## 결과 표 양식

실행한 뒤 사람이 채웁니다. **이 저장소에는 아직 채운 숫자가 없습니다.** (키가 없어 실제 실행을 하지 못했습니다.)

가족 청취 점수는 가족이 들어 본 뒤 정하는 값이고 척도는 팀이 정합니다.

| 모델 | 문장 번호 | 첫 응답까지 (초) | 전체 (초) | 가족 청취 점수 | 오류 코드 |
|---|---|---|---|---|---|
| Qwen3-TTS 1.7B | 1 | | | | |
| Qwen3-TTS 1.7B | 2 | | | | |
| Qwen3-TTS 1.7B | 3 | | | | |
| Qwen3-TTS 1.7B | 4 | | | | |
| Qwen3-TTS 1.7B | 5 | | | | |
| Chatterbox Multilingual | 1 | | | | |
| Chatterbox Multilingual | 2 | | | | |
| Chatterbox Multilingual | 3 | | | | |
| Chatterbox Multilingual | 4 | | | | |
| Chatterbox Multilingual | 5 | | | | |

| 항목 | 값 |
|---|---|
| Qwen 복제 단계 시간 (`qwen_clone_s`) | |

## fal 에 무엇이 나가고, 어떤 설정으로 줄였나

fal 로 나가는 것: **참조 음성(본인 녹음), 참조 자막, 시험 문장, 인증 키(헤더).** 그 결과로 합성 음성과 (Qwen 은) 목소리 정보 파일이 fal 쪽에 생깁니다.

| 설정 | 효과 | 출처 |
|---|---|---|
| `X-Fal-Store-IO: 0` | 요청 기록에 JSON 입출력을 저장하지 않음(기본은 90일 저장). CDN 파일에는 적용되지 않음 | <https://fal.ai/docs/documentation/model-apis/common-parameters> |
| `X-Fal-Object-Lifecycle-Preference: {"expiration_duration_seconds": 3600}` | 생성 파일(CDN) 보관을 1시간으로. 미설정이면 계정 설정을 따르며 만료 없음·공개 읽기 | <https://fal.ai/docs/documentation/model-apis/media-expiration> |
| 참조 음성을 data URI 로 요청에 직접 싣기 | 참조 음성을 fal CDN 에 올리지 않음(그래서 입력 파일은 삭제할 대상도 없음) | 두 모델의 API 페이지 Files 절 |
| 큐 방식 호출 | `request_id` 를 얻어 삭제 API 를 부르기 위함 | <https://fal.ai/docs/model-apis/model-endpoints/queue> |
| 끝난 뒤 삭제 API (`DELETE https://api.fal.ai/v1/models/requests/{request_id}/payloads`) | 생성 파일(합성 음성, Qwen 목소리 정보 파일)을 지움. Qwen 복제 결과는 2단계가 쓰므로 문장 5개를 모두 합성한 뒤 지움 | <https://fal.ai/docs/platform-apis/v1/models/requests/payloads> |
| `enable_safety_checker` 등 | 건드리지 않고 기본값 그대로 | 두 모델 API 페이지 |

삭제가 실패하면 경고만 출력하고 계속합니다(보관 기간이 지나면 만료됩니다). 헤더는 모든 요청에 붙고, 인증 헤더는 `Authorization: Key <키>` 입니다(<https://fal.ai/docs/model-apis/authentication>). 키는 환경변수 `YEOUN_FAL_API_KEY` 에서만 읽고 어디에도 저장·출력하지 않습니다.

## 미확인 항목

문서에서 확인하지 못했거나 실제 호출로 확인하지 못한 것입니다. 실제 실행 때 확인합니다.

- **실제 fal 호출 전체**: 이 컴퓨터에 키가 없어 한 번도 호출하지 못했습니다. 아래 항목과 별개로 응답 형식·지연은 실행해 봐야 압니다.
- 하위 경로가 있는 endpoint_id(`fal-ai/qwen-3-tts/clone-voice/1.7b` 등)를 큐 제출 주소에 그대로 붙이는 방식은 문서 예시 기준이며 실제 호출로는 미확인.
- `X-Fal-Object-Lifecycle-Preference` 로 줄 수 있는 최소 보관 시간 미확인(문서 예시값 3600초 사용).
- `X-Fal-Store-IO: 0` 이 적용되는 엔드포인트 범위 미확인(이 두 모델에 적용되는지 포함).
- 참조 음성을 data URI 로 보낼 때의 크기 제한 미확인(큰 파일은 성능에 영향이 있다고만 적혀 있음).
- 참조 음성 형식 제한(Qwen 복제, Chatterbox), Chatterbox 참조 음성 길이 제한 미확인. Qwen 복제는 최대 300초만 확인.
- Qwen 참조 자막 길이 제한, Qwen 합성 글자 수 제한 미확인. Qwen 2단계의 `reference_text` 가 결과에 주는 영향 미확인.
- Qwen3-TTS 의 체크포인트 이름(Base 여부) fal 페이지에 없음 — 미확인.
- 출력 파일 형식: Qwen 은 예시가 `audio/mpeg` 이나 고정값 아님, Chatterbox 는 미확인(예시 파일명은 .wav).
- 동기 주소(`https://fal.run/...`) 응답에서 `request_id` 를 얻는 방법 미확인 — 그래서 큐 방식만 씁니다.
- `X-Fal-Request-Timeout` 헤더는 문서에서 확인하지 못해 쓰지 않았습니다. 처리 시작 전 대기 제한(`X-Fal-Request-Start-Timeout`, 120초)은 이 도구가 정한 값입니다.
- NOVA: 한국어 지원 여부는 문서 페이지에 언어 목록이 없어 미확인(비교 대상이 아니므로 이 도구와는 무관).

## 파일

| 파일 | 내용 |
|---|---|
| [`tts_compare.py`](tts_compare.py) | 비교 도구. 인자 처리·요청 본문 조립·키 없음 처리는 네트워크 없이 확인할 수 있게 함수로 분리 |
| [`.gitignore`](.gitignore) | 출력 폴더와 음성 파일(`*.wav`, `*.mp3`, `*.m4a` 등)은 커밋하지 않음 |
