"""
오답노트 복습 알림 SMS 자동 발송 스크립트
=====================================

매일 KST 16:00 GitHub Actions cron에 의해 실행됩니다.

발송 규칙:
- 2차 이상 복습 일정만 대상 (1차는 수업 중에 처리되므로 알림 X)
- 오늘 예정 복습 → "오늘 복습" 알림
- 밀린 복습 (지난 일정 + 미완료) → "밀린 복습" 별도 알림
- 학생/학부모 각각 ON일 때만 발송, 번호 없으면 건너뜀

환경변수 (GitHub Secrets/Variables):
- SOLAPI_API_KEY      (secret) 솔라피 API Key
- SOLAPI_API_SECRET   (secret) 솔라피 API Secret
- SOLAPI_SENDER       (secret) 등록된 발신번호 (01000000000 형식)
- ACADEMY_NAME        (var)    학원/과외명 ([이름] 접두어용, 예: '문대승수학')
- ADMIN_PHONE         (secret) 관리자(본인) 휴대폰 - 오류 발생 시 알림용
- DRY_RUN             (input)  'true'면 실제 발송 안 하고 콘솔에만 출력
- TEST_ONLY_TO        (input)  이 번호가 설정되면 그 번호로만 발송 (테스트용)
"""

import json
import os
import sys
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# 설정 및 상수
# ============================================================

# KST 타임존
KST = timezone(timedelta(hours=9))

# 발송 일일 한도 (무한루프/오버플로 방어)
DAILY_SEND_LIMIT = 100

# 메시지 호칭 변형 규칙
# 학생용은 "민수야"/"민수 학생", 학부모용은 항상 "김민수 학생"

REPO_ROOT = Path(__file__).parent.parent
LOGS_DIR = REPO_ROOT / 'logs'


# ============================================================
# 데이터 로드
# ============================================================

def load_json(filename):
    """repo 루트의 JSON 파일을 로드. 없으면 None."""
    path = REPO_ROOT / filename
    if not path.exists():
        print(f"[WARN] {filename} 파일이 없습니다.")
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] {filename} 로드 실패: {e}")
        return None


# ============================================================
# 날짜 처리
# ============================================================

def today_kst_str():
    """KST 기준 오늘 날짜 YYYY-MM-DD"""
    return datetime.now(KST).strftime('%Y-%m-%d')


