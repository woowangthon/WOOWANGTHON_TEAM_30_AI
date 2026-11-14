#!/usr/bin/env python3
"""fal 음성 복제 모델 두 개 비교 도구 (여운 AI 저장소, 혼자 도는 도구).

참조 음성 1개와 그 자막, 시험 문장 5개로 아래 두 모델을 합성해 지연 시간과 결과 파일을 저장한다.
  1) Qwen3-TTS 1.7B  음성 복제 (복제 1단계 + 합성 2단계)
  2) Chatterbox Multilingual (1단계)

이 파일의 fal 값(모델 ID, 입력 필드, 주소, 헤더)은 조사 문서 research.md 에 적힌 값만 쓴다.
각 상수 위의 주석에 문서 주소를 적었고, 문서에 "미확인"이라고 된 것은 미확인이라고 적었다.
실제 호출은 YEOUN_FAL_API_KEY 가 있어야 한다. 이 도구는 BE 코드를 불러오지 않고 표준 라이브러리만 쓴다.

지연 정의 (스트리밍이 아니다):
  첫 응답까지(first_response_s) = 큐 제출 직전부터 결과 JSON(음성 주소가 든 응답)을 받을 때까지
  전체(total_s)                 = 제출 직전부터 음성 파일을 다 내려받을 때까지
  Qwen 은 복제 단계 시간(clone_s)을 따로 적는다. 복제는 한 번만 하고 5문장이 같은 결과를 쓰므로
  Qwen 문장별 값은 합성 2단계만의 시간이고, 복제 시간은 results.json 의 qwen_clone_s 에 따로 있다.
"""

import argparse
import base64
import http.client
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave

# --------------------------------------------------------------------------
# 상수 (출처: research.md 에 적힌 문서 주소)
# --------------------------------------------------------------------------

KEY_ENV = "YEOUN_FAL_API_KEY"
KEY_NEEDED_MESSAGE = "키 필요: YEOUN_FAL_API_KEY"

# 큐 제출 주소: POST https://queue.fal.run/<endpoint_id>
# 출처 https://fal.ai/docs/model-apis/model-endpoints/queue
# 미확인: 하위 경로가 있는 endpoint_id 를 제출 주소에 그대로 쓰는 것은 문서 예시 기준이며 실제 호출로는 확인하지 못했다.
QUEUE_BASE = "https://queue.fal.run/"

# 삭제 API: DELETE https://api.fal.ai/v1/models/requests/{request_id}/payloads
# 성공 200, 응답에 cdn_delete_results 배열. 요청이 끝나지 않았으면 400. 입력 CDN 파일은 지우지 않는다(우리는 입력을 CDN 에 올리지 않는다).
# 출처 https://fal.ai/docs/platform-apis/v1/models/requests/payloads
DELETE_URL = "https://api.fal.ai/v1/models/requests/{request_id}/payloads"

# 인증 헤더: Authorization: Key <키>   출처 https://fal.ai/docs/model-apis/authentication
AUTH_SCHEME = "Key"

# 개인정보 설정
# X-Fal-Store-IO: 0 -> 요청 기록의 JSON 입출력 저장을 끈다(기본은 90일 저장). CDN 파일에는 적용되지 않는다.
#   미확인: 이 설정이 적용되는 엔드포인트의 범위.
#   출처 https://fal.ai/docs/documentation/model-apis/common-parameters , https://fal.ai/docs/documentation/serverless/data-retention
STORE_IO_HEADER = ("X-Fal-Store-IO", "0")

# X-Fal-Object-Lifecycle-Preference: {"expiration_duration_seconds": 3600} -> 생성 파일(CDN) 보관 기간(초).
#   3600 은 문서 예시값이다. 미확인: 가능한 최소값(더 짧게 할 수 있는지). 미설정이면 계정 설정을 따르며 만료 없음·공개 읽기.
#   출처 https://fal.ai/docs/documentation/model-apis/media-expiration
LIFECYCLE_HEADER_NAME = "X-Fal-Object-Lifecycle-Preference"
LIFECYCLE_EXPIRATION_SECONDS = 3600

