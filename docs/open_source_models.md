# Pluggable Decision Engines in Jev-Ultrafast

`jev-ultrafast` now supports a universal, pluggable provider architecture for browser decision making. In addition to the proprietary TypeSafe System 1 model, you can now run open-source models like **Laya (ModernBERT-large)** completely free—either locally on your machine or remotely on Google Colab/GPU servers.

---

## Supported Decision Engines

| Engine | Type | Cost | Latency | Memory Required | Configuration |
|---|---|---|---|---|---|
| **TypeSafe (Jev)** | Proprietary API | Paid API key | ~150-250ms | 0 MB (Cloud) | `TYPESAFE_API_KEY=...` |
| **Laya Remote (Colab/Cloud)** | Open-Source (Apache 2.0) | **100% Free** | **~25-45ms** | **0 MB (Offloaded to Colab)** | `LAYA_ENDPOINT=https://...ngrok-free.app` |
| **Laya Local** | Open-Source (Apache 2.0) | **100% Free** | **~15-30ms** | ~1.5 GB RAM | `DECISION_ENGINE=laya-local` |

---

## 1. Zero-Cost, Zero-RAM Setup (Google Colab)

If you don't have a TypeSafe API key or have limited local RAM (e.g., 8 GB laptop), you can run the decision model on Google Colab's free 16 GB GPU runtime:

1. Open [`notebooks/Laya_Colab_Server.ipynb`](../notebooks/Laya_Colab_Server.ipynb) in [Google Colab](https://colab.research.google.com/).
2. Click **Runtime > Run All**.
3. Copy the generated public URL (e.g. `https://xxxx.ngrok-free.app`).
4. Set it in your `.env` or terminal:
   ```bash
   export LAYA_ENDPOINT="https://xxxx.ngrok-free.app"
   ```
5. Run your agent as usual:
   ```bash
   python -m jev_ultrafast.demo "Search for modern web standards"
   ```
   *`jev-ultrafast` will automatically route all browser action choices to your free Colab Laya instance!*

---

## 2. Local Open-Source Setup

To run Laya directly on your local CPU or GPU:

```bash
pip install laya
export DECISION_ENGINE=laya-local
python -m jev_ultrafast.demo "Open github.com and search for browser-use"
```

---

## 3. Debugging & Observability

Enable verbose diagnostic logs by setting `JEV_DEBUG=1`:

```bash
export JEV_DEBUG=1
```

This logs real-time decision metrics directly to `stderr`:
```text
[jev-ultrafast:debug] Resolved decision engine: 'laya-remote'
[jev-ultrafast:debug] Calling remote Laya endpoint: https://xxxx.ngrok-free.app/predict
[jev-ultrafast:debug] Remote Laya decision: op=CLICK, target=2, latency=28ms
```
