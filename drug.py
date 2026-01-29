#!/usr/bin/env python3
"""
Drug search tool - Unix-style pipeline commands for DailyMed and NADAC.

Usage:
    drug search <name>         Search DailyMed for drugs
    drug ingredients           Add inactive ingredients (stdin -> stdout)
    drug filter <excipient>    Filter out drugs with excipient
    drug ndcs                  Add NDC codes
    drug nadac                 Check NADAC availability
    drug fmt                   Format output

Examples:
    drug search fluoxetine | drug ingredients | drug filter "propylene glycol" | drug fmt
    drug search fluoxetine -n 10 | drug ingredients | drug fmt -f csv
"""

import json
import re
import sys
import requests
from collections import defaultdict

BASE_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
NADAC_API = "https://data.medicaid.gov/api/1/datastore/query/99315a95-37ac-4eee-946a-3c523b4c481e/0"
HEADERS = {"Accept": "application/json"}


# ============ SEARCH ============

def cmd_search(args):
    """Search DailyMed for drugs."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug search")
    parser.add_argument("drug_name", help="Drug name to search")
    parser.add_argument("-n", "--limit", type=int, default=0, help="Limit results")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args(args)

    results = []
    page = 1
    while True:
        if opts.verbose:
            print(f"Fetching page {page}...", file=sys.stderr)

        resp = requests.get(f"{BASE_URL}/spls.json",
                          params={"drug_name": opts.drug_name, "page": page, "pagesize": 100},
                          headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for drug in data.get("data", []):
            title = drug.get("title", "")
            match = re.search(r'\[([^\]]+)\]', title)
            results.append({
                "setid": drug.get("setid"),
                "title": title,
                "manufacturer": match.group(1) if match else "Unknown"
            })
            if opts.limit and len(results) >= opts.limit:
                break

        if opts.limit and len(results) >= opts.limit:
            break
        if page >= data.get("metadata", {}).get("total_pages", 1):
            break
        page += 1

    if opts.verbose:
        print(f"Found {len(results)} drugs", file=sys.stderr)

    json.dump(results, sys.stdout, indent=2)


# ============ INGREDIENTS ============

def cmd_ingredients(args):
    """Add inactive ingredients to drugs."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug ingredients")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args(args)

    drugs = json.load(sys.stdin)

    for i, drug in enumerate(drugs):
        if opts.verbose:
            print(f"[{i+1}/{len(drugs)}] Fetching ingredients...", file=sys.stderr)

        try:
            resp = requests.get(f"{BASE_URL}/spls/{drug['setid']}.xml", headers=HEADERS, timeout=30)
            pattern = r'<ingredient[^>]*classCode="IACT"[^>]*>.*?<name>([^<]+)</name>'
            matches = re.findall(pattern, resp.text, re.DOTALL | re.IGNORECASE)
            drug["inactive_ingredients"] = list(set(m.strip() for m in matches))
        except:
            drug["inactive_ingredients"] = []

    json.dump(drugs, sys.stdout, indent=2)


# ============ FILTER ============

def cmd_filter(args):
    """Filter out drugs containing excipient."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug filter")
    parser.add_argument("excipient", nargs="+", help="Excipients to exclude")
    parser.add_argument("--keep", action="store_true", help="Invert: keep matches")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args(args)

    drugs = json.load(sys.stdin)
    results = []

    for drug in drugs:
        ingredients = " ".join(drug.get("inactive_ingredients", [])).lower()
        has_excipient = any(e.lower() in ingredients for e in opts.excipient)

        if opts.keep:
            if has_excipient:
                results.append(drug)
        else:
            if not has_excipient:
                results.append(drug)

    if opts.verbose:
        print(f"Kept {len(results)}/{len(drugs)}", file=sys.stderr)

    json.dump(results, sys.stdout, indent=2)


# ============ NDCS ============

def cmd_ndcs(args):
    """Add NDC codes to drugs."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug ndcs")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args(args)

    def normalize(ndc):
        if "-" in ndc:
            p = ndc.split("-")
            if len(p) == 3:
                return p[0].zfill(5) + p[1].zfill(4) + p[2].zfill(2)
        return ndc.replace("-", "").zfill(11)

    drugs = json.load(sys.stdin)

    for i, drug in enumerate(drugs):
        if opts.verbose:
            print(f"[{i+1}/{len(drugs)}] Fetching NDCs...", file=sys.stderr)
        try:
            resp = requests.get(f"{BASE_URL}/spls/{drug['setid']}/ndcs.json", headers=HEADERS, timeout=30)
            ndcs = resp.json().get("data", {}).get("ndcs", [])
            drug["ndcs"] = [normalize(n["ndc"]) for n in ndcs if n.get("ndc")]
        except:
            drug["ndcs"] = []

    json.dump(drugs, sys.stdout, indent=2)


