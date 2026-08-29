import argparse
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
VERSION_RE = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def validate_release(
    version: str,
    package_version: str,
    release_notes: str,
    tag_commit: str,
    head_commit: str,
    github_sha: str,
) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ValueError("release version must be a semantic tag such as v0.1.35")
    if package_version != version.removeprefix("v"):
        raise ValueError(f"package version {package_version} does not match release tag {version}")
    if f"## {version}" not in release_notes.splitlines():
        raise ValueError(f"release notes do not contain a {version} section")
    if tag_commit != head_commit:
        raise ValueError(f"tag {version} points to {tag_commit}, but checkout is {head_commit}")
    if tag_commit != github_sha:
        raise ValueError(f"tag {version} points to {tag_commit}, but GITHUB_SHA is {github_sha}")


def git_commit(ref: str) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().lower()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"required release tag does not exist: {ref}") from exc


def verify_release(version: str, github_sha: str) -> None:
    init_text = (ROOT / "realtime_audio_translator" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
    if not match:
        raise ValueError("cannot read realtime_audio_translator.__version__")
    validate_release(
        version,
        match.group(1),
        (ROOT / "docs" / "RELEASE_NOTES.md").read_text(encoding="utf-8"),
        git_commit(f"refs/tags/{version}^{{commit}}"),
        git_commit("HEAD"),
        github_sha.lower(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--github-sha", required=True)
    args = parser.parse_args()
    verify_release(args.version, args.github_sha)
    print(f"Verified release {args.version} at {args.github_sha}")
