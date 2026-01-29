"""Check NADAC availability for drugs in results.csv"""

import csv
from dailymed_search import get_dailymed_ndcs, check_ndc_in_nadac, print_status, print_progress


def check_csv_availability(input_file: str, output_file: str = None):
    """
    Check NADAC availability for drugs in a CSV file.

    Args:
        input_file: Path to input CSV (must have SetID column)
        output_file: Optional path to output CSV with availability column
    """
    # Read input CSV
    with open(input_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        drugs = list(reader)

    print_progress(f"Checking NADAC availability for {len(drugs)} drugs...")

    available = []
    unavailable = []

    for i, drug in enumerate(drugs):
        setid = drug.get("SetID", "")
        title = drug.get("Title", "Unknown")
        manufacturer = drug.get("Manufacturer", "Unknown")

        short_title = title[:35] + "..." if len(title) > 35 else title
        print_status(i + 1, len(drugs), short_title)

        if not setid:
            drug["Available"] = "NO (no SetID)"
            unavailable.append(drug)
            continue

        # Get NDCs from DailyMed
        ndcs = get_dailymed_ndcs(setid)

        if not ndcs:
            drug["Available"] = "NO (no NDCs)"
            unavailable.append(drug)
            continue

        # Check first NDC in NADAC
        if check_ndc_in_nadac(ndcs[0]):
            drug["Available"] = "YES"
            drug["NDC"] = ndcs[0]
            available.append(drug)
        else:
            drug["Available"] = "NO"
            unavailable.append(drug)

    print_progress(f"\n\nResults: {len(available)} available, {len(unavailable)} not available")

    # Print available drugs grouped by manufacturer
    print_progress("\n" + "=" * 70)
    print_progress("AVAILABLE DRUGS (found in NADAC)")
    print_progress("=" * 70)

    # Group by manufacturer
    by_manufacturer = {}
    for drug in available:
        mfr = drug.get("Manufacturer", "Unknown")
        if mfr not in by_manufacturer:
            by_manufacturer[mfr] = []
        by_manufacturer[mfr].append(drug)

    for mfr in sorted(by_manufacturer.keys()):
        print_progress(f"\n{mfr}:")
        for drug in by_manufacturer[mfr]:
            title = drug.get("Title", "")
            active = drug.get("Active Ingredients", "")
            print_progress(f"  - {active}")

    # Save to CSV if output file specified
    if output_file:
        fieldnames = list(drugs[0].keys()) + ["Available", "NDC"]
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(available + unavailable)
        print_progress(f"\nSaved to: {output_file}")

    return available, unavailable


if __name__ == "__main__":
    import sys

    input_file = sys.argv[1] if len(sys.argv) > 1 else "results.csv"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "results_with_availability.csv"

    check_csv_availability(input_file, output_file)
