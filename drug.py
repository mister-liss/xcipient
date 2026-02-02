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

import io
import json
import re
import sys
import xml.etree.ElementTree as ET
import requests
from collections import defaultdict

# Ensure stdout handles unicode (box-drawing chars on Windows)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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

# SPL XML namespace
NS = {"v3": "urn:hl7-org:v3"}


def _normalize_ndc(ndc):
    """Normalize an NDC to 11-digit format."""
    if "-" in ndc:
        p = ndc.split("-")
        if len(p) == 3:
            return p[0].zfill(5) + p[1].zfill(4) + p[2].zfill(2)
    return ndc.replace("-", "").zfill(11)


def _parse_products(xml_text):
    """Parse per-product data from SPL XML subject blocks.

    Returns a list of dicts with keys: form, strength, inactive_ingredients, ndcs.
    Returns empty list if parsing fails (caller should fall back).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    products = []
    # Each <subject> contains one manufacturedProduct (one strength)
    for subj in root.iter(f"{{{NS['v3']}}}subject"):
        prod = subj.find(f".//{{{NS['v3']}}}manufacturedProduct")
        if prod is None:
            continue

        # Form: <formCode displayName="CAPSULE" .../>
        form_el = prod.find(f".//{{{NS['v3']}}}formCode")
        form = form_el.get("displayName", "") if form_el is not None else ""

        # Strength: first <numerator> with a real unit (not "1")
        strength = ""
        for num in prod.iter(f"{{{NS['v3']}}}numerator"):
            unit = num.get("unit", "")
            val = num.get("value", "")
            if unit and unit != "1" and val:
                strength = f"{val} {unit}"
                break

        # Inactive ingredients: <ingredient classCode="IACT"> -> <name>
        ingredients = []
        seen = set()
        for ing in prod.iter(f"{{{NS['v3']}}}ingredient"):
            if ing.get("classCode") != "IACT":
                continue
            name_el = ing.find(f".//{{{NS['v3']}}}name")
            if name_el is not None and name_el.text:
                name = name_el.text.strip()
                if name.lower() not in seen:
                    seen.add(name.lower())
                    ingredients.append(name)

        # NDCs: <code codeSystem="2.16.840.1.113883.6.69" code="..."/>
        # Only keep 3-segment NDCs (with two dashes)
        ndcs = []
        ndc_seen = set()
        for code_el in subj.iter(f"{{{NS['v3']}}}code"):
            if code_el.get("codeSystem") == "2.16.840.1.113883.6.69":
                raw = code_el.get("code", "")
                if raw.count("-") == 2 and raw not in ndc_seen:
                    ndc_seen.add(raw)
                    ndcs.append(_normalize_ndc(raw))

        products.append({
            "form": form,
            "strength": strength,
            "inactive_ingredients": ingredients,
            "ndcs": ndcs,
        })

    return products


def cmd_ingredients(args):
    """Add inactive ingredients to drugs, exploded per product."""
    import argparse
    parser = argparse.ArgumentParser(prog="drug ingredients")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args(args)

    drugs = json.load(sys.stdin)
    output = []

    for i, drug in enumerate(drugs):
        if opts.verbose:
            print(f"[{i+1}/{len(drugs)}] Fetching ingredients for {drug.get('title', '')[:40]}...",
                  file=sys.stderr)

        try:
            resp = requests.get(f"{BASE_URL}/spls/{drug['setid']}.xml", headers=HEADERS, timeout=30)
            resp.raise_for_status()
            products = _parse_products(resp.text)
        except Exception:
            products = []

        if products:
            for idx, prod in enumerate(products):
                record = {
                    "setid": drug["setid"],
                    "title": drug.get("title", ""),
                    "manufacturer": drug.get("manufacturer", ""),
                    "product_index": idx,
                    "form": prod["form"],
                    "strength": prod["strength"],
                    "inactive_ingredients": prod["inactive_ingredients"],
                    "ndcs": prod["ndcs"],
                }
                output.append(record)
        else:
            # Fallback: flat parse like before
            try:
                pattern = r'<ingredient[^>]*classCode="IACT"[^>]*>.*?<name>([^<]+)</name>'
                matches = re.findall(pattern, resp.text, re.DOTALL | re.IGNORECASE)
                ingredients = list(set(m.strip() for m in matches))
            except Exception:
                ingredients = []
            record = dict(drug)
            record["product_index"] = 0
            record["form"] = ""
            record["strength"] = ""
            record["inactive_ingredients"] = ingredients
            record["ndcs"] = []
            output.append(record)

    if opts.verbose:
        print(f"Exploded {len(drugs)} drugs into {len(output)} products", file=sys.stderr)

    json.dump(output, sys.stdout, indent=2)


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

    drugs = json.load(sys.stdin)

    for i, drug in enumerate(drugs):
        # Skip if NDCs already populated (e.g. by ingredients step)
        if drug.get("ndcs"):
            if opts.verbose:
                print(f"[{i+1}/{len(drugs)}] NDCs already present, skipping", file=sys.stderr)
            continue

        if opts.verbose:
            print(f"[{i+1}/{len(drugs)}] Fetching NDCs...", file=sys.stderr)
        try:
            resp = requests.get(f"{BASE_URL}/spls/{drug['setid']}/ndcs.json", headers=HEADERS, timeout=30)
            ndcs = resp.json().get("data", {}).get("ndcs", [])
            drug["ndcs"] = [_normalize_ndc(n["ndc"]) for n in ndcs if n.get("ndc")]
        except Exception:
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

def _short_form(form):
    """Abbreviate dosage form name."""
    form_upper = form.upper()
    if "CAPSULE" in form_upper:
        return "cap"
    if "TABLET" in form_upper:
        return "tab"
    if "SOLUTION" in form_upper or "LIQUID" in form_upper:
        return "liq"
    if "INJECTION" in form_upper:
        return "inj"
    if form:
        return form.lower()[:3]
    return "?"


def _short_strength(strength):
    """Abbreviate strength like '10 mg' -> '10mg'."""
    if not strength:
        return ""
    return strength.replace(" ", "")


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
        print("manufacturer,title,form,strength,available,ndcs,inactive_ingredients")
        for d in drugs:
            form = _short_form(d.get("form", "")) if d.get("form") else _short_form(d.get("title", ""))
            strength = d.get("strength", "")
            avail = "yes" if d.get("nadac_available") else "no"
            ndcs = ";".join(d.get("ndcs", [])[:3])
            ings = ";".join(d.get("inactive_ingredients", []))
            print(f'"{d.get("manufacturer","")}","{d.get("title","")}",{form},{strength},{avail},"{ndcs}","{ings}"')
        return

    # Summary format - group by (manufacturer, setid)
    by_mfr = {}
    mfr_order = []
    for d in drugs:
        mfr = d.get("manufacturer", "Unknown")
        if mfr not in by_mfr:
            mfr_order.append(mfr)
            by_mfr[mfr] = {"by_form": defaultdict(set), "available": False}

        form = d.get("form", "")
        short = _short_form(form) if form else _short_form(d.get("title", ""))
        strength = _short_strength(d.get("strength", ""))
        if strength:
            by_mfr[mfr]["by_form"][short].add(strength)
        else:
            by_mfr[mfr]["by_form"][short]  # ensure key exists

        if d.get("nadac_available"):
            by_mfr[mfr]["available"] = True

    def _sort_strengths(strengths):
        return sorted(strengths, key=lambda s: float(re.match(r"[\d.]+", s).group()) if re.match(r"[\d.]+", s) else 0)

    # Build rows
    rows = []
    for mfr in sorted(mfr_order):
        info = by_mfr[mfr]
        parts = []
        for form in sorted(info["by_form"]):
            strengths = info["by_form"][form]
            if strengths:
                parts.append(f"{form} {','.join(_sort_strengths(strengths))}")
            else:
                parts.append(form)
        forms_str = ", ".join(parts) or "?"
        avail = "YES" if info["available"] else "-"
        rows.append((mfr, forms_str, avail))

    # Column widths
    w0 = max((len(r[0]) for r in rows), default=12)
    w1 = max((len(r[1]) for r in rows), default=5)
    w2 = max((len(r[2]) for r in rows), default=5)
    w0 = max(w0, len("Manufacturer")) + 2
    w1 = max(w1, len("Forms")) + 2
    w2 = max(w2, len("Avail")) + 2

    top    = f"┌{'─' * w0}┬{'─' * w1}┬{'─' * w2}┐"
    sep    = f"├{'─' * w0}┼{'─' * w1}┼{'─' * w2}┤"
    bottom = f"└{'─' * w0}┴{'─' * w1}┴{'─' * w2}┘"

    def row_str(a, b, c):
        return f"│ {a:<{w0-2}} │ {b:<{w1-2}} │ {c:<{w2-2}} │"

    print(top)
    print(row_str("Manufacturer", "Forms", "Avail"))
    for r in rows:
        print(sep)
        print(row_str(*r))
    print(bottom)


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
