#!/usr/bin/env python3
"""
국내 여행 추천 프로그램 (코딧세이 2주차)

파이프라인:
  [1/3] LLM(OpenAI)  : 여행 날짜 → 추천 도시 JSON 생성
  [2/3] Naver Local  : 추천 도시 → 맛집 5곳 검색
  [3/3] LLM(OpenAI)  : 전체 데이터 → 최종 여행 리포트(Markdown) 생성

실행:
  python travel_planner.py --date "2026-03-15"
  python travel_planner.py --date "2026-03-15" --no-cache   # 캐시 무시(보너스)
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------
# 상수
# ---------------------------------------------------------------
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"
NAVER_LOCAL_URL = "https://openapi.naver.com/v1/search/local.json"
RESULTS_DIR = Path("results")
REQUEST_TIMEOUT = 30  # 초


# ---------------------------------------------------------------
# 공통: 오류 수집
# ---------------------------------------------------------------
class ErrorCollector:
    """실행 중 발생한 오류를 모아 결과 JSON/리포트에 남긴다."""

    def __init__(self):
        self.errors = []

    def add(self, step: str, err_type: str, message: str):
        self.errors.append({"step": step, "type": err_type, "message": message})
        print(f"    - 오류 기록: [{step}] {err_type} - {message}")

    def to_list(self):
        return self.errors


# ---------------------------------------------------------------
# 1) CLI / 입력 검증
# ---------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="travel_planner.py",
        description="여행 날짜를 입력하면 LLM + 지도 API로 국내 여행 리포트를 생성합니다.",
    )
    parser.add_argument(
        "--date",
        required=True,
        metavar='"YYYY-MM-DD"',
        help='여행 날짜 (예: --date "2026-03-15")',
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="저장된 원본 JSON이 있어도 무시하고 API를 새로 호출합니다.",
    )
    return parser.parse_args()


def validate_date(date_str: str) -> str:
    """YYYY-MM-DD 형식 검증. 잘못되면 사용법 출력 후 종료."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        print("[오류] 날짜 형식이 올바르지 않습니다.")
        print('사용법: python travel_planner.py --date "YYYY-MM-DD"')
        print('예시  : python travel_planner.py --date "2026-03-15"')
        sys.exit(1)


# ---------------------------------------------------------------
# 2) API 키 로딩 (.env)
# ---------------------------------------------------------------
def load_api_keys() -> dict:
    """환경변수/.env에서 키를 읽는다. 미설정 시 즉시 종료 + 안내."""
    load_dotenv()

    keys = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "NAVER_CLIENT_ID": os.getenv("NAVER_CLIENT_ID"),
        "NAVER_CLIENT_SECRET": os.getenv("NAVER_CLIENT_SECRET"),
    }
    missing = [name for name, value in keys.items() if not value]

    if missing:
        print("[오류] 다음 API 키가 설정되지 않았습니다:")
        for name in missing:
            print(f"    - {name}")
        print()
        print("설정 방법 (택1):")
        print('  1) 프로젝트 폴더에 .env 파일을 만들고 아래처럼 작성')
        print('       OPENAI_API_KEY=발급받은_키')
        print('       NAVER_CLIENT_ID=발급받은_ID')
        print('       NAVER_CLIENT_SECRET=발급받은_시크릿')
        print("  2) 터미널 환경변수로 설정")
        print('       macOS/Linux : export OPENAI_API_KEY="발급받은_키"')
        print('       PowerShell  : $env:OPENAI_API_KEY="발급받은_키"')
        sys.exit(1)

    return keys


# ---------------------------------------------------------------
# 3) OpenAI 호출 (공통 함수)
# ---------------------------------------------------------------
def call_openai(api_key: str, messages: list, json_mode: bool = False) -> str:
    """
    OpenAI Chat Completions API를 POST로 호출한다.
    json_mode=True 이면 response_format으로 JSON 출력을 강제한다.
    실패 시 RuntimeError를 던진다(호출한 쪽에서 try-except 처리).
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": 0.7,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            OPENAI_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"NETWORK_ERROR: {e}") from e

    if resp.status_code in (401, 403):
        raise RuntimeError(f"AUTH_ERROR: HTTP {resp.status_code} (키 값을 확인하세요)")
    if resp.status_code == 429:
        raise RuntimeError("QUOTA_ERROR: HTTP 429 (요청 한도/잔액을 확인하세요)")
    if resp.status_code != 200:
        raise RuntimeError(f"API_ERROR: HTTP {resp.status_code} - {resp.text[:200]}")

    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------
# 4) [1/3] LLM 1차 호출 - 도시 추천 JSON
# ---------------------------------------------------------------
RECOMMEND_PROMPT = """당신은 한국 국내여행 전문가입니다.
여행 날짜: {date}

