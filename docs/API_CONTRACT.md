# API 계약 확인 기록

확인일: 2026-09-07. 공식 문서: https://sms-man.com/api

- 문서는 control 경로의 GET/POST, token 매개변수를 설명한다.
- limits의 목록 항목은 application_id, country_id, numbers이다. 코드가 예전 count 객체 가정을 제거한다.
- 발급 성공은 request_id와 number, SMS 성공은 sms_code만 수용한다. 문서 외 id/phone/code/sms/messages 별칭과 본문 숫자 추출을 제거했다. SMS 대기는 공식 wait_sms만 수용하며 알 수 없는 응답은 결과 불명으로 중단한다.
- maxPrice는 Integer, currency는 RUB/USD/EUR이다. 문서는 통화별 세부 단위·반올림·상한 적용 보장을 충분히 설명하지 않는다. 소수는 차단하고 정수 문자열을 그대로 전달하며 실측 전 출시를 보류한다.
- set-status는 ready/close/reject/used를 열거하지만 상세 수명주기 의미는 불충분하다. 앱은 미사용 번호 reject만 사용하고 SMS 수신 성공 뒤 used/close를 추정해 전송하지 않는다.
- POST 본문 인코딩·지원의 실제 확인 없이 구매 요청을 시험하거나 GET fallback을 하지 않는다. 이번 후보는 기존 GET 유지, 호스트 제한·redirect 차단·원문 로그 금지 적용. POST 전환은 보류한다.
- 구매 응답 유실의 재조회·멱등성 기능은 확인되지 않았다. 앱은 결과 불명으로 중단하고 계정 수동 확인을 제공한다.
- API 실호출, 취소·환불, Windows 실측은 미수행이다.
