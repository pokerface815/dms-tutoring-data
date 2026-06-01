"""
공지/단체문자 발송 스크립트
=====================================

announcements/pending.json 파일이 GitHub에 push되면
GitHub Actions가 이 스크립트를 자동 실행하여 솔라피로 발송합니다.

처리 흐름:
1. announcements/pending.json 읽기 (대기 중인 공지 배열)
2. 각 공지의 recipients에게 솔라피로 발송
3. 발송 결과를 announcements/sent/{id}.json 에 기록
4. pending.json 비우기 (빈 배열로)

환경변수 (GitHub Secrets/Variables):
- SOLAPI_API_KEY      (secret) 솔라피 API Key
- SOLAPI_API_SECRET   (secret) 솔라피 API Secret
- SOLAPI_SENDER       (secret) 등록된 발신번호 (01000000000 형식)
- ACADEMY_NAME        (var)    학원/과외명 (현재 미사용, 메시지에 이미 포함)
- DRY_RUN             (input)  'true'면 실제 발송 안 하고 콘솔에만 출력
- TEST_ONLY_TO        (input)  이 번호가 설정되면 그 번호로만 발송 (테스트용)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
SEND_LIMIT = 300  # 안전장치: 한 번 실행에 최대 발송 건수

PENDING_PATH = Path('announcements/pending.json')
SENT_DIR = Path('announcements/sent')


def normalize_phone(p):
    """전화번호에서 숫자만 추출. 유효하지 않으면 None."""
    if not p:
        return None
    digits = re.sub(r'[^0-9]', '', str(p))
    if len(digits) < 9:
        return None
    return digits


def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"  [경고] {path} 읽기 실패: {e}")
        return None


def send_via_solapi(service, sender, to, text):
    """솔라피 SDK로 메시지 1건 발송."""
    try:
        from solapi.model import RequestMessage
        message = RequestMessage(from_=sender, to=to, text=text)
        service.send(message)
        return True, None
    except Exception as e:
        return False, str(e)


def main():
    print("=" * 60)
    print(f"공지/단체문자 발송 시작 — {datetime.now(KST).isoformat()}")
    print("=" * 60)

    api_key = os.environ.get('SOLAPI_API_KEY')
    api_secret = os.environ.get('SOLAPI_API_SECRET')
    sender = os.environ.get('SOLAPI_SENDER')
    dry_run = os.environ.get('DRY_RUN', 'false').lower() == 'true'
    test_only_to = normalize_phone(os.environ.get('TEST_ONLY_TO'))

    missing = []
    if not api_key: missing.append('SOLAPI_API_KEY')
    if not api_secret: missing.append('SOLAPI_API_SECRET')
    if not sender: missing.append('SOLAPI_SENDER')
    if missing:
        print(f"[오류] 필수 환경변수 누락: {', '.join(missing)}")
        sys.exit(1)

    sender_normalized = normalize_phone(sender) or sender

    # 대기 중인 공지 로드
    pending = load_json(PENDING_PATH)
    if pending is None:
        print("pending.json 없음 — 발송할 공지 없음. 종료.")
        return
    if not isinstance(pending, list) or len(pending) == 0:
        print("대기 중인 공지 없음. 종료.")
        return

    print(f"대기 중인 공지: {len(pending)}건")
    if dry_run:
        print("*** DRY_RUN 모드: 실제 발송하지 않음 ***")
    if test_only_to:
        print(f"*** TEST_ONLY_TO 모드: {test_only_to} 로만 발송 ***")

    # 솔라피 서비스 초기화
    service = None
    if not dry_run:
        try:
            from solapi import SolapiMessageService
            service = SolapiMessageService(api_key=api_key, api_secret=api_secret)
        except Exception as e:
            print(f"[오류] 솔라피 초기화 실패: {e}")
            sys.exit(1)

    SENT_DIR.mkdir(parents=True, exist_ok=True)
    total_sent = 0
    total_fail = 0

    for ann in pending:
        if not isinstance(ann, dict):
            continue
        ann_id = ann.get('id', 'unknown')
        message = (ann.get('message') or '').strip()
        recipients = ann.get('recipients') or []
        print(f"\n[공지 {ann_id}] 수신 {len(recipients)}건")

        if not message:
            print("  내용 없음 — 건너뜀")
            continue

        results = []
        for r in recipients:
            if total_sent >= SEND_LIMIT:
                print(f"  [중단] 발송 한도({SEND_LIMIT}) 도달")
                break
            name = r.get('name', '')
            rtype = r.get('type', '')
            phone = normalize_phone(r.get('phone'))
            if not phone:
                print(f"  [SKIP] {name}({rtype}) 번호 없음")
                results.append({'name': name, 'type': rtype, 'ok': False, 'error': 'no_phone'})
                continue

            actual_to = test_only_to if test_only_to else phone

            # 수신자별 개인 메시지가 있으면 사용 ({이름} 치환된 메시지), 없으면 공통
            r_message = (r.get('message') or message)

            if dry_run:
                print(f"  [DRY] → {name}({rtype}) {actual_to}: {r_message[:30]}...")
                results.append({'name': name, 'type': rtype, 'ok': True, 'dry_run': True})
                total_sent += 1
                continue

            ok, err = send_via_solapi(service, sender_normalized, actual_to, r_message)
            if ok:
                print(f"  [발송] → {name}({rtype}) {actual_to}")
                results.append({'name': name, 'type': rtype, 'ok': True})
                total_sent += 1
            else:
                print(f"  [실패] → {name}({rtype}): {err}")
                results.append({'name': name, 'type': rtype, 'ok': False, 'error': err})
                total_fail += 1

        # 발송 결과 기록
        sent_record = {
            'id': ann_id,
            'sentAt': datetime.now(KST).isoformat(),
            'target': ann.get('target'),
            'message': message,
            'dry_run': dry_run,
            'results': results,
            'okCount': sum(1 for x in results if x.get('ok')),
            'failCount': sum(1 for x in results if not x.get('ok')),
        }
        if not dry_run:
            sent_path = SENT_DIR / f"{ann_id}.json"
            try:
                with open(sent_path, 'w', encoding='utf-8') as f:
                    json.dump(sent_record, f, ensure_ascii=False, indent=2)
                print(f"  결과 기록: {sent_path}")
            except Exception as e:
                print(f"  [경고] 결과 기록 실패: {e}")

    # pending 비우기 (dry_run이 아닐 때만)
    if not dry_run:
        try:
            with open(PENDING_PATH, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            print("\npending.json 비움 (처리 완료)")
        except Exception as e:
            print(f"[경고] pending.json 비우기 실패: {e}")

    print("\n" + "=" * 60)
    print(f"발송 완료 — 성공 {total_sent}건, 실패 {total_fail}건")
    print("=" * 60)


if __name__ == '__main__':
    main()
