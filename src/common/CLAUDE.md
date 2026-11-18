# CLAUDE.md — common 계층

모든 계층이 공유하는 설정·경로·상수만 둔다. 루트 `CLAUDE.md` 규칙이 우선한다.

## 책임

- `.env`·`config/*.yaml` 로딩과 키 접근 (`config.py`)
- `data/` 경로 상수, 기간(`get_period()`), 모델명, DB·벡터DB 경로
- DART 상수 (`dart.py`: 원문 링크 형식, 공시 유형 코드)

## 규칙

- 다른 계층을 import하지 않는다. 비즈니스 로직(수집, 분류, 검색)을 두지 않는다.
- 키가 없으면 **이름만** 알려주고 실패한다. 키 값을 `print`/로그/`repr`로 내보내지 않는다.
- 새 비밀값을 추가하면 `SECRET_NAMES`에도 추가해 `mask_secret()` 대상이 되게 한다.
- `corp_code`는 문자열로 다룬다 (앞자리 0 보존).
