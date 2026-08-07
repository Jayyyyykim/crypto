"""console.py — 한국어 윈도우에서 출력 때문에 죽지 않는지.

한국어 윈도우에서 파이썬은 콘솔에 직접 쓸 때는 UTF-16을 쓰지만, 출력을
파일이나 파이프로 넘기면 cp949를 쓴다. cp949에는 이모지가 없다.

봇은 보통 스케줄러가 로그 파일로 돌린다. 그러면 소급 채점이 다 끝나고
성공 메시지를 찍는 그 줄에서 작업 전체가 죽는다 — 일은 이미 끝난 뒤에.
"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import call_journal as cj
import console
import setup_ledger


class FakeConsole:
    """실제 콘솔처럼, 인코딩 못 하는 글자를 만나면 예외를 던진다."""

    def __init__(self, encoding):
        self.encoding = encoding
        self.chunks = []
        self.flushed = 0

    def write(self, s):
        s.encode(self.encoding)          # 여기서 UnicodeEncodeError
        self.chunks.append(s)

    def flush(self):
        self.flushed += 1

    @property
    def text(self):
        return "".join(self.chunks)


class RedirectCase(unittest.TestCase):
    def run_under(self, encoding, fn):
        real = sys.stdout
        sys.stdout = FakeConsole(encoding)
        crash = None
        try:
            fn()
        except UnicodeEncodeError as e:
            crash = e
        finally:
            cap, sys.stdout = sys.stdout, real
        return crash, cap


class TestSaySurvivesNarrowEncoding(RedirectCase):
    def test_plain_print_would_crash(self):
        """고치기 전 동작 — 이 테스트가 실패하면 전제가 바뀐 것이다."""
        crash, _ = self.run_under("cp949", lambda: print("🗑️ 초기화"))
        self.assertIsInstance(crash, UnicodeEncodeError)

    def test_say_does_not_crash(self):
        crash, cap = self.run_under("cp949", lambda: console.say("🗑️ 초기화"))
        self.assertIsNone(crash)
        self.assertIn("초기화", cap.text)

    def test_say_keeps_the_readable_part(self):
        crash, cap = self.run_under(
            "cp949", lambda: console.say("✅ [원장] 소급 채점 100건 적재"))
        self.assertIsNone(crash)
        self.assertIn("[원장] 소급 채점 100건 적재", cap.text)

    def test_say_is_unchanged_on_utf8(self):
        crash, cap = self.run_under("utf-8", lambda: console.say("✅ 완료"))
        self.assertIsNone(crash)
        self.assertIn("✅ 완료", cap.text)

    def test_say_supports_print_kwargs(self):
        _, cap = self.run_under(
            "utf-8", lambda: console.say("a", "b", sep="-", end="!"))
        self.assertEqual(cap.text, "a-b!")

    def test_say_flush(self):
        _, cap = self.run_under("utf-8", lambda: console.say("x", flush=True))
        self.assertEqual(cap.flushed, 1)


class TestSafe(unittest.TestCase):
    def test_replaces_unencodable(self):
        out = console.safe("✅ ok", FakeConsole("cp949"))
        self.assertNotIn("✅", out)
        self.assertIn("ok", out)

    def test_passes_through_when_encodable(self):
        self.assertEqual(console.safe("✅ ok", FakeConsole("utf-8")), "✅ ok")

    def test_unknown_encoding_does_not_raise(self):
        self.assertIn("ok", console.safe("✅ ok", FakeConsole("no-such-codec")))

    def test_say_survives_a_stream_that_rejects_everything(self):
        """스트림이 뭘 써도 거부해도 죽지 않아야 한다 (최후 방어선)."""

        class Hostile:
            encoding = "no-such-codec"

            def __init__(self):
                self.written = []

            def write(self, s):
                if any(ord(c) > 127 for c in s):
                    raise UnicodeEncodeError("x", "", 0, 1, "bad")
                self.written.append(s)

        real = sys.stdout
        sys.stdout = Hostile()
        try:
            console.say("✅ 한글 포함 메시지")
            out = "".join(sys.stdout.written)
        finally:
            sys.stdout = real
        self.assertTrue(out)
        self.assertTrue(all(ord(c) <= 127 for c in out))


class TestEnableUtf8(unittest.TestCase):
    def test_noop_when_already_utf8(self):
        real = sys.stdout
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        try:
            self.assertEqual(console.enable_utf8(("stdout",)), [])
        finally:
            sys.stdout = real

    def test_switches_narrow_stream(self):
        real = sys.stdout
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
        try:
            changed = console.enable_utf8(("stdout",))
            self.assertEqual(changed, ["stdout"])
            self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")
        finally:
            sys.stdout = real

    def test_survives_stream_without_reconfigure(self):
        real = sys.stdout
        sys.stdout = FakeConsole("cp949")          # reconfigure 없음
        try:
            self.assertEqual(console.enable_utf8(("stdout",)), [])
        finally:
            sys.stdout = real


class TestLibraryOutputIsSafe(RedirectCase):
    """실제 모듈들이 cp949 리다이렉트에서 죽지 않아야 한다."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def test_ledger_messages(self):
        p = os.path.join(self.dir.name, "l.json")

        def work():
            setup_ledger.reset(p)
            setup_ledger.backfill_universe([], lambda *a, **k: None, path=p)

        crash, cap = self.run_under("cp949", work)
        self.assertIsNone(crash)
        self.assertIn("소급 채점", cap.text)

    def test_journal_messages(self):
        p = os.path.join(self.dir.name, "j.jsonl")
        crash, cap = self.run_under("cp949", lambda: cj.reset(p))
        self.assertIsNone(crash)
        self.assertIn("발행 기록장 초기화", cap.text)

    def test_fetch_failure_messages(self):
        def work():
            def bad(*a, **k):
                raise RuntimeError("망함")
            setup_ledger.backfill_universe(
                ["A/USDT"], bad, path=os.path.join(self.dir.name, "l2.json"))

        crash, cap = self.run_under("cp949", work)
        self.assertIsNone(crash)
        self.assertIn("조회 실패", cap.text)


class TestJournalBytesAreStable(unittest.TestCase):
    """기록장은 OS가 달라도 같은 바이트여야 한다.

    해시는 내용으로 계산하므로 CRLF여도 사슬은 안 깨진다. 다만 '한 번 적으면
    안 바뀐다'는 파일이 OS를 옮겼다고 바이트가 달라지면 곤란하다.
    """

    def test_no_crlf_written(self):
        d = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(d.name, "j.jsonl")
            cj.register_criteria({"v": 1}, path=p)
            cj.publish("BTC", 1, 100.0, "근거", path=p)
            with open(p, "rb") as f:
                self.assertNotIn(b"\r\n", f.read())
        finally:
            d.cleanup()

    def test_chain_survives_crlf_file(self):
        """그래도 CRLF로 저장된 파일을 받으면 읽을 수는 있어야 한다."""
        d = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(d.name, "j.jsonl")
            cj.register_criteria({"v": 1}, path=p)
            cj.publish("BTC", 1, 100.0, "근거", path=p)
            with open(p, "rb") as f:
                raw = f.read()
            with open(p, "wb") as f:
                f.write(raw.replace(b"\n", b"\r\n"))
            self.assertTrue(cj.verify(p)[0])
        finally:
            d.cleanup()


if __name__ == "__main__":
    unittest.main()
