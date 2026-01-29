"""
DailyMed Drug Search Script

Searches for drugs by name pattern, filters by inactive ingredients (excipients),
and retrieves manufacturer and available doses.

DailyMed API Documentation: https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
"""

import re
import requests
import sys
import time
from typing import Optional


BASE_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
NADAC_API_URL = "https://data.medicaid.gov/api/1/datastore/query/99315a95-37ac-4eee-946a-3c523b4c481e/0"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "DailyMed-Search-Script/1.0"
}


def print_progress(message: str, end: str = "\n", flush: bool = True):
    """Print progress message with immediate flush."""
    print(message, end=end, flush=flush)


def print_status(current: int, total: int, message: str = "", width: int = 30):
    """Print a progress bar."""
    percent = current / total if total > 0 else 0
    filled = int(width * percent)
    bar = "#" * filled + "-" * (width - filled)
    status = f"\r  [{bar}] {current}/{total}"
    if message:
        status += f" - {message[:40]}"
    print(status, end="", flush=True)
    if current >= total:
        print()  # Newline when complete


def search_drugs(drug_name: str, page_size: int = 100, verbose: bool = True) -> list[dict]:
    """
    Search for drugs matching a name pattern.

    Args:
        drug_name: Drug name or partial name to search for
        page_size: Number of results per page (max 100)
        verbose: Show progress output

    Returns:
        List of matching drug records with setid, title, etc.
    """
    url = f"{BASE_URL}/spls.json"
    params = {
        "drug_name": drug_name,
        "pagesize": page_size
    }

    all_results = []
    page = 1
    total_pages = None

    while True:
        params["page"] = page

        if verbose:
            if total_pages:
                print_progress(f"\r  Fetching page {page}/{total_pages}...", end="")
            else:
                print_progress(f"\r  Fetching page {page}...", end="")

        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = data.get("data", [])
        if not results:
            break

        all_results.extend(results)

        # Check if there are more pages
        metadata = data.get("metadata", {})
        total_pages = metadata.get("total_pages", 1)
        total_elements = metadata.get("total_elements", len(all_results))

        if verbose:
            print_progress(f"\r  Fetching page {page}/{total_pages}... found {len(all_results)}/{total_elements} drugs", end="")

        if page >= total_pages:
            break

        page += 1
        time.sleep(0.5)  # Rate limiting

    if verbose:
        print()  # Newline after progress

    return all_results


def parse_inactive_ingredients_from_xml(xml_text: str) -> list[dict]:
    """Extract inactive ingredients from SPL XML text."""
    ingredients = []
    seen = set()  # Avoid duplicates

    # Method 1: Regex for structured XML format
    # Match: <ingredient classCode="IACT">...<name>INGREDIENT NAME</name>...
    # Using non-greedy match to get each ingredient block
    pattern = r'<ingredient[^>]*classCode="IACT"[^>]*>.*?<name>([^<]+)</name>'
    matches = re.findall(pattern, xml_text, re.DOTALL | re.IGNORECASE)
    for name in matches:
        name = name.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            ingredients.append({"name": name})

    # Method 2: Regex for plain text format
    # Pattern matches text like "Inactive ingredients include X, Y and Z"
    if not ingredients:
        pattern = r'[Ii]nactive\s+ingredients?\s+(?:include|:)\s*([^<]+)'
        match = re.search(pattern, xml_text)
        if match:
            text = match.group(1)
            text = re.sub(r'\s+', ' ', text).strip().rstrip('.')
            parts = re.split(r',\s*|\s+and\s+', text)
            for part in parts:
                part = part.strip()
                if part and len(part) > 1 and part.lower() not in seen:
                    seen.add(part.lower())
                    ingredients.append({"name": part})

    return ingredients


def get_inactive_ingredients(setid: str) -> list[dict]:
    """
    Get only inactive ingredients for a drug (fast, for filtering).

    Args:
        setid: The unique identifier for the drug's SPL document

    Returns:
        List of inactive ingredient dicts
    """
    try:
        url = f"{BASE_URL}/spls/{setid}.xml"
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return parse_inactive_ingredients_from_xml(response.text)
    except requests.RequestException:
        return []


