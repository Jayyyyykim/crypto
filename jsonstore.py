"""
jsonstore.py — JSON 상태 파일 원자적 읽기/쓰기

paper_trader.py가 쓰던 tmp→replace 패턴을 모듈로 뽑았다.
봇은 장시간 돌면서 수시로 상태를 저장하는데, 저장 도중 죽으면
파일이 반쯤 쓰인 채로 남아 다음 기동에서 통째로 날아간다.
os.replace는 같은 파일시스템 안에서 원자적이라 그 창을 없앤다.
"""

import json
import os


def load(path, default=None):
    """없거나 깨졌으면 default를 돌려준다 (예외를 위로 던지지 않는다).

    상태 파일이 깨졌다고 봇 전체가 멈추면 안 된다 — 통계 하나 잃는 것보다
    스케줄이 죽는 쪽이 훨씬 비싸다.
    """
    if not os.path.exists(path):
        return default if default is not None else []
    # 빈 파일은 '아직 아무것도 안 쌓임'이라는 정상 상태다 (첫 기동, 리셋 직후).
    # 이걸 파싱 실패로 로그에 찍으면 진짜 손상과 구분이 안 된다.
    try:
        if os.path.getsize(path) == 0:
            return default if default is not None else []
    except OSError:
        pass
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"[jsonstore] 읽기 실패 ({path}): {e}")
        return default if default is not None else []


def save(path, obj):
    """tmp에 먼저 쓰고 replace — 중간에 죽어도 기존 파일은 온전하다."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError) as e:
        print(f"[jsonstore] 저장 실패 ({path}): {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
