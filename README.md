# dms-tutoring-data

오답노트 학생 데이터 + SMS 알림 자동 발송 시스템 (private repo)

## 구조

```
.
├── students.json              ← 학생 정보 (시스템이 자동 push)
├── assignments.json           ← 오답노트 배정 (시스템이 자동 push)
├── scripts/
│   ├── send_daily_sms.py     ← SMS 발송 메인 스크립트
│   └── requirements.txt       ← Python 의존성
├── .github/workflows/
│   └── daily-sms.yml          ← 매일 16:00 (KST) 자동 실행
└── logs/                      ← 발송 결과 자동 기록
    └── YYYY-MM-DD.json
```

## 동작 방식

1. **데이터 수집**: 오답노트 시스템(HTML)이 학생 정보·배정 정보를 이 repo로 자동 push
2. **자동 발송**: GitHub Actions가 매일 KST 16:00에 `scripts/send_daily_sms.py` 실행
3. **로그 기록**: 발송 결과가 `logs/YYYY-MM-DD.json`으로 자동 commit

## 환경변수 설정

### GitHub Secrets (Settings → Secrets and variables → Actions → Secrets)

| Secret 이름 | 값 | 용도 |
|-------------|------|------|
| `SOLAPI_API_KEY` | 솔라피 API Key | SMS 발송 인증 |
| `SOLAPI_API_SECRET` | 솔라피 API Secret | SMS 발송 인증 |
| `SOLAPI_SENDER` | 등록된 발신번호 (예: `01012345678`) | "보낸 사람"으로 표시 |
| `ADMIN_PHONE` | 본인 휴대폰 (예: `01012345678`) | 발송 실패 시 알림 받을 번호 |

### GitHub Variables (같은 메뉴 → Variables 탭)

| Variable 이름 | 값 | 용도 |
|---------------|------|------|
| `ACADEMY_NAME` | 학원명 (예: `문대승수학`) | 메시지 접두어 `[학원명]` |

## 수동 실행 (테스트)

GitHub repo → Actions 탭 → "Daily SMS Notification" → "Run workflow"

옵션:
- **dry_run: true** — 실제 발송 안 함, 로그만 생성
- **test_only_to: 01012345678** — 이 번호로만 발송 (전체 학생 대상이지만 수신은 단일 번호)

## 발송 규칙

- **대상**: 2차 이상 복습 일정만 (1차는 수업 중에 직접 처리)
- **오늘 예정**: 학생/학부모 각각 알림 ON + 번호 있음 → 발송
- **밀린 복습**: 별도 메시지로 발송 (예정일 지났고 미완료)
- **알림 OFF / 번호 없음**: 발송하지 않음

## 메시지 템플릿

**학생용 (오늘 복습)**
```
[문대승수학] 김민수 학생, 오늘 2차 복습 오답노트 N개 있어요.
(주제1, 주제2, 주제3)
```

**학부모용 (오늘 복습)**
```
[문대승수학] 김민수 학생이 오늘 복습할 오답노트는 N개이며, 학생에게 안내 문자 발송하였습니다.
```

**학생용 (밀린 복습)**
```
[문대승수학] 김민수 학생, 밀린 복습 오답노트 N개 있어요. 챙겨서 진행해주세요!
```

**학부모용 (밀린 복습)**
```
[문대승수학] 김민수 학생, 밀린 복습 오답노트 N개 있습니다. 학생에게 안내하였습니다.
```

## 안전장치

- **일일 발송 한도**: 100건 (무한루프 방어)
- **드라이런 모드**: 실제 발송 없이 시뮬레이션
- **에러 알림**: 발송 중 실패 발생 시 관리자 휴대폰으로 알림
- **번호 마스킹**: 로그에는 휴대폰 번호 일부만 기록 (010-****-1234)

## 비용

- 일반 SMS: 약 9~13원/건
- LMS (90바이트 초과): 약 28~33원/건
- 학원명·메시지 길이에 따라 일부 메시지는 LMS로 자동 전환됨

## 보안 주의

⚠️ **이 repo는 반드시 private이어야 합니다.** 학생 휴대폰 번호가 들어있습니다.
⚠️ Secrets에 저장된 API Key/Secret은 코드나 commit 메시지에 절대 노출 X
⚠️ 토큰 만료 시 GitHub Settings에서 새로 발급 후 Secret 갱신
