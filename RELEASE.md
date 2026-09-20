# 0.2.0-rc1 배포 게이트

상태: **BLOCKED / 미출시**. 기본 검증 형태는 Python 소스입니다. EXE는 미생성입니다.

## 재현

저장소 전체를 동일한 커밋으로 받습니다. Python 3.12+와 Tcl/Tk를 설치하고 저장소 루트에서 실행합니다.

```powershell
git rev-parse HEAD
py -3.12 -m unittest discover -s tests -v
py -3.12 -m compileall -q sms_api.py sms_engine.py sms_ui.py sms_man_app.py
py -3.12 sms_man_app.py
```

의존성 설치 명령은 없습니다. Python 버전과 Windows 빌드, 검증한 커밋을 결과에 기록하세요. 소스 ZIP을 만들 경우 해당 커밋으로 git archive를 생성하고 `Get-FileHash 파일명 -Algorithm SHA256`으로 해시를 구합니다. 실제 생성하지 않은 파일의 해시는 기재하지 않습니다.

## 출시 전 확인

- [ ] 단가 상한의 통화별 의미·강제 여부, 거절·환불 및 완료 상태 실측
- [ ] Windows에서 전체 테스트 통과(특히 GUI 클래스가 skip되지 않아야 함)
- [ ] 키보드 조작과 100/125/200% 배율 수동 검증
- [ ] 실제 느린 네트워크에서 중지·거절·종료 UX 확인
- [ ] 전체 Git 이력 전문 비밀정보 검사와 보안 설정 확인
- [x] MIT 라이선스 확정 및 LICENSE 추가: Copyright (c) 2026 JaeM-beginner
- [x] 비공개 문의·보안 신고 채널 확정: ljm1327@gmail.com
- [ ] 개인정보 안내 운영 정보 확정
- [ ] 4개 독립 리뷰와 수정 영향 재검증
- [ ] 최종 소스 SHA·배포물 해시·검증 기록 정합성 확인

CI 파일 존재는 원격 CI 통과의 증거가 아닙니다. Linux/headless 자동 테스트는 Windows 실측을 대체하지 않습니다. 코드 서명·EXE·자동 업데이트·공개 릴리스 생성은 이번 후보에 포함하지 않았습니다.

## 사용자 사용 확인

2026-09-07: 사용자가 실제 사용에서 문제가 없었다고 보고했습니다. 대상 버전·운영체제·개별 검증 시나리오는 특정되지 않았습니다. 이 결과는 사용자 사용 확인으로 기록하며, 위 세부 검증 항목의 완료 여부는 각 항목의 기록에 따라 판단합니다.
