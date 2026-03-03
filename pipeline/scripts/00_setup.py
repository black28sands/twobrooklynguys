"""Step 0: Environment setup — validate tools and install dependencies."""

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

# Directories to create under C:\Podcast
REQUIRED_DIRS = [
    "pipeline/scripts/helpers",
    "pipeline/templates",
    "episodes",
    "assets/artwork",
    "assets/audio",
    "assets/fonts",
    "website",
]


def check_python() -> tuple[bool, str]:
    v = sys.version_info
    ok = v.major >= 3 and v.minor >= 10
    return ok, f"{v.major}.{v.minor}.{v.micro}"


def check_command(cmd: str) -> tuple[bool, str]:
    path = shutil.which(cmd)
    if not path:
        return False, "not found"
    try:
        result = subprocess.run(
            [cmd, "--version"], capture_output=True, text=True, check=False
        )
        version = result.stdout.strip().split("\n")[0] if result.stdout else result.stderr.strip().split("\n")[0]
        return True, version[:80]
    except Exception as e:
        return False, str(e)


def check_python_package(package: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Version:"):
                    return True, line.split(":")[1].strip()
            return True, "installed"
        return False, "not installed"
    except Exception as e:
        return False, str(e)


def create_directories(root: Path) -> None:
    for d in REQUIRED_DIRS:
        path = root / d
        path.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]Created directory structure under {root}[/green]")


def run_check() -> bool:
    """Run all checks and print a status table. Returns True if all pass."""
    console.print("\n[bold]Two Brooklyn Guys Podcast Pipeline — Environment Check[/bold]\n")

    all_ok = True

    # System tools
    table = Table(title="System Tools")
    table.add_column("Tool", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details")

    checks = [
        ("Python 3.10+", check_python()),
        ("ffmpeg", check_command("ffmpeg")),
        ("ffprobe", check_command("ffprobe")),
        ("git", check_command("git")),
        ("node", check_command("node")),
        ("npm", check_command("npm")),
        ("gh (GitHub CLI)", check_command("gh")),
    ]

    for name, (ok, detail) in checks:
        status = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
        if not ok:
            all_ok = False
        table.add_row(name, status, detail)

    console.print(table)

    # Python packages
    pkg_table = Table(title="Python Packages")
    pkg_table.add_column("Package", style="cyan")
    pkg_table.add_column("Status", style="bold")
    pkg_table.add_column("Version")

    req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
    packages = []
    if req_path.exists():
        with open(req_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    pkg_name = line.split(">=")[0].split("==")[0].split("<")[0].strip()
                    packages.append(pkg_name)

    for pkg in packages:
        ok, version = check_python_package(pkg)
        status = "[green]PASS[/green]" if ok else "[yellow]MISSING[/yellow]"
        if not ok:
            all_ok = False
        pkg_table.add_row(pkg, status, version)

    console.print(pkg_table)

    # Directory structure
    root = Path(__file__).resolve().parent.parent.parent
    dir_table = Table(title="Directory Structure")
    dir_table.add_column("Directory", style="cyan")
    dir_table.add_column("Status", style="bold")

    for d in REQUIRED_DIRS:
        path = root / d
        exists = path.exists()
        status = "[green]EXISTS[/green]" if exists else "[yellow]MISSING[/yellow]"
        dir_table.add_row(str(d), status)

    console.print(dir_table)

    if all_ok:
        console.print("\n[bold green]All checks passed! Environment is ready.[/bold green]\n")
    else:
        console.print("\n[bold red]Some checks failed. Fix the issues above before proceeding.[/bold red]")
        console.print("\nTo install missing Python packages:")
        console.print(f"  pip install -r {req_path}")
        console.print("\nTo install FFmpeg:")
        console.print("  winget install Gyan.FFmpeg")
        console.print("\nTo install GitHub CLI:")
        console.print("  winget install GitHub.cli\n")

    return all_ok


def run_install() -> None:
    """Install Python dependencies and create directory structure."""
    root = Path(__file__).resolve().parent.parent.parent

    console.print("[bold]Setting up Two Brooklyn Guys Podcast Pipeline...[/bold]\n")

    # Create directories
    create_directories(root)

    # Install Python packages
    req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
    if req_path.exists():
        console.print("Installing Python dependencies...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
            check=True,
        )
        console.print("[green]Python dependencies installed.[/green]\n")

    # Run the check
    run_check()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 0: Environment setup")
    parser.add_argument(
        "--check", action="store_true",
        help="Only run checks, don't install anything",
    )
    args = parser.parse_args()

    if args.check:
        ok = run_check()
        sys.exit(0 if ok else 1)
    else:
        run_install()
