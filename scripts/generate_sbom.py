import argparse
import importlib.metadata
import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]


def package_ref(name: str, version: str) -> str:
    return f"pkg:pypi/{name.lower().replace('_', '-')}@{version}"


def license_entry(value: str) -> dict:
    if " AND " in value or " OR " in value:
        return {"expression": value}
    if re.fullmatch(r"[A-Za-z0-9.+-]+", value):
        return {"license": {"id": value}}
    return {"license": {"name": value}}


def license_files(name: str, version: str, verify_installed: bool = False) -> list[str]:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        if verify_installed:
            raise RuntimeError(f"locked package is not installed: {name}=={version}")
        return []
    if distribution.version != version:
        if verify_installed:
            raise RuntimeError(
                f"installed package does not match lock: {name}=={distribution.version}, expected {version}"
            )
        return []
    texts = []
    for file in distribution.files or ():
        filename = Path(str(file)).name.lower()
        if not filename.startswith(("license", "copying", "notice")):
            continue
        try:
            text = distribution.locate_file(file).read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, UnicodeError):
            continue
        if text and text not in texts:
            texts.append(text)
    return texts


def validate_sbom(path: Path) -> None:
    try:
        from cyclonedx.schema import OutputFormat, SchemaVersion
        from cyclonedx.validation import make_schemabased_validator
    except ImportError as exc:
        raise RuntimeError("install cyclonedx-python-lib[validation] to validate the SBOM") from exc
    validator = make_schemabased_validator(OutputFormat.JSON, SchemaVersion.V1_6)
    errors = validator.validate_str(path.read_text(encoding="utf-8"), all_errors=True)
    if errors:
        raise ValueError(f"CycloneDX 1.6 schema validation failed: {errors}")


def generate(output_dir: Path, version: str = "dev", verify_installed: bool = False) -> None:
    lock = json.loads((ROOT / "release-lock.json").read_text(encoding="utf-8"))
    requirements = {}
    for line in (ROOT / lock["python"]["requirements"]).read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ]+)", line)
        if match:
            requirements[match.group(1).lower().replace("_", "-")] = match.group(2)
    inventory = {item["name"].lower().replace("_", "-"): item["version"] for item in lock["python_packages"]}
    if requirements != inventory:
        raise ValueError("release-lock.json Python inventory does not match requirements-release.txt")

    components = []
    for package in lock["python_packages"]:
        ref = package_ref(package["name"], package["version"])
        components.append({
            "bom-ref": ref,
            "type": "library",
            "name": package["name"],
            "version": package["version"],
            "purl": ref,
            "licenses": [license_entry(package["license"])],
            "externalReferences": [{"type": "vcs", "url": package["source"]}],
        })
    for model in lock["translation_models"]["packages"]:
        ref = f"pkg:generic/argos/{model['name']}@{model['version']}"
        components.append({
            "bom-ref": ref,
            "type": "machine-learning-model",
            "name": model["name"],
            "version": model["version"],
            "licenses": [{"license": {"id": model["license"]}}],
            "hashes": [{"alg": "SHA-256", "content": model["sha256"]}],
            "externalReferences": [{"type": "distribution", "url": model["url"]}],
        })

    app_ref = "pkg:github/Honguan/Real-time-audio"
    sbom = {
        "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "bom-ref": app_ref,
                "type": "application",
                "name": "realtime-audio-translator",
                "version": version.removeprefix("v"),
                "licenses": [{"license": {"id": "MIT"}}],
                "externalReferences": [{"type": "vcs", "url": "https://github.com/Honguan/Real-time-audio"}],
            }
        },
        "components": components,
        "dependencies": [{"ref": app_ref, "dependsOn": [item["bom-ref"] for item in components]}],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "SBOM.cdx.json").write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    notices = ["Third-party package licenses", "============================", ""]
    for package in lock["python_packages"]:
        notices.extend((
            f"{package['name']} {package['version']}",
            f"License: {package['license']}",
            f"Source: {package['source']}",
        ))
        texts = license_files(package["name"], package["version"], verify_installed)
        notices.extend(texts or ("License text was not present in the installed distribution; see the source above.",))
        notices.extend(("", "-" * 72, ""))
    (output_dir / "THIRD_PARTY_LICENSES.txt").write_text("\n".join(notices), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--version", default="dev")
    parser.add_argument("--verify-installed", action="store_true")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    generate(args.output_dir, args.version, args.verify_installed)
    if args.validate:
        validate_sbom(args.output_dir / "SBOM.cdx.json")