# X-Fal-Request-Start-Timeout: <초> -> 처리 시작 전 대기 제한(만료 시 504). 처리 시작 후에는 적용되지 않는다.
#   X-Fal-Request-Timeout 헤더는 문서에서 확인하지 못해 쓰지 않는다.
#   출처 https://fal.ai/docs/model-apis/model-endpoints/queue
START_TIMEOUT_HEADER_NAME = "X-Fal-Request-Start-Timeout"
START_TIMEOUT_SECONDS = 120  # 이 값 자체는 우리가 정한 값이다(문서 값 아님).

# 모델 1: Qwen3-TTS 1.7B (2단계).
#   fal 페이지는 "Qwen3-TTS ... [1.7B]" 라고만 적고 "Base" 체크포인트 이름은 밝히지 않는다(미확인).
#   1단계 복제 입력: audio_url(필수, 참조 음성, 최대 300초), reference_text(선택), enable_safety_checker(기본 true, 건드리지 않음)
#     출력: speaker_embedding File(url, content_type, file_name, file_size) - safetensors, fal CDN 에 생긴다.
#     미확인: 참조 음성 형식 제한, 참조 자막 길이 제한.
#     출처 https://fal.ai/models/fal-ai/qwen-3-tts/clone-voice/1.7b/api
#   2단계 합성 입력: text(필수, 글자 수 제한 미확인), language("Korean"), speaker_voice_embedding_file_url(1단계 speaker_embedding.url),
#     reference_text(선택). voice 는 임베딩이 있으면 무시되므로 보내지 않는다. prompt/top_k/top_p/temperature/
#     repetition_penalty/max_new_tokens 등은 기본값 그대로 둔다(보내지 않는다).
#     출력: audio.url (예시 응답 content type audio/mpeg 이나 스키마 고정값이 아님 -> 내려받을 때 판별).
#     미확인: 2단계의 reference_text 가 결과에 미치는 영향(필드가 있어 1단계와 같은 자막을 함께 보낸다).
#     출처 https://fal.ai/models/fal-ai/qwen-3-tts/text-to-speech/1.7b/llms.txt
QWEN_CLONE_ENDPOINT = "fal-ai/qwen-3-tts/clone-voice/1.7b"
QWEN_TTS_ENDPOINT = "fal-ai/qwen-3-tts/text-to-speech/1.7b"
QWEN_LANGUAGE = "Korean"
QWEN_REF_MAX_SECONDS = 300  # 문서: audio_url 참조 음성 최대 300초

# 모델 2: Chatterbox Multilingual (1단계).
#   입력: text(필수, 최대 300자), voice(언어 코드 또는 참조 음성 주소. 참조 주소를 넣을 때는 custom_audio_language 를 함께 지정),
#     custom_audio_language("korean"). exaggeration/temperature/cfg_scale/seed/enable_safety_checker 는 기본값 그대로(보내지 않음).
#     참조 자막 필드는 없다(ref_text 는 이 모델에 보내지 않는다). 잘못된 참조 음성은 422(과금 없음).
#     미확인: 참조 음성 길이·형식 제한, 출력 content type(예시 파일명은 .wav).
#     출처 https://fal.ai/models/fal-ai/chatterbox/text-to-speech/multilingual/api
CHATTERBOX_ENDPOINT = "fal-ai/chatterbox/text-to-speech/multilingual"
CHATTERBOX_LANGUAGE = "korean"
CHATTERBOX_MAX_CHARS = 300

# 파일 입력: 두 모델의 API 페이지 Files 절에 "You can pass a Base64 data URI as a file input." 라고 적혀 있다.
#   -> 참조 음성은 fal CDN 에 올리지 않고 data:<mime>;base64,... 로 요청 본문에 직접 싣는다.
#   미확인: 크기 제한(큰 파일은 성능에 영향이 있다고만 적혀 있음).
#   출처 https://fal.ai/models/fal-ai/qwen-3-tts/clone-voice/1.7b/api , https://fal.ai/models/fal-ai/chatterbox/text-to-speech/multilingual/api

