"""Render sprite-verification reports as the markdown a CI summary expects."""

import json
import pathlib
import sys

# From scripts/model/sprite-verification.json, for a side-by-side read.
BASELINE = "Shipped 151-class model: **151/151 correct**, 146 accepted."


def main() -> None:
    paths = [pathlib.Path(argument) for argument in sys.argv[1:]]
    reports = []
    for path in paths:
        if path.is_file():
            reports.append(json.loads(path.read_text(encoding="utf-8")))

    print("## Full-dex candidate vs the 151 bundled sprites")
    print()
    if not reports:
        print("No report was produced; the verification step did not finish.")
        return

    print(f"Candidate: `{reports[0]['repository']}` at `{reports[0]['revision'][:12]}`")
    print()
    print(BASELINE)
    print()
    print("| resize | classes | correct top-1 | accepted | accepted but wrong | median confidence |")
    print("| --- | --- | --- | --- | --- | --- |")
    for report in reports:
        print(f"| {report['resample']} | {report['classes']} | "
              f"{report['correctTop1']}/{report['count']} | {report['accepted']} | "
              f"{report['acceptedWrong']} | {report['medianConfidence']} |")
    print()

    for report in reports:
        mismatches = report.get("labelAlignmentMismatches") or []
        if mismatches:
            print(f"### Label alignment problems ({report['resample']})")
            print()
            for entry in mismatches[:20]:
                print(f"- #{entry['dex']} `{entry['catalogue']}` — index holds `{entry['labelAtIndex']}`")
            print()

        misses = report.get("misses") or []
        if misses:
            print(f"### Misses ({report['resample']}, {len(misses)})")
            print()
            for entry in misses[:40]:
                print(f"- #{entry['expectedID']} {entry['expected']} → "
                      f"**{entry['topLabel']}** ({entry['confidence']:.3f})")
            print()


if __name__ == "__main__":
    main()
