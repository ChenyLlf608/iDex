"""Render the Core ML conversion report as markdown for a CI summary."""

import json
import pathlib
import sys


def megabytes(value) -> str:
    return "—" if value in (None, 0) else f"{value / 1048576:.1f} MB"


def main() -> None:
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    print("## Full-dex candidate as Core ML")
    print()
    if path is None or not path.is_file():
        print("No report was produced; the conversion step did not finish.")
        return

    report = json.loads(path.read_text(encoding="utf-8"))
    baseline = report.get("baseline", {})
    print(f"Candidate: `{report['repository']}` at `{report['revision'][:12]}`, "
          f"{report['classes']} classes")
    print()
    print(f"Shipped baseline: {baseline.get('name')}, {megabytes(baseline.get('compiledBytes'))}, "
          f"{baseline.get('correctTop1')}/{baseline.get('count')} correct, "
          f"{baseline.get('accepted')} accepted.")
    print()
    print("| variant | package | compiled | correct top-1 | accepted | accepted but wrong | median confidence |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for entry in report.get("variants", []):
        if entry.get("error"):
            print(f"| {entry['variant']} | — | — | failed | — | — | — |")
            continue
        print(f"| {entry['variant']} | {megabytes(entry.get('packageBytes'))} | "
              f"{megabytes(entry.get('compiledBytes'))} | "
              f"{entry.get('correctTop1')}/{entry.get('count')} | {entry.get('accepted')} | "
              f"{entry.get('acceptedWrong')} | {entry.get('medianConfidence')} |")
    print()

    for entry in report.get("variants", []):
        if entry.get("error"):
            print(f"### `{entry['variant']}` failed")
            print()
            print(f"```\n{entry['error']}\n```")
            print()

    for entry in report.get("variants", []):
        misses = entry.get("misses") or []
        if misses:
            print(f"### Misses ({entry['variant']}, {len(misses)})")
            print()
            for miss in misses[:40]:
                print(f"- #{miss['expectedID']} {miss['expected']} → "
                      f"**{miss['topLabel']}** ({miss['confidence']:.3f})")
            print()


if __name__ == "__main__":
    main()
