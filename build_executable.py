import subprocess
import sys
from pathlib import Path

def run_command(args, shell=False):
    print(f"Running: {' '.join(args) if isinstance(args, list) else args}")
    res = subprocess.run(args, shell=shell)
    if res.returncode != 0:
        print(f"Command failed with exit code {res.returncode}")
        sys.exit(res.returncode)

def main():
    project_dir = Path(__file__).parent.resolve()
    venv_dir = project_dir / ".venv"
    
    # 1. Create venv if not exists
    if not venv_dir.exists():
        print("Creating virtual environment...")
        run_command(["uv", "venv", ".venv"])
    
    # 2. Install dependencies and pyinstaller
    print("Installing dependencies...")
    run_command(["uv", "pip", "install", "-e", ".[mcp]", "pyinstaller"])
    
    # 3. Build executable
    pyinstaller_exe = venv_dir / "Scripts" / "pyinstaller.exe"
    print("Building executable with PyInstaller...")
    run_command([str(pyinstaller_exe), "--clean", "soft-ue-cli.spec"])
    
    print("Build complete!")

if __name__ == "__main__":
    main()
