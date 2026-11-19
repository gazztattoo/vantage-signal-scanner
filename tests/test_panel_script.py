import pathlib
import re
import subprocess
import tempfile
import unittest


class PanelJavaScriptSyntaxTests(unittest.TestCase):
    def test_production_panel_is_gold_only(self):
        html = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        self.assertNotIn('symbol:"EUR/USD"', html)
        self.assertIn('symbol:"XAU/USD"', html)
        self.assertNotIn("BTC/USD", html)
        self.assertNotIn('class="trend-name">CRIPTO', html)
        self.assertIn("El motor usa velas OHLC de BiQuote", html)
        self.assertIn("ESTADO REAL DE LA ENTRADA", html)
        self.assertIn("CALIDAD TÉCNICA DE LOS INDICADORES", html)

    def test_panel_does_not_claim_automatic_scanning_while_production_is_paused(self):
        html = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        self.assertIn("Producción pausada: M5/M15 disponibles bajo consulta.", html)
        self.assertIn("El escaneo automático de producción está pausado.", html)
        self.assertNotIn("M5 · automático", html)
        self.assertNotIn("M15 · automático", html)
        self.assertIn("Motor conectado", html)
        self.assertNotIn("revisa el feed BiQuote arriba", html)

    def test_live_tick_polling_is_throttled_and_pauses_in_background(self):
        html = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        self.assertIn("const POLL_INTERVAL_MS=2000", html)
        self.assertIn('if(document.hidden)stop()', html)
        self.assertIn('else if(accessToken)begin()', html)

    def test_inline_scripts_parse_with_node(self):
        html = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL)
        self.assertTrue(scripts, "No se encontraron scripts inline en el panel.")
        for index, script in enumerate(scripts):
            if not script.strip():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as handle:
                handle.write(script)
                handle.flush()
                result = subprocess.run(["node", "--check", handle.name], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, f"Error de sintaxis en el script inline {index}:\n{result.stderr}")

    def test_production_panel_has_no_test_push_control(self):
        html = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        self.assertNotIn('id="pushTest"', html)
        self.assertNotIn('PUSH_API+"/test"', html)
        self.assertIn('id="pushToggle"', html)

    def test_scan_persists_m15_confirmation_before_m5_confluence_check(self):
        app = pathlib.Path("app.py").read_text(encoding="utf-8")
        self.assertIn('selected_intervals = [requested_interval] if requested_interval else ["15min", "5min"]', app)
        self.assertLess(app.index('for symbol, interval in ('), app.index('if interval == "5min" and direction in ("COMPRA", "VENTA")'))
        self.assertIn('"timeframe": "eq.15min"', app)

    def test_scheduled_runner_orders_m15_before_m5(self):
        runner = pathlib.Path("scripts/run_signal_scan.py").read_text(encoding="utf-8")
        self.assertLess(runner.index('due_scans.append(("XAU/USD", "15min"))'),
                        runner.index('due_scans.append(("XAU/USD", "5min"))'))
        self.assertNotIn("EUR/USD", runner)
        self.assertLess(runner.index('if enabled not in {"1", "true", "yes", "on"}'),
                        runner.index('session = requests.Session()'))

    def test_push_payload_targets_production_card_and_worker_opens_same_origin(self):
        app = pathlib.Path("app.py").read_text(encoding="utf-8")
        worker = pathlib.Path("service-worker.js").read_text(encoding="utf-8")
        panel = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        self.assertIn('https://vantage-signal-scanner.onrender.com/panel_escaner_vantage.html?view=scanner&symbol=', app)
        self.assertIn('&timeframe=', app)
        self.assertIn('const targetUrl = new URL(', worker)
        self.assertIn('event.notification.data?.url || "./panel_escaner_vantage.html"', worker)
        self.assertIn('if (targetOrigin !== self.location.origin) return;', worker)
        self.assertIn('notificationParams.get("symbol")', panel)
        self.assertIn('scrollIntoView({behavior:"smooth",block:"center"})', panel)

    def test_push_subscription_endpoints_require_panel_authentication(self):
        app = pathlib.Path("app.py").read_text(encoding="utf-8")
        self.assertIn('"/api/push/subscribe"', app)
        self.assertIn('"/api/push/unsubscribe"', app)
        self.assertIn('"/api/push/test"', app)
        self.assertIn('authorization.startswith("Bearer ")', app)

    def test_production_scan_workflow_remains_paused(self):
        workflow = pathlib.Path(".github/workflows/vantage-signal-scanner-cron.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn('SCANNER_ENABLED: "false"', workflow)

    def test_pwa_cache_and_entry_url_are_bumped(self):
        worker = pathlib.Path("service-worker.js").read_text(encoding="utf-8")
        index = pathlib.Path("index.html").read_text(encoding="utf-8")
        self.assertIn('global-signal-scanner-shell-v10', worker)
        self.assertIn('panel_escaner_vantage.html?v=20261116-3', index)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as handle:
            handle.write(worker)
            handle.flush()
            result = subprocess.run(["node", "--check", handle.name], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, f"Error de sintaxis en service-worker.js:\\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
