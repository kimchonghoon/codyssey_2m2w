# 국내 여행 추천 프로그램 (코딧세이 2주차)

여행 날짜를 입력하면 **LLM API(OpenAI)** 와 **지도 API(Naver Local Search)** 를 조합하여
국내 여행 추천 리포트를 자동 생성하는 CLI 프로그램입니다.

## 동작 흐름

```
--date 입력
   │
   ▼
[1/3] OpenAI  : 해당 시기에 좋은 도시 추천 (JSON 구조화 출력)
   │              recommended_city / weather / events / reason
   ▼
[2/3] Naver   : recommended_city 기준 맛집 최대 5곳 검색 (GET)
   │              실패·0건이어도 "데이터 없음"으로 계속 진행
   ▼
[3/3] OpenAI  : 전체 데이터 → 최종 여행 리포트 (Markdown)
   │
   ▼
results/ 폴더에 원본 JSON + 리포트 MD 저장
```

## 실행 환경

- Python 3.10 이상
- 의존 패키지: `requests`, `python-dotenv`

```bash
pip install -r requirements.txt
```

## API 키 설정 방법

> ⚠️ **API 키는 절대 코드·README·결과 파일에 직접 쓰지 않습니다.**
> 아래 두 방법 중 하나로 설정하세요. 키가 없으면 프로그램이 즉시 종료되며 안내를 출력합니다.

### 방법 1 — .env 파일 (권장)

프로젝트 폴더에 `.env` 파일을 만들고 아래 형식으로 작성합니다.
(`.env.example` 파일을 복사해서 값만 채워도 됩니다)

```
OPENAI_API_KEY=발급받은_OpenAI_키
NAVER_CLIENT_ID=발급받은_네이버_클라이언트_ID
NAVER_CLIENT_SECRET=발급받은_네이버_시크릿
```

- OpenAI 키 발급: https://platform.openai.com/api-keys
- Naver 키 발급: https://developers.naver.com/apps → 애플리케이션 등록 → **"검색" API 추가**

### 방법 2 — 환경변수 (현재 터미널 세션에만 적용)

```bash
# macOS / Linux
export OPENAI_API_KEY="발급받은_키"

# Windows PowerShell
$env:OPENAI_API_KEY="발급받은_키"
```

## 실행 방법

```bash
python travel_planner.py --date "2026-03-15"
```

같은 날짜로 재실행하면 저장된 원본 JSON을 재사용하여 API 호출을 건너뜁니다(캐싱, 보너스 기능).
새로 호출하려면:

```bash
python travel_planner.py --date "2026-03-15" --no-cache
```

## 결과물 확인 방법

실행이 끝나면 `results/` 폴더에 두 파일이 생성됩니다.

| 파일 | 내용 |
|------|------|
| `results/{날짜}_raw_data.json` | 1차 추천 JSON + 맛집 검색 결과 + 오류 요약(errors) |
| `results/{날짜}_travel_plan.md` | 최종 여행 리포트 (추천 지역/이유/날씨/행사/맛집/1일 일정/오류 요약) |

## 에러 처리 정책

| 상황 | 동작 |
|------|------|
| API 키 미설정 | 즉시 종료 + 설정 방법 안내 출력 |
| 날짜 형식 오류 | 사용법 출력 후 종료 |
| LLM JSON 파싱 실패 | "필수 키만 JSON으로" 프롬프트로 **1회만** 재시도 |
| 지도 API 실패 (401/403/429/네트워크) | 맛집 = "데이터 없음" 처리 후 리포트 생성 계속 진행 |
| 검색 결과 0건 | 중단 없이 "데이터 없음"으로 다음 단계 진행 |
| 모든 오류 | 내부 errors 리스트에 기록 → 원본 JSON과 리포트의 오류 요약 섹션에 남김 |

## 보안 주의 사항

- `.gitignore`에 `.env`가 포함되어 있어 키 파일은 커밋되지 않습니다.
- 키를 코드에 직접 쓰지 않는 이유:
  1. 협업/공유 시 실수로 키가 공개되는 사고를 막는다.
  2. 키를 교체해도 코드를 수정할 필요가 없다.
  3. 과금·쿼터가 걸린 서비스의 비용 사고를 예방한다.
- 제출 전, 로그·결과 파일에 키 문자열이 들어가지 않았는지 한 번 더 확인하세요.
