import pathlib
import re
import subprocess
import tempfile
import unittest


class PanelJavaScriptSyntaxTests(unittest.TestCase):
    def test_production_panel_only_lists_eurusd_and_xauusd(self):
        html = pathlib.Path("panel_escaner_vantage.html").read_text(encoding="utf-8")
        self.assertIn('symbol:"EUR/USD"', html)
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

    def test_pwa_cache_and_entry_url_are_bumped(self):
        worker = pathlib.Path("service-worker.js").read_text(encoding="utf-8")
        index = pathlib.Path("index.html").read_text(encoding="utf-8")
        self.assertIn('global-signal-scanner-shell-v9', worker)
        self.assertIn('panel_escaner_vantage.html?v=20261116-2', index)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as handle:
            handle.write(worker)
            handle.flush()
            result = subprocess.run(["node", "--check", handle.name], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, f"Error de sintaxis en service-worker.js:\\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()