# 큐 상태 값: IN_QUEUE, IN_PROGRESS, COMPLETED. 상태·결과 주소는 직접 조립하지 않고 제출 응답의 status_url·response_url 을 그대로 쓴다.
# 실패 시 응답 필드 error, error_type.   출처 https://fal.ai/docs/model-apis/model-endpoints/queue
STATUS_COMPLETED = "COMPLETED"

# 취소: PUT cancel_url. 응답 200 {"status":"OK"}, 202 {"status":"CANCELLATION_REQUESTED"},
#   400 {"status":"ALREADY_COMPLETED"}, 404 {"status":"NOT_FOUND"}.
#   출처 https://fal.ai/docs/model-apis/model-endpoints/queue (3차 지시에서 전달받은 값이며 research.md 에는 이 응답 코드 줄이 없다).
#   200·202 는 취소 요청 성공, 400(ALREADY_COMPLETED)은 "이미 끝남"으로 보고 경고 없이 삭제를 시도한다.
CANCEL_OK_CODES = (200, 202)
CANCEL_ALREADY_COMPLETED_CODE = 400

POLL_INTERVAL_S = 0.5      # 우리가 정한 값
POLL_TIMEOUT_S = 600       # 우리가 정한 값
HTTP_TIMEOUT_S = 120       # 우리가 정한 값

SENTENCE_COUNT = 5
MODEL_QWEN = "qwen3-tts-1.7b"
MODEL_CHATTERBOX = "chatterbox-multilingual"

# 시험 문장 예시 (사람 이름·실제 대화 내용이 아닌 일반 문장)
EXAMPLE_SENTENCES = [
    "오늘은 날씨가 맑고 바람이 시원합니다.",
    "저녁 식사는 여섯 시에 시작합니다.",
    "창문을 열어 두었으니 환기가 잘 될 거예요.",
    "내일 아침에는 조금 일찍 출발해야 합니다.",
    "책상 위에 있는 상자를 서랍 안에 넣어 주세요.",
]

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_KEY_NEEDED = 2
EXIT_RUN_FAILED = 3
EXIT_INTERRUPTED = 130

AUDIO_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
CONTENT_TYPE_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
}


