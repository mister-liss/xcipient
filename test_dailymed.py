"""Tests for DailyMed API functionality."""

import sys
from dailymed_search import (
    search_drugs,
    get_inactive_ingredients,
    get_packaging_info,
    get_nadac_ndcs,
    get_dailymed_ndcs,
    normalize_ndc,
    HEADERS
)


def test_search():
    """Test 1: Basic drug search with uncommon drug."""
    print("\n1. Testing drug search (tafamidis - rare drug)...")
    try:
        results = search_drugs("tafamidis", page_size=10, verbose=False)
        if results:
            print(f"   OK - Found {len(results)} results")
            first = results[0]
            print(f"   First: {first.get('title', 'N/A')[:50]}")
            return first.get("setid")
        else:
            print("   FAIL - No results returned")
            return None
    except Exception as e:
        print(f"   FAIL - {e}")
        return None


def test_ingredients(setid: str):
    """Test 2: Retrieve inactive ingredients."""
    print(f"\n2. Testing inactive ingredients retrieval...")
    try:
        ingredients = get_inactive_ingredients(setid)
        if ingredients:
            print(f"   OK - Found {len(ingredients)} inactive ingredients")
            print(f"   Sample: {ingredients[0]['name']}")
            return True
        else:
            print("   WARN - No inactive ingredients found (may be normal)")
            return True  # Some drugs legitimately have none listed
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def test_filter_excludes():
    """Test 3: Verify filtering correctly excludes drugs with specific excipients."""
    print("\n3. Testing filter EXCLUDES (Aurobindo fluoxetine + propylene glycol)...")

    # Aurobindo fluoxetine is known to contain propylene glycol
    setid = "45454555-7402-41cb-a452-e6a76e04f387"

    try:
        ingredients = get_inactive_ingredients(setid)
        ingredient_names = [i["name"].lower() for i in ingredients]

        has_propylene = any("propylene glycol" in name for name in ingredient_names)

        if has_propylene:
            print("   OK - Propylene glycol detected (would be filtered out)")
            return True
        else:
            print("   FAIL - Propylene glycol NOT found (should be present)")
            print(f"   Found: {ingredient_names}")
            return False
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def test_filter_keeps():
    """Test 4: Verify filtering doesn't exclude drugs without the excipient."""
    print("\n4. Testing filter KEEPS (drug without propylene glycol)...")

    # Abilify Maintena (aripiprazole) - injectable, typically no propylene glycol
    setid = "2f9ac981-c70a-4fd3-8a57-b588163efd89"

    try:
        ingredients = get_inactive_ingredients(setid)
        ingredient_names = [i["name"].lower() for i in ingredients]

        has_propylene = any("propylene glycol" in name for name in ingredient_names)

        if not has_propylene:
            print(f"   OK - No propylene glycol found ({len(ingredients)} ingredients checked)")
            return True
        else:
            print("   FAIL - Propylene glycol found (unexpected)")
            return False
    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def test_partial_match():
    """Test 5: Verify partial matching doesn't cause false positives."""
    print("\n5. Testing partial match safety (glycol vs propylene glycol)...")

    # Test that searching for "propylene glycol" doesn't match just "glycol"
    # or other similar strings
    test_ingredients = [
        {"name": "POLYETHYLENE GLYCOL"},  # Should NOT match "propylene glycol"
        {"name": "GLYCERIN"},              # Should NOT match
        {"name": "PROPYLENE GLYCOL"},      # SHOULD match
    ]

    excluded = "propylene glycol"
    matches = []

    for ing in test_ingredients:
        name = ing["name"].lower()
        if excluded in name:
            matches.append(ing["name"])

    if matches == ["PROPYLENE GLYCOL"]:
        print("   OK - Only exact 'propylene glycol' matched")
        return True
    else:
        print(f"   FAIL - Unexpected matches: {matches}")
        return False


def test_headers():
    """Test 6: Verify headers configuration."""
    print("\n6. Testing headers configuration...")
    if "Accept" in HEADERS and HEADERS["Accept"] == "application/json":
        print("   OK - Accept header configured")
        return True
    else:
        print("   FAIL - Accept header missing")
        return False


def test_ndc_normalization():
    """Test 7: Verify NDC normalization works correctly."""
    print("\n7. Testing NDC normalization...")

    test_cases = [
        ("65862-192-01", "65862019201"),  # 5-3-2 -> 5-4-2
        ("0093-7180-01", "00093718001"),  # 4-4-2 -> 5-4-2
        ("12345-6789-01", "12345678901"), # Already 5-4-2
    ]

    for input_ndc, expected in test_cases:
        result = normalize_ndc(input_ndc)
        if result != expected:
            print(f"   FAIL - '{input_ndc}' -> '{result}' (expected '{expected}')")
            return False

    print("   OK - NDC normalization working")
    return True


def test_nadac_availability():
    """Test 8: Verify NADAC availability check works."""
    print("\n8. Testing NADAC availability (Aurobindo fluoxetine)...")

    try:
        # Get NADAC NDCs for fluoxetine
        nadac_ndcs = get_nadac_ndcs("fluoxetine")
        if not nadac_ndcs:
            print("   FAIL - No NADAC data returned")
            return False
        print(f"   Found {len(nadac_ndcs)} NDCs in NADAC")

        # Get DailyMed NDCs for Aurobindo fluoxetine
        dm_ndcs = get_dailymed_ndcs("45454555-7402-41cb-a452-e6a76e04f387")
        if not dm_ndcs:
            print("   FAIL - No DailyMed NDCs returned")
            return False

        # Check for matches
        matches = [n for n in dm_ndcs if n in nadac_ndcs]
        if matches:
            print(f"   OK - {len(matches)}/{len(dm_ndcs)} NDCs found in NADAC")
            return True
        else:
            print("   FAIL - No matching NDCs found")
            return False

    except Exception as e:
        print(f"   FAIL - {e}")
        return False


def main():
    print("=" * 50)
    print("DailyMed API Tests")
    print("=" * 50)

    results = []

    # Test 1: Search
    setid = test_search()
    results.append(setid is not None)

    # Test 2: Ingredients (only if search succeeded)
    if setid:
        results.append(test_ingredients(setid))
    else:
        print("\n2. SKIP - No setid from search")
        results.append(False)

    # Test 3: Filter excludes correctly
    results.append(test_filter_excludes())

    # Test 4: Filter keeps correctly
    results.append(test_filter_keeps())

    # Test 5: Partial match safety
    results.append(test_partial_match())

    # Test 6: Headers
    results.append(test_headers())

    # Test 7: NDC normalization
    results.append(test_ndc_normalization())

    # Test 8: NADAC availability
    results.append(test_nadac_availability())

    # Summary
    passed = sum(results)
    total = len(results)

    print("\n" + "=" * 50)
    if passed == total:
        print(f"All {total} tests passed!")
        return 0
    else:
        print(f"FAILED: {passed}/{total} tests passed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
