"""Opt-in subscription test: python3 tests/live_screen.py (uses Codex quota)."""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobFilter import screen
from jobFilter.codex import run_codex
from jobFilter.models import Job
from jobFilter.store import Store


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "test.db")
        job = Job.from_hit({"objectID": "subscription-test"})
        job.title, job.company = "Graduate Software Engineer", "Fictional Test Employer"
        store.upsert_many([job], "test")
        store.save_company_sponsorship(screen.company_key(job.company), job.company, "unknown", ["Fictional test employer; no research needed."], [])
        with patch.object(screen, "fetch_description", return_value="Recent graduates welcome. No experience required. We cannot sponsor employment visas now or in the future."):
            verdict = screen.screen_job(store, store.get(job.id), screen.screening_config({"screening": {"provider": "codex"}}))
        assert verdict["status"] == "ok", verdict
        assert verdict["verdict"] == "unlikely" and verdict["fit_score"] <= 2, verdict
        assert verdict["new_grad_fit"] and verdict["model"].startswith("codex/"), verdict
        print("PASS real Codex screening", flush=True)
        result, meta = run_codex("Use web search to open the official OpenAI Codex authentication documentation. Does it support signing in with ChatGPT? Return supported and the official source URL.",
                                 {"type": "object", "properties": {"supported": {"type": "boolean"}, "source": {"type": "string"}}, "required": ["supported", "source"]})
        assert result["supported"] and "web_search" in meta["tool_types"], (result, meta)
        report = {"screening": verdict, "web_search": result, "metadata": meta, "passed": True}
        path = Path("data/codex-verification") / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-screen.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2))
        print(f"PASS real Codex web search; report: {path}", flush=True)
        store.close()


if __name__ == "__main__":
    main()