이 시기에 여행하기 좋은 국내 도시 1곳을 추천해 주세요.
반드시 아래 키를 가진 JSON 객체만 출력하세요. 다른 설명은 쓰지 마세요.

{{
  "recommended_city": "도시명 (예: 제주, 강릉)",
  "weather": "해당 시기의 일반적인 날씨 요약 (1~2문장)",
  "events": ["행사/축제 후보 1", "행사/축제 후보 2"],
  "reason": "추천 근거 2~4문장"
}}"""

RETRY_PROMPT = """아래 4개 키만 포함한 순수 JSON 객체 하나만 출력하세요.
마크다운 코드블록, 설명 문장 등 JSON 외의 텍스트는 절대 포함하지 마세요.
필수 키: recommended_city(string), weather(string), events(array of string), reason(string)
여행 날짜: {date}"""


def parse_llm_json(text: str) -> dict:
    """LLM 응답에서 JSON을 추출/파싱한다. 코드블록으로 감싸진 경우도 처리."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    result = json.loads(cleaned)

    required = ["recommended_city", "weather", "events", "reason"]
    missing = [k for k in required if k not in result]
    if missing:
        raise ValueError(f"필수 키 누락: {missing}")
    return result


def get_recommendation(date: str, api_key: str, collector: ErrorCollector) -> dict | None:
    """1차 추천 JSON을 얻는다. 파싱 실패 시 프롬프트를 바꿔 1회만 재시도."""
    print("[1/3] 1차 추천 생성 중(LLM)...")

    prompts = [
        RECOMMEND_PROMPT.format(date=date),  # 1차 시도
        RETRY_PROMPT.format(date=date),      # 재시도(필수 키만 다시 JSON으로)
    ]

    for attempt, prompt in enumerate(prompts, start=1):
        try:
            raw = call_openai(
                api_key,
                messages=[{"role": "user", "content": prompt}],
                json_mode=True,
            )
            result = parse_llm_json(raw)
            print(f'    - recommended_city: "{result["recommended_city"]}"')
            return result
        except (json.JSONDecodeError, ValueError) as e:
            collector.add("llm_recommend", "PARSE_ERROR", f"{attempt}차 시도 파싱 실패: {e}")
            if attempt == 1:
                print("    - JSON 파싱 실패. 프롬프트를 수정해 1회 재시도합니다...")
        except RuntimeError as e:
            err_type = str(e).split(":")[0]
            collector.add("llm_recommend", err_type, str(e))
            return None  # API 자체 실패는 재시도하지 않고 종료 처리

    return None  # 재시도까지 실패


# ---------------------------------------------------------------
# 5) [2/3] Naver Local Search - 맛집 검색
# ---------------------------------------------------------------
def clean_naver_text(text: str) -> str:
    """네이버 응답의 <b> 태그와 HTML 엔티티를 제거한다."""
    return html.unescape(re.sub(r"</?b>", "", text))