def get_packaging_info(setid: str) -> dict:
    """
    Get packaging/product info (active ingredients, dosage forms).

    Args:
        setid: The unique identifier for the drug's SPL document

    Returns:
        Dict with products and active_ingredient
    """
    try:
        url = f"{BASE_URL}/spls/{setid}/packaging.json"
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        pkg_data = response.json().get("data", {})
        products = pkg_data.get("products", [])

        result = {"products": products}
        if products:
            result["active_ingredient"] = products[0].get("active_ingredients", [])

        return result
    except requests.RequestException:
        return {}


def get_drug_details(setid: str, title: str = "") -> Optional[dict]:
    """
    Get detailed information for a specific drug by its setid.

    Args:
        setid: The unique identifier for the drug's SPL document
        title: Optional title (to extract manufacturer without extra API call)

    Returns:
        Drug details including inactive ingredients, manufacturer, dosage forms
    """
    result = {"data": {}}

    # Extract manufacturer from title if provided (saves an API call)
    if title:
        match = re.search(r'\[([^\]]+)\]', title)
        if match:
            result["data"]["labeler"] = match.group(1)

    # Get packaging info (active ingredients, dosage forms)
    pkg_info = get_packaging_info(setid)
    result["data"]["products"] = pkg_info.get("products", [])
    result["data"]["active_ingredient"] = pkg_info.get("active_ingredient", [])

    # Get inactive ingredients from XML
    result["data"]["inactive_ingredient"] = get_inactive_ingredients(setid)

    return result if result["data"] else None


def get_ndc_info(setid: str) -> list[dict]:
    """
    Get NDC (National Drug Code) information including packaging and doses.

    Args:
        setid: The unique identifier for the drug's SPL document

    Returns:
        List of NDC records with packaging info
    """
    url = f"{BASE_URL}/ndcs.json"
    params = {"setid": setid}

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except requests.RequestException as e:
        print(f"Error fetching NDC info for {setid}: {e}")
        return []


def normalize_ndc(ndc: str) -> str:
    """
    Normalize NDC to 11-digit 5-4-2 format for comparison.

    NDCs come in various formats:
    - 4-4-2 (10 digits) -> add leading zero to labeler
    - 5-3-2 (10 digits) -> add leading zero to product
    - 5-4-1 (10 digits) -> add leading zero to package
    - 5-4-2 (11 digits) -> already normalized

    If NDC has dashes, we use those to determine segment lengths.
    If no dashes, we assume 5-4-2 and just pad to 11.
    """
    if "-" in ndc:
        parts = ndc.split("-")
        if len(parts) == 3:
            labeler = parts[0].zfill(5)
            product = parts[1].zfill(4)
            package = parts[2].zfill(2)
            return labeler + product + package
    # No dashes - just pad to 11 digits
    return ndc.replace("-", "").zfill(11)