# ============ NADAC ============

def cmd_nadac(args):
    """Check NADAC availability."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug nadac")
    parser.add_argument("--filter", action="store_true", help="Only output available drugs")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args(args)

    def check_ndc(ndc):
        try:
            resp = requests.get(NADAC_API, params={
                "conditions[0][property]": "ndc",
                "conditions[0][value]": ndc,
                "limit": 1
            }, timeout=10)
            return resp.json().get("count", 0) > 0
        except:
            return False

    drugs = json.load(sys.stdin)
    results = []

    for i, drug in enumerate(drugs):
        if opts.verbose:
            print(f"[{i+1}/{len(drugs)}] Checking NADAC...", file=sys.stderr)

        ndcs = drug.get("ndcs", [])
        drug["nadac_available"] = bool(ndcs) and check_ndc(ndcs[0])

        if not opts.filter or drug["nadac_available"]:
            results.append(drug)

    if opts.verbose:
        avail = sum(1 for d in results if d.get("nadac_available"))
        print(f"Available: {avail}/{len(drugs)}", file=sys.stderr)

    json.dump(results, sys.stdout, indent=2)


# ============ FORMAT ============

def cmd_fmt(args):
    """Format drug data for display."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug fmt")
    parser.add_argument("-f", "--format", choices=["summary", "table", "csv", "json"], default="summary")
    opts = parser.parse_args(args)

    drugs = json.load(sys.stdin)

    if opts.format == "json":
        json.dump(drugs, sys.stdout, indent=2)
        return

    if opts.format == "csv":
        print("manufacturer,title,form,available,ndcs,inactive_ingredients")
        for d in drugs:
            form = "cap" if "CAPSULE" in d.get("title","").upper() else "tab" if "TABLET" in d.get("title","").upper() else "liq" if "SOLUTION" in d.get("title","").upper() or "LIQUID" in d.get("title","").upper() else "?"
            avail = "yes" if d.get("nadac_available") else "no"
            ndcs = ";".join(d.get("ndcs", [])[:3])
            ings = ";".join(d.get("inactive_ingredients", []))
            print(f'"{d.get("manufacturer","")}","{d.get("title","")}",{form},{avail},"{ndcs}","{ings}"')
        return

    # Summary format - group by manufacturer
    by_mfr = defaultdict(lambda: {"forms": set(), "available": False})
    for d in drugs:
        mfr = d.get("manufacturer", "Unknown")
        title = d.get("title", "").upper()

        if "CAPSULE" in title:
            by_mfr[mfr]["forms"].add("cap")
        elif "TABLET" in title:
            by_mfr[mfr]["forms"].add("tab")
        elif "SOLUTION" in title or "LIQUID" in title:
            by_mfr[mfr]["forms"].add("liq")

        if d.get("nadac_available"):
            by_mfr[mfr]["available"] = True

    print(f"{'Manufacturer':<45} {'Forms':<12} {'Avail':<6}")
    print("-" * 65)
    for mfr in sorted(by_mfr.keys()):
        info = by_mfr[mfr]
        forms = ",".join(sorted(info["forms"])) or "?"
        avail = "YES" if info["available"] else "-"
        print(f"{mfr:<45} {forms:<12} {avail:<6}")


# ============ MAIN ============

COMMANDS = {
    "search": cmd_search,
    "ingredients": cmd_ingredients,
    "filter": cmd_filter,
    "ndcs": cmd_ndcs,
    "nadac": cmd_nadac,
    "fmt": cmd_fmt,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(f"Available: {', '.join(COMMANDS.keys())}", file=sys.stderr)
        sys.exit(1)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