def search_restaurants(
    city: str, client_id: str, client_secret: str, collector: ErrorCollector
) -> list:
    """
    추천 도시의 맛집을 최대 5곳 검색한다(GET 요청).
    실패/0건이어도 프로그램은 계속 진행한다(빈 리스트 반환).
    """
    print("[2/3] 맛집 검색 중(Naver Local Search)...")

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": f"{city} 맛집", "display": 5, "sort": "random"}

    try:
        resp = requests.get(
            NAVER_LOCAL_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        collector.add("place_search", "NETWORK_ERROR", str(e))
        print("    - 네트워크 오류. 맛집 섹션은 '데이터 없음'으로 처리하고 계속 진행합니다.")
        return []

    if resp.status_code in (401, 403):
        collector.add("place_search", "AUTH_ERROR", f"HTTP {resp.status_code}")
        print(f"    - 오류: 인증 실패({resp.status_code}). Client ID/Secret과 헤더명을 확인하세요.")
        print("    - 맛집 섹션은 '데이터 없음'으로 처리하고 계속 진행합니다.")
        return []
    if resp.status_code == 429:
        collector.add("place_search", "QUOTA_ERROR", "HTTP 429")
        print("    - 오류: 요청 한도 초과. 맛집 섹션은 '데이터 없음'으로 처리하고 계속 진행합니다.")
        return []
    if resp.status_code != 200:
        collector.add("place_search", "API_ERROR", f"HTTP {resp.status_code}")
        print("    - API 오류. 맛집 섹션은 '데이터 없음'으로 처리하고 계속 진행합니다.")
        return []

    try:
        items = resp.json().get("items", [])
    except json.JSONDecodeError as e:
        collector.add("place_search", "PARSE_ERROR", str(e))
        return []

    if not items:
        collector.add(
            "place_search", "EMPTY_RESULT", f"0 results for query={params['query']}"
        )
        print("    - 검색 결과 0건. '데이터 없음' 상태로 다음 단계로 진행합니다.")
        return []

    restaurants = []
    for item in items:
        # mapx/mapy는 WGS84 좌표 * 10^7 정수 문자열 → 경도/위도로 변환
        try:
            lng = int(item.get("mapx", 0)) / 1e7
            lat = int(item.get("mapy", 0)) / 1e7
        except (ValueError, TypeError):
            lng, lat = None, None

        restaurants.append(
            {
                "name": clean_naver_text(item.get("title", "")),
                "address": item.get("roadAddress") or item.get("address", ""),
                "category": item.get("category", ""),
                "url": item.get("link", ""),
                "lat": lat,
                "lng": lng,
            }
        )

    print(f"    - 맛집 {len(restaurants)}곳 검색 완료")
    return restaurants


# ---------------------------------------------------------------
# 6) [3/3] LLM 2차 호출 - 최종 리포트 생성
# ---------------------------------------------------------------
REPORT_PROMPT = """당신은 여행 리포트 작가입니다.
아래 데이터를 바탕으로 여행 리포트를 Markdown으로 작성하세요.

[여행 날짜] {date}
[1차 추천 데이터(JSON)]
{recommendation}

[맛집 검색 결과(JSON, 빈 배열이면 데이터 없음)]
{restaurants}

작성 규칙:
- 제목: "# {date} 국내 여행 추천 리포트"
- 반드시 포함할 섹션(순서대로):
  ## 추천 지역 / ## 추천 이유 / ## 날씨 요약 / ## 행사·축제 / ## 맛집 추천 / ## 1일 일정 제안
- 맛집 검색 결과가 빈 배열이면 맛집 추천 섹션에 "- 데이터 없음 (장소 검색 결과 0건)"이라고만 쓴다.
- 맛집이 있으면 이름, 주소, 카테고리를 목록으로 정리한다.
- 1일 일정 제안은 오전/오후/저녁 수준으로 간단히 작성한다.
- "## 오류 요약" 섹션은 작성하지 않는다(프로그램이 별도로 추가함).
- Markdown 텍스트만 출력한다."""


def generate_report(
    date: str, recommendation: dict, restaurants: list,
    api_key: str, collector: ErrorCollector,
) -> str:
    """최종 여행 리포트(Markdown)를 생성한다. 오류 요약 섹션은 코드에서 직접 붙인다."""
    print("[3/3] 최종 리포트 생성 중(LLM)...")

    prompt = REPORT_PROMPT.format(
        date=date,
        recommendation=json.dumps(recommendation, ensure_ascii=False, indent=2),
        restaurants=json.dumps(restaurants, ensure_ascii=False, indent=2),
    )

    try:
        report = call_openai(api_key, messages=[{"role": "user", "content": prompt}])
    except RuntimeError as e:
        err_type = str(e).split(":")[0]
        collector.add("llm_report", err_type, str(e))
        # LLM 실패 시에도 최소한의 리포트를 코드로 직접 조립(안전망)
        report = build_fallback_report(date, recommendation, restaurants)

    # 오류 요약 섹션은 항상 코드가 결정론적으로 추가
    report += "\n\n## 오류 요약(errors)\n"
    if collector.to_list():
        for err in collector.to_list():
            report += f"- [{err['step']}] {err['type']}: {err['message']}\n"
    else:
        report += "- 없음\n"

    print("    - 리포트 생성 완료")
    return report


def build_fallback_report(date: str, rec: dict, restaurants: list) -> str:
    """리포트용 LLM 호출까지 실패했을 때 데이터만으로 조립하는 최소 리포트."""
    lines = [
        f"# {date} 국내 여행 추천 리포트",
        "",
        "## 추천 지역",
        f"- {rec.get('recommended_city', '알 수 없음')}",
        "",
        "## 추천 이유",
        rec.get("reason", "-"),
        "",
        "## 날씨 요약",
        rec.get("weather", "-"),
        "",
        "## 행사·축제",
    ]
    lines += [f"- {e}" for e in rec.get("events", [])] or ["- 데이터 없음"]
    lines += ["", "## 맛집 추천"]
    if restaurants:
        lines += [f"- {r['name']} ({r['category']}) - {r['address']}" for r in restaurants]
    else:
        lines.append("- 데이터 없음 (장소 검색 결과 0건)")
    lines += ["", "## 1일 일정 제안", "- 리포트 생성 오류로 일정 제안을 생략합니다."]
    return "\n".join(lines)


# ---------------------------------------------------------------
# 7) 결과 저장 + 캐싱(보너스)
# ---------------------------------------------------------------
def raw_json_path(date: str) -> Path:
    return RESULTS_DIR / f"{date}_raw_data.json"


def report_path(date: str) -> Path:
    return RESULTS_DIR / f"{date}_travel_plan.md"


def save_raw_data(date: str, recommendation: dict, restaurants: list, collector: ErrorCollector):
    RESULTS_DIR.mkdir(exist_ok=True)
    raw = {
        "date": date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recommendation": recommendation,
        "restaurants": restaurants,
        "errors": collector.to_list(),
    }
    path = raw_json_path(date)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_cached_raw(date: str) -> dict | None:
    """같은 날짜의 원본 JSON이 있으면 로드(보너스: 결과 캐싱)."""
    path = raw_json_path(date)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def save_report(date: str, report: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = report_path(date)
    path.write_text(report, encoding="utf-8")
    return path


# ---------------------------------------------------------------
# 메인
# ---------------------------------------------------------------
def main():
    args = parse_args()
    date = validate_date(args.date)
    keys = load_api_keys()
    collector = ErrorCollector()

    # --- 보너스: 캐싱. 같은 날짜의 원본 JSON이 있으면 API 호출 건너뛰기 ---
    cached = None if args.no_cache else load_cached_raw(date)
    if cached:
        print(f"[캐시] {raw_json_path(date)} 발견. 1·2단계 API 호출을 건너뜁니다.")
        print("       (새로 호출하려면 --no-cache 옵션을 사용하세요)")
        recommendation = cached["recommendation"]
        restaurants = cached["restaurants"]
    else:
        # [1/3] LLM 1차 추천
        recommendation = get_recommendation(date, keys["OPENAI_API_KEY"], collector)
        if recommendation is None:
            print("[중단] 1차 추천 JSON을 얻지 못해 프로그램을 종료합니다.")
            save_raw_data(date, {}, [], collector)
            print(f"오류 내역은 {raw_json_path(date)} 에 저장했습니다.")
            sys.exit(1)

        # [2/3] 맛집 검색 (실패해도 계속 진행)
        restaurants = search_restaurants(
            recommendation["recommended_city"],
            keys["NAVER_CLIENT_ID"],
            keys["NAVER_CLIENT_SECRET"],
            collector,
        )

    # [3/3] 최종 리포트
    report = generate_report(
        date, recommendation, restaurants, keys["OPENAI_API_KEY"], collector
    )

    # 결과 저장
    json_path = save_raw_data(date, recommendation, restaurants, collector)
    md_path = save_report(date, report)

    print()
    print(f"완료! 아래 파일을 확인하세요.")
    print(f"  - 원본 데이터 : {json_path}")
    print(f"  - 여행 리포트 : {md_path}")


if __name__ == "__main__":
    main()