def check_ndc_in_nadac(ndc: str) -> bool:
    """
    Check if a single NDC exists in NADAC.

    Args:
        ndc: Normalized 11-digit NDC

    Returns:
        True if found in NADAC
    """
    params = {
        "conditions[0][property]": "ndc",
        "conditions[0][value]": ndc,
        "limit": 1
    }

    try:
        response = requests.get(NADAC_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("count", 0) > 0
    except requests.RequestException:
        return False


def check_ndcs_in_nadac(ndcs: list[str]) -> list[str]:
    """
    Check which NDCs from a list exist in NADAC.

    Args:
        ndcs: List of normalized 11-digit NDCs

    Returns:
        List of NDCs that exist in NADAC
    """
    # Query NADAC for all NDCs at once using OR conditions
    # NADAC API supports multiple conditions
    if not ndcs:
        return []

    # Check each NDC (could batch but API doesn't support OR well)
    found = []
    for ndc in ndcs:
        if check_ndc_in_nadac(ndc):
            found.append(ndc)

    return found


def get_nadac_ndcs(drug_name: str) -> set[str]:
    """
    Get all NDCs for a drug from NADAC (drugs being actively purchased).

    Args:
        drug_name: Drug name to search for

    Returns:
        Set of NDC codes found in NADAC (normalized to 11 digits)
    """
    ndcs = set()
    offset = 0
    limit = 500

    while True:
        params = {
            "conditions[0][property]": "ndc_description",
            "conditions[0][operator]": "LIKE",
            "conditions[0][value]": f"%{drug_name.upper()}%",
            "limit": limit,
            "offset": offset
        }

        try:
            response = requests.get(NADAC_API_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if not results:
                break

            for rec in results:
                ndc = rec.get("ndc", "")
                if ndc:
                    ndcs.add(normalize_ndc(ndc))

            if len(results) < limit:
                break

            offset += limit

        except requests.RequestException:
            break

    return ndcs


def get_dailymed_ndcs(setid: str) -> list[str]:
    """
    Get NDC codes for a drug from DailyMed.

    Args:
        setid: The drug's setid

    Returns:
        List of NDC codes (normalized to 11 digits)
    """
    url = f"{BASE_URL}/spls/{setid}/ndcs.json"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", {})
        ndcs = data.get("ndcs", [])
        return [normalize_ndc(n.get("ndc", "")) for n in ndcs if n.get("ndc")]
    except requests.RequestException:
        return []


def filter_by_availability(
    drugs: list[dict],
    verbose: bool = False
) -> list[dict]:
    """
    Filter drugs to only those with NDCs in NADAC (actively purchased).

    Checks each drug's NDCs directly against NADAC by NDC lookup,
    not by drug name search. This is more accurate when naming
    conventions differ between DailyMed and NADAC.

    Args:
        drugs: List of drug records
        verbose: Print progress information

    Returns:
        Filtered list of drugs with at least one NDC in NADAC
    """
    available = []
    unavailable_count = 0

    for i, drug in enumerate(drugs):
        setid = drug.get("setid")
        title = drug.get("title", "Unknown")

        if verbose:
            short_title = title[:30] + "..." if len(title) > 30 else title
            print_status(i + 1, len(drugs), f"{short_title}")

        # Get this drug's NDCs from DailyMed
        drug_ndcs = get_dailymed_ndcs(setid)

        # Check which of these NDCs exist in NADAC
        if drug_ndcs:
            # Just check first NDC to see if drug is available (faster)
            # If first NDC exists, drug is in supply chain
            if check_ndc_in_nadac(drug_ndcs[0]):
                drug["_available_ndcs"] = drug_ndcs
                available.append(drug)
            else:
                unavailable_count += 1
        else:
            unavailable_count += 1

    if verbose:
        print_progress(f"\n  {unavailable_count} drugs not found in NADAC (not being purchased)")

    return available


def filter_by_excipients(
    drugs: list[dict],
    excluded_excipients: list[str],
    verbose: bool = False
) -> list[dict]:
    """
    Filter drugs to exclude those containing specific inactive ingredients.

    Optimized: Only 1 API call per drug for filtering (XML for inactive ingredients).
    Only fetches packaging info for drugs that pass the filter.

    Args:
        drugs: List of drug records from search
        excluded_excipients: List of excipient names to exclude (case-insensitive)
        verbose: Print progress information

    Returns:
        Filtered list of drugs not containing excluded excipients
    """
    excluded_lower = [e.lower() for e in excluded_excipients]
    passed = []
    excluded_count = 0

    # Phase 1: Filter by inactive ingredients (1 API call each)
    for i, drug in enumerate(drugs):
        setid = drug.get("setid")
        title = drug.get("title", "Unknown")

        if verbose:
            short_title = title[:30] + "..." if len(title) > 30 else title
            print_status(i + 1, len(drugs), f"{short_title} ({len(passed)} kept, {excluded_count} excluded)")

        inactive_ingredients = get_inactive_ingredients(setid)

        # Check if any excluded excipient is present
        has_excluded = False
        for ingredient in inactive_ingredients:
            ingredient_name = ingredient.get("name", "").lower()
            for excluded in excluded_lower:
                if excluded in ingredient_name:
                    has_excluded = True
                    break
            if has_excluded:
                break

        if has_excluded:
            excluded_count += 1
        else:
            drug["_inactive_ingredients"] = inactive_ingredients
            # Extract manufacturer from title (no API call needed)
            match = re.search(r'\[([^\]]+)\]', title)
            drug["_manufacturer"] = match.group(1) if match else "Unknown"
            passed.append(drug)

    # Phase 2: Fetch packaging only for drugs that passed
    if passed:
        if verbose:
            print_progress(f"\n  Fetching dosage info for {len(passed)} drugs...")

        for i, drug in enumerate(passed):
            if verbose:
                print_status(i + 1, len(passed), "")

            pkg_info = get_packaging_info(drug.get("setid"))
            drug["_details"] = {
                "products": pkg_info.get("products", []),
                "active_ingredient": pkg_info.get("active_ingredient", []),
                "inactive_ingredient": drug.get("_inactive_ingredients", []),
                "labeler": drug.get("_manufacturer", "Unknown"),
            }

    return passed


def extract_drug_info(drug: dict) -> dict:
    """
    Extract relevant information from a drug record.

    Args:
        drug: Drug record with details attached

    Returns:
        Dictionary with manufacturer, doses, and other relevant info
    """
    details = drug.get("_details", {})

    # Get manufacturer/labeler info
    manufacturer = details.get("labeler", "Unknown")

    # Get active ingredients and strengths
    active_ingredients = details.get("active_ingredient", [])

    # Get dosage forms
    products = details.get("products", [])
    dosage_info = []
    for product in products:
        dose_form = product.get("dosage_form", "")
        strength = product.get("strength", "")
        route = product.get("route", "")
        if dose_form or strength:
            dosage_info.append({
                "dosage_form": dose_form,
                "strength": strength,
                "route": route
            })

    return {
        "setid": drug.get("setid"),
        "title": drug.get("title"),
        "manufacturer": manufacturer,
        "active_ingredients": active_ingredients,
        "dosage_forms": dosage_info,
        "inactive_ingredients": [
            ing.get("name") for ing in drug.get("_inactive_ingredients", [])
        ]
    }


def search_and_filter(
    drug_name: str,
    excluded_excipients: list[str] = None,
    check_availability: bool = False,
    verbose: bool = True
) -> list[dict]:
    """
    Main function to search for drugs and filter by excipients.

    Args:
        drug_name: Drug name or pattern to search
        excluded_excipients: List of inactive ingredients to exclude
        check_availability: If True, filter to only drugs found in NADAC
        verbose: Print progress information

    Returns:
        List of drug info dictionaries
    """
    if excluded_excipients is None:
        excluded_excipients = []

    print_progress(f"Searching DailyMed for drugs matching: '{drug_name}'...")
    drugs = search_drugs(drug_name, verbose=verbose)
    print_progress(f"Found {len(drugs)} matching drugs in DailyMed")

    if not drugs:
        return []

    if excluded_excipients:
        print_progress(f"\nFiltering out drugs containing: {', '.join(excluded_excipients)}")
        drugs = filter_by_excipients(drugs, excluded_excipients, verbose)
        print_progress(f"{len(drugs)} drugs remaining after filtering")
    else:
        # Fetch details for all drugs (2 API calls each: XML + packaging)
        print_progress("\nFetching drug details...")
        for i, drug in enumerate(drugs):
            setid = drug.get("setid")
            title = drug.get("title", "")

            if verbose:
                short_title = title[:40] + "..." if len(title) > 40 else title
                print_status(i + 1, len(drugs), short_title)

            details = get_drug_details(setid, title)
            if details:
                drug["_details"] = details.get("data", {})
                drug["_inactive_ingredients"] = details.get("data", {}).get(
                    "inactive_ingredient", []
                )

    if not drugs:
        return []

    # Filter by NADAC availability (check each drug's NDCs)
    if check_availability:
        print_progress(f"\nChecking availability in NADAC (by NDC lookup)...")
        drugs = filter_by_availability(drugs, verbose)
        print_progress(f"{len(drugs)} drugs available in NADAC")

        if not drugs:
            return []

    # Extract relevant info
    print_progress("\nProcessing results...")
    results = [extract_drug_info(drug) for drug in drugs]
    print_progress(f"Done! {len(results)} drugs found.")

    return results


def print_results(results: list[dict]):
    """Pretty print the search results."""
    print("\n" + "=" * 80)
    print("SEARCH RESULTS")
    print("=" * 80)

    for i, drug in enumerate(results, 1):
        print(f"\n[{i}] {drug['title']}")
        print(f"    Manufacturer: {drug['manufacturer']}")

        if drug['active_ingredients']:
            ingredients = ", ".join(
                f"{ing.get('name', 'Unknown')} ({ing.get('strength', 'N/A')})"
                for ing in drug['active_ingredients']
            )
            print(f"    Active Ingredients: {ingredients}")

        if drug['dosage_forms']:
            print("    Available Forms:")
            for form in drug['dosage_forms']:
                strength = form.get('strength', 'N/A')
                dosage = form.get('dosage_form', 'N/A')
                route = form.get('route', '')
                print(f"      - {dosage}, {strength}" + (f" ({route})" if route else ""))

        print(f"    SetID: {drug['setid']}")


def save_to_json(results: list[dict], filepath: str):
    """Save results to a JSON file."""
    import json
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to: {filepath}")


def save_to_csv(results: list[dict], filepath: str):
    """Save results to a CSV file."""
    import csv

    if not results:
        print("No results to save.")
        return

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            "Title",
            "Manufacturer",
            "Active Ingredients",
            "Dosage Forms",
            "Strengths",
            "Routes",
            "Inactive Ingredients",
            "SetID"
        ])

        # Data rows
        for drug in results:
            active = "; ".join(
                f"{ing.get('name', '')} ({ing.get('strength', '')})"
                for ing in drug.get("active_ingredients", [])
            )
            forms = "; ".join(
                f.get("dosage_form", "") for f in drug.get("dosage_forms", [])
            )
            strengths = "; ".join(
                f.get("strength", "") for f in drug.get("dosage_forms", [])
            )
            routes = "; ".join(
                f.get("route", "") for f in drug.get("dosage_forms", [])
            )
            inactive = "; ".join(drug.get("inactive_ingredients", []))

            writer.writerow([
                drug.get("title", ""),
                drug.get("manufacturer", ""),
                active,
                forms,
                strengths,
                routes,
                inactive,
                drug.get("setid", "")
            ])

    print(f"Results saved to: {filepath}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Search DailyMed for drugs and filter by inactive ingredients.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s acetaminophen
  %(prog)s lisinopril --exclude lactose gluten
  %(prog)s ibuprofen --exclude "corn starch" lactose --output results.json
  %(prog)s aspirin --exclude lactose --csv results.csv --quiet
  %(prog)s fluoxetine --exclude "propylene glycol" --available
        """
    )

    parser.add_argument(
        "drug_name",
        help="Drug name or pattern to search for"
    )

    parser.add_argument(
        "-e", "--exclude",
        nargs="+",
        default=[],
        metavar="EXCIPIENT",
        help="Inactive ingredients (excipients) to exclude"
    )

    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="Save results to JSON file"
    )

    parser.add_argument(
        "--csv",
        metavar="FILE",
        help="Save results to CSV file"
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress progress output"
    )

    parser.add_argument(
        "-n", "--no-print",
        action="store_true",
        help="Don't print results to console (use with --output or --csv)"
    )

    parser.add_argument(
        "-a", "--available",
        action="store_true",
        help="Only show drugs found in NADAC (actively being purchased by pharmacies)"
    )

    args = parser.parse_args()

    # Run search
    results = search_and_filter(
        drug_name=args.drug_name,
        excluded_excipients=args.exclude,
        check_availability=args.available,
        verbose=not args.quiet
    )

    # Output results
    if not args.no_print:
        print_results(results)

    if args.output:
        save_to_json(results, args.output)

    if args.csv:
        save_to_csv(results, args.csv)

    if not results:
        print_progress("\nNo drugs found matching your criteria.")


if __name__ == "__main__":
    main()
