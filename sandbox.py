"""
sandbox.py
----------
A sandboxed test runner for the Write-Test-Fix loop. Given implementation code
and pytest test code, it runs them together in a THROWAWAY temporary directory
and returns the result (pass/fail + captured output).

Safety design:
  - Code runs in a fresh temp dir under the OS temp location, NOT your project
    or home folder. The dir is deleted when the run finishes.
  - A hard timeout kills runaway/infinite-loop code.
  - The runner only executes what it's handed — it never imports your project.

This is a "soft" sandbox (process + temp dir isolation), which is right for a
local personal project. For stronger isolation against hostile code, swap the
subprocess call for a Docker container — see run_in_docker() at the bottom.
"""

import os
import sys
import tempfile
import subprocess
import shutil

TIMEOUT_SECONDS = 30


def jarvis_run_tests(implementation_code: str, test_code: str):
    """
    Run implementation + pytest tests together in an isolated temp directory.

    Args:
        implementation_code: the module under test. Saved as solution.py.
        test_code: pytest tests. Saved as test_solution.py. Should import from
                   'solution' (e.g. `from solution import my_func`).

    Returns a result string the model can read: either a SUCCESS summary or a
    FAILURE block containing the captured traceback/stdout for self-correction.
    """
    workdir = tempfile.mkdtemp(prefix="jarvis_sandbox_")
    try:
        impl_path = os.path.join(workdir, "solution.py")
        test_path = os.path.join(workdir, "test_solution.py")
        with open(impl_path, "w", encoding="utf-8") as f:
            f.write(implementation_code)
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_code)

        # Run pytest in the temp dir. -q quiet, but full tracebacks on failure.
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", test_path],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return (
                "FAILURE: TIMEOUT\n"
                f"Tests exceeded {TIMEOUT_SECONDS}s and were killed. "
                "Likely an infinite loop or a blocking call (network/input)."
            )
        except FileNotFoundError:
            return (
                "FAILURE: pytest not installed.\n"
                "Install it in the venv:  pip install pytest"
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        output = output.strip() or "(no output)"

        if proc.returncode == 0:
            return f"SUCCESS: all tests passed.\n\n{output}"
        return (
            f"FAILURE: tests failed (exit code {proc.returncode}).\n\n"
            f"--- captured output ---\n{output}"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# OPTIONAL stronger isolation: run inside a disposable Docker container.
# Requires Docker Desktop installed and running. Not used by default.
# To enable, call run_in_docker() from jarvis_run_tests instead of subprocess.
# ---------------------------------------------------------------------------
def run_in_docker(implementation_code: str, test_code: str,
                  image: str = "python:3.12-slim"):
    """Run the same thing inside a throwaway Docker container (if available)."""
    workdir = tempfile.mkdtemp(prefix="jarvis_docker_")
    try:
        with open(os.path.join(workdir, "solution.py"), "w") as f:
            f.write(implementation_code)
        with open(os.path.join(workdir, "test_solution.py"), "w") as f:
            f.write(test_code)
        try:
            proc = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", "none",            # no network access
                    "-v", f"{workdir}:/app:ro",     # mount read-only
                    "-w", "/app",
                    image,
                    "sh", "-c",
                    "pip install --quiet pytest && pytest -q --tb=short test_solution.py",
                ],
                capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError:
            return "FAILURE: Docker not found. Install Docker Desktop or use the temp-dir runner."
        except subprocess.TimeoutExpired:
            return "FAILURE: TIMEOUT inside Docker (120s)."

        output = ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"
        if proc.returncode == 0:
            return f"SUCCESS: all tests passed (Docker).\n\n{output}"
        return f"FAILURE: tests failed in Docker.\n\n{output}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
