from subprocess import CompletedProcess

import scripts.install_playwright as install_playwright


def test_install_script_calls_playwright_all(monkeypatch):
    """When run with defaults the script should call the Playwright CLI to install all browsers."""
    calls = []

    def fake_run(cmd, capture_output=True, text=True):
        # record the exact command and mimic a successful CompletedProcess
        calls.append({"cmd": cmd, "capture_output": capture_output, "text": text})
        return CompletedProcess(cmd, 0, stdout="installed", stderr="")

    monkeypatch.setattr(install_playwright.subprocess, "run", fake_run)

    # Call with empty argv to use the default (which is ['all'])
    rc = install_playwright.main([])

    assert rc == 0
    assert len(calls) == 1, "subprocess.run should have been called once"

    cmd = calls[0]["cmd"]
    # The script may call the `playwright` CLI directly; ensure the expected tokens are present
    assert isinstance(cmd, (list, tuple))
    assert "playwright" in cmd and "install" in cmd, f"unexpected command called: {cmd}"


def test_install_script_calls_playwright_chromium(monkeypatch):
    """When requesting only chromium, the subprocess.run call should include 'chromium' in the args."""
    calls = []

    def fake_run(cmd, capture_output=True, text=True):
        calls.append({"cmd": cmd, "capture_output": capture_output, "text": text})
        return CompletedProcess(cmd, 0, stdout="installed chromium", stderr="")

    monkeypatch.setattr(install_playwright.subprocess, "run", fake_run)

    rc = install_playwright.main(["--browsers", "chromium"])

    assert rc == 0
    assert len(calls) == 1, "subprocess.run should have been called once"

    cmd = calls[0]["cmd"]
    assert isinstance(cmd, (list, tuple))
    # Ensure chromium is one of the arguments passed to the playwright install command
    assert "chromium" in cmd, f"'chromium' not found in subprocess command: {cmd}"
