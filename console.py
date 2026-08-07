"""
console.py — 어느 OS에서 돌려도 출력 때문에 죽지 않게

왜 필요한가
──────────
이 저장소의 메시지에는 이모지가 섞여 있다. 한국어 윈도우에서 파이썬은
콘솔에 직접 쓸 때는 UTF-16으로 내보내 문제가 없지만, **출력을 파일이나
파이프로 넘기면** 로캘 인코딩(cp949)을 쓴다. cp949에는 이모지가 없다.

    >>> print("✅ [원장] 소급 채점 100건 적재")   # > log.txt 로 넘기면
    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2705'

봇은 보통 스케줄러가 로그 파일로 돌린다. 그러면 소급 채점이 끝나고
성공 메시지를 찍는 그 줄에서 작업 전체가 죽는다 — 정작 일은 다 끝난 뒤에.

두 가지를 제공한다
────────────────
* :func:`say` — 라이브러리 내부용. 인코딩이 모자라면 대체 문자로 바꿔서라도
  **찍고 넘어간다.** 로그 한 줄 때문에 작업이 죽는 것보다 낫다.
* :func:`enable_utf8` — 옵트인. 표준 출력을 UTF-8로 돌려 이모지를 그대로
  남긴다. 봇 시작할 때 한 번 부르면 ``print(get_report(...))`` 처럼
  직접 찍는 코드까지 같이 안전해진다.

리포트 문자열 자체는 건드리지 않는다. 텔레그램으로 나가는 값은 UTF-8로
전송되므로 콘솔 인코딩과 무관하다.
"""

import sys


def enable_utf8(streams=("stdout", "stderr")):
    """표준 출력을 UTF-8로 돌린다 (파이썬 3.7+).

    봇 진입점에서 한 번 부르면 된다. 리눅스·맥에서는 대개 이미 UTF-8이라
    아무 일도 하지 않는다. 되돌릴 수 없는 조작이 아니고, 실패해도 조용히
    넘어간다 — 이 함수 때문에 봇이 못 뜨면 본말전도다.
    """
    changed = []
    for name in streams:
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
            changed.append(name)
        except (ValueError, OSError):
            pass
    return changed


def safe(text, stream=None):
    """지금 스트림 인코딩으로 낼 수 있는 형태로 바꾼다.

    못 내는 글자는 '?'로 바뀐다. 뜻이 조금 상하더라도 줄 자체가 사라지거나
    예외로 죽는 것보다 낫다.
    """
    stream = stream or sys.stdout
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
        return text
    except UnicodeEncodeError:
        return text.encode(enc, errors="replace").decode(enc, errors="replace")
    except LookupError:
        # 파이썬이 모르는 인코딩 이름. 같은 이름으로 다시 시도해 봐야 또 터지니
        # ASCII로 낮춘다 — 한글까지 '?'가 되지만, 죽는 것보다는 낫다.
        return text.encode("ascii", errors="replace").decode("ascii")


def say(*parts, **kw):
    """print 대신 쓰는 안전한 출력.

    평소에는 print와 똑같고, 인코딩이 모자랄 때만 대체 문자로 낮춰 찍는다.
    """
    stream = kw.pop("file", None) or sys.stdout
    text = kw.pop("sep", " ").join(str(p) for p in parts)
    end = kw.pop("end", "\n")
    try:
        stream.write(text + end)
    except UnicodeEncodeError:
        try:
            stream.write(safe(text, stream) + end)
        except (UnicodeEncodeError, LookupError):
            # 마지막 방어선. 여기까지 왔으면 스트림 인코딩이 뭔지 믿을 수
            # 없으므로 ASCII로 못박아 쓴다. 로그 한 줄 때문에 작업이 죽는
            # 것만은 막는다는 게 이 모듈의 약속이다.
            stream.write(text.encode("ascii", "replace").decode("ascii") + end)
    if kw.pop("flush", False):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