def parse_date_str(s):
    """YYYY-MM-DD 문자열을 date 객체로. 실패 시 None."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()
    except Exception:
        return None


# ============================================================
# 복습 대상 추출
# ============================================================

def parse_note_name(note_name, student_name=''):
    """
    noteName에서 단원/주제만 추출.
    형식: "학생이름_교재이름_단원이름" (예: "송유림_개념루트_삼각함수의 그래프")
    
    파싱 전략:
    1. '_'로 분할 시도
    2. 학생이름이 첫 부분에 있으면 제거
    3. 마지막 부분(또는 마지막 2번째 이후 전부)을 단원으로 사용
    4. 분할 실패 시 원본 그대로 반환
    """
    if not note_name:
        return '복습'
    
    parts = note_name.split('_')
    if len(parts) <= 1:
        return note_name.strip()
    
    # 첫 부분이 학생 이름이면 제거 (있을 때)
    if student_name and parts[0].strip() == student_name.strip():
        parts = parts[1:]
    
    if len(parts) <= 1:
        return parts[0].strip() if parts else note_name
    
    # 마지막 부분이 보통 단원명. 길이가 의미 있으면 그것만 사용
    last = parts[-1].strip()
    if last:
        return last
    return note_name.strip()


def extract_review_targets(student_id, assignments, today, student_name=''):
    """
    한 학생에 대해 (오늘 예정, 밀린 것) 두 리스트를 반환.
    각 항목: {'subject': '삼각함수의 그래프', 'round': 2}
    
    실제 스키마:
        {
          "id": "a_...",
          "studentId": "s_...",
          "noteName": "학생이름_교재_단원",
          "subject": "대수",
          "schedule": [
            { "stage": 1, "planned": "2026-05-09", "actualDone": true, "doneAt": "..." },
            { "stage": 2, "planned": "2026-05-11", "actualDone": false, "doneAt": null },
            ...
          ]
        }
    """
    today_items = []
    overdue_items = []

    if not isinstance(assignments, list):
        return today_items, overdue_items

    for item in assignments:
        if not isinstance(item, dict):
            continue
        # 학생 매칭
        if item.get('studentId') != student_id:
            continue

        # 단원/주제 추출 (noteName에서 파싱)
        note_name = item.get('noteName', '')
        topic = parse_note_name(note_name, student_name)

        # 복습 일정 추출
        schedule = item.get('schedule')
        if not isinstance(schedule, list):
            continue

        for sch in schedule:
            if not isinstance(sch, dict):
                continue
            stage = sch.get('stage')
            planned = sch.get('planned')
            done = sch.get('actualDone')
            
            if stage is None or not planned:
                continue
            if stage < 2:  # 2차부터만
                continue
            if done:
                continue
            
            d = parse_date_str(planned)
            if not d:
                continue
            
            target = {
                'subject': topic,
                'round': stage,
                'assignment_id': item.get('id'),
                'note_name': note_name,  # 학생용 메시지에 전체 노트명 표시
            }
            if d == today:
                today_items.append(target)
            elif d < today:
                overdue_items.append(target)

    return today_items, overdue_items


# ============================================================
# 과제(일반/반복) 미확인 대상 추출
# ============================================================

def extract_unconfirmed_tasks(student, assignments, confirms, today):
    """
    한 학생에 대해 '오늘 알림 보낼 과제' 리스트를 반환.
    각 항목: {'title': '과제내용', 'assignment_id': ..., 'round': ...}

    대상 조건:
    - 일반 과제: 마감일이 오늘 또는 내일 (전날 + 당일 알림)
    - 반복 과제: 오늘 요일이 weekdays에 포함
    - 위 조건 만족 + confirms에서 confirmed=true가 아닌 것 (Q1=b: 선생님 미확인)
    """
    result = []
    if not isinstance(assignments, list):
        return result

    student_id = student.get('id')
    page_code = student.get('pageCode', '')
    tomorrow = today + timedelta(days=1)
    # 파이썬 weekday: 월=0~일=6 / JS getDay: 일=0~토=6 → 변환
    today_weekday_js = (today.weekday() + 1) % 7

    for item in assignments:
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'task':
            continue
        if item.get('studentId') != student_id:
            continue

        title = (item.get('title') or '과제').strip()
        aid = item.get('id')
        rep = item.get('repeat')

        if rep and rep.get('enabled') and isinstance(rep.get('weekdays'), list):
            # 반복 과제: 오늘 요일이 해당되면
            if today_weekday_js not in rep['weekdays']:
                continue
            round_val = int(today.strftime('%Y%m%d'))  # JS와 동일 규칙
            if is_task_confirmed(confirms, page_code, aid, round_val):
                continue
            result.append({'title': title, 'assignment_id': aid, 'round': round_val})
        else:
            # 일반 과제: 마감일 있고 오늘 또는 내일이면
            due = parse_date_str(item.get('dueDate'))
            if not due:
                continue
            if due != today and due != tomorrow:
                continue
            if is_task_confirmed(confirms, page_code, aid, 1):
                continue
            result.append({'title': title, 'assignment_id': aid, 'round': 1})

    return result


def is_task_confirmed(confirms, page_code, assignment_id, round_val):
    """confirms.json에서 해당 과제가 확인 완료됐는지 확인."""
    if not isinstance(confirms, dict) or not page_code:
        return False
    key = f"{page_code}__{assignment_id}__{round_val}"
    item = confirms.get(key)
    return bool(item and item.get('confirmed') is True)


# ============================================================
# 메시지 빌드
# ============================================================

def circled_number(n):
    """1~20은 원문자(①②③…⑳), 그 이상은 'N.' 형식으로 반환."""
    if 1 <= n <= 20:
        # ①(U+2460)부터 시작
        return chr(0x2460 + (n - 1))
    return f"{n}."


def build_student_message(academy, name, items, is_overdue=False):
    """
    학생용 메시지 생성. 형식:
    <오늘 복습할 오답노트>
    ① 전체 노트명
    ② 전체 노트명
    ...
    - 노트 제목 앞에 번호(①②③…)를 붙여 여러 개일 때 구분.
    - 회차(차수)는 학생용 메시지에서 표시하지 않음 (선생님 확인용에는 표시).
    - 학원명 및 학생 이름 별도 표시 없음 (노트명에 학생명 포함).
    - 노트 1개면 보통 SMS, 2개 이상이면 LMS 전환.
    """
    header = "<밀린 복습 오답노트>" if is_overdue else "<오늘 복습할 오답노트>"
    lines = [header]
    for idx, it in enumerate(items, start=1):
        note_name = it.get('note_name', '').strip()
        if not note_name:
            note_name = it.get('subject', '오답노트')  # 노트명 없으면 fallback
        lines.append(f"{circled_number(idx)} {note_name}")
    return "\n".join(lines)


def build_student_task_message(items):
    """
    학생용 과제 미제출 알림. 형식:
    <오늘 제출할 과제>
    ① 과제 제목
    ② 과제 제목
    """
    lines = ["<오늘 제출할 과제>"]
    for idx, it in enumerate(items, start=1):
        title = it.get('title', '과제').strip() or '과제'
        lines.append(f"{circled_number(idx)} {title}")
    return "\n".join(lines)


def build_parent_task_message(academy, name, items):
    """학부모용 과제 미제출 보고."""
    count = len(items)
    return f"[{academy}] {name} 학생, 오늘 제출할 과제 {count}개가 아직 확인되지 않았습니다. 학생에게 안내하였습니다."


def build_parent_message(academy, name, items, is_overdue=False):
    """학부모용: 학원이 관리하고 있음을 알리는 보고형 문구."""
    count = len(items)
    if is_overdue:
        msg = f"[{academy}] {name} 학생, 밀린 복습 오답노트 {count}개 있습니다. 학생에게 안내하였습니다."
    else:
        msg = f"[{academy}] {name} 학생이 오늘 복습할 오답노트는 {count}개이며, 학생에게 안내 문자 발송하였습니다."
    return msg


# ============================================================
# 발송
# ============================================================

def normalize_phone(phone):
    """전화번호에서 숫자만 남김. 빈 문자열이면 None."""
    if not phone:
        return None
    digits = re.sub(r'[^0-9]', '', phone)
    if not re.match(r'^01[016789][0-9]{7,8}$', digits):
        return None
    return digits


def send_via_solapi(service, sender, to, text):
    """솔라피 SDK로 메시지 1건 발송. 성공 시 True 리턴."""
    try:
        from solapi.model import RequestMessage
        message = RequestMessage(
            from_=sender,
            to=to,
            text=text,
        )
        response = service.send(message)
        return True, None
    except Exception as e:
        return False, str(e)


# ============================================================
# 메인 로직
# ============================================================

def main():
    print("=" * 60)
    print(f"오답노트 SMS 알림 발송 시작 — {datetime.now(KST).isoformat()}")
    print("=" * 60)

    # 환경변수 읽기
    api_key = os.environ.get('SOLAPI_API_KEY')
    api_secret = os.environ.get('SOLAPI_API_SECRET')
    sender = os.environ.get('SOLAPI_SENDER')
    academy = os.environ.get('ACADEMY_NAME', '오답노트').strip()
    admin_phone = normalize_phone(os.environ.get('ADMIN_PHONE'))
    dry_run = os.environ.get('DRY_RUN', 'false').lower() == 'true'
    test_only_to = normalize_phone(os.environ.get('TEST_ONLY_TO'))

    # 환경변수 검증
    missing = []
    if not api_key: missing.append('SOLAPI_API_KEY')
    if not api_secret: missing.append('SOLAPI_API_SECRET')
    if not sender: missing.append('SOLAPI_SENDER')
    if missing:
        print(f"[ERROR] 필수 환경변수 누락: {', '.join(missing)}")
        sys.exit(1)

    sender_normalized = normalize_phone(sender) or sender

    if dry_run:
        print("[MODE] 드라이런 — 실제 발송 안 함")
    if test_only_to:
        print(f"[MODE] 테스트 모드 — {test_only_to} 번호로만 발송")

    # 데이터 로드
    students = load_json('students.json')
    assignments = load_json('assignments.json')
    confirms = load_json('submissions/confirms.json')
    if confirms is None or not isinstance(confirms, dict):
        confirms = {}

    if students is None:
        print("[ERROR] students.json을 읽을 수 없습니다.")
        sys.exit(1)
    if assignments is None:
        print("[WARN] assignments.json이 없습니다. 발송할 데이터가 없을 수 있습니다.")
        assignments = []

    today = datetime.now(KST).date()
    print(f"[INFO] 오늘 날짜 (KST): {today}")
    print(f"[INFO] 학생 수: {len(students)}")

    # 솔라피 서비스 초기화 (드라이런 아닐 때만)
    service = None
    if not dry_run:
        try:
            from solapi import SolapiMessageService
            service = SolapiMessageService(api_key=api_key, api_secret=api_secret)
        except Exception as e:
            print(f"[ERROR] 솔라피 SDK 초기화 실패: {e}")
            sys.exit(1)

    # 발송 로그
    log_entries = []
    sent_count = 0
    failed_count = 0
    skipped_count = 0

    for student in students:
        sid = student.get('id')
        name = student.get('name', '').strip()
        if not sid or not name:
            continue

        notify_student = bool(student.get('notifyStudent'))
        notify_parent = bool(student.get('notifyParent'))
        student_phone = normalize_phone(student.get('studentPhone'))
        parent_phone = normalize_phone(student.get('parentPhone'))

        # 알림이 모두 OFF면 스킵
        if not notify_student and not notify_parent:
            continue

        # 복습 대상 추출
        today_items, overdue_items = extract_review_targets(sid, assignments, today, name)
        # 과제 미확인 대상 추출
        task_items = extract_unconfirmed_tasks(student, assignments, confirms, today)

        if not today_items and not overdue_items and not task_items:
            continue  # 발송할 내용 없음

        print(f"\n[학생] {name} (id={sid})")
        print(f"  오늘 예정: {len(today_items)}개, 밀린 것: {len(overdue_items)}개, 과제: {len(task_items)}개")

        # 발송 계획
        plans = []
        if today_items:
            if notify_student and student_phone:
                plans.append(('today', 'student', student_phone, build_student_message(academy, name, today_items, False)))
            elif notify_student and not student_phone:
                print(f"  [SKIP] 학생 알림 ON이지만 번호 없음")
            if notify_parent and parent_phone:
                plans.append(('today', 'parent', parent_phone, build_parent_message(academy, name, today_items, False)))
            elif notify_parent and not parent_phone:
                print(f"  [SKIP] 학부모 알림 ON이지만 번호 없음")
        if overdue_items:
            if notify_student and student_phone:
                plans.append(('overdue', 'student', student_phone, build_student_message(academy, name, overdue_items, True)))
            if notify_parent and parent_phone:
                plans.append(('overdue', 'parent', parent_phone, build_parent_message(academy, name, overdue_items, True)))
        if task_items:
            if notify_student and student_phone:
                plans.append(('task', 'student', student_phone, build_student_task_message(task_items)))
            if notify_parent and parent_phone:
                plans.append(('task', 'parent', parent_phone, build_parent_task_message(academy, name, task_items)))

        # 실제 발송
        for category, recipient_type, to_number, text in plans:
            # 일일 한도 체크
            if sent_count + failed_count >= DAILY_SEND_LIMIT:
                print(f"  [LIMIT] 일일 한도 {DAILY_SEND_LIMIT}건 도달, 발송 중단")
                break

            # 테스트 모드: test_only_to가 설정되어 있으면 그 번호로만
            actual_to = test_only_to if test_only_to else to_number

            entry = {
                'student_id': sid,
                'student_name': name,
                'category': category,         # 'today' | 'overdue'
                'recipient_type': recipient_type,  # 'student' | 'parent'
                'to_number_masked': mask_phone(actual_to),
                'text_length': len(text),
                'dry_run': dry_run,
            }

            print(f"  → [{category}/{recipient_type}] to={mask_phone(actual_to)} text_len={len(text)}")

            if dry_run:
                entry['result'] = 'dry-run'
                entry['text_preview'] = text
                sent_count += 1  # 드라이런도 카운트 (한도 체크용)
                log_entries.append(entry)
                continue

            ok, err = send_via_solapi(service, sender_normalized, actual_to, text)
            if ok:
                entry['result'] = 'sent'
                sent_count += 1
            else:
                entry['result'] = 'failed'
                entry['error'] = err
                failed_count += 1
                print(f"    [FAIL] {err}")
            log_entries.append(entry)

    # 결과 요약
    print("\n" + "=" * 60)
    print(f"발송 완료 — 성공 {sent_count}, 실패 {failed_count}")
    print("=" * 60)

    # 로그 파일 저장
    save_log(today, log_entries, sent_count, failed_count, dry_run, test_only_to)

    # 실패 다수 시 관리자 알림
    if not dry_run and failed_count > 0 and admin_phone:
        try:
            from solapi.model import RequestMessage
            alert = RequestMessage(
                from_=sender_normalized,
                to=admin_phone,
                text=f"[알림시스템] {today} SMS 발송 중 {failed_count}건 실패. logs/ 확인 필요."
            )
            service.send(alert)
            print(f"[INFO] 관리자에게 실패 알림 발송")
        except Exception as e:
            print(f"[WARN] 관리자 알림 발송 실패: {e}")


def mask_phone(phone):
    """전화번호 일부 마스킹 (로그 보안)"""
    if not phone or len(phone) < 8:
        return phone or ''
    return phone[:3] + '-****-' + phone[-4:]


def save_log(date, entries, sent, failed, dry_run, test_only_to):
    """logs/YYYY-MM-DD.json에 저장"""
    LOGS_DIR.mkdir(exist_ok=True)
    filename = LOGS_DIR / f"{date}.json"
    # 같은 날짜 파일이 있으면 append 모드
    existing = []
    if filename.exists():
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
        except Exception:
            existing = []
    run_data = {
        'run_at': datetime.now(KST).isoformat(),
        'dry_run': dry_run,
        'test_mode': bool(test_only_to),
        'summary': {'sent': sent, 'failed': failed, 'total': sent + failed},
        'entries': entries,
    }
    existing.append(run_data)
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"[LOG] 저장됨: {filename}")


if __name__ == '__main__':
    main()
