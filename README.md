# crypto — 봇 확장 모듈

친구가 만든 [MarketSurfer](docs/marketsurfer_분석.md) 사이트를 분석해서,
지금 봇(`jayyyyykim/auto`)에 없던 것들을 드롭인 모듈로 만든 저장소.

## 왜

봇의 2단계 승격 게이트는 **"4주 + 30건"** 이다. 그런데 `detect_core_signal()`은
세 조건이 동시에 맞아야 발화해서 며칠씩 0건인 날이 정상이고, 애초에
**30건으로는 승률 55%와 45%를 구분할 수 없다** (표준오차 ±9%p).

친구 사이트는 같은 문제를 다르게 풀었다. 진입을 세지 않고 **셋업 사건**을
센다 — 그래서 표본이 1,833건이다. 이 저장소는 그 방식을 가져오고,
거기에 **과거 봉 소급 채점**을 더해서 4주를 기다릴 필요조차 없앴다.

## 모듈

| 파일 | 하는 일 |
|---|---|
| `setup_ledger.py` | 셋업 사건 자동 채점 원장. 소급 채점 + **기준선/초과 적중률** |
| `regime_gate.py` | 매매환경 판정(일·3일·주·월 중 3정렬) + 페이퍼 진입 게이트 |
| `level_map.py` | 지지·저항 1·2·3차 + 관성 무효화가 + 레벨 근접 레이더 |
| `expected_range.py` | 비대칭 다음 봉 예상 범위 + 커버리지 자가채점 + 손절 sanity check |
| `demo_edge.py` | "적중률 81.9%"가 왜 실력이 아닐 수 있는지 재현 |

## 먼저 읽을 것

사이트가 공개한 3일 적중률은 상승 셋업이 죄다 69~82%, 하락 셋업이 죄다
26~37%다. **이건 셋업이 좋아서 나오는 모양이 아니다** — 표본 기간에 시장이
올랐다는 뜻이다. `demo_edge.py`는 예측력이 정확히 0인 합성 계열에서 같은
표를 재현한다.

```bash
python3 demo_edge.py
```

그래서 이 저장소의 채점표는 **기준선**(아무 날이나 잡았을 때의 확률)과
**초과 적중률**(적중률 − 기준선)을 같이 낸다. 초과가 0 근처면 그 셋업이
한 일은 아무것도 없다.

자세한 내용: [`docs/marketsurfer_분석.md`](docs/marketsurfer_분석.md)

## 쓰는 법

```bash
python3 -m unittest discover -s tests -t .   # 87개 테스트
python3 demo_edge.py
```

봇에 붙이는 방법: [`docs/통합_가이드.md`](docs/통합_가이드.md)

가장 먼저 할 일은 소급 채점이다.

```python
import setup_ledger
setup_ledger.backfill_universe(coins, get_ohlcv, timeframe="1d", bars=400)
print(setup_ledger.get_report(horizon=3))
```

## 면책

정보 제공·연구 목적이다. 특정 금융투자상품의 매매를 권유하지 않는다.
소급 채점 표본은 현재 상장 종목만 보므로 생존 편향이 있고, 실제 성적은
여기서 나온 것보다 나쁠 가능성이 높다.