class StepError(Exception):
    """한 단계가 실패했다. code 는 짧은 오류 코드이며 문장·자막·키를 담지 않는다."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------
# 인자·입력 처리 (네트워크 없음)
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="tts_compare.py",
        description="fal 음성 복제 모델 두 개(Qwen3-TTS 1.7B, Chatterbox Multilingual)를 같은 참조 음성·문장으로 합성해 "
                    "지연 시간과 결과 파일을 저장한다.",
        epilog=(
            "지연 정의: 스트리밍이 아니므로 첫 응답까지 = 제출부터 결과 JSON(음성 주소)을 받을 때까지, "
            "전체 = 음성 파일을 다 내려받을 때까지. Qwen 은 복제 단계 시간을 따로 적는다.\n"
            "키: 환경변수 YEOUN_FAL_API_KEY (없으면 호출하지 않고 '키 필요'로 끝난다). --dry-run 은 키 없이 가능하다.\n"
            "주의: 실제 사람 목소리·유명인 목소리는 쓰지 않는다. 팀원 본인 녹음만 쓴다.\n"
            "시험 문장 파일 예시(한 줄에 한 문장, 5개):\n  " + "\n  ".join(EXAMPLE_SENTENCES)
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ref-audio", required=True, help="참조 음성 파일 경로 (wav, mp3, m4a, ogg, flac). 팀원 본인 녹음만.")
    p.add_argument("--ref-text", required=True, help="참조 자막: 문자열 또는 자막 파일 경로")
    p.add_argument("--sentences", required=True, help="시험 문장 파일 경로 (한 줄에 한 문장, 5개)")
    p.add_argument("--out", default="out", help="출력 폴더 (기본: out). 음성 파일과 results.json 이 저장된다.")
    p.add_argument("--dry-run", action="store_true",
                   help="호출 없이 요청 본문의 필드 이름만 출력한다 (키 불필요).")
    return p


def read_text_arg(value):
    """자막 인자: 존재하는 파일 경로면 파일 내용, 아니면 문자열 그대로. 내용은 출력하지 않는다."""
    if os.path.isfile(value):
        with open(value, "r", encoding="utf-8-sig") as f:
            text = f.read().strip()
    else:
        text = value.strip()
    if not text:
        raise ValueError("참조 자막이 비어 있습니다.")
    return text


def load_sentences(path):
    """한 줄에 한 문장, 빈 줄은 무시. 정확히 5개여야 하고 Chatterbox 한도(300자)를 넘으면 안 된다."""
    if not os.path.isfile(path):
        raise ValueError("시험 문장 파일을 찾을 수 없습니다.")
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    if len(lines) != SENTENCE_COUNT:
        raise ValueError("시험 문장은 %d개여야 합니다 (현재 %d개)." % (SENTENCE_COUNT, len(lines)))
    for i, s in enumerate(lines, 1):
        if len(s) > CHATTERBOX_MAX_CHARS:
            raise ValueError("%d번 문장이 %d자를 넘습니다 (Chatterbox 한도)." % (i, CHATTERBOX_MAX_CHARS))
    return lines


def audio_to_data_uri(path):
    """참조 음성을 data URI 로 만든다. fal CDN 에 올리지 않는다."""
    if not os.path.isfile(path):
        raise ValueError("참조 음성 파일을 찾을 수 없습니다.")
    ext = os.path.splitext(path)[1].lower()
    mime = AUDIO_MIME.get(ext) or mimetypes.guess_type(path)[0]
    if not mime or not mime.startswith("audio/"):
        raise ValueError("지원하지 않는 참조 음성 형식입니다 (wav, mp3, m4a, ogg, flac).")
    if ext == ".wav":
        try:
            with wave.open(path, "rb") as w:
                seconds = w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            seconds = None  # 읽지 못하면 길이 검사만 건너뛴다. 서버가 판단한다.
        if seconds is not None and seconds > QWEN_REF_MAX_SECONDS:
            raise ValueError("참조 음성이 %d초를 넘습니다 (Qwen 복제 한도)." % QWEN_REF_MAX_SECONDS)
    with open(path, "rb") as f:
        raw = f.read()
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def build_privacy_headers():
    """개인정보 설정 헤더. 제출·상태·결과 요청에 붙는다 (삭제·취소 요청에는 붙이지 않는다)."""
    return {
        STORE_IO_HEADER[0]: STORE_IO_HEADER[1],
        LIFECYCLE_HEADER_NAME: json.dumps({"expiration_duration_seconds": LIFECYCLE_EXPIRATION_SECONDS}),
    }


def build_auth_headers(key):
    """삭제·취소 요청용: Authorization 만."""
    return {"Authorization": "%s %s" % (AUTH_SCHEME, key)}


def build_headers(key):
    """큐 제출 요청 헤더: 개인정보 설정 + 시작 대기 제한 + 본문 형식 (+ 키가 있으면 Authorization)."""
    h = {"Content-Type": "application/json", START_TIMEOUT_HEADER_NAME: str(START_TIMEOUT_SECONDS)}
    h.update(build_privacy_headers())
    if key:
        h.update(build_auth_headers(key))
    return h


def build_query_headers(key):
    """상태·결과 조회(GET) 요청 헤더: 개인정보 설정 + Authorization."""
    h = build_privacy_headers()
    h.update(build_auth_headers(key))
    return h


def build_qwen_clone_body(data_uri, ref_text):
    return {"audio_url": data_uri, "reference_text": ref_text}


def build_qwen_tts_body(text, embedding_url, ref_text):
    return {
        "text": text,
        "language": QWEN_LANGUAGE,
        "speaker_voice_embedding_file_url": embedding_url,
        "reference_text": ref_text,
    }


def build_chatterbox_body(text, data_uri):
    return {"text": text, "voice": data_uri, "custom_audio_language": CHATTERBOX_LANGUAGE}


def describe_plan():
    """--dry-run 용: 단계별 주소와 요청 본문의 필드 이름만. 값(음성·자막·문장)은 담지 않는다."""
    headers = sorted(build_headers("x").keys())
    steps = [
        (MODEL_QWEN + " 1단계 복제", QWEN_CLONE_ENDPOINT,
         sorted(build_qwen_clone_body("d", "t").keys()), "1회"),
        (MODEL_QWEN + " 2단계 합성", QWEN_TTS_ENDPOINT,
         sorted(build_qwen_tts_body("t", "u", "t").keys()), "문장 %d개" % SENTENCE_COUNT),
        (MODEL_CHATTERBOX, CHATTERBOX_ENDPOINT,
         sorted(build_chatterbox_body("t", "d").keys()), "문장 %d개" % SENTENCE_COUNT),
    ]
    lines = ["[dry-run] 호출하지 않습니다. 요청 본문은 필드 이름만 표시합니다.",
             "큐 제출 주소: %s<endpoint_id>" % QUEUE_BASE,
             "제출 요청 헤더 이름: " + ", ".join(headers),
             "상태·결과 조회 헤더 이름: " + ", ".join(sorted(build_query_headers("x").keys())),
             "취소(PUT cancel_url)·삭제 요청 헤더 이름: Authorization",
             "참조 음성: data URI 로 요청 본문에 직접 싣습니다 (fal CDN 에 올리지 않음).",
             "끝난 요청마다 삭제 API 호출: DELETE " + DELETE_URL, ""]
    for name, endpoint, fields, count in steps:
        lines.append("- %s (%s) 호출 %s" % (name, endpoint, count))
        lines.append("    본문 필드: " + ", ".join(fields))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# fal 호출 (네트워크)
# --------------------------------------------------------------------------

def exc_code(exc):
    """예상 밖 예외를 짧은 오류 코드로 바꾼다 (예외 메시지는 쓰지 않는다)."""
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException)):
        return "NETWORK"
    return "ERROR_" + safe_code(type(exc).__name__)


def http_call(method, url, headers, body=None, timeout=HTTP_TIMEOUT_S):
    """(상태코드, 바이트, 응답헤더)를 돌려준다. 네트워크·읽기·조립 중 어떤 예외든(KeyboardInterrupt 제외)
    StepError 로 바꾼다 (예: http.client.IncompleteRead -> NETWORK)."""
    try:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            try:
                payload = e.read()
            except Exception:
                payload = b""
            return e.code, payload, e.headers
    except StepError:
        raise
    except Exception as e:
        raise StepError(exc_code(e))


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # JSON 오류·디코드 오류 등
        return None


def safe_code(text, fallback="UNKNOWN"):
    """오류 유형 문자열을 짧은 코드로 정리한다 (영문·숫자·밑줄만)."""
    if not isinstance(text, str):
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", text)[:40]
    return cleaned or fallback


def run_queue(endpoint, body, headers, key, state=None):
    """큐 방식 호출. (결과 JSON, request_id, 첫 응답까지 초)를 돌려준다. 실패 시 StepError(code) 이며
    request_id 를 알고 있으면 e.request_id 에 담는다. 완료 전에 시간 초과·오류로 끝나면 cancel_url 로 취소를 시도한다."""
    t0 = time.perf_counter()
    status, raw, _ = http_call("POST", QUEUE_BASE + endpoint, headers, body)
    sub = parse_json(raw)
    # 미확인: 제출 응답 코드는 문서에서 확인하지 못했다 -> 200 만 받지 않고 2xx 를 성공으로 본다.
    if not (200 <= status < 300) or not isinstance(sub, dict):
        raise StepError("HTTP_%d" % status)
    request_id = sub.get("request_id")
    status_url, response_url = sub.get("status_url"), sub.get("response_url")
    cancel_url = sub.get("cancel_url")
    if not (request_id and status_url and response_url):
        raise StepError("BAD_SUBMIT_RESPONSE")
    if state is not None:
        state["ids"].append(request_id)  # 어떤 일이 생겨도 끝에서 삭제를 시도할 수 있게 바로 기록
    query_headers = build_query_headers(key)
    completed = False
    try:
        deadline = t0 + POLL_TIMEOUT_S
        while True:
            s_code, s_raw, _ = http_call("GET", status_url, query_headers)
            s_json = parse_json(s_raw)
            if s_code not in (200, 202) or not isinstance(s_json, dict):
                raise StepError("HTTP_%d" % s_code)
            if s_json.get("error"):  # 실패 응답 필드 error/error_type 이 값으로 있으면 바로 끝낸다
                raise StepError("FAL_" + safe_code(s_json.get("error_type")))
            if s_json.get("status") == STATUS_COMPLETED:
                completed = True
                break
            if time.perf_counter() > deadline:
                raise StepError("TIMEOUT")
            time.sleep(POLL_INTERVAL_S)
        r_code, r_raw, _ = http_call("GET", response_url, query_headers)
        result = parse_json(r_raw)
        first = time.perf_counter() - t0
        if r_code != 200 or not isinstance(result, dict):
            raise StepError("HTTP_%d" % r_code)
        if result.get("error"):  # null·빈 값이면 실패로 보지 않는다
            raise StepError("FAL_" + safe_code(result.get("error_type")))
    except StepError as e:
        e.request_id = request_id
        if not completed:
            cancel_request(cancel_url, key)
        raise
    except KeyboardInterrupt:
        if not completed:
            cancel_request(cancel_url, key)
        raise
    except Exception as e:  # 예상 밖 예외도 StepError 로 바꿔 정리·저장이 이어지게 한다
        err = StepError(exc_code(e))
        err.request_id = request_id
        if not completed:
            cancel_request(cancel_url, key)
        raise err
    return result, request_id, first


def cancel_request(cancel_url, key):
    """끝나지 않은 요청 취소: PUT cancel_url (Authorization 만). 실패는 경고만 하고 계속한다."""
    if not cancel_url:
        print("경고: 취소 주소가 없어 취소하지 못함.", file=sys.stderr)
        return False
    try:
        code, _, _ = http_call("PUT", cancel_url, build_auth_headers(key))
    except StepError:
        print("경고: 취소 호출 실패(NETWORK).", file=sys.stderr)
        return False
    if code in CANCEL_OK_CODES:
        return True
    if code == CANCEL_ALREADY_COMPLETED_CODE:
        return True  # 이미 끝남(ALREADY_COMPLETED): 경고 없이 이어서 삭제를 시도한다
    print("경고: 취소 호출 실패(HTTP_%d)." % code, file=sys.stderr)
    return False


def download(url):
    """음성 파일을 내려받는다. CDN 주소에는 키를 보내지 않는다. (바이트, content_type)"""
    code, raw, hdrs = http_call("GET", url, {})
    if code != 200:
        raise StepError("DOWNLOAD_HTTP_%d" % code)
    ctype = (hdrs.get("Content-Type") or "").split(";")[0].strip().lower() if hdrs else ""
    return raw, ctype


def delete_payloads(request_id, key):
    """요청 결과(CDN 파일 등) 삭제 (Authorization 만). 끝나지 않은 요청은 문서상 400 이다. 실패는 경고만 하고 계속한다."""
    url = DELETE_URL.format(request_id=request_id)
    warn = "경고: 지우지 못함(%s): 결과 파일은 보관 기간 설정(" + str(LIFECYCLE_EXPIRATION_SECONDS) + "초)에만 의존"
    try:
        code, _, _ = http_call("DELETE", url, build_auth_headers(key))
    except StepError:
        print(warn % "NETWORK", file=sys.stderr)
        return False
    if code != 200:
        print(warn % ("HTTP_%d" % code), file=sys.stderr)
        return False
    return True


def pick_extension(ctype, file_info):
    if ctype in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ctype]
    if isinstance(file_info, dict):
        declared = (file_info.get("content_type") or "").lower()
        if declared in CONTENT_TYPE_EXT:
            return CONTENT_TYPE_EXT[declared]
        suffix = os.path.splitext(file_info.get("file_name") or "")[1].lower()
        if suffix in AUDIO_MIME:
            return suffix
    return ".bin"


def synth_one(model, endpoint, body, headers, key, out_dir, number, report, state):
    """한 문장 합성. 결과 행을 report["results"] 에 바로 기록한다. 오류는 error 코드로만 기록한다."""
    row = {"model": model, "sentence_no": number, "first_response_s": None, "total_s": None,
           "file": None, "error": None}
    request_id = None
    t0 = time.perf_counter()
    try:
        result, request_id, first = run_queue(endpoint, body, headers, key, state)
        row["first_response_s"] = round(first, 3)
        audio = result.get("audio")
        if not isinstance(audio, dict) or not audio.get("url"):
            raise StepError("NO_AUDIO_URL")
        raw, ctype = download(audio["url"])
        row["total_s"] = round(time.perf_counter() - t0, 3)
        name = "%s_%02d%s" % (model, number, pick_extension(ctype, audio))
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(raw)
        row["file"] = name
    except StepError as e:
        row["error"] = e.code
        request_id = request_id or getattr(e, "request_id", None)
    except KeyboardInterrupt:
        row["error"] = "INTERRUPTED"
        raise
    except Exception as e:
        row["error"] = exc_code(e)
    finally:
        report["results"].append(row)
    if request_id:
        delete_payloads(request_id, key)
        discard_id(state, request_id)
    print_row(row)


def discard_id(state, request_id):
    if request_id in state["ids"]:
        state["ids"].remove(request_id)


def cleanup(state, key):
    """아직 지우지 못한 request_id 전부에 삭제를 시도한다 (실패는 경고만). 예외가 나도 멈추지 않는다."""
    for request_id in list(state["ids"]):
        try:
            delete_payloads(request_id, key)
        except Exception:
            print("경고: 지우지 못함(ERROR): 결과 파일은 보관 기간 설정(%d초)에만 의존" % LIFECYCLE_EXPIRATION_SECONDS,
                  file=sys.stderr)
        discard_id(state, request_id)


def new_report():
    return {
        "schema": 1,
        "latency_definition": {
            "first_response_s": "큐 제출 직전부터 결과 JSON(음성 주소)을 받을 때까지 (스트리밍 아님)",
            "total_s": "큐 제출 직전부터 음성 파일을 다 내려받을 때까지",
            "qwen_clone_s": "Qwen 복제 1단계의 제출부터 결과 JSON 까지(1회). Qwen 문장별 값은 합성 2단계만의 시간",
        },
        "qwen_clone_s": None,
        "qwen_clone_error": None,
        "aborted": None,  # 중간에 멈췄으면 INTERRUPTED 또는 오류 코드
        "results": [],
    }


def run_all(ref_data_uri, ref_text, sentences, key, out_dir, report, state):
    """모든 호출을 실행한다. 결과는 report 에 쌓이므로 중간에 예외가 나도 그때까지의 것이 남는다."""
    headers = build_headers(key)

    # Qwen 1단계: 복제 (1회). 임베딩 파일은 2단계에서 쓰므로 삭제는 마지막(cleanup)에 한다.
    embedding_url = None
    try:
        result, _, first = run_queue(
            QWEN_CLONE_ENDPOINT, build_qwen_clone_body(ref_data_uri, ref_text), headers, key, state)
        report["qwen_clone_s"] = round(first, 3)
        emb = result.get("speaker_embedding")
        if not isinstance(emb, dict) or not emb.get("url"):
            raise StepError("NO_EMBEDDING_URL")
        embedding_url = emb["url"]
    except StepError as e:
        report["qwen_clone_error"] = e.code
    except Exception as e:
        report["qwen_clone_error"] = exc_code(e)
    print("%s 복제 단계: %s" % (MODEL_QWEN, ("%.3f초" % report["qwen_clone_s"]) if report["qwen_clone_s"] is not None
                              else "실패(%s)" % report["qwen_clone_error"]))

    for i, text in enumerate(sentences, 1):
        if embedding_url:
            synth_one(MODEL_QWEN, QWEN_TTS_ENDPOINT,
                      build_qwen_tts_body(text, embedding_url, ref_text),
                      headers, key, out_dir, i, report, state)
        else:
            row = {"model": MODEL_QWEN, "sentence_no": i, "first_response_s": None, "total_s": None,
                   "file": None, "error": "CLONE_FAILED"}
            report["results"].append(row)
            print_row(row)

    cleanup(state, key)  # Qwen 복제 결과(임베딩 파일) 삭제

    for i, text in enumerate(sentences, 1):
        synth_one(MODEL_CHATTERBOX, CHATTERBOX_ENDPOINT,
                  build_chatterbox_body(text, ref_data_uri), headers, key, out_dir, i, report, state)


def print_row(row):
    if row["error"]:
        print("%s #%d 오류 %s" % (row["model"], row["sentence_no"], row["error"]))
    else:
        print("%s #%d 첫 응답까지 %.3f초, 전체 %.3f초, 파일 %s" % (
            row["model"], row["sentence_no"], row["first_response_s"], row["total_s"], row["file"]))


def main(argv=None):
    args = build_parser().parse_args(argv)
    key = os.environ.get(KEY_ENV)

    # 키가 없으면 어떤 호출도 하지 않는다 (mock 결과도 만들지 않는다). --dry-run 은 호출이 없어 예외.
    if not key and not args.dry_run:
        print(KEY_NEEDED_MESSAGE, file=sys.stderr)
        return EXIT_KEY_NEEDED

    try:
        ref_text = read_text_arg(args.ref_text)
        sentences = load_sentences(args.sentences)
        data_uri = audio_to_data_uri(args.ref_audio)
    except (ValueError, OSError) as e:
        print("입력 오류: %s" % (e if isinstance(e, ValueError) else "파일을 읽을 수 없습니다."), file=sys.stderr)
        return EXIT_INPUT_ERROR

    if args.dry_run:
        print(describe_plan())
        return EXIT_OK

    os.makedirs(args.out, exist_ok=True)
    report = new_report()
    state = {"ids": []}  # 받은 request_id 중 아직 삭제하지 못한 것
    code = EXIT_OK
    try:
        run_all(data_uri, ref_text, sentences, key, args.out, report, state)
    except KeyboardInterrupt:
        report["aborted"] = "INTERRUPTED"
        code = EXIT_INTERRUPTED
        print("중단됨: 정리와 저장을 시도합니다.", file=sys.stderr)
    except Exception as e:
        report["aborted"] = exc_code(e)
        code = EXIT_RUN_FAILED
        print("실행 중 오류(%s): 정리와 저장을 시도합니다." % report["aborted"], file=sys.stderr)
    finally:
        try:
            cleanup(state, key)
        except BaseException:
            pass  # 정리 중 중단돼도 저장은 시도한다
        path = os.path.join(args.out, "results.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print("results.json 저장: %s" % path)
        except OSError:
            print("경고: results.json 을 저장하지 못했습니다.", file=sys.stderr)
    if code == EXIT_OK and (any(r["error"] for r in report["results"]) or report["qwen_clone_error"]):
        code = EXIT_RUN_FAILED
    return code


if __name__ == "__main__":
    sys.exit(main())
