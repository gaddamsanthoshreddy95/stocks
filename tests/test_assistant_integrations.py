import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.assistant.codex_service import CodexService
from src.assistant.context_tools import StockAnalyzerTools, UIContext
from src.assistant.openai_assistant import (OpenAIAnalyst, OpenAIAuthenticationError,
                                            OpenAIConfigurationError)
from src.assistant.local_assistant import OllamaAnalyst, OllamaConfigurationError
from src.assistant.gemini_assistant import (GeminiAnalyst, GeminiAuthenticationError,
                                            GeminiConfigurationError)
from src.ui.database import ReportDatabase


class AssistantIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "risk.py").write_text(
            "def adverse_probability():\n    return 18\n", encoding="utf-8")
        (self.root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
        self.database = ReportDatabase(self.root / "reports.db")
        self.report_id = self.database.save_report({
            "run_id": "run-ai", "date": "2026-07-22", "market": {}, "summary": {},
            "trades": [{"symbol": "SBIN", "status": "TRADE", "final_action": "BUY",
                        "selection_reason": "Strong evidence", "quality_score": 82,
                        "levels": {"entry": 800, "target_1": 850},
                        "adverse_move_risk": {
                            "available": True, "adverse_barrier_percent": 3,
                            "probability_adverse_barrier_before_target": 18}}],
            "watchlist": [], "rejected": [],
        }, "cache")
        self.tools = StockAnalyzerTools(self.database, self.root)

    def tearDown(self):
        self.directory.cleanup()

    def test_candidate_summary_is_grounded_in_saved_report(self):
        result = self.tools.candidate_summary("sbin", self.report_id)
        self.assertTrue(result["available"])
        self.assertEqual(result["decision"]["quality_score"], 82)
        self.assertEqual(result["adverse_move_risk"][
            "probability_adverse_barrier_before_target"], 18)

    def test_report_wide_context_includes_adverse_probability(self):
        selected = self.tools.selected_stocks(self.report_id)
        adverse = selected["items"][0]["adverse_move_risk"]
        self.assertTrue(adverse["available"])
        self.assertEqual(adverse["adverse_barrier_percent"], 3)
        self.assertEqual(adverse["probability_adverse_barrier_before_target"], 18)

    def test_project_search_and_read_exclude_secrets(self):
        results = self.tools.search_project("adverse probability")["results"]
        self.assertEqual(results[0]["path"], "src/risk.py")
        with self.assertRaises(ValueError):
            self.tools.read_project_file(".env")
        with self.assertRaises(ValueError):
            self.tools.read_project_file("../outside.py")

    def test_question_context_adds_candidate_and_relevant_source(self):
        context = self.tools.context_for_question(
            "How is adverse probability calculated in code?",
            UIContext(symbol="SBIN", report_id=self.report_id),
        )
        self.assertEqual(context["candidate"]["decision"]["symbol"], "SBIN")
        self.assertEqual(context["project_snippets"][0]["path"], "src/risk.py")

    def test_unconfigured_openai_client_fails_with_setup_instruction(self):
        client = OpenAIAnalyst(api_key="")
        client.api_key = None
        with self.assertRaises(OpenAIConfigurationError):
            client.answer("why", {})

    def test_openai_output_parser_handles_responses_message(self):
        payload = {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Grounded answer"}]}]}
        self.assertEqual(OpenAIAnalyst._output_text(payload), "Grounded answer")

    def test_unconfigured_gemini_client_fails_with_setup_instruction(self):
        client = GeminiAnalyst(api_key="")
        client.api_key = None
        with self.assertRaises(GeminiConfigurationError):
            client.answer("why", {})

    def test_gemini_output_parser_handles_candidate_parts(self):
        payload = {"candidates": [{"content": {"parts": [
            {"text": "Grounded"}, {"text": " answer"},
        ]}}]}
        self.assertEqual(GeminiAnalyst._output_text(payload), "Grounded\n answer")

    def test_gemini_search_is_limited_to_current_information_questions(self):
        self.assertTrue(GeminiAnalyst._needs_current_search(
            "What is the latest JSWENERGY news today?"))
        self.assertFalse(GeminiAnalyst._needs_current_search(
            "Why did JSWENERGY fail the risk gate?"))

    def test_gemini_uses_current_stable_default_model(self):
        with patch.dict("os.environ", {}, clear=True):
            analyst = GeminiAnalyst(api_key="test-key")
            self.assertEqual(analyst.model, "gemini-3.5-flash")
            self.assertEqual(analyst.fallback_model, "gemini-3.1-flash-lite")
            self.assertEqual(analyst.web_search, "always")

    @patch("src.assistant.gemini_assistant.requests.post")
    def test_gemini_answer_uses_grounded_context(self, post):
        response = Mock(ok=True)
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Gemini answer"}]}}],
        }
        post.return_value = response
        result = GeminiAnalyst(api_key="test-key", model="test-model").answer(
            "Why?", {"symbol": "SBIN"})
        self.assertEqual(result["text"], "Gemini answer")
        self.assertIn("SBIN", post.call_args.kwargs["json"]["contents"][-1]["parts"][0]["text"])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "test-key")
        self.assertNotIn("temperature", post.call_args.kwargs["json"]["generationConfig"])
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [{"google_search": {}}])

    @patch("src.assistant.gemini_assistant.time.sleep")
    @patch("src.assistant.gemini_assistant.requests.post")
    def test_gemini_retries_then_uses_fallback_during_high_demand(self, post, sleep):
        busy = Mock(ok=False, status_code=503, text="busy", headers={})
        busy.json.return_value = {"error": {"message": "high demand", "status": "UNAVAILABLE"}}
        success = Mock(ok=True)
        success.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Fallback answer"}]}}],
        }
        post.side_effect = [busy, busy, success]
        result = GeminiAnalyst(api_key="test-key").answer("Why?", {"symbol": "SBIN"})
        self.assertEqual(result["text"], "Fallback answer")
        self.assertEqual(result["model"], "gemini-3.1-flash-lite")
        self.assertIn("gemini-3.1-flash-lite", post.call_args.args[0])
        self.assertEqual(sleep.call_count, 1)

    @patch("src.assistant.gemini_assistant.requests.post")
    def test_gemini_current_news_uses_search_and_returns_sources(self, post):
        response = Mock(ok=True)
        response.json.return_value = {
            "candidates": [{
                "content": {"parts": [{"text": "Current company news."}]},
                "groundingMetadata": {"groundingChunks": [
                    {"web": {"title": "Exchange filing", "uri": "https://example.com/filing"}},
                ]},
            }],
        }
        post.return_value = response
        result = GeminiAnalyst(api_key="test-key", model="test-model").answer(
            "What is the news today?", {"symbol": "JSWENERGY"})
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [{"google_search": {}}])
        self.assertTrue(result["search_grounded"])
        self.assertEqual(result["sources"][0]["title"], "Exchange filing")
        self.assertIn("[Exchange filing](https://example.com/filing)", result["text"])

    @patch("src.assistant.gemini_assistant.requests.post")
    def test_invalid_gemini_key_becomes_login_request(self, post):
        response = Mock(ok=False, status_code=400, text="invalid")
        response.json.return_value = {
            "error": {"message": "API key not valid", "status": "INVALID_ARGUMENT"},
        }
        post.return_value = response
        with self.assertRaises(GeminiAuthenticationError):
            GeminiAnalyst(api_key="bad-key").answer("why", {})

    @patch("src.assistant.openai_assistant.requests.post")
    def test_invalid_openai_key_becomes_login_request(self, post):
        response = Mock(ok=False, status_code=401, text="unauthorized")
        response.json.return_value = {"error": {"message": "invalid key"}}
        post.return_value = response
        with self.assertRaises(OpenAIAuthenticationError):
            OpenAIAnalyst(api_key="bad-key").answer("why", {})

    def test_codex_implementation_requires_confirmation(self):
        service = CodexService(self.root, executable="definitely-not-installed-codex")
        with self.assertRaises(PermissionError):
            service.run("change code", "IMPLEMENT", confirmed=False)

    def test_codex_extracts_current_nested_agent_message(self):
        event = {"type": "item.completed", "item": {
            "id": "item_0", "type": "agent_message", "text": "Grounded explanation",
        }}
        self.assertEqual(CodexService._event_text(event), "Grounded explanation")

    def test_codex_ignores_non_message_event_items(self):
        event = {"type": "item.completed", "item": {
            "id": "item_0", "type": "command_execution", "text": "secret shell output",
        }}
        self.assertEqual(CodexService._event_text(event), "")

    @patch("src.assistant.codex_service.subprocess.run")
    @patch("src.assistant.codex_service.shutil.which", return_value="/usr/bin/codex")
    def test_codex_run_prefers_agent_answer_over_stderr_warnings(self, which, run):
        run.return_value = Mock(
            stdout=(
                '{"type":"thread.started","thread_id":"thread-1"}\n'
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"The selected stock passed its risk gates."}}\n'
            ),
            stderr="WARN shell snapshot unavailable",
            returncode=0,
        )
        result = CodexService(self.root).run("Explain selection", "EXPLAIN")
        self.assertEqual(result.output, "The selected stock passed its risk gates.")
        self.assertEqual(result.return_code, 0)

    @patch("src.assistant.local_assistant.requests.post")
    def test_ollama_answer_uses_grounded_context(self, post):
        response = Mock(ok=True)
        response.json.return_value = {"message": {"content": "Local answer"}}
        post.return_value = response
        result = OllamaAnalyst(model="test-model").answer("Why?", {"symbol": "SBIN"})
        self.assertEqual(result["text"], "Local answer")
        self.assertIn("SBIN", post.call_args.kwargs["json"]["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
