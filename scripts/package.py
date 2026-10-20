"""Build a source-only distribution; never include sessions, state, exports or credentials."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
output = root / "dist" / "TelegramCommunityMigrator.zip"
output.parent.mkdir(exist_ok=True)
files = [root / name for name in ("pyproject.toml", ".gitignore", ".env.example", "README.md", "VERIFICATION.md")]
for folder, pattern in (("src", "*.py"), ("tests", "*.py"), ("examples", "*.csv"), ("scripts", "*.py")):
    files.extend(sorted((root / folder).rglob(pattern)))
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for file in files:
        archive.write(file, "TelegramCommunityMigrator/" + file.relative_to(root).as_posix())
print(f"Created {output.name}: {len(files)} source/documentation/example files")
